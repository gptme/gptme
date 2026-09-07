"""
Memory entry schema — byte-compatible with Claude Code's memory format.

CC writes files with this frontmatter:
    ---
    name: slug
    description: "one-line summary"
    metadata:
      type: general
    ---

    body

We extend it additively with optional fields that CC ignores.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


@dataclass
class MemoryEntry:
    """One memory entry, parsed from a markdown-plus-frontmatter file."""

    name: str
    description: str
    body: str
    # Metadata sub-dict (type, …)
    metadata: dict[str, Any] = field(default_factory=dict)
    # Extended fields (ignored by CC, used by gptme)
    status: str = "living"  # living | superseded | historical
    supersedes: str | None = None
    superseded_by: str | None = None
    keywords: list[str] = field(default_factory=list)
    confidence: float | None = None
    provenance: dict[str, str] = field(default_factory=dict)
    # Source path when loaded from disk
    path: Path | None = None

    @property
    def type(self) -> str:
        return self.metadata.get("type", "general")

    def is_living(self) -> bool:
        return self.status == "living"


def _parse_yaml_simple(text: str) -> dict[str, Any]:
    """Minimal YAML parser for the subset used in memory frontmatter.

    Handles: scalar strings (quoted/unquoted), lists, nested dicts one level
    deep. Avoids a yaml dependency so the package stays dependency-free.
    """
    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    current_key: str | None = None
    current_list: list[str] | None = None
    current_dict: dict[str, str] | None = None

    def _unquote(s: str) -> str:
        s = s.strip()
        if (s.startswith('"') and s.endswith('"')) or (
            s.startswith("'") and s.endswith("'")
        ):
            return s[1:-1]
        return s

    while i < len(lines):
        line = lines[i]
        # continuation of a list
        if current_list is not None and line.startswith("  - "):
            current_list.append(_unquote(line[4:]))
            i += 1
            continue
        # continuation of a nested dict
        if current_dict is not None and line.startswith("  "):
            sub = line.strip()
            if ":" in sub:
                k, _, v = sub.partition(":")
                current_dict[k.strip()] = _unquote(v)
            i += 1
            continue

        # flush pending list/dict
        if current_list is not None and current_key is not None:
            result[current_key] = current_list
            current_list = None
            current_key = None
        if current_dict is not None and current_key is not None:
            result[current_key] = current_dict
            current_dict = None
            current_key = None

        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if ":" in stripped:
            k, _, v = stripped.partition(":")
            k = k.strip()
            v = v.strip()
            if v == "":
                # peek: nested dict or list?
                if i + 1 < len(lines) and lines[i + 1].startswith("  - "):
                    current_key = k
                    current_list = []
                elif i + 1 < len(lines) and lines[i + 1].startswith("  "):
                    current_key = k
                    current_dict = {}
                else:
                    result[k] = None
            elif v == "~" or v == "null":
                result[k] = None
            else:
                result[k] = _unquote(v)
        i += 1

    # flush
    if current_list is not None and current_key is not None:
        result[current_key] = current_list
    if current_dict is not None and current_key is not None:
        result[current_key] = current_dict

    return result


def _to_yaml_simple(data: dict[str, Any]) -> str:
    """Serialize a shallow dict to YAML, matching CC's format."""
    lines: list[str] = []
    for k, v in data.items():
        if v is None:
            lines.append(f"{k}: ~")
        elif isinstance(v, dict):
            lines.append(f"{k}:")
            for dk, dv in v.items():
                lines.append(f"  {dk}: {dv}")
        elif isinstance(v, list):
            lines.append(f"{k}:")
            lines.extend(f"  - {item}" for item in v)
        elif isinstance(v, float):
            lines.append(f"{k}: {v}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            sv = str(v)
            # Quote if it contains colon, quote, or leading/trailing whitespace
            if any(c in sv for c in ('"', ":", "#")) or sv != sv.strip():
                sv = '"' + sv.replace("\\", "\\\\").replace('"', '\\"') + '"'
            lines.append(f"{k}: {sv}")
    return "\n".join(lines)


def parse_entry(path: Path) -> MemoryEntry:
    """Parse a memory entry from a markdown file with YAML frontmatter."""
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        # No frontmatter — treat whole file as body with filename as name
        return MemoryEntry(
            name=path.stem,
            description=path.stem,
            body=text.strip(),
            path=path,
        )
    fm_text = m.group(1)
    body = text[m.end() :].strip()

    fm = _parse_yaml_simple(fm_text)
    name = str(fm.get("name") or path.stem)
    description = str(fm.get("description") or "")
    metadata = fm.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    status = str(fm.get("status") or "living")
    supersedes = fm.get("supersedes")
    superseded_by = fm.get("superseded_by")
    keywords_raw = fm.get("keywords") or []
    keywords = [str(k) for k in keywords_raw] if isinstance(keywords_raw, list) else []
    confidence_raw = fm.get("confidence")
    confidence: float | None = None
    if confidence_raw is not None:
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            pass
    provenance = fm.get("provenance") or {}
    if not isinstance(provenance, dict):
        provenance = {}

    return MemoryEntry(
        name=name,
        description=description,
        body=body,
        metadata=metadata,
        status=status,
        supersedes=str(supersedes) if supersedes is not None else None,
        superseded_by=str(superseded_by) if superseded_by is not None else None,
        keywords=keywords,
        confidence=confidence,
        provenance={k: str(v) for k, v in provenance.items()},
        path=path,
    )


def write_entry(path: Path, entry: MemoryEntry) -> None:
    """Write a memory entry to a markdown file with YAML frontmatter."""
    safe_desc = entry.description.replace("\\", "\\\\").replace('"', '\\"')

    # Build the core fields that CC writes (and always quotes description).
    lines: list[str] = [
        f"name: {entry.name}",
        f'description: "{safe_desc}"',
    ]

    metadata = entry.metadata or {"type": "general"}
    lines.append("metadata:")
    for k, v in metadata.items():
        lines.append(f"  {k}: {v}")

    # Extended fields — only write if non-default to stay byte-close to CC output.
    if entry.status != "living":
        lines.append(f"status: {entry.status}")
    if entry.supersedes is not None:
        lines.append(f"supersedes: {entry.supersedes}")
    if entry.superseded_by is not None:
        lines.append(f"superseded_by: {entry.superseded_by}")
    if entry.keywords:
        lines.append("keywords:")
        lines.extend(f"  - {kw}" for kw in entry.keywords)
    if entry.confidence is not None:
        lines.append(f"confidence: {entry.confidence}")
    if entry.provenance:
        lines.append("provenance:")
        for k, v in entry.provenance.items():
            lines.append(f"  {k}: {v}")

    frontmatter = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n\n{entry.body}\n", encoding="utf-8")
