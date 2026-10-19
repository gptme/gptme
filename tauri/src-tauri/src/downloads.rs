//! Download destination resolution for the desktop webview.

use std::path::{Path, PathBuf};

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

/// Resolve a collision-free destination inside `downloads_dir`.
///
/// Creates the directory when missing and fails closed (returns `Err`) rather
/// than silently falling back to the process working directory. The file itself
/// is not created here: WebKit refuses to download to an existing path, so
/// reserving it with a placeholder would break the download.
pub fn resolve_destination(downloads_dir: &Path, suggested: &Path) -> Result<PathBuf, String> {
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
    if !candidate.exists() {
        return Ok(candidate);
    }

    let (stem, extension) = split_filename(&filename);
    let mut counter = 1u32;
    loop {
        let candidate = downloads_dir.join(format!("{stem} ({counter}){extension}"));
        if !candidate.exists() {
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

        let resolved = resolve_destination(&nested, Path::new("Export.md")).unwrap();

        assert_eq!(resolved, nested.join("Export.md"));
        assert!(nested.is_dir());
    }

    #[test]
    fn resolve_avoids_overwriting_existing_exports() {
        let dir = TempDir::new("collision");
        std::fs::create_dir_all(&dir.0).unwrap();
        std::fs::write(dir.0.join("Export.md"), "first").unwrap();
        std::fs::write(dir.0.join("Export (1).md"), "second").unwrap();

        let resolved = resolve_destination(&dir.0, Path::new("/tmp/Export.md")).unwrap();

        assert_eq!(resolved, dir.0.join("Export (2).md"));
    }

    #[test]
    fn resolve_preserves_the_extension_when_renaming() {
        let dir = TempDir::new("extension");
        std::fs::create_dir_all(&dir.0).unwrap();
        std::fs::write(dir.0.join("archive.backup.json"), "old").unwrap();

        let resolved = resolve_destination(&dir.0, Path::new("archive.backup.json")).unwrap();

        assert_eq!(resolved, dir.0.join("archive.backup (1).json"));
    }

    #[test]
    fn resolve_handles_names_without_an_extension() {
        let dir = TempDir::new("noext");
        std::fs::create_dir_all(&dir.0).unwrap();
        std::fs::write(dir.0.join("Export"), "old").unwrap();

        let resolved = resolve_destination(&dir.0, Path::new("Export")).unwrap();

        assert_eq!(resolved, dir.0.join("Export (1)"));
    }

    #[test]
    fn resolve_stays_inside_the_downloads_directory() {
        let dir = TempDir::new("traversal");

        let resolved = resolve_destination(&dir.0, Path::new("../../etc/passwd")).unwrap();

        assert_eq!(resolved, dir.0.join("passwd"));
    }

    #[test]
    fn resolve_rejects_an_unusable_suggested_name() {
        let dir = TempDir::new("unusable");

        assert!(resolve_destination(&dir.0, Path::new("..")).is_err());
        assert!(resolve_destination(&dir.0, Path::new("")).is_err());
    }

    #[test]
    fn resolve_requires_an_absolute_downloads_directory() {
        assert!(
            resolve_destination(Path::new("relative/downloads"), Path::new("Export.md")).is_err()
        );
    }

    #[test]
    fn resolve_fails_closed_when_the_downloads_path_is_not_a_directory() {
        let dir = TempDir::new("notdir");
        std::fs::create_dir_all(dir.0.parent().unwrap()).unwrap();
        std::fs::write(&dir.0, "not a directory").unwrap();

        assert!(resolve_destination(&dir.0, Path::new("Export.md")).is_err());
    }
}
