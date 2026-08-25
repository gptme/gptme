"""Guard against docs/tool-formats.rst drifting from the model registry.

The "How well populated is this?" section quotes concrete coverage counts for
the ModelMeta tool-calling fields. Those numbers went stale once already (they
described the pre-#3566 state, where only openai-subscription models carried a
``default_tool_format``). This test fails the moment the registry and the prose
disagree, so the fix lands with the change that caused it.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING

from gptme.llm.models.data import MODELS

if TYPE_CHECKING:
    from gptme.llm.models.types import Provider

DOCS = Path(__file__).parent.parent / "docs" / "tool-formats.rst"


def _counts() -> dict[str, int]:
    total = tool_format = parallel = strict = 0
    for models in MODELS.values():
        for props in models.values():
            total += 1
            tool_format += props.get("default_tool_format") == "tool"
            parallel += bool(props.get("supports_parallel_tool_calls"))
            strict += bool(props.get("supports_strict_tools"))
    return {
        "total": total,
        "default_tool_format": tool_format,
        "supports_parallel_tool_calls": parallel,
        "supports_strict_tools": strict,
    }


def test_documented_coverage_matches_registry():
    text = DOCS.read_text()
    counts = _counts()

    # "set to ``tool`` on 92 of the 111 models"
    m = re.search(
        r"``default_tool_format`` is set to ``tool`` on (\d+) of the (\d+) models",
        text,
    )
    assert m, "coverage sentence for default_tool_format not found in docs"
    assert int(m.group(1)) == counts["default_tool_format"], (
        f"docs say {m.group(1)} models set default_tool_format, "
        f"registry has {counts['default_tool_format']} — update docs/tool-formats.rst"
    )
    assert int(m.group(2)) == counts["total"], (
        f"docs say {m.group(2)} total models, registry has {counts['total']} "
        "— update docs/tool-formats.rst"
    )

    for field in ("supports_parallel_tool_calls", "supports_strict_tools"):
        m = re.search(rf"``{field}`` is set on (\d+) entries", text)
        assert m, f"coverage sentence for {field} not found in docs"
        assert int(m.group(1)) == counts[field], (
            f"docs say {m.group(1)} entries set {field}, registry has "
            f"{counts[field]} — update docs/tool-formats.rst"
        )


def test_excluded_providers_really_are_excluded():
    """anthropic/mock are documented as deliberately not stamped."""
    excluded: list[Provider] = ["anthropic", "mock"]
    for provider in excluded:
        stamped = [
            name
            for name, props in MODELS[provider].items()
            if props.get("default_tool_format")
        ]
        assert not stamped, (
            f"{provider} models now carry default_tool_format ({stamped}), but "
            "docs/tool-formats.rst documents them as excluded"
        )


def test_empty_registry_providers_have_no_static_entries():
    """azure/local/nvidia/gptme are documented as having no static registry entries.

    These are OpenAI-compat providers that inherit default_tool_format at
    resolution time rather than from a per-model entry in the static registry.
    If a static entry is added for any of them, the docs must be updated to
    reflect it (the count and the explanation both change).
    """
    empty_registry: list[Provider] = ["azure", "local", "nvidia", "gptme"]
    for provider in empty_registry:
        if provider in MODELS:
            entries = MODELS[provider]
            assert not entries, (
                f"'{provider}' is documented as having no static registry entries "
                "(docs/tool-formats.rst), but now has "
                f"{len(entries)} ({list(entries)}) — update the docs to reflect "
                "the new entries, or keep the provider registry-free"
            )
