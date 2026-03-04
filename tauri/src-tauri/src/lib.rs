use std::net::TcpListener;
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
    } else {
        "tauri://localhost"
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

/// Extract auth code from a gptme:// deep-link URL and inject it into the webview.
///
/// Sets the URL hash to `#code=<hex>` and reloads the page, which triggers
/// the webui's existing auth code exchange flow in ApiContext.
fn handle_deep_link_urls(app: &tauri::AppHandle, urls: Vec<url::Url>) {
    for url in &urls {
        log::info!("Deep link received: {}", url);

        // Parse gptme://pairing-complete?code=<hex> or gptme://callback?code=<hex>
        if let Some(code) = url
            .query_pairs()
            .find(|(key, _)| key == "code")
            .map(|(_, value)| value.to_string())
        {
            // Sanitize: only allow alphanumeric characters (codes should be hex)
            let safe_code: String = code.chars().filter(|c| c.is_ascii_alphanumeric()).collect();
            if safe_code.is_empty() {
                log::warn!("Auth code was empty after sanitization");
                continue;
            }

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

                // Determine CORS origin based on build mode
                let cors_origin = if cfg!(debug_assertions) {
                    "http://localhost:5701" // Dev mode
                } else {
                    "tauri://localhost" // Production mode
                };

                log::info!(
                    "Port {} is available, starting gptme-server with CORS origin: {}",
                    GPTME_SERVER_PORT,
                    cors_origin
                );

                let sidecar_command = app_handle
                    .shell()
                    .sidecar("gptme-server")
                    .unwrap()
                    .args(["--cors-origin", cors_origin]);

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
