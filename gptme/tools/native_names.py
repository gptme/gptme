"""Transport-safe names for native tool schemas and conversation replay.

Canonical ToolSpec names remain unchanged for dispatch and allowlists. Derive
aliases from the context-local tool registry, not process-global mutable state.
"""

import hashlib
import re


def _name_map(extra: str = "") -> dict[str, str]:
    from . import get_tools

    names = {
        spec.block_types[0] if spec.block_types else spec.name for spec in get_tools()
    }
    if extra:
        names.add(extra)
    # Reserve every already-valid name before aliasing, including tools whose
    # names happen to match another tool's generated alias.
    mapping = {
        name: name for name in names if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name)
    }
    reserved = set(mapping)
    for name in sorted(names - mapping.keys()):
        stem = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:48]
        salt = 0
        while True:
            digest = hashlib.sha256(f"{name}:{salt}".encode()).hexdigest()[:12]
            alias = f"{stem}_{digest}"
            if alias not in reserved:
                break
            salt += 1
        mapping[name] = alias
        reserved.add(alias)
    return mapping


def native_tool_name(name: str) -> str:
    """Map a canonical or block name to its provider-safe transport name."""
    from . import get_tools

    spec = next((spec for spec in get_tools() if spec.name == name), None)
    raw = spec.block_types[0] if spec and spec.block_types else name
    return _name_map(raw)[raw]


def canonical_tool_name(name: str) -> str:
    """Resolve a returned native alias using only this context's loaded tools."""
    return next((raw for raw, alias in _name_map().items() if alias == name), name)
