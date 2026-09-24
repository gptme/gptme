//! LAN access: lets a phone/tablet on the same WiFi open gptme.
//!
//! # Security note
//! Phase 1 exposes the server on the LAN without a token. The QR code provides
//! discoverability protection (only someone who can see the screen can scan it),
//! but anyone on the same network who guesses or intercepts the URL can connect.
//! Phase 2 will add `--access-token` validation to gptme-server.
//! Only use on trusted networks (home WiFi, not a shared hotspot).

use serde::{Deserialize, Serialize};
use std::sync::Mutex;

/// Persistent LAN access state (held in Tauri managed state).
#[derive(Debug, Default)]
pub struct LanAccessInner {
    pub enabled: bool,
    pub lan_ip: Option<String>,
    pub port: u16,
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
    /// Full URL: `http://<lan_ip>:<port>` (None when disabled).
    pub url: Option<String>,
    /// SVG QR code for the URL (None when disabled).
    pub qr_svg: Option<String>,
}

impl LanAccessInner {
    fn build_status(&self) -> LanStatus {
        let url = if self.enabled {
            self.lan_ip
                .as_ref()
                .map(|ip| format!("http://{}:{}", ip, self.port))
        } else {
            None
        };
        LanStatus {
            enabled: self.enabled,
            lan_ip: self.lan_ip.clone(),
            port: self.port,
            url,
            qr_svg: self.qr_svg.clone(),
        }
    }
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

// ── Tauri commands ─────────────────────────────────────────────────────────

/// Enable LAN access: detect LAN IP, generate QR code, update state.
///
/// Note: this does NOT yet restart the gptme-server sidecar with
/// `--host 0.0.0.0 --allowed-hosts <LAN_IP>`. That wiring is Phase 2 —
/// the user must restart manually or wait for the Phase 2 server integration.
#[cfg(desktop)]
#[tauri::command]
pub fn enable_lan_access(state: tauri::State<'_, LanAccess>) -> Result<LanStatus, String> {
    let lan_ip = detect_lan_ip()
        .ok_or_else(|| "Could not detect a LAN IP address on this machine".to_string())?;

    let mut inner = state.0.lock().map_err(|e| e.to_string())?;
    inner.enabled = true;
    inner.lan_ip = Some(lan_ip.clone());

    let url = format!("http://{}:{}", lan_ip, inner.port);
    inner.qr_svg = Some(generate_qr_svg(&url)?);

    log::info!("LAN access enabled: {url}");
    Ok(inner.build_status())
}

#[cfg(not(desktop))]
#[tauri::command]
pub fn enable_lan_access(_state: tauri::State<'_, LanAccess>) -> Result<LanStatus, String> {
    Err("LAN access is only available on desktop builds".to_string())
}

/// Disable LAN access and clear state.
#[tauri::command]
pub fn disable_lan_access(state: tauri::State<'_, LanAccess>) -> Result<(), String> {
    let mut inner = state.0.lock().map_err(|e| e.to_string())?;
    inner.enabled = false;
    inner.lan_ip = None;
    inner.qr_svg = None;
    log::info!("LAN access disabled");
    Ok(())
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
            qr_svg: None,
        };
        let status = s.build_status();
        assert!(status.enabled);
        assert_eq!(status.url, Some("http://192.168.1.42:5700".to_string()));
    }

    #[test]
    fn disabled_with_stale_ip_produces_no_url() {
        let s = LanAccessInner {
            enabled: false,
            lan_ip: Some("192.168.1.42".to_string()),
            port: 5700,
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
            qr_svg: Some("<svg>test</svg>".to_string()),
        };
        let status = s.build_status();
        assert_eq!(status.qr_svg, Some("<svg>test</svg>".to_string()));
    }

    #[test]
    #[cfg(desktop)]
    fn qr_svg_roundtrip() {
        let url = "http://192.168.1.42:5700";
        let svg = generate_qr_svg(url).expect("QR generation should succeed");
        assert!(svg.contains("<svg"), "output should be an SVG");
    }
}
