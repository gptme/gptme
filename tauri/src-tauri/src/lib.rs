#[cfg(desktop)]
use std::net::TcpListener;
#[cfg(desktop)]
use std::sync::atomic::{AtomicBool, Ordering};
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
#[cfg(desktop)]
use tauri_plugin_updater::UpdaterExt;

const GPTME_SERVER_PORT: u16 = 5700;
#[cfg(not(desktop))]
const LOCAL_SERVER_UNSUPPORTED: &str =
    "Local gptme-server management is desktop-only. Connect to a remote gptme instance instead.";

#[cfg(desktop)]
fn is_port_available(port: u16) -> bool {
    TcpListener::bind(format!("127.0.0.1:{}", port)).is_ok()
}

#[cfg(desktop)]
async fn is_server_responsive(port: u16) -> bool {
    use std::time::Duration;
    use tokio::net::TcpStream;
    use tokio::time::timeout;
    let addr = format!("127.0.0.1:{}", port);
    timeout(Duration::from_millis(500), TcpStream::connect(&addr))
        .await
        .map(|r| r.is_ok())
        .unwrap_or(false)
}

/// Outcome of probing an existing gptme-server to decide whether this app can
/// reuse it.
#[cfg(desktop)]
#[derive(Debug, PartialEq, Eq)]
enum ServerProbe {
    /// No TCP connection / no parseable HTTP response — not a usable server
    /// (nothing there, or a non-responsive foreign process).
    Unreachable,
    /// Responded in a way the app can use (2xx, or any non-auth status such as
    /// 404 from a healthy but differently-routed server).
    Usable,
    /// Responded but rejected the request as unauthenticated (401/403). The app
    /// has no token for it, so silently reusing it would 401 every API call.
    AuthRequired,
}

/// Parse the numeric status code from an HTTP/1.x status line, e.g.
/// `"HTTP/1.1 401 Unauthorized"`. Returns `None` if the bytes don't begin with
/// a recognizable HTTP status line.
#[cfg(desktop)]
fn parse_http_status(bytes: &[u8]) -> Option<u16> {
    let head = std::str::from_utf8(bytes).ok()?;
    let line = head.lines().next()?;
    let mut parts = line.split_whitespace();
    let version = parts.next()?;
    if !version.starts_with("HTTP/") {
        return None;
    }
    parts.next()?.parse::<u16>().ok()
}

/// Probe an occupied port with a real HTTP request to decide whether this app
/// can reuse the server there. A bare TCP connect (`is_server_responsive`)
/// cannot distinguish a usable server from a leftover auth-gated one that 401s
/// every API call, leaving the app silently degraded (gptme/gptme#2457,
/// finding F2). Uses a minimal raw HTTP/1.1 request over the existing tokio
/// `TcpStream` so we don't pull in a full HTTP client dependency. `/api/v2/models`
/// is auth-gated, so it cleanly separates 2xx (usable) from 401/403 (auth-gated).
#[cfg(desktop)]
async fn probe_server(port: u16) -> ServerProbe {
    use std::time::Duration;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpStream;
    use tokio::time::timeout;

    let addr = format!("127.0.0.1:{}", port);
    let probe = async {
        let mut stream = TcpStream::connect(&addr).await.ok()?;
        let request = format!(
            "GET /api/v2/models HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\n\r\n",
            port
        );
        stream.write_all(request.as_bytes()).await.ok()?;
        let mut buf = [0u8; 256];
        let n = stream.read(&mut buf).await.ok()?;
        if n == 0 {
            return None;
        }
        parse_http_status(&buf[..n])
    };

    match timeout(Duration::from_millis(750), probe).await {
        Ok(Some(401)) | Ok(Some(403)) => ServerProbe::AuthRequired,
        Ok(Some(_)) => ServerProbe::Usable,
        // Connect/read failed, response wasn't HTTP, or the probe timed out:
        // treat as not-reusable so we never silently adopt a port held by
        // something that isn't a working gptme-server.
        Ok(None) | Err(_) => ServerProbe::Unreachable,
    }
}

