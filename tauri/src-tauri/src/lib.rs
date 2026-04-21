use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tauri::Manager;
use tauri_plugin_deep_link::DeepLinkExt;
use tauri_plugin_dialog::{
    DialogExt, MessageDialogBuilder, MessageDialogButtons, MessageDialogKind,
};
use tauri_plugin_log::{Target, TargetKind};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

const GPTME_SERVER_PORT: u16 = 5700;

/// Check if a port is available
fn is_port_available(port: u16) -> bool {
    TcpListener::bind(format!("127.0.0.1:{}", port)).is_ok()
}

/// Managed state holding the gptme-server child process for cleanup on exit.
struct ServerProcess(Arc<Mutex<Option<CommandChild>>>);

#[derive(serde::Serialize)]
struct ServerStatus {
    running: bool,
    port: u16,
    port_available: bool,
}

/// Get the current status of the local gptme-server.
#[tauri::command]
fn get_server_status(state: tauri::State<'_, ServerProcess>) -> ServerStatus {
    let running = state.0.lock().map(|guard| guard.is_some()).unwrap_or(false);
    ServerStatus {
        running,
        port: GPTME_SERVER_PORT,
        port_available: is_port_available(GPTME_SERVER_PORT),
    }
}

/// Stop the local gptme-server process.
#[tauri::command]
fn stop_server(state: tauri::State<'_, ServerProcess>) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|e| format!("Lock error: {}", e))?;
    if let Some(child) = guard.take() {
        log::info!("Stopping gptme-server via IPC command");
        child.kill().map_err(|e| format!("Kill error: {}", e))?;
        log::info!("gptme-server stopped successfully");
        Ok(())
    } else {
        Err("No server process running".to_string())
    }
}

/// Start the local gptme-server process (if not already running).
#[tauri::command]
async fn start_server(
    app: tauri::AppHandle,
    state: tauri::State<'_, ServerProcess>,
) -> Result<u16, String> {
    // Check if already running
    {
        let guard = state.0.lock().map_err(|e| format!("Lock error: {}", e))?;
        if guard.is_some() {
            return Err("Server is already running".to_string());
        }
    }

    if !is_port_available(GPTME_SERVER_PORT) {
        return Err(format!("Port {} is already in use", GPTME_SERVER_PORT));
    }

    let cors_origin = if cfg!(debug_assertions) {
        "http://localhost:5701"
    } else if cfg!(target_os = "macos") {
        "tauri://localhost"
    } else {
        // Linux and Windows use http://tauri.localhost in Tauri v2
        "http://tauri.localhost"
    };

    log::info!(
        "Starting gptme-server on port {} with CORS origin: {}",
        GPTME_SERVER_PORT,
        cors_origin
    );

    let sidecar_command = app
        .shell()
        .sidecar("gptme-server")
        .map_err(|e| format!("Sidecar error: {}", e))?
        .args(["--cors-origin", cors_origin]);

    let (mut rx, child) = sidecar_command
        .spawn()
        .map_err(|e| format!("Spawn error: {}", e))?;

    log::info!(
        "gptme-server started successfully with PID: {}",
        child.pid()
    );

    // Store child process
    {
        let mut guard = state.0.lock().map_err(|e| format!("Lock error: {}", e))?;
        *guard = Some(child);
    }

    // Clone the Arc so the async task can clear state when server terminates
    let state_arc = state.0.clone();

    // Handle server output in background
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                tauri_plugin_shell::process::CommandEvent::Stdout(data) => {
                    let output = String::from_utf8_lossy(&data);
                    for line in output.lines() {
                        if !line.trim().is_empty() {
                            log::info!("[gptme-server] {}", line.trim());
                        }
                    }
                }
                tauri_plugin_shell::process::CommandEvent::Stderr(data) => {
                    let output = String::from_utf8_lossy(&data);
                    for line in output.lines() {
                        if !line.trim().is_empty() {
                            log::warn!("[gptme-server] {}", line.trim());
                        }
                    }
                }
                tauri_plugin_shell::process::CommandEvent::Error(error) => {
                    log::error!("[gptme-server] Process error: {}", error);
                }
                tauri_plugin_shell::process::CommandEvent::Terminated(payload) => {
                    log::warn!(
                        "[gptme-server] Process terminated with code: {:?}",
                        payload.code
                    );
                    // Clear state so get_server_status correctly reports not running
                    if let Ok(mut guard) = state_arc.lock() {
                        *guard = None;
                    }
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(GPTME_SERVER_PORT)
}

/// Providers gptme recognises for in-app API key entry.
///
/// Keep in sync with `PROVIDER_API_KEYS` in `gptme/llm/__init__.py`.
const KNOWN_PROVIDERS: &[&str] = &[
    "openai",
    "anthropic",
    "openrouter",
    "gemini",
    "groq",
    "xai",
    "deepseek",
    "azure",
];

/// Resolve the env var name a provider's API key is stored under.
///
/// Matches `PROVIDER_API_KEYS` in `gptme/llm/__init__.py` — the `azure` provider
/// uses `AZURE_OPENAI_API_KEY`, all others use `{PROVIDER_UPPER}_API_KEY`.
fn provider_env_var(provider: &str) -> String {
    match provider {
        "azure" => "AZURE_OPENAI_API_KEY".to_string(),
        other => format!("{}_API_KEY", other.to_uppercase()),
    }
}

/// Resolve the user's gptme config path.
///
/// gptme hardcodes `~/.config/gptme/config.toml` on every platform (see
/// `gptme/config/user.py`), so we do the same. Returns an error if `HOME`
/// (or `USERPROFILE` on Windows) is not set.
fn gptme_config_path() -> Result<PathBuf, String> {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| "HOME (or USERPROFILE) not set".to_string())?;
    Ok(PathBuf::from(home)
        .join(".config")
        .join("gptme")
        .join("config.toml"))
}

