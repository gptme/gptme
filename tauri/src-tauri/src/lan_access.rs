//! LAN access: lets a phone/tablet on the same WiFi open gptme.
//!
//! # Security note
//! Enabling LAN access rebinds the gptme-server sidecar to `0.0.0.0` with
//! bearer-token auth (the token is shared only with the sidecar and the
//! Tauri webview). The QR code embeds the base URL plus the session token
//! in the webui's hash-based connection config, so only someone who can see
//! the screen can scan it. Anyone who scans or reads the QR gains session
//! access — only use on trusted networks (home WiFi, not a shared hotspot).

use serde::{Deserialize, Serialize};
use std::sync::Mutex;

/// Persistent LAN access state (held in Tauri managed state).
#[derive(Debug, Default)]
pub struct LanAccessInner {
    pub enabled: bool,
    pub lan_ip: Option<String>,
    pub port: u16,
    /// Full connect URL including the auth-token hash fragment (None when disabled).
    pub url: Option<String>,
    /// Cached QR SVG so status polling can return it without regenerating.
    pub qr_svg: Option<String>,
}

/// Thread-safe wrapper registered with `app.manage()`.
pub struct LanAccess(pub Mutex<LanAccessInner>);

impl LanAccess {
    pub fn new(port: u16) -> Self {
        LanAccess(Mutex::new(LanAccessInner {
            enabled: false,
            lan_ip: None,
            port,
            url: None,
            qr_svg: None,
        }))
    }
}

/// Serializable snapshot returned to the frontend.
#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct LanStatus {
    pub enabled: bool,
    pub lan_ip: Option<String>,
    pub port: u16,
    /// Full connect URL: `http://<lan_ip>:<port>#baseUrl=...&userToken=...` (None when disabled).
    pub url: Option<String>,
    /// SVG QR code for the URL (None when disabled).
    pub qr_svg: Option<String>,
}

impl LanAccessInner {
    fn build_status(&self) -> LanStatus {
        LanStatus {
            enabled: self.enabled,
            lan_ip: self.lan_ip.clone(),
            port: self.port,
            url: if self.enabled { self.url.clone() } else { None },
            qr_svg: self.qr_svg.clone(),
        }
    }
}

/// Build the QR/connect URL: base URL plus the webui's hash-based connection
/// config (`#baseUrl=<url>&userToken=<token>`), so a phone opening it connects
/// authenticated without any manual token entry.
fn build_connect_url(base_url: &str, token: &str) -> String {
    // Both values are percent-escaped so they cannot inject additional
    // fragment parameters (the webui parses the hash as a query string).
    // The server token is hex today, but encoding it keeps the contract
    // robust if the token format ever changes.
    let encoded = base_url
        .replace('%', "%25")
        .replace('#', "%23")
        .replace('&', "%26");
    let encoded_token = token
        .replace('%', "%25")
        .replace('#', "%23")
        .replace('&', "%26");
    format!("{base_url}#baseUrl={encoded}&userToken={encoded_token}")
}

// ── platform-specific helpers ──────────────────────────────────────────────

/// Detect the primary LAN IPv4 address of this machine.
#[cfg(desktop)]
fn detect_lan_ip() -> Option<String> {
    local_ip_address::local_ip().ok().map(|ip| ip.to_string())
}

/// Render an SVG QR code for `url`.
#[cfg(desktop)]
fn generate_qr_svg(url: &str) -> Result<String, String> {
    use qrcode::render::svg;
    use qrcode::{EcLevel, QrCode};

    let code = QrCode::with_error_correction_level(url.as_bytes(), EcLevel::M)
        .map_err(|e| format!("QR generation failed: {e}"))?;

    Ok(code
        .render::<svg::Color>()
        .min_dimensions(200, 200)
        .max_dimensions(300, 300)
        .build())
}

// ── Sidecar rebinding (desktop) ────────────────────────────────────────────

