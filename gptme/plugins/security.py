"""Static security checks for installed gptme plugins.

The scanner intentionally uses distribution metadata only. It must run before an
entry point is imported, since importing untrusted plugin code in order to inspect
it defeats the point of the check.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class PluginSecurityFinding:
    """A high-confidence malware pattern found in plugin source."""

    path: str
    line: int
    category: str
    message: str

    def verdict(self) -> str:
        """Return the compact form used in doctor JSON details."""
        return (
            f"security:error({self.path}:{self.line} [{self.category}] {self.message})"
        )


@dataclass(frozen=True)
class PluginSecurityScan:
    """Result of scanning one plugin distribution."""

    scanned_files: int
    findings: tuple[PluginSecurityFinding, ...]
    entry_point_scanned: bool = False


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern[str]
    category: str
    message: str
    suffixes: frozenset[str]


def _pattern(regex: str, category: str, message: str, suffixes: set[str]) -> _Pattern:
    return _Pattern(
        re.compile(regex, re.IGNORECASE | re.DOTALL),
        category,
        message,
        frozenset(suffixes),
    )


# Comment markers per suffix. The scan searches raw source lines, so a comment
# like ``# open('.env.example')`` would otherwise read as a credential harvest.
_COMMENT_PREFIXES: dict[str, tuple[str, ...]] = {
    ".py": ("#",),
    ".sh": ("#",),
    ".bash": ("#",),
    ".js": ("//",),
    ".ts": ("//",),
    ".json": (),
}


def _is_comment_only(line: str, suffix: str) -> bool:
    """Return True for blank or comment-only lines (not executable code)."""
    stripped = line.lstrip()
    if not stripped:
        return True
    return any(
        stripped.startswith(prefix) for prefix in _COMMENT_PREFIXES.get(suffix, ("#",))
    )


def _code_portion(line: str, suffix: str) -> str:
    """Return the executable portion of ``line``, stripping trailing comments.

    Comment-only lines become empty. A trailing comment on a code line
    (``x = 1  # open('~/.ssh/id_rsa')``) is dropped so it cannot trip a
    pattern. Quotes are tracked so a ``#`` inside a string is kept.
    """
    prefixes = _COMMENT_PREFIXES.get(suffix, ("#",))
    if not prefixes:
        return line
    in_str: str | None = None
    i = 0
    while i < len(line):
        char = line[i]
        if in_str:
            if char == "\\" and in_str == '"':
                i += 2
                continue
            if char == in_str:
                in_str = None
            i += 1
            continue
        if char in {'"', "'"}:
            in_str = char
            i += 1
            continue
        if any(line.startswith(prefix, i) for prefix in prefixes):
            return line[:i]
        i += 1
    return line


# High-severity subset of Bob's idea #524 MCP/skill malware scanner. Doctor
# blocks import only for these narrow patterns; broader suspicious-code signals
# remain better suited to an audit command with human review.
_PATTERNS = (
    _pattern(
        # Allow any expression before the path so f-strings and concatenation
        # (``open(f'{home}/.ssh/id_rsa')``, ``open('/home/' + u + '/.ssh/x')``)
        # are caught, not just a bare quoted literal right after the call.
        r"(?:readFileSync|open|read_text)\s*\([^)]*"
        r"(?:\.ssh|\.gnupg|\.env(?!\.(?:example|sample|template|dist|default))|"
        r"credentials(?:\.(?:json|ya?ml|txt|ini))?)",
        "credential-harvest",
        "reads SSH or credential files",
        {".js", ".ts", ".py"},
    ),
    _pattern(
        r"glob\s*\(\s*[\"'].*\*.*(?:id_rsa|id_ed25519|\.pem|\.key)[\"']",
        "credential-harvest",
        "searches for private keys",
        {".js", ".ts", ".py"},
    ),
    _pattern(
        r"(?:eval|exec)\s*\(\s*(?:Buffer\.from|atob|base64\.b64decode)",
        "obfuscation",
        "executes decoded content",
        {".js", ".ts", ".py"},
    ),
    _pattern(
        r"(?:appendFileSync|write|open)\s*\(\s*[\"']?(?:.*\.bashrc|.*\.profile|.*\.zshrc|.*crontab)",
        "persistence",
        "writes to shell startup or cron files",
        {".js", ".ts", ".py"},
    ),
    _pattern(
        r"(?:os\.system|subprocess\.(?:run|call|check_output|Popen)).*(?:crontab\s+-[el]|systemctl\s+enable|launchctl\s+load)",
        "persistence",
        "installs a cron or system service",
        {".py"},
    ),
    _pattern(
        r"[\"'](?:preinstall|postinstall|prepare|prepack)[\"']\s*:\s*[\"'][^\"']*(?:curl|wget|eval|base64|node -e)",
        "lifecycle-hook",
        "package lifecycle hook downloads or evaluates code",
        {".json"},
    ),
    _pattern(
        r"(?:curl|wget)\s+[^\s]+\s+.*(?:\$HOME|~|/etc/|\.ssh)",
        "exfiltration",
        "sends a sensitive local path over the network",
        {".sh", ".bash"},
    ),
    _pattern(
        r"(?:/dev/tcp/|\bnc\s+-e\b|\bncat\s+-e\b)",
        "reverse-shell",
        "opens a reverse shell",
        {".sh", ".bash", ".py", ".js", ".ts"},
    ),
)

_SCANNABLE_SUFFIXES = frozenset(
    suffix for pattern in _PATTERNS for suffix in pattern.suffixes
)
# Only skip directories that are never importable. `tests`/`docs`/`build` inside
# an installed package remain importable (`package.tests.payload`) and must be
# scanned with the rest of the entry point's package.
_SKIP_PARTS = frozenset({".git", "__pycache__"})
_MAX_FILE_BYTES = 1_000_000


def _entry_point_module_paths(entry_point: object) -> frozenset[str]:
    """Distribution-relative paths that would hold the entry point's module."""
    value = getattr(entry_point, "value", None)
    module = getattr(entry_point, "module", None)
    dotted = module or (str(value).split(":")[0].strip() if value else "")
    if not dotted:
        return frozenset()
    base = dotted.replace(".", "/")
    return frozenset({f"{base}.py", f"{base}/__init__.py"})


def _editable_source_dir(distribution: object) -> Path | None:
    """Return the source directory of an editable install, or ``None``.

    ``pip install -e`` / ``uv pip install -e`` distributions list only their
    editable shim in ``files`` metadata, so the entry-point module is not
    discoverable from that list. ``direct_url.json`` records the source
    directory the install points at.
    """
    read_text = getattr(distribution, "read_text", None)
    if read_text is None:
        return None
    try:
        raw = read_text("direct_url.json")
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not data.get("dir_info", {}).get("editable"):
        return None
    url = str(data.get("url", ""))
    if not url.startswith("file://"):
        return None
    path = unquote(urlparse(url).path)
    # Windows file URLs look like file:///C:/... — strip the leading slash.
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    source = Path(path)
    return source if source.is_dir() else None


def _scan_source(path: Path, display: str) -> list[PluginSecurityFinding] | None:
    """Scan one source file; ``None`` when it cannot be inspected."""
    try:
        if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
            return None
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    suffix = path.suffix.lower()
    # Blank comment-only lines so they cannot match, but keep them in the
    # haystack so match offsets still map to original line numbers. Scan the
    # whole file (not each line independently) so a split expression such as
    # ``open(\n    "~/.ssh/id_rsa"\n)`` cannot evade the patterns.
    haystack = "\n".join(
        "" if _is_comment_only(line, suffix) else _code_portion(line, suffix)
        for line in content.splitlines()
    )
    findings: list[PluginSecurityFinding] = []
    for pattern in _PATTERNS:
        if suffix not in pattern.suffixes:
            continue
        findings.extend(
            PluginSecurityFinding(
                path=display,
                line=haystack[: match.start()].count("\n") + 1,
                category=pattern.category,
                message=pattern.message,
            )
            for match in pattern.regex.finditer(haystack)
        )
    return findings


def _iter_scannable_sources(directory: Path) -> list[Path]:
    """Return scannable source files under ``directory``, recursively.

    Mirrors the skip rules applied to distribution metadata so an editable
    install is scanned to the same depth as a wheel install.
    """
    sources: list[Path] = []
    try:
        paths = sorted(directory.rglob("*"))
    except OSError:
        return sources
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in _SCANNABLE_SUFFIXES:
            continue
        if any(
            part.lower() in _SKIP_PARTS or part.endswith(".dist-info")
            for part in path.relative_to(directory).parts[:-1]
        ):
            continue
        sources.append(path)
    return sources


def _editable_scan_targets(
    source_dir: Path, relative_paths: frozenset[str]
) -> tuple[Path | None, list[Path]]:
    """Resolve an editable install's entry point and every file it can import.

    Returns the entry-point module path (``None`` when unresolved) and the source
    files importing it may execute: the module itself plus every scannable file
    in its top-level package. The entry point is frequently a submodule
    (``pkg.cli:main``) rather than the package root, so the scan boundary is the
    top-level package directory — the same code ``doctor`` subsequently imports.
    Also probes a ``src/`` layout, since pip/uv editable installs of src-layout
    projects put the importable package one level below the project root.
    """
    for relative in sorted(relative_paths):
        for root in (source_dir, source_dir / "src"):
            candidate = root / relative
            if not candidate.is_file():
                continue
            targets = [candidate]
            # ``relative`` may be a submodule (``pkg.cli`` -> ``pkg/cli.py``),
            # not just the package root, so scan from the top-level package
            # directory: every sibling the entry point can import lives there.
            package_root = root / Path(relative).parts[0]
            if package_root.is_dir():
                targets.extend(
                    path
                    for path in _iter_scannable_sources(package_root)
                    if path != candidate
                )
            return candidate, targets
    return None, []


def scan_plugin_entry_point(entry_point: object) -> PluginSecurityScan | None:
    """Scan an entry point's third-party distribution without importing it.

    Returns ``None`` only when the entry point belongs to gptme itself. That
    code is already covered by gptme's own review and test pipeline. A
    third-party entry point with no distribution metadata is returned as an
    unverified scan so callers fail closed instead of importing it.

    The returned scan records ``entry_point_scanned`` so callers can fail closed:
    a clean result only means something when the code that will be imported was
    actually inspected. A third-party distribution whose file list is unavailable
    (``files is None``, e.g. installers that omit ``RECORD``) or whose executable
    module was skipped (over the size limit, unreadable) must not be reported as
    verified.
    """
    # importlib.metadata.EntryPoint always has a ``dist`` attribute (it may be
    # None). Test doubles and other non-distribution objects omit it entirely;
    # those are not third-party installs, so there is nothing to scan.
    if not hasattr(entry_point, "dist"):
        return None
    distribution = entry_point.dist
    if distribution is None:
        return PluginSecurityScan(0, (), entry_point_scanned=False)

    name = str(getattr(distribution, "name", ""))
    if name.lower().replace("_", "-") == "gptme":
        return None

    files = getattr(distribution, "files", None)
    if files is None:
        # No file list to scan: return an unverified (not clean) result so the
        # caller fails closed rather than importing unscanned third-party code.
        return PluginSecurityScan(0, (), entry_point_scanned=False)

    entry_candidates = _entry_point_module_paths(entry_point)
    findings: list[PluginSecurityFinding] = []
    scanned_files = 0
    entry_point_scanned = False
    for package_path in files:
        relative = Path(str(package_path))
        if relative.suffix.lower() not in _SCANNABLE_SUFFIXES:
            continue
        if any(
            part.lower() in _SKIP_PARTS or part.endswith(".dist-info")
            for part in relative.parts
        ):
            continue

        file_findings = _scan_source(
            Path(distribution.locate_file(package_path)), relative.as_posix()
        )
        if file_findings is None:
            continue
        scanned_files += 1
        if relative.as_posix() in entry_candidates:
            entry_point_scanned = True
        findings.extend(file_findings)

    # Editable installs list only their editable shim in ``files``, so the
    # entry-point module never appears there. Scan it and the sibling modules it
    # can import from the install's source directory instead of failing closed on
    # the documented plugin workflow. A sibling holding a blocked pattern must be
    # caught here: the entry point imports it before any runtime check applies.
    if not entry_point_scanned:
        source_dir = _editable_source_dir(distribution)
        if source_dir is not None:
            module_path, targets = _editable_scan_targets(source_dir, entry_candidates)
            for candidate in targets:
                file_findings = _scan_source(candidate, candidate.as_posix())
                if file_findings is None:
                    continue
                scanned_files += 1
                findings.extend(file_findings)
                if candidate == module_path:
                    entry_point_scanned = True

    return PluginSecurityScan(scanned_files, tuple(findings), entry_point_scanned)
