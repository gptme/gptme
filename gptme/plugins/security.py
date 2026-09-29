"""Static security checks for installed gptme plugins.

The scanner intentionally uses distribution metadata only. It must run before an
entry point is imported, since importing untrusted plugin code in order to inspect
it defeats the point of the check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


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
        re.compile(regex, re.IGNORECASE), category, message, frozenset(suffixes)
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


# High-severity subset of Bob's idea #524 MCP/skill malware scanner. Doctor
# blocks import only for these narrow patterns; broader suspicious-code signals
# remain better suited to an audit command with human review.
_PATTERNS = (
    _pattern(
        r"(?:readFileSync|open|read_text)\s*\(\s*[\"'][^\"']*"
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
_SKIP_PARTS = frozenset(
    {".git", "__pycache__", "dist", "build", "docs", "examples", "test", "tests"}
)
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


def scan_plugin_entry_point(entry_point: object) -> PluginSecurityScan | None:
    """Scan an entry point's third-party distribution without importing it.

    Returns ``None`` when distribution metadata is unavailable or the entry point
    belongs to gptme itself. The latter is trusted project code already covered by
    gptme's own review and test pipeline.

    The returned scan records ``entry_point_scanned`` so callers can fail closed:
    a clean result only means something when the code that will be imported was
    actually inspected. A plugin whose executable module was skipped (for example
    under ``build``/``dist``, over the size limit, or unreadable) must not be
    reported as verified.
    """
    distribution = getattr(entry_point, "dist", None)
    if distribution is None:
        return None

    name = str(getattr(distribution, "name", ""))
    if name.lower().replace("_", "-") == "gptme":
        return None

    files = getattr(distribution, "files", None)
    if files is None:
        return None

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

        path = Path(distribution.locate_file(package_path))
        try:
            if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        scanned_files += 1
        if relative.as_posix() in entry_candidates:
            entry_point_scanned = True
        suffix = relative.suffix.lower()
        for line_number, line in enumerate(content.splitlines(), 1):
            if _is_comment_only(line, suffix):
                continue
            findings.extend(
                PluginSecurityFinding(
                    path=str(relative),
                    line=line_number,
                    category=pattern.category,
                    message=pattern.message,
                )
                for pattern in _PATTERNS
                if suffix in pattern.suffixes and pattern.regex.search(line)
            )

    return PluginSecurityScan(scanned_files, tuple(findings), entry_point_scanned)