/// Validate a user-supplied API key.
///
/// We only enforce lightweight sanity checks here — format validation is the
/// provider's responsibility. Rejects empty keys, anything obviously not a
/// secret (newlines/control chars), and unreasonably long input.
fn validate_api_key(api_key: &str) -> Result<(), String> {
    let trimmed = api_key.trim();
    if trimmed.is_empty() {
        return Err("API key is empty".to_string());
    }
    if trimmed.len() > 4096 {
        return Err("API key is too long".to_string());
    }
    if trimmed.chars().any(|c| c.is_control()) {
        return Err("API key contains control characters".to_string());
    }
    Ok(())
}

/// Write an API key into the user's gptme config under `[env]`.
///
/// Uses `toml_edit` so existing comments and formatting in the config file
/// survive the edit. Creates the config directory and file if they do not
/// exist yet.
///
/// The caller is expected to trigger a server restart after this returns —
/// `gptme-server` caches config on startup, so a running process will not
/// see the new key until it is restarted.
#[tauri::command]
fn save_api_key(provider: String, api_key: String) -> Result<(), String> {
    if !KNOWN_PROVIDERS.contains(&provider.as_str()) {
        return Err(format!("Unknown provider: {}", provider));
    }
    validate_api_key(&api_key)?;
    let env_var = provider_env_var(&provider);
    let trimmed = api_key.trim().to_string();
    let path = gptme_config_path()?;

    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to create {}: {}", parent.display(), e))?;
    }

    let existing = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(e) => return Err(format!("Failed to read {}: {}", path.display(), e)),
    };

    let mut doc = existing
        .parse::<toml_edit::DocumentMut>()
        .map_err(|e| format!("Failed to parse {}: {}", path.display(), e))?;

    if !doc.contains_key("env") {
        doc["env"] = toml_edit::Item::Table(toml_edit::Table::new());
    }
    let env_table = doc["env"]
        .as_table_mut()
        .ok_or_else(|| "[env] exists but is not a table".to_string())?;
    env_table[&env_var] = toml_edit::value(trimmed);

    std::fs::write(&path, doc.to_string())
        .map_err(|e| format!("Failed to write {}: {}", path.display(), e))?;

    log::info!("Saved {} to {}", env_var, path.display());
    Ok(())
}

/// Extract and sanitize an auth code from a deep-link URL.
///
/// Parses `gptme://pairing-complete?code=<hex>` or `gptme://callback?code=<hex>`
/// and returns the sanitized (alphanumeric-only) code, or `None`.
fn extract_auth_code(url: &url::Url) -> Option<String> {
    let code = url
        .query_pairs()
        .find(|(key, _)| key == "code")
        .map(|(_, value)| value.to_string())?;

    // Sanitize: only allow alphanumeric characters (codes should be hex)
    let safe_code: String = code.chars().filter(|c| c.is_ascii_alphanumeric()).collect();
    if safe_code.is_empty() {
        log::warn!("Auth code was empty after sanitization");
        return None;
    }
    Some(safe_code)
}

