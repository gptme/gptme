//! Download destination resolution for the desktop webview.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, OnceLock};

/// Emitted after a download either finishes or fails, so the frontend can name
/// the actual saved path instead of announcing success optimistically.
pub const DOWNLOAD_FINISHED_EVENT: &str = "gptme-download-finished";

#[derive(Clone, serde::Serialize)]
pub struct DownloadFinishedPayload {
    pub url: String,
    pub path: Option<String>,
    pub success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Keep only a single, safe file-name component from a suggested download path.
///
/// WebKit/wry may pass a full path (the webview's previous default destination)
/// in `DownloadEvent::Requested`. Using `file_name` drops every directory
/// component, so a suggestion like `../../etc/passwd` cannot escape the
/// Downloads directory.
pub fn sanitize_filename(suggested: &Path) -> Option<String> {
    let name = suggested.file_name()?.to_str()?;
    let cleaned: String = name
        .chars()
        .filter(|c| !c.is_control() && *c != '/' && *c != '\\')
        .collect();
    let cleaned = cleaned.trim();
    if cleaned.is_empty() || cleaned == "." || cleaned == ".." {
        None
    } else {
        Some(cleaned.to_string())
    }
}

/// Destinations claimed by downloads that have been requested but have not
/// reported completion yet, keyed by download URL.
///
/// Resolution only sees files on disk, so two exports of the same name
/// requested before the first one creates its file would both pick the same
/// path. This registry closes that window. A claim that never receives a
/// completion event (e.g. the user cancels) is simply never reused; the next
/// export of that name gets a numbered variant rather than overwriting it.
fn in_flight() -> &'static Mutex<HashMap<String, PathBuf>> {
    static IN_FLIGHT: OnceLock<Mutex<HashMap<String, PathBuf>>> = OnceLock::new();
    IN_FLIGHT.get_or_init(|| Mutex::new(HashMap::new()))
}

fn in_flight_guard() -> MutexGuard<'static, HashMap<String, PathBuf>> {
    // A panic while holding the lock must not disable every later download.
    in_flight()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Resolve a destination for `url` and claim it until the download finishes.
///
/// Resolution and the claim happen under one lock, so concurrent requests
/// cannot both pick the same free path.
pub fn claim_destination(
    downloads_dir: &Path,
    suggested: &Path,
    url: &str,
) -> Result<PathBuf, String> {
    let mut pending = in_flight_guard();
    let reserved: HashSet<PathBuf> = pending.values().cloned().collect();
    let path = resolve_destination_excluding(downloads_dir, suggested, &reserved)?;
    pending.insert(url.to_string(), path.clone());
    Ok(path)
}

/// Release the claim held for `url` once its download reports an outcome.
pub fn release_destination(url: &str) {
    in_flight_guard().remove(url);
}

/// Resolve a collision-free destination inside `downloads_dir`.
///
/// Creates the directory when missing and fails closed (returns `Err`) rather
/// than silently falling back to the process working directory. The file itself
/// is not created here: WebKit refuses to download to an existing path, so
/// reserving it with a placeholder would break the download.
fn resolve_destination_excluding(
    downloads_dir: &Path,
    suggested: &Path,
    reserved: &HashSet<PathBuf>,
) -> Result<PathBuf, String> {
    let filename = sanitize_filename(suggested).ok_or_else(|| {
        format!(
            "suggested download path has no usable file name: {}",
            suggested.display()
        )
    })?;

    if !downloads_dir.is_absolute() {
        return Err(format!(
            "Downloads directory is not absolute: {}",
            downloads_dir.display()
        ));
    }

    std::fs::create_dir_all(downloads_dir).map_err(|error| {
        format!(
            "could not create Downloads directory {}: {error}",
            downloads_dir.display()
        )
    })?;

    if !downloads_dir.is_dir() {
        return Err(format!(
            "Downloads path is not a directory: {}",
            downloads_dir.display()
        ));
    }

    let candidate = downloads_dir.join(&filename);
    if !candidate.exists() && !reserved.contains(&candidate) {
        return Ok(candidate);
    }

    let (stem, extension) = split_filename(&filename);
    let mut counter = 1u32;
    loop {
        let candidate = downloads_dir.join(format!("{stem} ({counter}){extension}"));
        if !candidate.exists() && !reserved.contains(&candidate) {
            return Ok(candidate);
        }
        counter = counter.saturating_add(1);
    }
}