/// Restart the managed gptme-server sidecar, optionally bound to the LAN.
/// `lan_ip = None` rebinds to the default loopback-only configuration.
///
/// Failure safety: once the old sidecar has been killed we never leave the
/// app without a backend — if the LAN rebind fails we respawn a loopback-only
/// server before returning the error.
#[cfg(desktop)]
async fn restart_sidecar_with_lan(
    server: &crate::ServerProcess,
    lan_ip: Option<&str>,
) -> Result<(), String> {
    use std::sync::atomic::Ordering;
    use std::time::Duration;

    // Only rebind a server we manage — killing a foreign server we merely
    // found on the port would be wrong. "Managed" means either we hold a
    // child handle, or we adopted a usable server on the port (owns_port).
    let child = server
        .child
        .lock()
        .map_err(|e| format!("Lock error: {e}"))?
        .take();
    let child = match child {
        Some(child) => Some(child),
        None => {
            if !server.owns_port.load(Ordering::Relaxed) {
                return Err(
                    "gptme-server is not managed by this app (external server on the port); \
                     restart gptme-tauri to enable LAN access"
                        .to_string(),
                );
            }
            // Adoption path (crash recovery): we own the port but hold no
            // child handle. Stop whatever serves the port, then rebind — but
            // only if the process still listening is actually a gptme-server.
            // The adopted process may have died since adoption and an
            // unrelated process taken the port; never kill one of those.
            match crate::server_pid_on_port(crate::server_port()) {
                Some(pid) if crate::pid_is_gptme_server(pid) => {
                    log::info!(
                        "No child handle for adopted server; killing gptme-server PID {pid} for LAN rebind"
                    );
                    crate::kill_server_on_port(crate::server_port());
                }
                Some(pid) => {
                    return Err(format!(
                        "Port {} is held by PID {pid}, which is not a gptme-server; \
                         refusing to kill it for LAN rebind",
                        crate::server_port()
                    ));
                }
                None => {
                    // Port is already free — nothing to kill, proceed to respawn.
                    log::info!("Adopted server no longer listening on port; respawning");
                }
            }
            None
        }
    };
    if let Some(child) = child {
        let old_pid = child.pid();
        log::info!("Stopping gptme-server for LAN rebind (lan_ip: {lan_ip:?})");
        crate::kill_subprocesses(old_pid);
        // A stale child handle (process already exited) is not fatal: the
        // port-free wait and loopback fallback below still run.
        if let Err(e) = child.kill() {
            log::warn!("Failed to kill old sidecar child (may have already exited): {e}");
        }
    }
    server.owns_port.store(false, Ordering::Relaxed);

    let app = server
        .app_handle
        .lock()
        .map_err(|e| format!("Lock error: {e}"))?
        .clone()
        .ok_or_else(|| "App handle not initialized".to_string())?;

    // Wait for the port to actually free up (uvicorn workers + TIME_WAIT).
    let mut port_free = false;
    for _ in 0..25 {
        if crate::is_port_available(crate::server_port()) {
            port_free = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    if !port_free {
        // A PyInstaller onefile orphan may survive the launcher kill and keep
        // holding the port — possibly LAN-bound with the old token (#2260).
        // Force-clear it before giving up, so the restore spawn below cannot
        // adopt (or fail against) an orphan that still serves the LAN.
        log::warn!(
            "Port {} did not free up; force-clearing possible orphan",
            crate::server_port()
        );
        crate::kill_server_on_port(crate::server_port());
        for _ in 0..10 {
            if crate::is_port_available(crate::server_port()) {
                port_free = true;
                break;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
    }
    if !port_free {
        // The port still never freed: restore a usable (loopback-only) server
        // before surfacing the error, so the app is not left without a backend.
        let restore = crate::spawn_server_sidecar(
            &app,
            server.child.clone(),
            server.owns_port.clone(),
            &server.token,
            None,
        )
        .await;
        let bind_err = format!(
            "Port {} did not free up after stopping gptme-server; try again",
            crate::server_port()
        );
        return match restore {
            Ok(()) => Err(bind_err),
            Err(e) => Err(format!("{bind_err}; automatic recovery also failed: {e}")),
        };
    }

    if let Err(e) = crate::spawn_server_sidecar(
        &app,
        server.child.clone(),
        server.owns_port.clone(),
        &server.token,
        lan_ip,
    )
    .await
    {
        // Rebind failed after we killed the old server — fall back to a
        // loopback-only server so the app keeps working.
        log::warn!("LAN rebind failed ({e}); falling back to loopback-only server");
        let restore = crate::spawn_server_sidecar(
            &app,
            server.child.clone(),
            server.owns_port.clone(),
            &server.token,
            None,
        )
        .await;
        return match restore {
            Ok(()) => Err(e),
            Err(e2) => Err(format!("{e}; automatic recovery also failed: {e2}")),
        };
    }
    Ok(())
}

// ── Tauri commands ─────────────────────────────────────────────────────────

/// Enable LAN access: detect LAN IP, generate QR code, rebind the sidecar to
/// the LAN, then update state.
#[cfg(desktop)]
#[tauri::command]
pub async fn enable_lan_access(
    state: tauri::State<'_, LanAccess>,
    server: tauri::State<'_, crate::ServerProcess>,
) -> Result<LanStatus, String> {
    let lan_ip = detect_lan_ip()
        .ok_or_else(|| "Could not detect a LAN IP address on this machine".to_string())?;

    let (port, token) = {
        let inner = state.0.lock().unwrap_or_else(|e| e.into_inner());
        (inner.port, server.token.clone())
    };
    let base_url = format!("http://{lan_ip}:{port}");
    let connect_url = build_connect_url(&base_url, &token);
    // Generate QR before touching the sidecar so a failure leaves everything
    // (state and server binding) unchanged.
    let qr_svg = generate_qr_svg(&connect_url)?;

    // Rebind the sidecar to 0.0.0.0 before reporting success.
    restart_sidecar_with_lan(&server, Some(&lan_ip)).await?;

    let mut inner = state.0.lock().unwrap_or_else(|e| e.into_inner());
    inner.enabled = true;
    inner.lan_ip = Some(lan_ip);
    inner.url = Some(connect_url.clone());
    inner.qr_svg = Some(qr_svg);

    // Never log the connect URL: it embeds the bearer token (userToken=...)
    // and tauri_plugin_log persists Info logs to disk.
    log::info!("LAN access enabled at {base_url}");
    Ok(inner.build_status())
}

#[cfg(not(desktop))]
#[tauri::command]
pub async fn enable_lan_access(
    _state: tauri::State<'_, LanAccess>,
    _server: tauri::State<'_, crate::ServerProcess>,
) -> Result<LanStatus, String> {
    Err("LAN access is only available on desktop builds".to_string())
}

/// Disable LAN access: rebind the sidecar to loopback-only and clear state.
#[cfg(desktop)]
#[tauri::command]
pub async fn disable_lan_access(
    state: tauri::State<'_, LanAccess>,
    server: tauri::State<'_, crate::ServerProcess>,
) -> Result<(), String> {
    // If the server is not managed we cannot rebind it: clear the LAN state
    // (so the toggle stops showing "enabled") and tell the user the external
    // server may still be exposed and must be reconfigured manually.
    // A server we adopted via the crash-recovery reuse path counts as
    // managed: we own the port even without a child handle.
    let managed = server.child.lock().map(|g| g.is_some()).unwrap_or(false)
        || server.owns_port.load(std::sync::atomic::Ordering::Relaxed);
    if !managed {
        {
            let mut inner = state.0.lock().unwrap_or_else(|e| e.into_inner());
            inner.enabled = false;
            inner.lan_ip = None;
            inner.url = None;
            inner.qr_svg = None;
        }
        log::warn!("LAN disable requested but gptme-server is externally managed");
        return Err(
            "gptme-server is not managed by this app; LAN access state cleared, \
             but an external server may still be exposed on the network — \
             restart it manually to rebind it to loopback"
                .to_string(),
        );
    }
    // Clear the LAN state regardless of the restart outcome: once the rebind
    // attempt is made the toggle must stop showing "enabled", even if the
    // rebind itself failed (the server is loopback-bound or dead either way).
    let restart_result = restart_sidecar_with_lan(&server, None).await;
    {
        let mut inner = state.0.lock().unwrap_or_else(|e| e.into_inner());
        inner.enabled = false;
        inner.lan_ip = None;
        inner.url = None;
        inner.qr_svg = None;
    }
    log::info!("LAN access disabled");
    restart_result
}

#[cfg(not(desktop))]
#[tauri::command]
pub async fn disable_lan_access(
    _state: tauri::State<'_, LanAccess>,
    _server: tauri::State<'_, crate::ServerProcess>,
) -> Result<(), String> {
    Err("LAN access is only available on desktop builds".to_string())
}

/// Return the current LAN access status (safe to call at any time).
#[tauri::command]
pub fn get_lan_access_status(state: tauri::State<'_, LanAccess>) -> LanStatus {
    let inner = state.0.lock().unwrap_or_else(|e| e.into_inner());
    inner.build_status()
}

// ── tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disabled_status_has_no_url() {
        let s = LanAccessInner {
            enabled: false,
            lan_ip: None,
            port: 5700,
            url: None,
            qr_svg: None,
        };
        let status = s.build_status();
        assert!(!status.enabled);
        assert!(status.url.is_none());
        assert!(status.qr_svg.is_none());
    }

    #[test]
    fn enabled_status_builds_url() {
        let s = LanAccessInner {
            enabled: true,
            lan_ip: Some("192.168.1.42".to_string()),
            port: 5700,
            url: Some("http://192.168.1.42:5700#baseUrl=x&userToken=t".to_string()),
            qr_svg: None,
        };
        let status = s.build_status();
        assert!(status.enabled);
        assert_eq!(
            status.url,
            Some("http://192.168.1.42:5700#baseUrl=x&userToken=t".to_string())
        );
    }

    #[test]
    fn disabled_with_stale_ip_produces_no_url() {
        let s = LanAccessInner {
            enabled: false,
            lan_ip: Some("192.168.1.42".to_string()),
            port: 5700,
            url: Some("http://192.168.1.42:5700".to_string()),
            qr_svg: None,
        };
        let status = s.build_status();
        assert!(status.url.is_none());
    }

    #[test]
    fn status_returns_stored_qr_svg() {
        let s = LanAccessInner {
            enabled: true,
            lan_ip: Some("192.168.1.42".to_string()),
            port: 5700,
            url: Some("http://192.168.1.42:5700".to_string()),
            qr_svg: Some("<svg>test</svg>".to_string()),
        };
        let status = s.build_status();
        assert_eq!(status.qr_svg, Some("<svg>test</svg>".to_string()));
    }

    #[test]
    fn connect_url_includes_base_url_and_token() {
        let url = build_connect_url("http://192.168.1.42:5700", "tok");
        assert!(url.starts_with("http://192.168.1.42:5700#"));
        assert!(url.contains("baseUrl=http://"));
        assert!(url.contains("userToken=tok"));
    }

    #[test]
    fn connect_url_escapes_special_characters() {
        let url = build_connect_url("http://1.2.3.4:5700", "a&b#c%d");
        assert!(url.contains("userToken=a%26b%23c%25d"));
        assert!(url.contains("baseUrl=http://1.2.3.4:5700"));
    }

    #[test]
    #[cfg(desktop)]
    fn qr_svg_roundtrip() {
        let url = build_connect_url("http://192.168.1.42:5700", "test-token");
        let svg = generate_qr_svg(&url).expect("QR generation should succeed");
        assert!(svg.contains("<svg"), "output should be an SVG");
    }
}
