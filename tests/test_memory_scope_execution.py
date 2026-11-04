"""Scope regressions through the actual ToolUse execution boundary."""

from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.hooks import ConfirmationResult
from gptme.memory import parse_entry
from gptme.tools import memory as memory_mod
from gptme.tools.base import ToolUse


def test_relative_destination_stays_put_during_confirmation(tmp_path, monkeypatch):
    original = tmp_path / "original"
    other = tmp_path / "other"
    original.mkdir()
    (other / "memories").mkdir(parents=True)
    sentinel = other / "memories" / "fact.md"
    sentinel.write_text("Must survive.")
    monkeypatch.chdir(original)
    monkeypatch.setenv("GPTME_MEMORY_DIRS", "./memories")

    def confirm(**kwargs):
        monkeypatch.chdir(other)
        return ConfirmationResult.confirm()

    with patch("gptme.hooks.get_confirmation", side_effect=confirm):
        messages = list(memory_mod.execute_memory("Summary.", ["fact"]))
    assert (original / "memories" / "fact.md").is_file()
    assert sentinel.read_text() == "Must survive."
    assert str(original / "memories" / "fact.md") in messages[-1].content


@pytest.mark.parametrize("parser", ["_iter_from_xml_lxml", "_iter_from_xml_etree"])
def test_xml_scoped_example_executes_in_user_root(tmp_path, monkeypatch, parser):
    monkeypatch.delenv("GPTME_MEMORY_DIRS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(memory_mod, "get_workspace", lambda: tmp_path)
    if parser.endswith("_lxml"):
        pytest.importorskip("lxml")
    calls = list(getattr(ToolUse, parser)(memory_mod.examples("xml")))
    assert len(calls) == 1
    with patch(
        "gptme.hooks.get_confirmation", return_value=ConfirmationResult.confirm()
    ):
        messages = list(calls[0].execute())
    target = tmp_path / "config" / "gptme" / "memory" / "prefer-short-answers.md"
    assert target.is_file(), [m.content for m in messages]
    assert (
        parse_entry(target).description
        == "User prefers short, direct answers without preamble."
    )
    assert "scope: user" in messages[-1].content


def test_native_destination_is_present_at_confirmation_before_write(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    target = tmp_path / "fact.md"
    call = ToolUse(
        "memory",
        None,
        None,
        kwargs={"name": "fact", "content": "Summary.", "scope": "explicit"},
        call_id="call_memory_scope_probe",
    )

    def confirm(**kwargs):
        assert not target.exists()
        assert str(target) in kwargs.get("preview_header", "")
        assert "scope: explicit" in kwargs["preview_header"]
        assert kwargs["preview"] == "Summary."
        return ConfirmationResult.confirm()

    with patch("gptme.hooks.get_confirmation", side_effect=confirm):
        messages = list(call.execute())
    assert target.is_file()
    assert messages[-1].call_id == call.call_id


@pytest.mark.parametrize("scope", ["user", "invalid"])
def test_xml_scope_failure_never_falls_back(tmp_path, monkeypatch, scope):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "only-root"))
    call = list(
        ToolUse._iter_from_xml_etree(
            f'<tool-use><memory args="fact --scope {scope}">Summary.</memory></tool-use>'
        )
    )[0]
    with patch("gptme.hooks.get_confirmation") as confirm:
        messages = list(call.execute())
    assert "Error selecting memory scope" in messages[-1].content
    confirm.assert_not_called()
    assert not (tmp_path / "only-root").exists()


def test_xml_bad_quoting_never_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "only-root"))
    call = list(
        ToolUse._iter_from_xml_etree(
            '<tool-use><memory args="fact --scope &quot;user">Summary.</memory></tool-use>'
        )
    )[0]
    with patch("gptme.hooks.get_confirmation") as confirm:
        messages = list(call.execute())
    assert "Error parsing memory arguments" in messages[-1].content
    confirm.assert_not_called()
    assert not (tmp_path / "only-root").exists()


def test_native_confirmation_hook_sees_frozen_destination_on_edit(
    tmp_path, monkeypatch
):
    from gptme.hooks import HookType, register_hook
    from gptme.hooks.registry import HookRegistry, get_registry, set_registry

    original = tmp_path / "original"
    other = tmp_path / "other"
    original.mkdir()
    other.mkdir()
    monkeypatch.chdir(original)
    monkeypatch.setenv("GPTME_MEMORY_DIRS", "./memories")
    target = original / "memories" / "fact.md"
    seen = []

    def confirm(
        tool_use: ToolUse, preview: str | None = None, workspace: Path | None = None
    ) -> ConfirmationResult:
        assert not target.exists()
        assert tool_use.confirmation_header is not None
        assert str(target) in tool_use.confirmation_header
        assert "scope: explicit" in tool_use.confirmation_header
        seen.append(preview)
        monkeypatch.chdir(other)
        return (
            ConfirmationResult.edit("Edited summary.\nEdited body.")
            if len(seen) == 1
            else ConfirmationResult.confirm()
        )

    registry = get_registry()
    set_registry(HookRegistry())
    try:
        register_hook("memory-scope-regression", HookType.TOOL_CONFIRM, confirm)
        messages = list(
            ToolUse(
                "memory",
                None,
                None,
                kwargs={"name": "fact", "content": "Original.", "scope": "explicit"},
                call_id="call_memory_hook_probe",
            ).execute()
        )
    finally:
        set_registry(registry)
    assert seen == ["Original.", "Edited summary.\nEdited body."]
    assert not (other / "memories").exists()
    assert parse_entry(target).description == "Edited summary."
    assert parse_entry(target).body == "Edited body."
    assert any(str(target) in m.content and "saved" in m.content for m in messages)
