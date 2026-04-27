#[cfg(desktop)]
use std::net::TcpListener;
#[cfg(desktop)]
use std::sync::{Arc, Mutex};

use tauri::Manager;
use tauri_plugin_deep_link::DeepLinkExt;
#[cfg(desktop)]
use tauri_plugin_dialog::{
    DialogExt, MessageDialogBuilder, MessageDialogButtons, MessageDialogKind,
};
use tauri_plugin_log::{Target, TargetKind};
#[cfg(desktop)]
use tauri_plugin_shell::process::CommandChild;
#[cfg(desktop)]
use tauri_plugin_shell::ShellExt;

const GPTME_SERVER_PORT: u16 = 5700;
#[cfg(not(desktop))]
const LOCAL_SERVER_UNSUPPORTED: &str =
    "Local gptme-server management is desktop-only. Connect to a remote gptme instance instead.";

#[cfg(desktop)]
fn is_port_available(port: u16) -> bool {
    TcpListener::bind(format!("127.0.0.1:{}", port)).is_ok()
}

#[cfg(desktop)]
fn is_server_responsive(port: u16) -> bool {
    use std::net::TcpStream;
    use std::time::Duration;
    let addr: std::net::SocketAddr = format!("127.0.0.1:{}", port)
        .parse()
        .expect("valid socket addr");
    TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok()
}

#[cfg(desktop)]
struct ServerProcess(Arc<Mutex<Option<CommandChild>>>);

#[derive(serde::Serialize)]
struct ServerStatus {
    running: bool,
    port: u16,
    port_available: bool,
    manages_local_server: bool,
    existing_server_detected: bool,
}

#[cfg(desktop)]
#[tauri::command]
fn get_server_status(state: tauri::State<'_, ServerProcess>) -> ServerStatus {
    let running = state.0.lock().map(|guard| guard.is_some()).unwrap_or(false);
    let port_available = is_port_available(GPTME_SERVER_PORT);
    ServerStatus {
        running,
        port: GPTME_SERVER_PORT,
        port_available,
        manages_local_server: true,
        existing_server_detected: !running && !port_available,
    }
}

#[cfg(not(desktop))]
#[tauri::command]
fn get_server_status() -> ServerStatus {
    ServerStatus {
        running: false,
        port: GPTME_SERVER_PORT,
        port_available: false,
        manages_local_server: false,
        existing_server_detected: false,
    }
}

#[cfg(desktop)]
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

#[cfg(not(desktop))]
#[tauri::command]
fn stop_server() -> Result<(), String> {
    Err(LOCAL_SERVER_UNSUPPORTED.to_string())
}

#[cfg(desktop)]
#[tauri::command]
async fn start_server(
    app: tauri::AppHandle,
    state: tauri::State<'_, ServerProcess>,
) -> Result<u16, String> {
    {
        let guard = state.0.lock().map_err(|e| format!("Lock error: {}", e))?;
        if guard.is_some() {
            return Err("Server is already running".to_string());
        }
    }

    spawn_server_sidecar(&app, state.0.clone())?;
    Ok(GPTME_SERVER_PORT)
}

#[cfg(not(desktop))]
#[tauri::command]
async fn start_server() -> Result<u16, String> {
    Err(LOCAL_SERVER_UNSUPPORTED.to_string())
}

#[cfg(desktop)]
fn desktop_cors_origin() -> &'static str {
    if cfg!(debug_assertions) {
        "http://localhost:5701"
    } else {
        // Webview origin differs per platform and Tauri version:
        //   - WKWebView (macOS) / WebKitGTK (Linux) send tauri://localhost
        //   - WebView2 (Windows) sends http://tauri.localhost
        //     (and historically https://tauri.localhost)
        // gptme-server accepts a comma-separated list, so allow all known
        // origins and let the running webview match whichever it sends.
        // See: gptme/gptme#2226
        "tauri://localhost,http://tauri.localhost,https://tauri.localhost"
    }
}

#[cfg(desktop)]
fn spawn_server_sidecar(
    app: &tauri::AppHandle,
    state_arc: Arc<Mutex<Option<CommandChild>>>,
) -> Result<(), String> {
    if !is_port_available(GPTME_SERVER_PORT) {
        // Port is occupied — check if a server is already responding there.
        // This is the common crash-recovery case: the gptme-server sidecar
        // outlived the Tauri process and is still listening on the port.
        // Reuse it silently rather than showing a blocking error dialog.
        if is_server_responsive(GPTME_SERVER_PORT) {
            log::info!(
                "Port {} is occupied and a server is already responding — \
                 reusing existing gptme-server (likely a leftover from a previous session)",
                GPTME_SERVER_PORT
            );
            return Ok(());
        }
        return Err(format!("Port {} is already in use", GPTME_SERVER_PORT));
    }

    let cors_origin = desktop_cors_origin();
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

    {
        let mut guard = state_arc.lock().map_err(|e| format!("Lock error: {}", e))?;
        *guard = Some(child);
    }

    let state_for_output = state_arc.clone();
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
                    if let Ok(mut guard) = state_for_output.lock() {
                        *guard = None;
                    }
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(())
}

fn extract_auth_code(url: &url::Url) -> Option<String> {
    let code = url
        .query_pairs()
        .find(|(key, _)| key == "code")
        .map(|(_, value)| value.to_string())?;

    let safe_code: String = code.chars().filter(|c| c.is_ascii_alphanumeric()).collect();
    if safe_code.is_empty() {
        log::warn!("Auth code was empty after sanitization");
        return None;
    }
    Some(safe_code)
}

