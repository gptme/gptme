use std::collections::HashMap;
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

mod lan_access;
use lan_access::{disable_lan_access, enable_lan_access, get_lan_access_status, LanAccess};

const GPTME_SERVER_PORT: u16 = 5700;
const SERVER_TOKEN_ENV: &str = "GPTME_SERVER_TOKEN";

/// Returns the port gptme-server should bind to.
/// Override at run time with `GPTME_SERVER_PORT=<port>` for development or
/// testing in environments where the default port is already occupied.
fn server_port() -> u16 {
    std::env::var("GPTME_SERVER_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(GPTME_SERVER_PORT)
}

#[cfg(desktop)]
fn generate_server_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).expect("OS randomness unavailable");
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
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
    // Bearer token shared only with the sidecar and the Tauri webview.
    token: String,
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
    auth_token: Option<String>,
}

#[cfg(desktop)]
#[tauri::command]
async fn get_server_status(state: tauri::State<'_, ServerProcess>) -> Result<ServerStatus, String> {
    let running = state
        .child
        .lock()
        .map(|guard| guard.is_some())
        .unwrap_or(false);
    let port_available = is_port_available(server_port());
    // Only probe TCP when the port is occupied but we're not managing it —
    // avoids false-positive existing_server_detected during TIME_WAIT after stop_server.
    let existing_server_detected =
        !running && !port_available && is_server_responsive(server_port()).await;
    Ok(ServerStatus {
        running,
        port: server_port(),
        port_available,
        manages_local_server: true,
        existing_server_detected,
        auth_token: Some(state.token.clone()),
    })
}

#[cfg(not(desktop))]
#[tauri::command]
fn get_server_status() -> ServerStatus {
    ServerStatus {
        running: false,
        port: server_port(),
        port_available: false,
        manages_local_server: false,
        existing_server_detected: false,
        auth_token: None,
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
    spawn_server_sidecar(
        &app,
        state.child.clone(),
        state.owns_port.clone(),
        &state.token,
    )
    .await?;
    Ok(server_port())
}

#[cfg(not(desktop))]
#[tauri::command]
async fn start_server() -> Result<u16, String> {
    Err(LOCAL_SERVER_UNSUPPORTED.to_string())
}

#[cfg(desktop)]
fn desktop_cors_origin() -> &'static str {
    // A debug build normally loads the Vite dev server, but E2E and other
    // custom-protocol debug builds load the embedded frontend instead. Allow
    // both sets of origins so the Rust build profile does not decide which
    // frontend can reach its managed sidecar.
    //
    // Webview origin differs per platform and Tauri version:
    //   - WKWebView (macOS) / WebKitGTK (Linux) send tauri://localhost
    //   - WebView2 (Windows) sends http://tauri.localhost
    //     (and historically https://tauri.localhost)
    // gptme-server accepts a comma-separated list, so allow all known origins
    // and let the running webview match whichever it sends.
    // See: gptme/gptme#2226
    "http://localhost:5701,tauri://localhost,http://tauri.localhost,https://tauri.localhost"
}

