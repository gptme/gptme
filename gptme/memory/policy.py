"""Opt-in, root-local policy for the always-on view, independent of recall."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from .schema import MemoryEntry, is_index_file

POLICY_FILENAME = ".memory-index.json"

# Minimum budget: the rendered header ("# Persistent Memory\n") that appears
# even when no entries are selected.  The trailing blank line is stripped when
# the selection is empty, so the actual minimum is 20 bytes, not 22.  A budget
# below this makes every index operation fail immediately.
POLICY_MIN_BUDGET = 20


@dataclass(frozen=True)
class IndexPolicy:
    budget: int
    selected: tuple[str, ...]

    @classmethod
    def read(cls, root: Path) -> IndexPolicy | None:
        """Missing policy keeps legacy behavior; malformed policy never does."""
        try:
            text = (root / POLICY_FILENAME).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"invalid {POLICY_FILENAME}: {exc}") from exc
        if not isinstance(data, dict) or set(data) != {"version", "budget", "selected"}:
            raise ValueError(f"{POLICY_FILENAME} requires version, budget, selected")
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError(f"unsupported {POLICY_FILENAME} version")
        budget = data["budget"]
        if type(budget) is not int or budget <= 0:
            raise ValueError("index policy budget must be a positive integer")
        if budget < POLICY_MIN_BUDGET:
            raise ValueError(
                f"index policy budget must be at least {POLICY_MIN_BUDGET} bytes "
                "(the index header alone requires that many bytes)"
            )
        selected = data["selected"]
        if not isinstance(selected, list) or any(
            not isinstance(name, str)
            or not name.endswith(".md")
            or name.startswith(".")
            or "/" in name
            or "\\" in name
            or is_index_file(Path(name))
            for name in selected
        ):
            raise ValueError("index policy selected must contain local entry filenames")
        if len(set(selected)) != len(selected):
            raise ValueError("index policy selected contains duplicate filenames")
        return cls(budget, tuple(selected))

    def to_json(self) -> str:
        return (
            json.dumps(
                {"version": 1, "budget": self.budget, "selected": list(self.selected)},
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )

    def supersede(self, old: str, new: str) -> IndexPolicy:
        """Transfer selection, retaining only the first occurrence of a filename."""
        return replace(
            self,
            selected=tuple(
                dict.fromkeys(new if f == old else f for f in self.selected)
            ),
        )

    def entries(self, entries: list[MemoryEntry]) -> list[MemoryEntry]:
        by_file = {entry.filename: entry for entry in entries}
        selected = []
        for name in self.selected:
            entry = by_file.get(name)
            if entry is None or not entry.is_living:
                raise ValueError(f"selected memory {name!r} is missing or not living")
            selected.append(entry)
        return selected