fn handle_deep_link_urls(app: &tauri::AppHandle, urls: Vec<url::Url>) {
    for url in &urls {
        log::info!("Deep link received: {}", url);

        if let Some(safe_code) = extract_auth_code(url) {
            log::info!("Auth code extracted from deep link, injecting into webview");

            if let Some(window) = app.get_webview_window("main") {
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

#[cfg(desktop)]
fn show_port_conflict_dialog(app: &tauri::AppHandle) {
    let message = format!(
        "Cannot start gptme-server because port {} is already in use.\n\n\
         This usually means another gptme-server instance is already running.\n\n\
         Please stop the existing gptme-server process and restart this application.",
        GPTME_SERVER_PORT
    );

    MessageDialogBuilder::new(app.dialog().clone(), "Port Conflict", message)
        .kind(MessageDialogKind::Error)
        .buttons(MessageDialogButtons::Ok)
        .show(|_result| {});
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default();

    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            log::info!("Single-instance callback: argv={:?}", argv);

            let urls: Vec<url::Url> = argv
                .iter()
                .filter_map(|arg| url::Url::parse(arg).ok())
                .filter(|url| url.scheme() == "gptme")
                .collect();

            if !urls.is_empty() {
                handle_deep_link_urls(app, urls);
            }

            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
            }
        }));
    }

    builder = builder
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
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_deep_link::init());

    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_shell::init());
    }

    builder
        .invoke_handler(tauri::generate_handler![
            get_server_status,
            start_server,
            stop_server,
        ])
        .setup(|app| {
            log::info!("Starting gptme-tauri application");

            #[cfg(desktop)]
            if cfg!(debug_assertions) {
                if let Err(e) = app.deep_link().register_all() {
                    log::warn!("Failed to register deep-link schemes: {}", e);
                } else {
                    log::info!("Deep-link scheme 'gptme://' registered for development");
                }
            }

            if let Ok(Some(urls)) = app.deep_link().get_current() {
                log::info!("App launched with deep link URLs: {:?}", urls);
                handle_deep_link_urls(app.handle(), urls);
            }

            let handle = app.handle().clone();
            app.deep_link().on_open_url(move |event| {
                let urls = event.urls();
                log::info!("Deep link event received: {:?}", urls);
                handle_deep_link_urls(&handle, urls);
            });

            #[cfg(desktop)]
            {
                let child_handle: Arc<Mutex<Option<CommandChild>>> = Arc::new(Mutex::new(None));
                app.manage(ServerProcess(child_handle.clone()));

                let app_handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(err) = spawn_server_sidecar(&app_handle, child_handle) {
                        log::error!("Failed to start gptme-server: {}", err);
                        if err.contains("already in use") {
                            show_port_conflict_dialog(&app_handle);
                        }
                    }
                });
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            #[cfg(desktop)]
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                log::info!("Window close requested, cleaning up gptme-server...");
                cleanup_server_process(window.app_handle());
            }

            #[cfg(not(desktop))]
            let _ = (window, event);
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app_handle, _event| {
            // ExitRequested fires on app-level exit paths that don't emit a
            // per-window CloseRequested first (macOS Cmd+Q, dock-quit, system
            // shutdown). Without this, the sidecar gptme-server outlives the
            // app and squats on port 5700 (gptme/gptme#2237).
            //
            // cleanup_server_process is idempotent — if CloseRequested already
            // killed and cleared the child, this is a no-op.
            #[cfg(desktop)]
            if let tauri::RunEvent::ExitRequested { .. } = _event {
                log::info!("App exit requested, cleaning up gptme-server...");
                cleanup_server_process(_app_handle);
            }
        });
}

#[cfg(desktop)]
fn cleanup_server_process(app: &tauri::AppHandle) {
    // Pre-setup state may not be registered yet (e.g. very early exit).
    let state = match app.try_state::<ServerProcess>() {
        Some(s) => s,
        None => return,
    };
    let arc = state.0.clone();
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
            Ok(_) => log::info!("gptme-server process terminated successfully"),
            Err(e) => log::error!("Failed to terminate gptme-server: {}", e),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[cfg(desktop)]
    fn test_is_port_available_on_unused_port() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        assert!(is_port_available(port));
    }

    #[test]
    #[cfg(desktop)]
    fn test_is_port_available_on_occupied_port() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(!is_port_available(port));
        drop(listener);
        assert!(is_port_available(port));
    }

    #[test]
    #[cfg(desktop)]
    fn test_is_server_responsive_on_listening_port() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(is_server_responsive(port));
        drop(listener);
        assert!(!is_server_responsive(port));
    }

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

    #[test]
    fn test_server_status_serialization() {
        let status = ServerStatus {
            running: false,
            port: 5700,
            port_available: true,
            manages_local_server: true,
            existing_server_detected: false,
        };
        let json = serde_json::to_string(&status).unwrap();
        assert!(json.contains("\"running\":false"));
        assert!(json.contains("\"port\":5700"));
        assert!(json.contains("\"port_available\":true"));
        assert!(json.contains("\"manages_local_server\":true"));
        assert!(json.contains("\"existing_server_detected\":false"));
    }

    #[test]
    #[cfg(desktop)]
    fn test_server_process_initial_state() {
        let handle: Arc<Mutex<Option<tauri_plugin_shell::process::CommandChild>>> =
            Arc::new(Mutex::new(None));
        let running = handle.lock().map(|guard| guard.is_some()).unwrap_or(false);
        assert!(!running);
    }

    #[test]
    #[cfg(desktop)]
    fn test_server_process_state_is_send_sync() {
        fn assert_send_sync<T: Send + Sync>() {}
        assert_send_sync::<ServerProcess>();
    }

    #[test]
    fn test_gptme_server_port_constant() {
        assert_eq!(GPTME_SERVER_PORT, 5700);
    }
}
