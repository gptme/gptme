"""Memory entry schema: markdown body + YAML frontmatter, Claude Code compatible.

The Claude Code fields (``name``, ``description``, ``metadata.type``,
``metadata.originSessionId``) are read and written exactly as CC writes them,
so CC's built-in memory feature keeps working on the same files. Every other
field is additive and optional.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import yaml

DEFAULT_TYPE = "general"
DEFAULT_STATUS = "living"
STATUSES = ("living", "superseded", "historical")
# Types in the order the generated index groups them; unknown types follow alphabetically.
TYPE_ORDER = ("user", "feedback", "project", "reference", "general")

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)
_KV_RE = re.compile(
    r"^(?P<indent> *)(?P<key>[A-Za-z_][A-Za-z0-9_-]*):[ \t]*(?P<value>.*)$"
)


class MemoryParseError(ValueError):
    """Raised when a file is not a memory entry (no or unusable frontmatter)."""


class MemoryFrontmatterError(MemoryParseError):
    """Raised when strict parsing is required but the YAML is invalid."""


def slugify(name: str) -> str:
    """Convert a name to a safe filename slug (``My Fact!`` → ``my-fact``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
    return slug or "memory"


def is_index_file(path: Path) -> bool:
    """``MEMORY.md`` and its siblings (``MEMORY-archive.md``) are indexes, not entries."""
    return path.name.upper().startswith("MEMORY")


@dataclass
class MemoryEntry:
    name: str
    description: str = ""
    type: str = DEFAULT_TYPE
    body: str = ""
    title: str | None = None
    status: str = DEFAULT_STATUS
    supersedes: list[str] = field(default_factory=list)
    superseded_by: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    keywords: list[str] = field(default_factory=list)
    recheck: str | None = None
    # The CC ``metadata`` block verbatim (minus ``type``, which is lifted to ``type``).
    metadata: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None
    scope: str | None = None

    @property
    def filename(self) -> str:
        return self.path.name if self.path is not None else f"{self.name}.md"

    @property
    def index_title(self) -> str:
        """Link text for the index line: the title when set, else the name."""
        return self.title or self.name

    @property
    def is_living(self) -> bool:
        return self.status == DEFAULT_STATUS

    def index_line(self) -> str:
        return f"- [{self.index_title}]({self.filename}) — {self.description}"

    def to_markdown(self) -> str:
        """Render frontmatter + body. Field order is fixed so diffs stay small."""
        lines = ["---", f"name: {self.name}"]
        if self.title:
            lines.append(f"title: {_quote(self.title)}")
        lines.append(f"description: {_quote(self.description)}")
        meta: dict[str, Any] = {"type": self.type, **self.metadata}
        lines.append("metadata:")
        lines.append(_dump_block(meta, indent=2))
        if self.status != DEFAULT_STATUS:
            lines.append(f"status: {self.status}")
        optional: dict[str, Any] = {}
        if self.supersedes:
            optional["supersedes"] = list(self.supersedes)
        if self.superseded_by:
            optional["superseded_by"] = self.superseded_by
        if self.provenance:
            optional["provenance"] = dict(self.provenance)
        if self.confidence is not None:
            optional["confidence"] = self.confidence
        if self.keywords:
            optional["keywords"] = list(self.keywords)
        if self.recheck:
            optional["recheck"] = self.recheck
        if optional:
            lines.append(_dump_block(optional, indent=0))
        lines.append("---")
        lines.append("")
        text = "\n".join(lines) + "\n"
        body = self.body.strip("\n")
        return text + (body + "\n" if body else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "type": self.type,
            "status": self.status,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "keywords": self.keywords,
            "recheck": self.recheck,
            "metadata": self.metadata,
            "path": str(self.path) if self.path else None,
            "scope": self.scope,
        }


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _dump_block(data: dict[str, Any], indent: int) -> str:
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10_000)
    pad = " " * indent
    return "\n".join(pad + line for line in dumped.rstrip("\n").splitlines())


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """Return ``(frontmatter, body)`` or ``None`` when the text has no frontmatter."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    return m.group(1), text[m.end() :]


def _lenient_load(raw: str) -> dict[str, Any]:
    """Line-based fallback for frontmatter that PyYAML rejects (unquoted colons etc.).

    Handles top-level scalars and a one-level nested block (``metadata:``).
    Lists and deeper nesting are dropped; ``audit`` will flag those files later.
    """
    data: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    for line in raw.splitlines():
        m = _KV_RE.match(line)
        if not m:
            continue
        key, value = m.group("key"), m.group("value").strip()
        if m.group("indent"):
            if current is not None:
                current[key] = value.strip("'\"")
            continue
        if value == "":
            current = {}
            data[key] = current
        else:
            current = None
            data[key] = value.strip("'\"")
    return data


def parse_frontmatter(raw: str, *, strict: bool = False) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        if strict:
            raise MemoryFrontmatterError(f"invalid YAML: {exc}") from exc
        return _lenient_load(raw)
    if not isinstance(loaded, dict):
        if strict:
            raise MemoryFrontmatterError("invalid YAML: frontmatter is not a mapping")
        return _lenient_load(raw)
    return loaded


def _as_list(
    value: Any, *, field_name: str = "value", strict: bool = False
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if strict and (
        not isinstance(value, list) or not all(isinstance(v, str) for v in value)
    ):
        raise MemoryFrontmatterError(f"invalid {field_name}: expected string list")
    if not isinstance(value, list):
        return [str(value)]
    return [str(v) for v in value]


def _optional_str(value: Any, *, field_name: str, strict: bool = False) -> str | None:
    if value is None or value == "":
        return None
    if strict and not isinstance(value, str):
        raise MemoryFrontmatterError(f"invalid {field_name}: expected string")
    return str(value)


def entry_from_text(
    text: str,
    path: Path | None = None,
    scope: str | None = None,
    *,
    strict: bool = False,
) -> MemoryEntry:
    parts = split_frontmatter(text)
    if parts is None:
        raise MemoryParseError(f"no frontmatter: {path or '<text>'}")
    raw, body = parts
    data = parse_frontmatter(raw, strict=strict)
    if not data:
        raise MemoryParseError(f"empty frontmatter: {path or '<text>'}")

    raw_metadata = data.get("metadata")
    if strict and raw_metadata is not None and not isinstance(raw_metadata, dict):
        raise MemoryFrontmatterError("invalid metadata: expected mapping")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    type_ = metadata.pop("type", None) or data.get("type") or DEFAULT_TYPE
    if strict and not isinstance(type_, str):
        raise MemoryFrontmatterError("invalid type: expected string")

    if "name" in data:
        raw_name = data["name"]
        if strict and (not isinstance(raw_name, str) or not raw_name.strip()):
            raise MemoryFrontmatterError("invalid name: expected non-empty string")
        # Strict mode already rejected non-string names above.
        # Lenient mode preserves the old behaviour: coerce truthy non-strings
        # with str() so existing entries with e.g. ``name: 42`` keep their
        # identity instead of silently switching to the filename stem.
        name = (
            raw_name
            if isinstance(raw_name, str) and raw_name
            else (
                str(raw_name) if raw_name else (path.stem if path is not None else None)
            )
        )
    else:
        name = path.stem if path is not None else None
    if not name:
        raise MemoryParseError(f"entry has no name: {path or '<text>'}")

    confidence = data.get("confidence")
    if confidence is None:
        parsed_confidence = None
    elif isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        if strict:
            raise MemoryFrontmatterError("invalid confidence: expected number")
        try:
            parsed_confidence = float(confidence)
        except (TypeError, ValueError):
            parsed_confidence = None
    else:
        parsed_confidence = float(confidence)
    if strict and parsed_confidence is not None and not 0 <= parsed_confidence <= 1:
        raise MemoryFrontmatterError("invalid confidence: expected 0..1")

    provenance = data.get("provenance")
    if strict and provenance is not None and not isinstance(provenance, dict):
        raise MemoryFrontmatterError("invalid provenance: expected mapping")
    if "status" in data:
        raw_status = data["status"]
    else:
        raw_status = DEFAULT_STATUS
    if strict:
        if not isinstance(raw_status, str) or raw_status not in STATUSES:
            raise MemoryFrontmatterError(
                f"invalid status: expected one of {', '.join(STATUSES)}"
            )
        status = raw_status
    else:
        status = str(raw_status) if raw_status else DEFAULT_STATUS
        if status not in STATUSES:
            status = DEFAULT_STATUS

    raw_description = data.get("description")
    if raw_description is None:
        description = ""
    elif strict and not isinstance(raw_description, str):
        raise MemoryFrontmatterError("invalid description: expected string")
    else:
        description = str(raw_description).strip()

    raw_title = data.get("title")
    if strict and raw_title is not None and not isinstance(raw_title, str):
        raise MemoryFrontmatterError("invalid title: expected string")
    title = str(raw_title) if raw_title else None

    return MemoryEntry(
        name=str(name),
        description=description,
        type=str(type_),
        body=body.strip("\n"),
        title=title,
        status=status,
        supersedes=_as_list(
            data.get("supersedes"), field_name="supersedes", strict=strict
        ),
        superseded_by=_optional_str(
            data.get("superseded_by"), field_name="superseded_by", strict=strict
        ),
        provenance=dict(provenance) if isinstance(provenance, dict) else {},
        confidence=parsed_confidence,
        keywords=_as_list(data.get("keywords"), field_name="keywords", strict=strict),
        recheck=_optional_str(data.get("recheck"), field_name="recheck", strict=strict),
        metadata=metadata,
        path=path,
        scope=scope,
    )


def parse_entry(
    path: Path, scope: str | None = None, *, strict: bool = False
) -> MemoryEntry:
    """Parse one memory file. Raises :class:`MemoryParseError` for non-entries."""
    return entry_from_text(
        path.read_text(encoding="utf-8"), path=path, scope=scope, strict=strict
    )