/// Split `name.ext` into `("name", ".ext")`, treating leading-dot names and
/// names without an extension as extensionless.
fn split_filename(filename: &str) -> (String, String) {
    match filename.rfind('.') {
        Some(index) if index > 0 => (filename[..index].to_string(), filename[index..].to_string()),
        _ => (filename.to_string(), String::new()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn resolve(dir: &Path, suggested: &Path) -> Result<PathBuf, String> {
        resolve_destination_excluding(dir, suggested, &HashSet::new())
    }

    struct TempDir(PathBuf);

    impl TempDir {
        fn new(label: &str) -> Self {
            use std::sync::atomic::{AtomicU64, Ordering};
            static COUNTER: AtomicU64 = AtomicU64::new(0);
            let id = COUNTER.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "gptme-downloads-{label}-{}-{id}",
                std::process::id()
            ));
            let _ = std::fs::remove_dir_all(&path);
            Self(path)
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn sanitize_keeps_only_the_file_name() {
        assert_eq!(
            sanitize_filename(Path::new("/home/user/Export-roundtrip-2179.md")),
            Some("Export-roundtrip-2179.md".to_string())
        );
        assert_eq!(
            sanitize_filename(Path::new("../../etc/passwd")),
            Some("passwd".to_string())
        );
        assert_eq!(
            sanitize_filename(Path::new("a/b/../c.md")),
            Some("c.md".to_string())
        );
    }

    #[test]
    fn sanitize_rejects_paths_without_a_usable_file_name() {
        assert_eq!(sanitize_filename(Path::new("")), None);
        assert_eq!(sanitize_filename(Path::new(".")), None);
        assert_eq!(sanitize_filename(Path::new("..")), None);
        assert_eq!(sanitize_filename(Path::new("/")), None);
    }

    #[test]
    fn sanitize_strips_control_characters_and_separators() {
        assert_eq!(
            sanitize_filename(Path::new("bad\u{0}name.md")),
            Some("badname.md".to_string())
        );
        assert_eq!(
            sanitize_filename(Path::new("a\\b.md")),
            Some("ab.md".to_string())
        );
    }

    #[test]
    fn sanitize_rejects_names_that_clean_to_nothing() {
        assert_eq!(sanitize_filename(Path::new("\u{0}")), None);
    }

    #[test]
    fn resolve_creates_the_downloads_directory() {
        let dir = TempDir::new("create");
        let nested = dir.0.join("nested/downloads");

        let resolved = resolve(&nested, Path::new("Export.md")).unwrap();

        assert_eq!(resolved, nested.join("Export.md"));
        assert!(nested.is_dir());
    }

    #[test]
    fn resolve_avoids_overwriting_existing_exports() {
        let dir = TempDir::new("collision");
        std::fs::create_dir_all(&dir.0).unwrap();
        std::fs::write(dir.0.join("Export.md"), "first").unwrap();
        std::fs::write(dir.0.join("Export (1).md"), "second").unwrap();

        let resolved = resolve(&dir.0, Path::new("/tmp/Export.md")).unwrap();

        assert_eq!(resolved, dir.0.join("Export (2).md"));
    }

    #[test]
    fn resolve_preserves_the_extension_when_renaming() {
        let dir = TempDir::new("extension");
        std::fs::create_dir_all(&dir.0).unwrap();
        std::fs::write(dir.0.join("archive.backup.json"), "old").unwrap();

        let resolved = resolve(&dir.0, Path::new("archive.backup.json")).unwrap();

        assert_eq!(resolved, dir.0.join("archive.backup (1).json"));
    }

    #[test]
    fn resolve_handles_names_without_an_extension() {
        let dir = TempDir::new("noext");
        std::fs::create_dir_all(&dir.0).unwrap();
        std::fs::write(dir.0.join("Export"), "old").unwrap();

        let resolved = resolve(&dir.0, Path::new("Export")).unwrap();

        assert_eq!(resolved, dir.0.join("Export (1)"));
    }

    #[test]
    fn resolve_stays_inside_the_downloads_directory() {
        let dir = TempDir::new("traversal");

        let resolved = resolve(&dir.0, Path::new("../../etc/passwd")).unwrap();

        assert_eq!(resolved, dir.0.join("passwd"));
    }

    #[test]
    fn resolve_rejects_an_unusable_suggested_name() {
        let dir = TempDir::new("unusable");

        assert!(resolve(&dir.0, Path::new("..")).is_err());
        assert!(resolve(&dir.0, Path::new("")).is_err());
    }

    #[test]
    fn resolve_requires_an_absolute_downloads_directory() {
        assert!(resolve(Path::new("relative/downloads"), Path::new("Export.md")).is_err());
    }

    #[test]
    fn claim_avoids_destinations_held_by_in_flight_downloads() {
        let dir = TempDir::new("inflight");
        std::fs::create_dir_all(&dir.0).unwrap();
        let first_url = "blob:inflight-first";
        let second_url = "blob:inflight-second";

        let first = claim_destination(&dir.0, Path::new("Export.md"), first_url).unwrap();
        // No file exists yet, but the first download already owns the name.
        let second = claim_destination(&dir.0, Path::new("Export.md"), second_url).unwrap();

        assert_eq!(first, dir.0.join("Export.md"));
        assert_eq!(second, dir.0.join("Export (1).md"));

        release_destination(first_url);
        release_destination(second_url);
    }

    #[test]
    fn released_destinations_are_reusable() {
        let dir = TempDir::new("released");
        std::fs::create_dir_all(&dir.0).unwrap();
        let first_url = "blob:released-first";
        let second_url = "blob:released-second";

        let first = claim_destination(&dir.0, Path::new("Export.md"), first_url).unwrap();
        release_destination(first_url);
        let second = claim_destination(&dir.0, Path::new("Export.md"), second_url).unwrap();

        assert_eq!(first, second);

        release_destination(second_url);
    }

    #[test]
    fn release_of_an_unknown_url_is_a_no_op() {
        release_destination("blob:never-claimed");
    }

    #[test]
    fn resolve_fails_closed_when_the_downloads_path_is_not_a_directory() {
        let dir = TempDir::new("notdir");
        std::fs::create_dir_all(dir.0.parent().unwrap()).unwrap();
        std::fs::write(&dir.0, "not a directory").unwrap();

        assert!(resolve(&dir.0, Path::new("Export.md")).is_err());
    }
}