/// Extract auth code from a gptme:// deep-link URL and inject it into the webview.
///
/// Sets the URL hash to `#code=<hex>` and reloads the page, which triggers
/// the webui's existing auth code exchange flow in ApiContext.
fn handle_deep_link_urls(app: &tauri::AppHandle, urls: Vec<url::Url>) {
    for url in &urls {
        log::info!("Deep link received: {}", url);

        if let Some(safe_code) = extract_auth_code(url) {
            log::info!("Auth code extracted from deep link, injecting into webview");

            if let Some(window) = app.get_webview_window("main") {
                // Set URL hash with the auth code and reload the page.
                // The webui's ApiContext checks window.location.hash on mount
                // and automatically exchanges the code for a token via fleet.gptme.ai.
                let js = format!(
                    "window.location.hash = '#code={}'; window.location.reload();",
                    safe_code
                );
                if let Err(e) = window.eval(&js) {
                    log::error!("Failed to inject auth code into webview: {}", e);
                }
            }
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default();

    // On desktop (Linux/Windows), deep links spawn a new process instance.
    // The single-instance plugin with deep-link feature catches these and
    // forwards the URL to the already-running instance instead.
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            log::info!("Single-instance callback: argv={:?}", argv);

            // On Linux/Windows, deep-link URLs arrive as CLI arguments
            let urls: Vec<url::Url> = argv
                .iter()
                .filter_map(|arg| url::Url::parse(arg).ok())
                .filter(|url| url.scheme() == "gptme")
                .collect();

            if !urls.is_empty() {
                handle_deep_link_urls(app, urls);
            }

            // Focus the main window when another instance tries to open
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
            }
        }));
    }

    builder
        .plugin(
            tauri_plugin_log::Builder::new()
                .targets([
                    Target::new(TargetKind::Stdout),
                    Target::new(TargetKind::LogDir {
                        file_name: Some("gptme-tauri".to_string()),
                    }),
                ])
                .level(log::LevelFilter::Info)
                .build(),
        )
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_deep_link::init())
        .invoke_handler(tauri::generate_handler![
            get_server_status,
            start_server,
            stop_server,
            save_api_key,
        ])
        .setup(|app| {
            log::info!("Starting gptme-tauri application");

            // Register deep-link schemes at runtime (needed for dev on Linux/Windows)
            #[cfg(desktop)]
            if cfg!(debug_assertions) {
                if let Err(e) = app.deep_link().register_all() {
                    log::warn!("Failed to register deep-link schemes: {}", e);
                } else {
                    log::info!("Deep-link scheme 'gptme://' registered for development");
                }
            }

            // Check if the app was launched via a deep link
            if let Ok(Some(urls)) = app.deep_link().get_current() {
                log::info!("App launched with deep link URLs: {:?}", urls);
                handle_deep_link_urls(app.handle(), urls);
            }

            // Listen for deep-link events (macOS sends these to the running app)
            let handle = app.handle().clone();
            app.deep_link().on_open_url(move |event| {
                let urls = event.urls();
                log::info!("Deep link event received: {:?}", urls);
                handle_deep_link_urls(&handle, urls);
            });

            let app_handle = app.handle().clone();

            // Shared handle to the child process — written by the spawn task,
            // read by the window-close handler for cleanup.
            let child_handle: Arc<Mutex<Option<CommandChild>>> = Arc::new(Mutex::new(None));
            let child_for_spawn = child_handle.clone();

            // Register state so the window-close handler can access it.
            app.manage(ServerProcess(child_handle));

            // Spawn gptme-server with output capture
            tauri::async_runtime::spawn(async move {
                // Check if port is available before starting
                if !is_port_available(GPTME_SERVER_PORT) {
                    log::error!(
                        "Port {} is already in use. Another gptme-server instance may be running.",
                        GPTME_SERVER_PORT
                    );

                    let message = format!(
                        "Cannot start gptme-server because port {} is already in use.\n\n\
                        This usually means another gptme-server instance is already running.\n\n\
                        Please stop the existing gptme-server process and restart this application.",
                        GPTME_SERVER_PORT
                    );

                    MessageDialogBuilder::new(
                        app_handle.dialog().clone(),
                        "Port Conflict",
                        message,
                    )
                    .kind(MessageDialogKind::Error)
                    .buttons(MessageDialogButtons::Ok)
                    .show(|_result| {});

                    return;
                }

                // Determine CORS origin based on build mode and platform.
                // Tauri v2 uses different URL schemes per platform:
                // - macOS: tauri://localhost (custom Tauri protocol)
                // - Linux/Windows: http://tauri.localhost
                let cors_origin = if cfg!(debug_assertions) {
                    "http://localhost:5701" // Dev mode
                } else if cfg!(target_os = "macos") {
                    "tauri://localhost" // macOS production
                } else {
                    "http://tauri.localhost" // Linux/Windows production
                };

                log::info!(
                    "Port {} is available, starting gptme-server with CORS origin: {}",
                    GPTME_SERVER_PORT,
                    cors_origin
                );

                let sidecar_command = match app_handle
                    .shell()
                    .sidecar("gptme-server")
                {
                    Ok(s) => s.args(["--cors-origin", cors_origin]),
                    Err(e) => {
                        log::error!("Failed to find gptme-server sidecar: {}", e);
                        return;
                    }
                };

                match sidecar_command.spawn() {
                    Ok((mut rx, child)) => {
                        log::info!(
                            "gptme-server started successfully with PID: {}",
                            child.pid()
                        );

                        // Store child process for later cleanup
                        if let Ok(mut guard) = child_for_spawn.lock() {
                            *guard = Some(child);
                        }

                        // Clone the Arc so the async task can clear state when server terminates
                        let child_for_output = child_for_spawn.clone();

                        // Handle server output
                        tauri::async_runtime::spawn(async move {
                            while let Some(event) = rx.recv().await {
                                match event {
                                    tauri_plugin_shell::process::CommandEvent::Stdout(data) => {
                                        let output = String::from_utf8_lossy(&data);
                                        for line in output.lines() {
                                            if !line.trim().is_empty() {
                                                log::info!("[gptme-server] {}", line.trim());
                                            }
                                        }
                                    }
                                    tauri_plugin_shell::process::CommandEvent::Stderr(data) => {
                                        let output = String::from_utf8_lossy(&data);
                                        for line in output.lines() {
                                            if !line.trim().is_empty() {
                                                log::warn!("[gptme-server] {}", line.trim());
                                            }
                                        }
                                    }
                                    tauri_plugin_shell::process::CommandEvent::Error(error) => {
                                        log::error!("[gptme-server] Process error: {}", error);
                                    }
                                    tauri_plugin_shell::process::CommandEvent::Terminated(
                                        payload,
                                    ) => {
                                        log::warn!(
                                            "[gptme-server] Process terminated with code: {:?}",
                                            payload.code
                                        );
                                        // Clear state so get_server_status correctly reports not running
                                        if let Ok(mut guard) = child_for_output.lock() {
                                            *guard = None;
                                        }
                                        break;
                                    }
                                    _ => {}
                                }
                            }
                        });
                    }
                    Err(e) => {
                        log::error!("Failed to start gptme-server: {}", e);
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                log::info!("Window close requested, cleaning up gptme-server...");

                let arc = window.state::<ServerProcess>().0.clone();
                let mut guard = match arc.lock() {
                    Ok(g) => g,
                    Err(_) => {
                        log::error!("Failed to acquire lock on server process");
                        return;
                    }
                };
                if let Some(child) = guard.take() {
                    log::info!("Terminating gptme-server process...");
                    match child.kill() {
                        Ok(_) => {
                            log::info!("gptme-server process terminated successfully");
                        }
                        Err(e) => {
                            log::error!("Failed to terminate gptme-server: {}", e);
                        }
                    }
                } else {
                    log::warn!("No gptme-server process found to terminate");
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── Port availability ──────────────────────────────────────────

    #[test]
    fn test_is_port_available_on_unused_port() {
        // Bind to port 0 to get an OS-assigned free port, then release it
        // and verify is_port_available returns true for that port.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        assert!(is_port_available(port));
    }

    #[test]
    fn test_is_port_available_on_occupied_port() {
        // Bind a port so it's occupied, then verify is_port_available returns false.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(!is_port_available(port));
        drop(listener);
        // After dropping the listener, the port should be available again.
        assert!(is_port_available(port));
    }

    // ── Deep-link auth code extraction ─────────────────────────────

    #[test]
    fn test_extract_auth_code_valid() {
        let url = url::Url::parse("gptme://pairing-complete?code=abc123def").unwrap();
        assert_eq!(extract_auth_code(&url), Some("abc123def".to_string()));
    }

    #[test]
    fn test_extract_auth_code_hex() {
        let url = url::Url::parse("gptme://callback?code=deadBEEF42").unwrap();
        assert_eq!(extract_auth_code(&url), Some("deadBEEF42".to_string()));
    }

    #[test]
    fn test_extract_auth_code_strips_special_chars() {
        // XSS attempt: special characters should be stripped
        let url =
            url::Url::parse("gptme://callback?code=abc%3Cscript%3Ealert(1)%3C/script%3E").unwrap();
        let code = extract_auth_code(&url).unwrap();
        assert_eq!(code, "abcscriptalert1script");
    }

    #[test]
    fn test_extract_auth_code_empty_after_sanitization() {
        let url = url::Url::parse("gptme://callback?code=%3C%3E%22%27").unwrap();
        assert_eq!(extract_auth_code(&url), None);
    }

    #[test]
    fn test_extract_auth_code_missing() {
        let url = url::Url::parse("gptme://pairing-complete?other=value").unwrap();
        assert_eq!(extract_auth_code(&url), None);
    }

    #[test]
    fn test_extract_auth_code_no_query() {
        let url = url::Url::parse("gptme://pairing-complete").unwrap();
        assert_eq!(extract_auth_code(&url), None);
    }

    // ── ServerStatus serialization ─────────────────────────────────

    #[test]
    fn test_server_status_serialization() {
        let status = ServerStatus {
            running: false,
            port: 5700,
            port_available: true,
        };
        let json = serde_json::to_string(&status).unwrap();
        assert!(json.contains("\"running\":false"));
        assert!(json.contains("\"port\":5700"));
        assert!(json.contains("\"port_available\":true"));
    }

    // ── ServerProcess state logic ─────────────────────────────────

    #[test]
    fn test_server_process_initial_state() {
        // With no server process, the guard should be None.
        let handle: Arc<Mutex<Option<tauri_plugin_shell::process::CommandChild>>> =
            Arc::new(Mutex::new(None));
        let running = handle.lock().map(|guard| guard.is_some()).unwrap_or(false);
        assert!(!running);
    }

    #[test]
    fn test_server_process_state_is_send_sync() {
        // ServerProcess must be Send + Sync for Tauri's managed state.
        fn assert_send_sync<T: Send + Sync>() {}
        assert_send_sync::<ServerProcess>();
    }

    #[test]
    fn test_gptme_server_port_constant() {
        assert_eq!(GPTME_SERVER_PORT, 5700);
    }

    // --- save_api_key helpers ---

    #[test]
    fn test_provider_env_var_standard() {
        assert_eq!(provider_env_var("openai"), "OPENAI_API_KEY");
        assert_eq!(provider_env_var("anthropic"), "ANTHROPIC_API_KEY");
        assert_eq!(provider_env_var("openrouter"), "OPENROUTER_API_KEY");
        assert_eq!(provider_env_var("groq"), "GROQ_API_KEY");
    }

    #[test]
    fn test_provider_env_var_azure() {
        // Azure uses a non-uniform env var name in gptme's PROVIDER_API_KEYS.
        assert_eq!(provider_env_var("azure"), "AZURE_OPENAI_API_KEY");
    }

    #[test]
    fn test_validate_api_key_accepts_normal_key() {
        assert!(validate_api_key("sk-ant-api03-abc123").is_ok());
        assert!(validate_api_key("  sk-xyz  ").is_ok()); // whitespace trimmed
    }

    #[test]
    fn test_validate_api_key_rejects_empty() {
        assert!(validate_api_key("").is_err());
        assert!(validate_api_key("   ").is_err());
    }

    #[test]
    fn test_validate_api_key_rejects_control_chars() {
        assert!(validate_api_key("sk-ant\nbad").is_err());
        assert!(validate_api_key("sk-ant\x00bad").is_err());
    }

    #[test]
    fn test_validate_api_key_rejects_too_long() {
        let long_key = "a".repeat(5000);
        assert!(validate_api_key(&long_key).is_err());
    }

    #[test]
    fn test_known_providers_covers_llm_provider_api_keys() {
        // Sanity check that the whitelist matches the Python source of truth.
        // If this drifts, save_api_key will reject providers gptme supports.
        for provider in [
            "openai",
            "anthropic",
            "openrouter",
            "gemini",
            "groq",
            "xai",
            "deepseek",
            "azure",
        ] {
            assert!(
                KNOWN_PROVIDERS.contains(&provider),
                "expected {} in KNOWN_PROVIDERS",
                provider
            );
        }
    }
}