#[cfg(desktop)]
struct ServerProcess {
    child: Arc<Mutex<Option<CommandChild>>>,
    // True if we started or reused a gptme-server; false if startup failed
    // (port occupied by an unresponsive foreign process).  Used in cleanup to
    // avoid killing a process that we never owned.
    owns_port: Arc<AtomicBool>,
    // Held at app setup so #[tauri::command] functions that need the handle
    // can fetch it from state instead of taking AppHandle as a command
    // parameter — the latter would break tests because AppHandle does not
    // implement Deserialize for MockRuntime command dispatch.
    app_handle: Arc<Mutex<Option<tauri::AppHandle>>>,
}

#[derive(Debug, serde::Deserialize, serde::Serialize, PartialEq, Eq)]
struct ServerStatus {
    running: bool,
    port: u16,
    port_available: bool,
    manages_local_server: bool,
    existing_server_detected: bool,
}

#[cfg(desktop)]
#[tauri::command]
async fn get_server_status(state: tauri::State<'_, ServerProcess>) -> Result<ServerStatus, String> {
    let running = state
        .child
        .lock()
        .map(|guard| guard.is_some())
        .unwrap_or(false);
    let port_available = is_port_available(GPTME_SERVER_PORT);
    // Only probe TCP when the port is occupied but we're not managing it —
    // avoids false-positive existing_server_detected during TIME_WAIT after stop_server.
    let existing_server_detected =
        !running && !port_available && is_server_responsive(GPTME_SERVER_PORT).await;
    Ok(ServerStatus {
        running,
        port: GPTME_SERVER_PORT,
        port_available,
        manages_local_server: true,
        existing_server_detected,
    })
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
    let mut guard = state
        .child
        .lock()
        .map_err(|e| format!("Lock error: {}", e))?;
    if let Some(child) = guard.take() {
        log::info!("Stopping gptme-server via IPC command");
        // Kill uvicorn workers before the parent; mirrors cleanup_server_process.
        kill_subprocesses(child.pid());
        child.kill().map_err(|e| format!("Kill error: {}", e))?;
        // Synchronously clear owns_port so cleanup_server_process doesn't
        // call kill_server_on_port(5700) after a user-initiated stop, which
        // could kill an unrelated process that bound to the port afterward.
        state.owns_port.store(false, Ordering::Relaxed);
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
async fn start_server(state: tauri::State<'_, ServerProcess>) -> Result<u16, String> {
    {
        let guard = state
            .child
            .lock()
            .map_err(|e| format!("Lock error: {}", e))?;
        if guard.is_some() {
            return Err("Server is already running".to_string());
        }
    }

    let app = state
        .app_handle
        .lock()
        .map_err(|e| format!("Lock error: {}", e))?
        .clone()
        .ok_or_else(|| "ServerProcess.app_handle not set".to_string())?;
    spawn_server_sidecar(&app, state.child.clone(), state.owns_port.clone()).await?;
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
async fn spawn_server_sidecar(
    app: &tauri::AppHandle,
    state_arc: Arc<Mutex<Option<CommandChild>>>,
    owns_port: Arc<AtomicBool>,
) -> Result<(), String> {
    if !is_port_available(GPTME_SERVER_PORT) {
        // Port is occupied — probe whether we can actually use the server there.
        // A bare TCP connect isn't enough: a leftover auth-gated gptme-server
        // accepts connections but 401s every API call, leaving the app silently
        // degraded (gptme/gptme#2457, finding F2). Only reuse a server we can
        // actually talk to.
        match probe_server(GPTME_SERVER_PORT).await {
            ServerProbe::Usable => {
                // Common crash-recovery case: the gptme-server sidecar outlived
                // the Tauri process and is still serving. Reuse it silently
                // rather than showing a blocking error dialog.
                log::info!(
                    "Port {} is occupied and a usable server is responding — \
                     reusing existing gptme-server (likely a leftover from a previous session)",
                    GPTME_SERVER_PORT
                );
                // Mark that we own (reuse) this port so cleanup_server_process
                // knows it should kill it on exit.
                owns_port.store(true, Ordering::Relaxed);
                return Ok(());
            }
            ServerProbe::AuthRequired => {
                // Responsive but rejects us as unauthenticated. Reusing it would
                // 401 every request; do NOT set owns_port (we didn't start it).
                log::warn!(
                    "Port {} is occupied by a gptme-server that requires authentication \
                     this app doesn't have — refusing to reuse it",
                    GPTME_SERVER_PORT
                );
                return Err(format!(
                    "Another gptme-server is already running on port {} and requires \
                     authentication this app doesn't have. Stop that server (or restart \
                     it without a token) and try again.",
                    GPTME_SERVER_PORT
                ));
            }
            ServerProbe::Unreachable => {
                // Port is occupied by a non-responsive / non-HTTP foreign process —
                // do NOT set owns_port; cleanup must not kill a process we never started.
                return Err(format!("Port {} is already in use", GPTME_SERVER_PORT));
            }
        }
    }

    let cors_origin = desktop_cors_origin();
    log::info!(
        "Starting gptme-server on port {} with CORS origin: {}",
        GPTME_SERVER_PORT,
        cors_origin
    );

    // --watch-pid: belt-and-suspenders backup for cleanup_server_process.
    // On macOS, Cmd+Q can terminate the Tauri process before our pkill/child.kill()
    // syscalls finish (gptme/gptme#2260). The PyInstaller bootloader (the direct
    // parent of the Python gptme-server process) survives reparenting to launchd,
    // so watching getppid() from inside the Python child is insufficient — it
    // still sees the bootloader. Pass the Tauri PID explicitly so the server
    // self-terminates when Tauri itself disappears.
    let tauri_pid = std::process::id().to_string();
    let sidecar_command = app
        .shell()
        .sidecar("gptme-server")
        .map_err(|e| format!("Sidecar error: {}", e))?
        .args([
            "--cors-origin",
            cors_origin,
            "--watch-pid",
            tauri_pid.as_str(),
        ]);

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
    owns_port.store(true, Ordering::Relaxed);

    let state_for_output = state_arc.clone();
    let owns_port_for_output = owns_port.clone();
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
                    // PyInstaller onefile bundles use a launcher process that
                    // spawns the actual Python interpreter as a child. When the
                    // launcher dies (cleanly or via SIGKILL), the Python child
                    // can survive — reparented to init — and keep port 5700
                    // bound until something explicitly kills it.  Verify the
                    // port is actually free before declaring the server gone;
                    // otherwise leave owns_port=true so cleanup_server_process
                    // catches the orphan on app exit (#2260).
                    if is_port_available(GPTME_SERVER_PORT) {
                        owns_port_for_output.store(false, Ordering::Relaxed);
                    } else {
                        log::warn!(
                            "[gptme-server] Sidecar exited but port {} still in use — \
                             likely an orphaned subprocess; deferring port cleanup to app exit",
                            GPTME_SERVER_PORT
                        );
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
async fn check_for_updates(app: tauri::AppHandle) {
    // Respect user opt-out via environment variable
    if std::env::var("GPTME_DISABLE_AUTO_UPDATE").is_ok() {
        log::debug!("Update check disabled via GPTME_DISABLE_AUTO_UPDATE");
        return;
    }

    // Skip if pubkey is absent or still a placeholder (key not yet configured)
    let pubkey = app
        .config()
        .plugins
        .0
        .get("updater")
        .and_then(|v| v.get("pubkey"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if pubkey.is_empty() || pubkey.starts_with("PLACEHOLDER") {
        log::debug!("Update check skipped: updater public key is not configured");
        return;
    }

    let updater = match app.updater() {
        Ok(u) => u,
        Err(e) => {
            log::debug!("Updater not available (pubkey not configured?): {}", e);
            return;
        }
    };
    let update = match updater.check().await {
        Ok(Some(u)) => u,
        Ok(None) => {
            log::debug!("No update available");
            return;
        }
        Err(e) => {
            log::warn!("Update check failed: {}", e);
            return;
        }
    };
    let version = update.version.clone();
    let body = update.body.clone().unwrap_or_default();
    log::info!("Update available: v{}", version);
    let msg = format!(
        "A new version of gptme is available: v{}\n\n{}\n\nDownload and install now?",
        version,
        body.lines().take(5).collect::<Vec<_>>().join("\n")
    );
    let (tx, rx) = tokio::sync::oneshot::channel::<bool>();
    MessageDialogBuilder::new(app.dialog().clone(), "Update Available", msg)
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Install Update".to_string(),
            "Later".to_string(),
        ))
        .show(move |accepted| {
            let _ = tx.send(accepted);
        });
    if rx.await.unwrap_or(false) {
        log::info!(
            "User accepted update, downloading and installing v{}",
            version
        );
        if let Err(e) = update.download_and_install(|_, _| {}, || {}).await {
            log::error!("Update install failed: {}", e);
            MessageDialogBuilder::new(
                app.dialog().clone(),
                "Update Failed",
                format!("Failed to install update v{}: {}", version, e),
            )
            .kind(MessageDialogKind::Error)
            .buttons(MessageDialogButtons::Ok)
            .show(|_| {});
        } else {
            log::info!("Update installed, restarting...");
            app.restart();
        }
    }
}

#[cfg(desktop)]
fn show_port_conflict_dialog(app: &tauri::AppHandle, message: String) {
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
        builder = builder.plugin(tauri_plugin_updater::Builder::new().build());
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
                let owns_port: Arc<AtomicBool> = Arc::new(AtomicBool::new(false));
                app.manage(ServerProcess {
                    child: child_handle.clone(),
                    owns_port: owns_port.clone(),
                    app_handle: Arc::new(Mutex::new(Some(app.handle().clone()))),
                });

                let app_handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(err) =
                        spawn_server_sidecar(&app_handle, child_handle, owns_port).await
                    {
                        log::error!("Failed to start gptme-server: {}", err);
                        if err.contains("already in use") {
                            show_port_conflict_dialog(
                                &app_handle,
                                format!(
                                    "Cannot start gptme-server because port {} is already in use.\n\n\
                                     This usually means another gptme-server instance is already running.\n\n\
                                     Please stop the existing gptme-server process and restart this application.",
                                    GPTME_SERVER_PORT
                                ),
                            );
                        } else if err.contains("requires authentication") {
                            show_port_conflict_dialog(
                                &app_handle,
                                format!(
                                    "Cannot start gptme-server because port {} is occupied by another \
                                     gptme-server that requires authentication this app doesn't have.\n\n\
                                     Stop that server (or restart it without a token) and restart this application.",
                                    GPTME_SERVER_PORT
                                ),
                            );
                        }
                    }
                });
            }

            #[cfg(desktop)]
            {
                let update_handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    check_for_updates(update_handle).await;
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
        .run(|app_handle, event| {
            // cleanup_server_process is idempotent (owns_port flag gates all
            // work), so calling it from multiple paths is safe.
            //
            // Two paths need coverage:
            //
            // 1. ExitRequested — fires when all windows are destroyed through
            //    the normal tao event loop (e.g. last window closed via Cmd+W).
            //    Run cleanup synchronously and let the exit proceed; do NOT
            //    call prevent_exit() + exit(0), which creates an infinite loop
            //    (exit(0) → RequestExit → ExitRequested → exit(0) → …).
            //
            // 2. RunEvent::Exit (LoopDestroyed) — fires on macOS Cmd+Q /
            //    dock-quit.  tao does NOT implement applicationShouldTerminate:,
            //    so macOS calls applicationWillTerminate: → AppState::exit() →
            //    Event::LoopDestroyed → RunEvent::Exit directly, bypassing
            //    ExitRequested entirely.  This was the root cause of #2260
            //    surviving every previous fix: the cleanup code never ran.
            #[cfg(desktop)]
            match event {
                tauri::RunEvent::ExitRequested { .. } => {
                    log::info!("Exit requested, cleaning up gptme-server...");
                    cleanup_server_process(app_handle);
                }
                tauri::RunEvent::Exit => {
                    log::info!("App exiting (LoopDestroyed / Cmd+Q), cleaning up gptme-server...");
                    cleanup_server_process(app_handle);
                }
                _ => {}
            }
            #[cfg(not(desktop))]
            let _ = (app_handle, event);
        });
}

#[cfg(desktop)]
fn cleanup_server_process(app: &tauri::AppHandle) {
    // Pre-setup state may not be registered yet (e.g. very early exit).
    let state = match app.try_state::<ServerProcess>() {
        Some(s) => s,
        None => return,
    };

    // Snapshot ownership before we start mutating state — kill_server_on_port
    // must run unconditionally below if we own the port, regardless of whether
    // we have a tracked child handle.
    let owns_port_at_entry = state.owns_port.load(Ordering::Relaxed);

    let arc = state.child.clone();
    let mut guard = match arc.lock() {
        Ok(g) => g,
        Err(_) => {
            log::error!("Failed to acquire lock on server process");
            return;
        }
    };
    if let Some(child) = guard.take() {
        let pid = child.pid();
        log::info!("Terminating gptme-server process (PID {})...", pid);
        // Kill child processes first (e.g. uvicorn workers spawned by gptme-server,
        // or the Python child of a PyInstaller onefile launcher).  child.kill()
        // only sends SIGKILL to the direct child; without this step, subprocesses
        // become orphans that keep port 5700 occupied (#2260).
        kill_subprocesses(pid);
        match child.kill() {
            Ok(_) => log::info!("gptme-server process (PID {}) terminated", pid),
            Err(e) => log::error!("Failed to terminate gptme-server (PID {}): {}", pid, e),
        }
    }

    // Always run port cleanup when we own the port.  This catches:
    //   1. PyInstaller onefile orphans — the launcher's Python child survives
    //      child.kill() and gets reparented to init, still holding port 5700.
    //      pkill -P only kills processes whose PARENT matches at the moment
    //      it runs; the orphan reparented to init slips past that check.
    //   2. The reuse path (#2258) where no CommandChild handle was tracked,
    //      so the `if let Some(child)` branch above didn't fire.
    //   3. Any leftover server process holding the port for any other reason.
    // Skipped only when we never owned the port (e.g. startup failed against
    // a non-responsive foreign process — owns_port stays false in that case),
    // so this branch will not kill unrelated foreign processes.
    if owns_port_at_entry {
        log::info!(
            "Cleaning up any remaining process on port {}...",
            GPTME_SERVER_PORT
        );
        kill_server_on_port(GPTME_SERVER_PORT);
        state.owns_port.store(false, Ordering::Relaxed);
    }
}

// Kill all direct children of `pid` (e.g. uvicorn workers).  The parent is
// killed separately via CommandChild::kill() so we don't need /T here.
#[cfg(unix)]
fn kill_subprocesses(pid: u32) {
    let _ = std::process::Command::new("pkill")
        .args(["-9", "-P", &pid.to_string()])
        .status();
}

#[cfg(windows)]
fn kill_subprocesses(pid: u32) {
    // taskkill /T kills the whole process tree including the root; that's fine
    // here because we call this before child.kill(), so the parent gets a
    // second kill attempt which is harmless.
    let _ = std::process::Command::new("taskkill")
        .args(["/F", "/T", "/PID", &pid.to_string()])
        .status();
}

// Kill whatever is listening on `port` — defensive cleanup that runs on every
// app exit when we own the port.  Catches three cases:
//   - PyInstaller onefile orphan: launcher dies, Python child reparented to init
//   - Reuse path (#2258): no CommandChild was tracked
//   - Subprocess survival: pkill -P missed children for any reason
#[cfg(unix)]
fn kill_server_on_port(port: u16) {
    // -sTCP:LISTEN restricts output to the process actually listening on the
    // port, excluding established client connections (e.g. the Tauri WebView).
    // my_pid guard is belt-and-suspenders in case lsof returns our own PID.
    let my_pid = std::process::id();
    let output = match std::process::Command::new("lsof")
        .args(["-ti", &format!(":{}", port), "-sTCP:LISTEN"])
        .output()
    {
        Ok(o) => o,
        Err(e) => {
            log::warn!(
                "lsof unavailable, cannot kill orphan server on port {}: {}",
                port,
                e
            );
            return;
        }
    };
    for pid_str in String::from_utf8_lossy(&output.stdout).split_whitespace() {
        if let Ok(pid) = pid_str.parse::<u32>() {
            if pid == my_pid {
                log::debug!("Skipping self (PID {}) in port {} cleanup", pid, port);
                continue;
            }
            log::info!("Killing orphan gptme-server PID {} on port {}", pid, port);
            kill_subprocesses(pid);
            let _ = std::process::Command::new("kill")
                .args(["-9", &pid.to_string()])
                .status();
        }
    }
}

#[cfg(windows)]
fn kill_server_on_port(port: u16) {
    // netstat -ano columns: Proto  LocalAddress  ForeignAddress  State  PID
    // Match the local-address field (col[1]) exactly so ":5700" does not
    // accidentally match ":57001" via substring search.
    let output = match std::process::Command::new("netstat")
        .args(["-ano"])
        .output()
    {
        Ok(o) => o,
        Err(_) => return,
    };
    let port_suffix = format!(":{}", port);
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        if !line.contains("LISTENING") {
            continue;
        }
        let cols: Vec<&str> = line.split_whitespace().collect();
        // col[1] is the local address, e.g. "0.0.0.0:5700" or "[::]:5700"
        let local_addr = cols.get(1).copied().unwrap_or("");
        if !local_addr.ends_with(&port_suffix) {
            continue;
        }
        if let Some(pid_str) = cols.last() {
            log::info!(
                "Killing orphan gptme-server PID {} on port {}",
                pid_str,
                port
            );
            let _ = std::process::Command::new("taskkill")
                .args(["/F", "/T", "/PID", pid_str])
                .status();
        }
    }
}

// Stub for platforms that are neither unix nor windows (shouldn't happen for desktop targets).
#[cfg(not(any(unix, windows)))]
fn kill_subprocesses(_pid: u32) {}

#[cfg(not(any(unix, windows)))]
fn kill_server_on_port(_port: u16) {}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(desktop)]
    use tauri::test::{assert_ipc_response, get_ipc_response, mock_builder, MockRuntime};
    #[cfg(desktop)]
    use tauri::Manager;
    #[cfg(desktop)]
    use tauri_plugin_shell::ShellExt;

    #[cfg(desktop)]
    fn build_test_app() -> tauri::App<MockRuntime> {
        let app = mock_builder()
            .plugin(tauri_plugin_shell::init())
            .manage(ServerProcess {
                child: Arc::new(Mutex::new(None)),
                owns_port: Arc::new(AtomicBool::new(false)),
                // app_handle left as None: no test calls start_server
                // in a way that reads it (all tests seed first, causing
                // start_server to return early).  A future test that
                // actually starts a server via IPC will need a
                // runtime-generic ServerProcess instead.
                app_handle: Arc::new(Mutex::new(None)),
            })
            .invoke_handler(tauri::generate_handler![
                get_server_status,
                start_server,
                stop_server,
            ])
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .unwrap();
        app
    }

    #[cfg(desktop)]
    fn build_test_webview(app: &tauri::App<MockRuntime>) -> tauri::WebviewWindow<MockRuntime> {
        tauri::WebviewWindowBuilder::new(app, "main", Default::default())
            .build()
            .unwrap()
    }

    #[cfg(desktop)]
    fn test_invoke_request(cmd: &str) -> tauri::webview::InvokeRequest {
        tauri::webview::InvokeRequest {
            cmd: cmd.into(),
            callback: tauri::ipc::CallbackFn(0),
            error: tauri::ipc::CallbackFn(1),
            url: "http://tauri.localhost".parse().unwrap(),
            body: tauri::ipc::InvokeBody::default(),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.to_string(),
        }
    }

    #[cfg(desktop)]
    fn invoke_server_status(webview: &tauri::WebviewWindow<MockRuntime>) -> ServerStatus {
        get_ipc_response(webview, test_invoke_request("get_server_status"))
            .unwrap()
            .deserialize()
            .unwrap()
    }

    #[cfg(desktop)]
    type TestCommandReceiver =
        tauri::async_runtime::Receiver<tauri_plugin_shell::process::CommandEvent>;

    #[cfg(desktop)]
    struct SeededServer {
        // Keep the shell event channel alive until the test finishes so the
        // plugin can still deliver the child termination event when we stop it.
        _events: TestCommandReceiver,
    }

    #[cfg(desktop)]
    fn spawn_seeded_server_child(
        app: &tauri::App<MockRuntime>,
    ) -> (
        TestCommandReceiver,
        tauri_plugin_shell::process::CommandChild,
    ) {
        #[cfg(unix)]
        {
            return app.shell().command("sleep").args(["60"]).spawn().unwrap();
        }
        #[cfg(windows)]
        {
            return app
                .shell()
                .command("powershell")
                .args(["-Command", "Start-Sleep -Seconds 60"])
                .spawn()
                .unwrap();
        }
        #[cfg(not(any(unix, windows)))]
        {
            panic!("seed_running_server requires unix or windows desktop targets");
        }
    }

    #[cfg(desktop)]
    fn seed_running_server(app: &tauri::App<MockRuntime>) -> SeededServer {
        let (events, child) = spawn_seeded_server_child(app);
        let state = app.state::<ServerProcess>();
        *state.child.lock().unwrap() = Some(child);
        state.owns_port.store(true, Ordering::Relaxed);
        SeededServer { _events: events }
    }

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

    #[tokio::test]
    #[cfg(desktop)]
    async fn test_is_server_responsive_on_listening_port() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(is_server_responsive(port).await);
        drop(listener);
        assert!(!is_server_responsive(port).await);
    }

    #[test]
    #[cfg(desktop)]
    fn test_parse_http_status() {
        assert_eq!(parse_http_status(b"HTTP/1.1 200 OK\r\n\r\n"), Some(200));
        assert_eq!(
            parse_http_status(b"HTTP/1.1 401 Unauthorized\r\n"),
            Some(401)
        );
        assert_eq!(parse_http_status(b"HTTP/1.0 403 Forbidden\r\n"), Some(403));
        assert_eq!(parse_http_status(b"not http at all"), None);
        assert_eq!(parse_http_status(b""), None);
    }

    /// Spawn a one-shot TCP server that returns `response` to the first
    /// connection, and return the port it's listening on.
    #[cfg(desktop)]
    async fn spawn_canned_http_server(response: &'static str) -> u16 {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use tokio::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            if let Ok((mut socket, _)) = listener.accept().await {
                let mut buf = [0u8; 1024];
                let _ = socket.read(&mut buf).await;
                let _ = socket.write_all(response.as_bytes()).await;
            }
        });
        port
    }

    #[tokio::test]
    #[cfg(desktop)]
    async fn test_probe_server_usable_on_2xx() {
        let port = spawn_canned_http_server("HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n").await;
        assert_eq!(probe_server(port).await, ServerProbe::Usable);
    }

    #[tokio::test]
    #[cfg(desktop)]
    async fn test_probe_server_auth_required_on_401() {
        let port =
            spawn_canned_http_server("HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
                .await;
        assert_eq!(probe_server(port).await, ServerProbe::AuthRequired);
    }

    #[tokio::test]
    #[cfg(desktop)]
    async fn test_probe_server_unreachable_when_nothing_listening() {
        // Bind then drop to obtain a port nothing is listening on.
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        assert_eq!(probe_server(port).await, ServerProbe::Unreachable);
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
    #[cfg(desktop)]
    fn test_get_server_status_reports_running_via_ipc() {
        let app = build_test_app();
        let webview = build_test_webview(&app);
        let _server = seed_running_server(&app);

        let status = invoke_server_status(&webview);
        assert!(status.running);
        assert_eq!(status.port, GPTME_SERVER_PORT);
        assert!(status.manages_local_server);
        assert!(!status.existing_server_detected);

        assert_ipc_response(&webview, test_invoke_request("stop_server"), Ok(()));
    }

    #[test]
    #[cfg(desktop)]
    fn test_start_server_rejects_duplicate_running_process_via_ipc() {
        let app = build_test_app();
        let webview = build_test_webview(&app);
        let _server = seed_running_server(&app);

        assert_ipc_response(
            &webview,
            test_invoke_request("start_server"),
            Err("Server is already running".to_string()),
        );

        assert_ipc_response(&webview, test_invoke_request("stop_server"), Ok(()));
    }

    #[test]
    #[cfg(desktop)]
    fn test_stop_server_clears_running_state_via_ipc() {
        let app = build_test_app();
        let webview = build_test_webview(&app);
        let _server = seed_running_server(&app);

        assert!(invoke_server_status(&webview).running);
        assert_ipc_response(&webview, test_invoke_request("stop_server"), Ok(()));
        assert!(!invoke_server_status(&webview).running);

        let state = app.state::<ServerProcess>();
        assert!(state.child.lock().unwrap().is_none());
        assert!(!state.owns_port.load(Ordering::Relaxed));
    }

    #[test]
    fn test_gptme_server_port_constant() {
        assert_eq!(GPTME_SERVER_PORT, 5700);
    }
}