#[cfg(desktop)]
async fn spawn_server_sidecar(
    app: &tauri::AppHandle,
    state_arc: Arc<Mutex<Option<CommandChild>>>,
    owns_port: Arc<AtomicBool>,
    token: &str,
) -> Result<(), String> {
    if !is_port_available(server_port()) {
        // Port is occupied — probe whether we can actually use the server there.
        // A bare TCP connect isn't enough: a leftover auth-gated gptme-server
        // accepts connections but 401s every API call, leaving the app silently
        // degraded (gptme/gptme#2457, finding F2). Only reuse a server we can
        // actually talk to.
        match probe_server(server_port()).await {
            ServerProbe::Usable => {
                // Common crash-recovery case: the gptme-server sidecar outlived
                // the Tauri process and is still serving. Reuse it silently
                // rather than showing a blocking error dialog.
                log::info!(
                    "Port {} is occupied and a usable server is responding — \
                     reusing existing gptme-server (likely a leftover from a previous session)",
                    server_port()
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
                    server_port()
                );
                return Err(format!(
                    "Another gptme-server is already running on port {} and requires \
                     authentication this app doesn't have. Stop that server (or restart \
                     it without a token) and try again.",
                    server_port()
                ));
            }
            ServerProbe::Unreachable => {
                // Port is occupied by a non-responsive / non-HTTP foreign process —
                // do NOT set owns_port; cleanup must not kill a process we never started.
                return Err(format!("Port {} is already in use", server_port()));
            }
        }
    }

    let cors_origin = desktop_cors_origin();
    log::info!(
        "Starting gptme-server on port {} with CORS origin: {}",
        server_port(),
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
    let port_str = server_port().to_string();
    let sidecar_command = app
        .shell()
        .sidecar("gptme-server")
        .map_err(|e| format!("Sidecar error: {}", e))?
        .args([
            "--cors-origin",
            cors_origin,
            "--port",
            port_str.as_str(),
            "--watch-pid",
            tauri_pid.as_str(),
        ])
        .env(SERVER_TOKEN_ENV, token);

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
                    if is_port_available(server_port()) {
                        owns_port_for_output.store(false, Ordering::Relaxed);
                    } else {
                        log::warn!(
                            "[gptme-server] Sidecar exited but port {} still in use — \
                             likely an orphaned subprocess; deferring port cleanup to app exit",
                            server_port()
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
    url.query_pairs()
        .find(|(key, _)| key == "code")
        .map(|(_, value)| value.to_string())
        .filter(|code| !code.is_empty())
}

/// JS that `window.eval` runs to hand an OAuth code to the webui.
///
/// `code` is JSON-encoded so it is a safe JS string literal (quotes, backslashes,
/// control chars cannot break out). `encodeURIComponent` then puts it in the URL
/// hash so `URLSearchParams` on the frontend recovers the original characters,
/// including base64url `-_=+/` that the previous alphanumeric-only filter
/// silently stripped.
fn auth_code_injection_js(code: &str) -> String {
    let json_code = serde_json::to_string(&code).unwrap_or_else(|_| "\"\"".to_string());
    format!(
        "window.location.hash = '#code=' + encodeURIComponent({}); window.location.reload();",
        json_code
    )
}

fn handle_deep_link_urls(app: &tauri::AppHandle, urls: Vec<url::Url>) {
    for url in &urls {
        log::info!("Deep link received: {}", url);

        if let Some(code) = extract_auth_code(url) {
            log::info!("Auth code extracted from deep link, injecting into webview");

            if let Some(window) = app.get_webview_window("main") {
                if let Err(e) = window.eval(auth_code_injection_js(&code)) {
                    log::error!("Failed to inject auth code into webview: {}", e);
                }
            }
        }
    }
}

#[cfg(desktop)]
async fn check_for_updates(app: tauri::AppHandle) {
    // Respect user opt-out via environment variable (truthy values: 1, true, yes, on)
    if std::env::var("GPTME_DISABLE_AUTO_UPDATE")
        .map(|v| matches!(v.to_lowercase().as_str(), "1" | "true" | "yes" | "on"))
        .unwrap_or(false)
    {
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

// --- MCP config panel backend ---

/// JSON (de)serialization for `extra`: converts preserved unknown TOML items
/// to JSON and back so they survive the Tauri IPC boundary
/// (get_mcp_config → frontend → save_mcp_config).
mod extra_items {
    pub fn item_to_json(item: &toml_edit::Item) -> Option<serde_json::Value> {
        if let Some(v) = item.as_value() {
            return value_to_json(v);
        }
        if let Some(t) = item.as_table_like() {
            let mut map = serde_json::Map::new();
            for (k, v) in t.iter() {
                map.insert(k.to_string(), item_to_json(v)?);
            }
            return Some(serde_json::Value::Object(map));
        }
        None
    }

    fn value_to_json(v: &toml_edit::Value) -> Option<serde_json::Value> {
        if let Some(s) = v.as_str() {
            return Some(s.to_string().into());
        }
        if let Some(b) = v.as_bool() {
            return Some(b.into());
        }
        if let Some(i) = v.as_integer() {
            return Some(i.into());
        }
        if let Some(f) = v.as_float() {
            return Some(f.into());
        }
        if let Some(d) = v.as_datetime() {
            return Some(d.to_string().into());
        }
        if let Some(a) = v.as_array() {
            let mut out = Vec::new();
            for val in a.iter() {
                out.push(value_to_json(val)?);
            }
            return Some(serde_json::Value::Array(out));
        }
        if let Some(it) = v.as_inline_table() {
            let mut map = serde_json::Map::new();
            for (k, val) in it.iter() {
                map.insert(k.to_string(), value_to_json(val)?);
            }
            return Some(serde_json::Value::Object(map));
        }
        None
    }

    pub fn json_to_item(v: &serde_json::Value) -> Option<toml_edit::Item> {
        Some(toml_edit::Item::Value(json_to_value(v)?))
    }

    fn json_to_value(v: &serde_json::Value) -> Option<toml_edit::Value> {
        use toml_edit::Value;
        Some(match v {
            serde_json::Value::String(s) => Value::from(s.as_str()),
            serde_json::Value::Bool(b) => Value::from(*b),
            serde_json::Value::Number(n) => {
                if let Some(i) = n.as_i64() {
                    Value::from(i)
                } else {
                    Value::from(n.as_f64()?)
                }
            }
            serde_json::Value::Array(a) => {
                let mut arr = toml_edit::Array::new();
                for val in a {
                    arr.push(json_to_value(val)?);
                }
                Value::from(arr)
            }
            serde_json::Value::Object(m) => {
                let mut it = toml_edit::InlineTable::new();
                for (k, val) in m {
                    it.insert(k.as_str(), json_to_value(val)?);
                }
                Value::from(it)
            }
            serde_json::Value::Null => return None,
        })
    }

    pub fn serialize<S: serde::Serializer>(
        extra: &[(String, toml_edit::Item)],
        s: S,
    ) -> Result<S::Ok, S::Error> {
        let mut map = serde_json::Map::new();
        for (k, item) in extra {
            if let Some(v) = item_to_json(item) {
                map.insert(k.clone(), v);
            }
        }
        use serde::Serialize as _;
        serde_json::Value::Object(map).serialize(s)
    }

    pub fn deserialize<'de, D: serde::Deserializer<'de>>(
        d: D,
    ) -> Result<Vec<(String, toml_edit::Item)>, D::Error> {
        use serde::Deserialize as _;
        let v = serde_json::Value::deserialize(d)?;
        let mut out = Vec::new();
        if let serde_json::Value::Object(m) = v {
            for (k, val) in m {
                if let Some(item) = json_to_item(&val) {
                    out.push((k, item));
                }
            }
        }
        Ok(out)
    }
}

/// JSON view of a single MCP server entry from config.toml.
#[derive(serde::Serialize, serde::Deserialize, Clone, Debug)]
pub struct MCPServerView {
    pub name: String,
    pub enabled: bool,
    pub command: Option<String>,
    pub args: Vec<String>,
    pub env: HashMap<String, String>,
    pub url: Option<String>,
    /// HTTP headers (Authorization, etc.). Phase 1 has no GUI editor for these,
    /// but the backend must round-trip them or a save would strip credentials.
    #[serde(default)]
    pub headers: HashMap<String, String>,
    /// Unknown keys from the original server entry, preserved so a save does
    /// not silently drop settings the view does not model. Serialized through
    /// the IPC JSON boundary so the get → save round trip keeps them.
    #[serde(default, with = "extra_items")]
    pub extra: Vec<(String, toml_edit::Item)>,
}

/// Keys handled by MCPServerView fields; anything else in a server entry is
/// round-tripped via `extra`.
const KNOWN_SERVER_KEYS: [&str; 7] = [
    "name", "enabled", "command", "args", "env", "url", "headers",
];

/// JSON view of the [mcp] section of config.toml.
#[derive(serde::Serialize, serde::Deserialize, Debug)]
pub struct MCPConfigView {
    pub enabled: bool,
    pub auto_start: bool,
    pub servers: Vec<MCPServerView>,
}

fn mcp_config_defaults() -> MCPConfigView {
    MCPConfigView {
        enabled: true,
        auto_start: false,
        servers: vec![],
    }
}

/// User-level config.toml path, matching Python `gptme.dirs.get_config_dir()`.
///
/// That helper is `platformdirs.user_config_dir("gptme")`:
/// - Unix: `$XDG_CONFIG_HOME/gptme` or `~/.config/gptme`
/// - macOS: `~/Library/Application Support/gptme`
/// - Windows: `%APPDATA%\gptme` (Roaming — platformdirs.user_config_dir
///   defaults to Roaming on Windows, and `dirs::config_dir()` matches it)
fn gptme_config_path() -> Result<std::path::PathBuf, String> {
    dirs::config_dir()
        .ok_or_else(|| "Cannot determine system config directory".to_string())
        .map(|d| d.join("gptme").join("config.toml"))
}

/// Render a scalar TOML value as a plain string so stringification of
/// non-string scalars (integers, booleans, floats) never silently drops
/// entries from env/headers maps.
fn scalar_value_to_string(v: &toml_edit::Value) -> Option<String> {
    if let Some(s) = v.as_str() {
        return Some(s.to_string());
    }
    if let Some(b) = v.as_bool() {
        return Some(b.to_string());
    }
    if let Some(i) = v.as_integer() {
        return Some(i.to_string());
    }
    v.as_float().map(|f| f.to_string())
}

fn parse_str_map_from_value(value: Option<&toml_edit::Value>) -> HashMap<String, String> {
    let mut map = HashMap::new();
    let Some(value) = value else {
        return map;
    };
    if let Some(it) = value.as_inline_table() {
        for (k, v) in it.iter() {
            if let Some(s) = scalar_value_to_string(v) {
                map.insert(k.to_string(), s);
            }
        }
    }
    map
}

fn parse_str_map_from_item(item: Option<&toml_edit::Item>) -> HashMap<String, String> {
    let Some(item) = item else {
        return HashMap::new();
    };
    if let Some(val) = item.as_value() {
        return parse_str_map_from_value(Some(val));
    }
    if let Some(t) = item.as_table() {
        let mut map = HashMap::new();
        for (k, v) in t.iter() {
            if let Some(val) = v.as_value() {
                if let Some(s) = scalar_value_to_string(val) {
                    map.insert(k.to_string(), s);
                }
            }
        }
        return map;
    }
    HashMap::new()
}

fn parse_args_from_value(value: Option<&toml_edit::Value>) -> Vec<String> {
    value
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(scalar_value_to_string).collect())
        .unwrap_or_default()
}

fn collect_extra<'a, I>(entries: I) -> Vec<(String, toml_edit::Item)>
where
    I: Iterator<Item = (&'a str, &'a toml_edit::Item)>,
{
    entries
        .filter(|(k, _)| !KNOWN_SERVER_KEYS.contains(k))
        .map(|(k, v)| (k.to_string(), v.clone()))
        .collect()
}

/// Preserve a known key whose value has an unexpected type: the typed view
/// cannot represent it and it is excluded from `extra` by name, so it would
/// otherwise be silently dropped on save.
fn keep_if_mistyped(
    item: Option<toml_edit::Item>,
    valid: impl Fn(&toml_edit::Item) -> bool,
    key: &str,
    extra: &mut Vec<(String, toml_edit::Item)>,
) {
    if let Some(item) = item {
        if !valid(&item) {
            extra.push((key.to_string(), item));
        }
    }
}

fn parse_server_table(st: &toml_edit::Table) -> MCPServerView {
    MCPServerView {
        name: st
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        enabled: st.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true),
        command: st
            .get("command")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        args: parse_args_from_value(st.get("args").and_then(|v| v.as_value())),
        env: parse_str_map_from_item(st.get("env")),
        url: st.get("url").and_then(|v| v.as_str()).map(str::to_string),
        headers: parse_str_map_from_item(st.get("headers")),
        extra: {
            let mut extra = collect_extra(st.iter());
            keep_if_mistyped(
                st.get("enabled").cloned(),
                |i| i.as_value().and_then(|v| v.as_bool()).is_some(),
                "enabled",
                &mut extra,
            );
            keep_if_mistyped(
                st.get("command").cloned(),
                |i| i.as_value().and_then(|v| v.as_str()).is_some(),
                "command",
                &mut extra,
            );
            keep_if_mistyped(
                st.get("args").cloned(),
                |i| i.as_value().and_then(|v| v.as_array()).is_some(),
                "args",
                &mut extra,
            );
            keep_if_mistyped(
                st.get("env").cloned(),
                |i| {
                    i.as_value()
                        .map(|v| v.as_inline_table().is_some())
                        .unwrap_or(i.as_table().is_some())
                },
                "env",
                &mut extra,
            );
            keep_if_mistyped(
                st.get("url").cloned(),
                |i| i.as_value().and_then(|v| v.as_str()).is_some(),
                "url",
                &mut extra,
            );
            keep_if_mistyped(
                st.get("headers").cloned(),
                |i| {
                    i.as_value()
                        .map(|v| v.as_inline_table().is_some())
                        .unwrap_or(i.as_table().is_some())
                },
                "headers",
                &mut extra,
            );
            extra
        },
    }
}

fn parse_server_inline(it: &toml_edit::InlineTable) -> MCPServerView {
    MCPServerView {
        name: it
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        enabled: it.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true),
        command: it
            .get("command")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        args: parse_args_from_value(it.get("args")),
        env: parse_str_map_from_value(it.get("env")),
        url: it.get("url").and_then(|v| v.as_str()).map(str::to_string),
        headers: parse_str_map_from_value(it.get("headers")),
        extra: {
            let entries: Vec<(&str, toml_edit::Item)> = it
                .iter()
                .map(|(k, v)| (k, toml_edit::Item::Value(v.clone())))
                .collect();
            let mut extra = collect_extra(entries.iter().map(|(k, v)| (*k, v)));
            keep_if_mistyped(
                it.get("enabled").map(|v| toml_edit::Item::Value(v.clone())),
                |i| i.as_value().and_then(|v| v.as_bool()).is_some(),
                "enabled",
                &mut extra,
            );
            keep_if_mistyped(
                it.get("command").map(|v| toml_edit::Item::Value(v.clone())),
                |i| i.as_value().and_then(|v| v.as_str()).is_some(),
                "command",
                &mut extra,
            );
            keep_if_mistyped(
                it.get("args").map(|v| toml_edit::Item::Value(v.clone())),
                |i| i.as_value().and_then(|v| v.as_array()).is_some(),
                "args",
                &mut extra,
            );
            keep_if_mistyped(
                it.get("env").map(|v| toml_edit::Item::Value(v.clone())),
                |i| {
                    i.as_value()
                        .map(|v| v.as_inline_table().is_some())
                        .unwrap_or(false)
                },
                "env",
                &mut extra,
            );
            keep_if_mistyped(
                it.get("url").map(|v| toml_edit::Item::Value(v.clone())),
                |i| i.as_value().and_then(|v| v.as_str()).is_some(),
                "url",
                &mut extra,
            );
            keep_if_mistyped(
                it.get("headers").map(|v| toml_edit::Item::Value(v.clone())),
                |i| {
                    i.as_value()
                        .map(|v| v.as_inline_table().is_some())
                        .unwrap_or(false)
                },
                "headers",
                &mut extra,
            );
            extra
        },
    }
}

/// Accept both `[[mcp.servers]]` (array of tables) and
/// `servers = [{ name = "…" }]` (inline array of tables).
fn parse_servers(item: Option<&toml_edit::Item>) -> Vec<MCPServerView> {
    let Some(item) = item else {
        return Vec::new();
    };
    if let Some(aot) = item.as_array_of_tables() {
        return aot.iter().map(parse_server_table).collect();
    }
    if let Some(arr) = item.as_array() {
        return arr
            .iter()
            .filter_map(|value| value.as_inline_table().map(parse_server_inline))
            .collect();
    }
    Vec::new()
}

fn insert_str_map(st: &mut toml_edit::Table, key: &str, map: &HashMap<String, String>) {
    if map.is_empty() {
        return;
    }
    let mut it = toml_edit::InlineTable::new();
    for (k, v) in map {
        it.insert(k.as_str(), toml_edit::Value::from(v.as_str()));
    }
    st.insert(
        key,
        toml_edit::Item::Value(toml_edit::Value::InlineTable(it)),
    );
}

/// Parse an `[mcp]` section from TOML text. Empty input yields defaults.
fn parse_mcp_config(content: &str) -> Result<MCPConfigView, String> {
    if content.is_empty() {
        return Ok(mcp_config_defaults());
    }
    let doc: toml_edit::DocumentMut = content
        .parse()
        .map_err(|e: toml_edit::TomlError| format!("Failed to parse TOML: {e}"))?;
    let mcp = doc.get("mcp");
    Ok(MCPConfigView {
        enabled: mcp
            .and_then(|t| t.get("enabled"))
            .and_then(|v| v.as_bool())
            .unwrap_or(true),
        auto_start: mcp
            .and_then(|t| t.get("auto_start"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        servers: parse_servers(mcp.and_then(|t| t.get("servers"))),
    })
}

/// Rebuild `[mcp]` from `mcp`, preserving every other top-level table.
fn serialize_mcp_config(existing: &str, mcp: &MCPConfigView) -> Result<String, String> {
    let mut doc: toml_edit::DocumentMut = existing
        .parse()
        .map_err(|e: toml_edit::TomlError| format!("Failed to parse TOML: {e}"))?;
    // Keep any unknown keys that lived inside the original [mcp] table so a
    // save does not silently drop settings the view does not model. Known
    // keys are rebuilt below.
    let mut mcp_table = match doc.get("mcp") {
        Some(toml_edit::Item::Table(t)) => t.clone(),
        // `mcp = { ... }` inline tables promote to a regular table on save.
        Some(toml_edit::Item::Value(v)) => {
            let mut t = toml_edit::Table::new();
            if let Some(it) = v.as_inline_table() {
                for (k, val) in it.iter() {
                    t.insert(k, toml_edit::Item::Value(val.clone()));
                }
            }
            t
        }
        _ => toml_edit::Table::new(),
    };
    mcp_table.remove("enabled");
    mcp_table.remove("auto_start");
    mcp_table.remove("servers");
    mcp_table.insert("enabled", toml_edit::value(mcp.enabled));
    mcp_table.insert("auto_start", toml_edit::value(mcp.auto_start));

    let mut servers_aot = toml_edit::ArrayOfTables::new();
    for server in &mcp.servers {
        let mut st = toml_edit::Table::new();
        // Always write `name`, even when empty: the Python loader requires the
        // key (MCPServerConfig.name has no default) and skips entries without
        // it, so omitting it would make a configured server disappear.
        st.insert("name", toml_edit::value(server.name.as_str()));
        st.insert("enabled", toml_edit::value(server.enabled));
        if let Some(cmd) = &server.command {
            st.insert("command", toml_edit::value(cmd.as_str()));
        }
        if !server.args.is_empty() {
            let mut arr = toml_edit::Array::new();
            for a in &server.args {
                arr.push(a.as_str());
            }
            st.insert("args", toml_edit::value(arr));
        }
        insert_str_map(&mut st, "env", &server.env);
        if let Some(url) = &server.url {
            st.insert("url", toml_edit::value(url.as_str()));
        }
        insert_str_map(&mut st, "headers", &server.headers);
        // Restore the original entry's extra keys after the known fields so a
        // save never drops settings the view does not model. This includes
        // known keys whose original value had an unexpected type (they cannot
        // be represented by the typed fields), so they override the defaulted
        // typed value.
        for (k, item) in &server.extra {
            st.insert(k, item.clone());
        }
        servers_aot.push(st);
    }
    mcp_table.insert("servers", toml_edit::Item::ArrayOfTables(servers_aot));
    doc.insert("mcp", toml_edit::Item::Table(mcp_table));
    Ok(doc.to_string())
}

fn replace_file(from: &std::path::Path, to: &std::path::Path) -> std::io::Result<()> {
    #[cfg(windows)]
    {
        windows_replace_file(from, to)
    }
    #[cfg(not(windows))]
    {
        std::fs::rename(from, to)
    }
}

/// Replace `to` with `from` without deleting `to` first.
///
/// `std::fs::rename` cannot overwrite on Windows. Deleting `to` and then
/// renaming is not acceptable for a credential-bearing config: a failed
/// rename (or a crash between the two operations) plus the error path's
/// temp-file cleanup would drop the original file. `MoveFileExW` with
/// `MOVEFILE_REPLACE_EXISTING` is the Windows replacement primitive.
#[cfg(windows)]
fn windows_replace_file(from: &std::path::Path, to: &std::path::Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;

    #[link(name = "kernel32")]
    extern "system" {
        #[link_name = "MoveFileExW"]
        fn move_file_ex_w(
            existing_file_name: *const u16,
            new_file_name: *const u16,
            flags: u32,
        ) -> i32;
    }

    const MOVEFILE_REPLACE_EXISTING: u32 = 0x1;
    const MOVEFILE_WRITE_THROUGH: u32 = 0x8;

    fn to_wide(path: &std::path::Path) -> Vec<u16> {
        path.as_os_str().encode_wide().chain(Some(0)).collect()
    }

    let from_w = to_wide(from);
    let to_w = to_wide(to);
    // SAFETY: both buffers are null-terminated UTF-16 paths. MoveFileExW only
    // reads them for the duration of the call and does not retain the pointers.
    let ok = unsafe {
        move_file_ex_w(
            from_w.as_ptr(),
            to_w.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if ok == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

/// Write `contents` over `path` via a sibling temp file.
///
/// On Unix the temp file inherits the destination mode when it exists, otherwise
/// `0o600`, so a save cannot weaken a private credential file to `0644`.
fn write_config_atomically(path: &std::path::Path, contents: &str) -> Result<(), String> {
    let tmp = path.with_extension("toml.tmp");
    let _ = std::fs::remove_file(&tmp);

    #[cfg(unix)]
    {
        use std::io::Write;
        use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
        let mode = std::fs::metadata(path)
            .map(|m| m.permissions().mode())
            .unwrap_or(0o600);
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(mode)
            .open(&tmp)
            .map_err(|e| format!("Failed to create temp file: {e}"))?;
        file.write_all(contents.as_bytes())
            .map_err(|e| format!("Failed to write temp file: {e}"))?;
        file.sync_all()
            .map_err(|e| format!("Failed to sync temp file: {e}"))?;
    }
    #[cfg(not(unix))]
    {
        std::fs::write(&tmp, contents).map_err(|e| format!("Failed to write temp file: {e}"))?;
    }

    if let Err(e) = replace_file(&tmp, path) {
        // Only drop the temp file when the destination is still there. If a
        // replacement implementation ever deletes dest before succeeding, keep
        // the temp copy so the user's config is not both dest-gone and tmp-gone.
        if path.exists() {
            let _ = std::fs::remove_file(&tmp);
        }
        return Err(format!("Failed to replace config: {e}"));
    }
    Ok(())
}

/// Read the [mcp] section of the user-level gptme config.toml.
/// Returns safe defaults when the file or section is absent.
#[tauri::command]
fn get_mcp_config() -> Result<MCPConfigView, String> {
    let path = gptme_config_path()?;
    if !path.exists() {
        return Ok(mcp_config_defaults());
    }
    let content =
        std::fs::read_to_string(&path).map_err(|e| format!("Failed to read config: {e}"))?;
    parse_mcp_config(&content)
}

/// Replace the [mcp] section of the user-level gptme config.toml atomically.
/// All other config sections are preserved unchanged.
#[tauri::command]
fn save_mcp_config(mcp: MCPConfigView) -> Result<(), String> {
    // Serialize the read-modify-write cycle: two concurrent invocations (rapid
    // clicks, two windows) would otherwise both read the same original file
    // and the second write silently lose the first save's updates.
    static SAVE_LOCK: std::sync::OnceLock<std::sync::Mutex<()>> = std::sync::OnceLock::new();
    let _guard = SAVE_LOCK
        .get_or_init(std::sync::Mutex::default)
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let path = gptme_config_path()?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to create config directory: {e}"))?;
    }
    let content = if path.exists() {
        std::fs::read_to_string(&path).map_err(|e| format!("Failed to read config: {e}"))?
    } else {
        String::new()
    };
    let serialized = serialize_mcp_config(&content, &mcp)?;
    write_config_atomically(&path, &serialized)
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
                        file_name: Some("gptme".to_string()),
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
            enable_lan_access,
            disable_lan_access,
            get_lan_access_status,
            get_mcp_config,
            save_mcp_config,
        ])
        .setup(|app| {
            log::info!("Starting gptme application");

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

            // LAN state must be managed on all platforms so commands can extract it
            // (even though enable_lan_access returns an error on non-desktop).
            app.manage(LanAccess::new(server_port()));

            #[cfg(desktop)]
            {
                let child_handle: Arc<Mutex<Option<CommandChild>>> = Arc::new(Mutex::new(None));
                let owns_port: Arc<AtomicBool> = Arc::new(AtomicBool::new(false));
                let token = generate_server_token();
                app.manage(ServerProcess {
                    child: child_handle.clone(),
                    owns_port: owns_port.clone(),
                    token: token.clone(),
                    app_handle: Arc::new(Mutex::new(Some(app.handle().clone()))),
                });

                let app_handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(err) =
                        spawn_server_sidecar(&app_handle, child_handle, owns_port, &token).await
                    {
                        log::error!("Failed to start gptme-server: {}", err);
                        if err.contains("already in use") {
                            show_port_conflict_dialog(
                                &app_handle,
                                format!(
                                    "Cannot start gptme-server because port {} is already in use.\n\n\
                                     This usually means another gptme-server instance is already running.\n\n\
                                     Please stop the existing gptme-server process and restart this application.",
                                    server_port()
                                ),
                            );
                        } else if err.contains("requires authentication") {
                            show_port_conflict_dialog(
                                &app_handle,
                                format!(
                                    "Cannot start gptme-server because port {} is occupied by another \
                                     gptme-server that requires authentication this app doesn't have.\n\n\
                                     Stop that server (or restart it without a token) and restart this application.",
                                    server_port()
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
            server_port()
        );
        kill_server_on_port(server_port());
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
                token: "test-tauri-server-token".to_string(),
                // app_handle left as None: no test calls start_server
                // in a way that reads it (all tests seed first, causing
                // start_server to return early).  A future test that
                // actually starts a server via IPC will need a
                // runtime-generic ServerProcess instead.
                app_handle: Arc::new(Mutex::new(None)),
            })
            .manage(LanAccess::new(server_port()))
            .invoke_handler(tauri::generate_handler![
                get_server_status,
                start_server,
                stop_server,
                enable_lan_access,
                disable_lan_access,
                get_lan_access_status,
                get_mcp_config,
                save_mcp_config,
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
    fn test_extract_auth_code_preserves_base64url_chars() {
        // OAuth codes use base64url encoding: A-Za-z0-9 plus hyphen and underscore.
        // The old alphanumeric-only filter silently stripped these, causing exchange failures.
        let url = url::Url::parse("gptme://callback?code=abc-def_ghi%3Djkl").unwrap();
        let code = extract_auth_code(&url).unwrap();
        assert_eq!(code, "abc-def_ghi=jkl");
    }

    #[test]
    fn test_extract_auth_code_preserves_plus_and_slash() {
        let url = url::Url::parse("gptme://callback?code=abc%2Bdef%2Fghi").unwrap();
        assert_eq!(extract_auth_code(&url).unwrap(), "abc+def/ghi");
    }

    #[test]
    fn test_extract_auth_code_empty_code_returns_none() {
        let url = url::Url::parse("gptme://callback?code=").unwrap();
        assert_eq!(extract_auth_code(&url), None);
    }

    fn json_literal_from_injection_js(js: &str) -> &str {
        const PREFIX: &str = "window.location.hash = '#code=' + encodeURIComponent(";
        const SUFFIX: &str = "); window.location.reload();";
        assert!(
            js.starts_with(PREFIX),
            "unexpected injection JS prefix: {js}"
        );
        assert!(js.ends_with(SUFFIX), "unexpected injection JS suffix: {js}");
        &js[PREFIX.len()..js.len() - SUFFIX.len()]
    }

    #[test]
    fn test_auth_code_injection_js_roundtrips_json_for_oauth_and_hostile_chars() {
        let cases = [
            "abc-def_ghi=jkl",
            "a+b/c==",
            "foo\"bar\\baz",
            "<script>alert(1)</script>",
            "percent%2Fencoded",
            "code with space",
            "line\nfeed",
        ];
        for code in cases {
            let js = auth_code_injection_js(code);
            let json_literal = json_literal_from_injection_js(&js);
            let recovered: String =
                serde_json::from_str(json_literal).expect("injection JS is not a JSON string");
            assert_eq!(recovered, code, "round-trip failed for {code:?}");
        }
    }

    #[test]
    fn test_auth_code_injection_js_escapes_quotes_and_backslashes() {
        let js = auth_code_injection_js("foo\"bar\\baz");
        let json_literal = json_literal_from_injection_js(&js);
        assert_eq!(json_literal, r#""foo\"bar\\baz""#);
        assert!(
            !js.contains(r#"encodeURIComponent("foo"bar"#),
            "unescaped quote would terminate the JS string: {js}"
        );
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
            auth_token: Some("test-token".to_string()),
        };
        let json = serde_json::to_string(&status).unwrap();
        assert!(json.contains("\"running\":false"));
        assert!(json.contains("\"port\":5700"));
        assert!(json.contains("\"port_available\":true"));
        assert!(json.contains("\"manages_local_server\":true"));
        assert!(json.contains("\"existing_server_detected\":false"));
        assert!(json.contains("\"auth_token\":\"test-token\""));
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
    fn test_desktop_cors_origin_allows_dev_and_embedded_frontends() {
        let origins: Vec<_> = desktop_cors_origin().split(',').collect();
        assert!(origins.contains(&"http://localhost:5701"));
        assert!(origins.contains(&"tauri://localhost"));
        assert!(origins.contains(&"http://tauri.localhost"));
        assert!(origins.contains(&"https://tauri.localhost"));
    }

    #[test]
    fn test_gptme_server_port_constant() {
        assert_eq!(GPTME_SERVER_PORT, 5700);
    }

    // --- MCP config tests ---
    // These call the same parse/serialize/write helpers as the Tauri commands.

    fn unique_temp_dir() -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "gptme-mcp-config-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn test_get_mcp_config_missing_file_returns_defaults() {
        let cfg = parse_mcp_config("").unwrap();
        assert!(cfg.enabled);
        assert!(!cfg.auto_start);
        assert!(cfg.servers.is_empty());
    }

    #[test]
    fn test_mcp_config_round_trip_preserves_unrelated_sections() {
        let original = r#"
[provider]
default = "openai"
api_key = "sk-test"

[mcp]
enabled = true
auto_start = false

[[mcp.servers]]
name = "filesystem"
enabled = true
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem"]
"#;
        let cfg = parse_mcp_config(original).unwrap();
        let updated = serialize_mcp_config(original, &cfg).unwrap();

        // [provider] section must survive unchanged
        assert!(updated.contains("[provider]"));
        assert!(updated.contains("default = \"openai\""));
        assert!(updated.contains("api_key = \"sk-test\""));

        // MCP section must be present
        assert!(updated.contains("[mcp]"));
        assert!(updated.contains("[[mcp.servers]]"));
        assert!(updated.contains("name = \"filesystem\""));
    }

    #[test]
    fn test_mcp_config_env_round_trip() {
        let original = r#"
[mcp]
enabled = true
auto_start = false

[[mcp.servers]]
name = "my-server"
enabled = true
command = "my-cmd"
args = []
env = { PATH = "/usr/bin", DEBUG = "1" }
"#;
        let cfg = parse_mcp_config(original).unwrap();
        assert_eq!(cfg.servers.len(), 1);
        let srv = &cfg.servers[0];
        assert_eq!(srv.env.get("PATH").map(String::as_str), Some("/usr/bin"));
        assert_eq!(srv.env.get("DEBUG").map(String::as_str), Some("1"));

        let updated = serialize_mcp_config(original, &cfg).unwrap();
        let cfg2 = parse_mcp_config(&updated).unwrap();
        assert_eq!(
            cfg2.servers[0].env.get("PATH").map(String::as_str),
            Some("/usr/bin")
        );
        assert_eq!(
            cfg2.servers[0].env.get("DEBUG").map(String::as_str),
            Some("1")
        );
    }

    #[test]
    fn test_mcp_config_non_string_map_values_are_stringified() {
        // Non-string scalars in env/headers must survive a save instead of
        // being silently dropped (data loss).
        let original = r#"
[mcp]
enabled = true

[[mcp.servers]]
name = "srv"
enabled = true
command = "cmd"
env = { RETRIES = 3, VERBOSE = true }
"#;
        let cfg = parse_mcp_config(original).unwrap();
        assert_eq!(
            cfg.servers[0].env.get("RETRIES").map(String::as_str),
            Some("3")
        );
        assert_eq!(
            cfg.servers[0].env.get("VERBOSE").map(String::as_str),
            Some("true")
        );

        let updated = serialize_mcp_config(original, &cfg).unwrap();
        let cfg2 = parse_mcp_config(&updated).unwrap();
        assert_eq!(
            cfg2.servers[0].env.get("RETRIES").map(String::as_str),
            Some("3")
        );
        assert_eq!(
            cfg2.servers[0].env.get("VERBOSE").map(String::as_str),
            Some("true")
        );
    }

    #[test]
    fn test_mcp_config_nameless_server_gets_empty_name_key() {
        // The Python loader requires the `name` key (MCPServerConfig.name has
        // no default and entries without it are skipped), so saving must
        // always write `name`, even when it was absent in the original.
        let original = r#"
[mcp]
enabled = true

[[mcp.servers]]
enabled = true
command = "cmd"
"#;
        let cfg = parse_mcp_config(original).unwrap();
        assert_eq!(cfg.servers[0].name, "");

        let updated = serialize_mcp_config(original, &cfg).unwrap();
        assert!(updated.contains("name = \"\""));
    }

    #[test]
    fn test_mcp_config_unknown_server_keys_survive_save() {
        // Unknown keys inside a server entry must survive a save.
        let original = r#"
[mcp]
enabled = true

[[mcp.servers]]
name = "x"
enabled = true
command = "cmd"
transport = "streamable"
timeout = 30
"#;
        let cfg = parse_mcp_config(original).unwrap();
        assert_eq!(cfg.servers[0].extra.len(), 2);

        let updated = serialize_mcp_config(original, &cfg).unwrap();
        assert!(updated.contains("transport = \"streamable\""));
        assert!(updated.contains("timeout = 30"));
    }

    #[test]
    fn test_mcp_config_unknown_keys_survive_ipc_json_round_trip() {
        // `extra` must survive the Tauri IPC boundary: get_mcp_config returns
        // JSON, the frontend sends it back to save_mcp_config, and the view's
        // serde layer has to carry the unknown keys across or a save drops them.
        let original = r#"
[[mcp.servers]]
name = "x"
enabled = true
command = "cmd"
transport = "streamable"
timeout = 30
tags = ["a", "b"]
"#;
        let cfg = parse_mcp_config(original).unwrap();

        // Simulate the IPC boundary with serde_json on the view.
        let json = serde_json::to_value(&cfg).unwrap();
        assert!(
            json["servers"][0]["extra"]["transport"].is_string(),
            "unknown keys must appear in the IPC JSON under `extra`"
        );
        let round_tripped: MCPConfigView = serde_json::from_value(json).unwrap();
        assert_eq!(round_tripped.servers[0].extra.len(), 3);

        let updated = serialize_mcp_config(original, &round_tripped).unwrap();
        assert!(updated.contains("transport = \"streamable\""));
        assert!(updated.contains("timeout = 30"));
        assert!(updated.contains("tags = [\"a\", \"b\"]"));
    }

    #[test]
    fn test_mcp_config_mistyped_known_keys_survive_save() {
        // A known key with an unexpected type cannot be represented by the
        // typed view; it must still not be silently dropped on save.
        let original = r#"
[[mcp.servers]]
name = "x"
enabled = "yes"
command = 123
args = "not-an-array"
"#;
        let cfg = parse_mcp_config(original).unwrap();
        assert!(cfg.servers[0].extra.iter().any(|(k, _)| k == "enabled"));
        assert!(cfg.servers[0].extra.iter().any(|(k, _)| k == "command"));
        assert!(cfg.servers[0].extra.iter().any(|(k, _)| k == "args"));

        let updated = serialize_mcp_config(original, &cfg).unwrap();
        assert!(updated.contains("enabled = \"yes\""));
        assert!(updated.contains("command = 123"));
        assert!(updated.contains("args = \"not-an-array\""));
    }

    #[test]
    fn test_mcp_config_unknown_keys_survive_save() {
        // Keys inside [mcp] the view does not model must survive a save.
        let original = r#"
[mcp]
enabled = true
auto_start = false
log_level = "debug"

[[mcp.servers]]
name = "srv"
enabled = true
command = "cmd"
"#;
        let cfg = parse_mcp_config(original).unwrap();
        let updated = serialize_mcp_config(original, &cfg).unwrap();
        assert!(updated.contains("log_level = \"debug\""));
        assert!(updated.contains("enabled = true"));
        assert!(updated.contains("name = \"srv\""));
    }

    #[test]
    fn test_mcp_config_inline_table_keys_survive_save() {
        // `mcp = { ... }` inline form must not lose unknown keys on save.
        let original = r#"
mcp = { enabled = true, log_level = "debug", servers = [{ name = "srv", enabled = true, command = "cmd" }] }
"#;
        let cfg = parse_mcp_config(original).unwrap();
        let updated = serialize_mcp_config(original, &cfg).unwrap();
        assert!(updated.contains("log_level = \"debug\""));
        assert!(updated.contains("name = \"srv\""));
    }

    #[test]
    fn test_mcp_config_http_server_round_trip() {
        let original = r#"
[mcp]
enabled = false
auto_start = true

[[mcp.servers]]
name = "remote"
enabled = true
url = "https://mcp.example.com"
"#;
        let cfg = parse_mcp_config(original).unwrap();
        assert!(!cfg.enabled);
        assert!(cfg.auto_start);
        let srv = &cfg.servers[0];
        assert_eq!(srv.url.as_deref(), Some("https://mcp.example.com"));
        assert!(srv.command.is_none());

        let updated = serialize_mcp_config(original, &cfg).unwrap();
        let cfg2 = parse_mcp_config(&updated).unwrap();
        assert_eq!(
            cfg2.servers[0].url.as_deref(),
            Some("https://mcp.example.com")
        );
    }

    #[test]
    fn test_mcp_config_headers_round_trip() {
        let original = r#"
[mcp]
enabled = true
auto_start = false

[[mcp.servers]]
name = "remote"
enabled = true
url = "https://mcp.example.com"
headers = { Authorization = "Bearer secret", X-Custom = "1" }
"#;
        let cfg = parse_mcp_config(original).unwrap();
        assert_eq!(
            cfg.servers[0]
                .headers
                .get("Authorization")
                .map(String::as_str),
            Some("Bearer secret")
        );
        assert_eq!(
            cfg.servers[0].headers.get("X-Custom").map(String::as_str),
            Some("1")
        );

        let updated = serialize_mcp_config(original, &cfg).unwrap();
        let cfg2 = parse_mcp_config(&updated).unwrap();
        assert_eq!(
            cfg2.servers[0]
                .headers
                .get("Authorization")
                .map(String::as_str),
            Some("Bearer secret")
        );
        assert!(updated.contains("Authorization"));
    }

    #[test]
    fn test_mcp_config_inline_servers_are_read_not_erased() {
        let original = r#"
[mcp]
enabled = true
auto_start = false
servers = [{ name = "inline", enabled = true, command = "echo", url = "https://x.example" }]
"#;
        let cfg = parse_mcp_config(original).unwrap();
        assert_eq!(cfg.servers.len(), 1);
        assert_eq!(cfg.servers[0].name, "inline");
        assert_eq!(cfg.servers[0].command.as_deref(), Some("echo"));
        assert_eq!(cfg.servers[0].url.as_deref(), Some("https://x.example"));

        let updated = serialize_mcp_config(original, &cfg).unwrap();
        let cfg2 = parse_mcp_config(&updated).unwrap();
        assert_eq!(cfg2.servers.len(), 1);
        assert_eq!(cfg2.servers[0].name, "inline");
        assert!(updated.contains("[[mcp.servers]]") || updated.contains("name = \"inline\""));
    }

    #[test]
    fn test_gptme_config_path_matches_platformdirs_layout() {
        let path = gptme_config_path().unwrap();
        assert!(path.ends_with(std::path::Path::new("gptme").join("config.toml")));
        #[cfg(windows)]
        {
            let roaming = dirs::config_dir()
                .unwrap()
                .join("gptme")
                .join("config.toml");
            assert_eq!(path, roaming);
        }
        #[cfg(not(windows))]
        {
            let unix = dirs::config_dir()
                .unwrap()
                .join("gptme")
                .join("config.toml");
            assert_eq!(path, unix);
        }
    }

    #[test]
    fn test_atomic_write_replaces_existing_file() {
        let dir = unique_temp_dir();
        let path = dir.join("config.toml");
        std::fs::write(&path, "old = true\n").unwrap();
        write_config_atomically(&path, "new = true\n").unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "new = true\n");
        assert!(!path.with_extension("toml.tmp").exists());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn test_replace_file_failure_leaves_destination_intact() {
        let dir = unique_temp_dir();
        let dest = dir.join("config.toml");
        std::fs::create_dir(&dest).unwrap();
        let marker = dest.join("keep-me");
        std::fs::write(&marker, "still here\n").unwrap();
        let tmp = dir.join("config.toml.tmp");
        std::fs::write(&tmp, "new = true\n").unwrap();

        assert!(replace_file(&tmp, &dest).is_err());
        assert!(
            marker.exists(),
            "replace must not delete dest before succeeding"
        );
        assert!(
            tmp.exists(),
            "source must remain so the caller can decide cleanup"
        );
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn test_atomic_write_preserves_existing_mode() {
        use std::os::unix::fs::PermissionsExt;
        let dir = unique_temp_dir();
        let path = dir.join("config.toml");
        std::fs::write(&path, "old = true\n").unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        write_config_atomically(&path, "new = true\n").unwrap();
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        // OpenOptions::mode() is subject to the ambient umask on creation, so
        // the exact resulting mode is `0o600 & !umask` and cannot be asserted
        // exactly without reading the umask (not in std). The security-relevant
        // property — no group/other access beyond the umask — is umask-proof.
        assert_eq!(mode & 0o077, 0, "file must not gain group/other access");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn test_atomic_write_new_file_is_private() {
        use std::os::unix::fs::PermissionsExt;
        let dir = unique_temp_dir();
        let path = dir.join("config.toml");
        write_config_atomically(&path, "new = true\n").unwrap();
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        // See note above: assert the umask-proof security property, not the
        // exact 0o600 bits (the ambient umask masks them on creation).
        assert_eq!(mode & 0o077, 0, "file must not gain group/other access");
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
