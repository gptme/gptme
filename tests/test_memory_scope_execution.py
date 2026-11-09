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


@pytest.mark.parametrize(
    "name", ["My Cool Fact", "Erik's preference", "deployment --scope user"]
)
def test_legacy_xml_name_round_trip(tmp_path, monkeypatch, name):
    from gptme.memory import slugify

    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    xml = ToolUse("memory", [name], "Summary.").to_output("xml")
    call = list(ToolUse._iter_from_xml_etree(xml))[0]
    with patch(
        "gptme.hooks.get_confirmation", return_value=ConfirmationResult.confirm()
    ):
        messages = list(call.execute())
    assert (tmp_path / f"{slugify(name)}.md").is_file(), [m.content for m in messages]


@pytest.mark.parametrize("name", ["fact", "My Cool Fact", "Erik's preference"])
@pytest.mark.parametrize("tool_format", ["xml", "tool"])
def test_scoped_memory_serialization_preserves_operation(
    tmp_path, monkeypatch, name, tool_format
):
    from gptme.memory import slugify

    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    original = ToolUse("memory", [name, "--scope", "explicit"], "Summary.\nDetail.")
    serialized = original.to_output(tool_format)
    if tool_format == "tool":
        import json

        params = json.loads(serialized.split(": ", 1)[1])
        assert params == {
            "name": name,
            "content": "Summary.\nDetail.",
            "scope": "explicit",
        }
        call = ToolUse("memory", None, None, kwargs=params)
    else:
        call = list(
            ToolUse.iter_from_content(serialized, tool_format_override=tool_format)
        )[0]
    with patch(
        "gptme.hooks.get_confirmation", return_value=ConfirmationResult.confirm()
    ):
        messages = list(call.execute())
    target = tmp_path / f"{slugify(name)}.md"
    assert target.is_file(), [m.content for m in messages]
    entry = parse_entry(target)
    assert entry.description == "Summary."
    assert entry.body == "Detail."
    assert "scope: explicit" in messages[-1].content


@pytest.mark.parametrize("parser", ["_iter_from_xml_lxml", "_iter_from_xml_etree"])
@pytest.mark.parametrize("name", ["fact", "My Cool Fact", "Erik's preference"])
def test_parsed_scoped_xml_to_native_preserves_operation(parser, name):
    if parser.endswith("_lxml"):
        pytest.importorskip("lxml")
    xml = ToolUse("memory", [name, "--scope", "user"], "Summary.\nDetail.").to_output(
        "xml"
    )
    call = list(getattr(ToolUse, parser)(xml))[0]
    assert call._to_params() == {
        "name": name,
        "content": "Summary.\nDetail.",
        "scope": "user",
    }


def test_xml_literal_scope_token_can_be_quoted(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    call = list(
        ToolUse._iter_from_xml_etree(
            '<tool-use><memory args="&quot;deployment --scope user&quot;">Summary.</memory></tool-use>'
        )
    )[0]
    assert call._to_params() == {
        "name": "deployment --scope user",
        "content": "Summary.",
    }
    with patch(
        "gptme.hooks.get_confirmation", return_value=ConfirmationResult.confirm()
    ):
        messages = list(call.execute())
    assert (tmp_path / "deployment-scope-user.md").exists(), [
        m.content for m in messages
    ]


@pytest.mark.parametrize("parser", ["_iter_from_xml_lxml", "_iter_from_xml_etree"])
@pytest.mark.parametrize(
    ("name", "scope"),
    [
        ("fact", "user"),
        ("My Cool Fact", "user"),
        ("Erik's preference", "user"),
        ("deployment --scope user", None),
    ],
)
def test_parsed_xml_round_trip_preserves_write(
    tmp_path, monkeypatch, parser, name, scope
):
    """Re-emitting a parsed attribute must not turn scope flags into a name."""
    from gptme.memory import slugify

    if parser.endswith("_lxml"):
        pytest.importorskip("lxml")
    monkeypatch.delenv("GPTME_MEMORY_DIRS", raising=False)
    monkeypatch.delenv("GPTME_AGENT_WORKSPACE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(memory_mod, "get_workspace", lambda: tmp_path)
    (tmp_path / "memory").mkdir()
    args = [name, "--scope", scope] if scope else [name]
    xml = ToolUse("memory", args, "Summary.\nDetail.").to_output("xml")
    for _ in range(3):
        call = list(getattr(ToolUse, parser)(xml))[0]
        xml = call.to_output("xml")
    reparsed = list(getattr(ToolUse, parser)(xml))[0]
    expected = {"name": name, "content": "Summary.\nDetail."}
    if scope:
        expected["scope"] = scope
    assert reparsed._to_params() == expected
    with patch(
        "gptme.hooks.get_confirmation", return_value=ConfirmationResult.confirm()
    ):
        messages = list(reparsed.execute())
    root = tmp_path / "config" / "gptme" / "memory" if scope else tmp_path / "memory"
    target = root / f"{slugify(name)}.md"
    assert target.is_file(), [m.content for m in messages]
    assert parse_entry(target).description == "Summary."
    assert parse_entry(target).body == "Detail."
    if scope:
        assert not list((tmp_path / "memory").iterdir())
