"""Tests for the vent tool (friction signal emitter)."""

import json

import pytest

from gptme.tools.vent import (
    _parse_resolution_owner,
    _reset_vent_limit,
    _vent_this_turn,
    execute_vent,
)


@pytest.fixture(autouse=True)
def reset_rate_limit():
    """Reset per-turn rate-limit flag before each test."""
    _vent_this_turn.set(False)
    yield
    _vent_this_turn.set(False)


@pytest.fixture()
def ledger_path(tmp_path, monkeypatch):
    """Point the vent ledger at a temp directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path / "gptme" / "friction-ledger.jsonl"


class TestExecuteVent:
    def test_records_entry(self, ledger_path):
        msg = execute_vent("Stuck on import resolution", None, None)
        assert "recorded" in msg.content.lower()
        assert ledger_path.exists()
        entries = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["message"] == "Stuck on import resolution"
        assert "timestamp" in entry
        assert "workspace" in entry

    def test_strips_whitespace(self, ledger_path):
        execute_vent("  trailing spaces  \n", None, None)
        entry = json.loads(ledger_path.read_text().strip())
        assert entry["message"] == "trailing spaces"

    def test_empty_message_no_write(self, ledger_path):
        msg = execute_vent("", None, None)
        assert "no message" in msg.content.lower()
        assert not ledger_path.exists()

    def test_whitespace_only_no_write(self, ledger_path):
        msg = execute_vent("   \n\t  ", None, None)
        assert "no message" in msg.content.lower()
        assert not ledger_path.exists()

    def test_none_message_no_write(self, ledger_path):
        execute_vent(None, None, None)
        assert not ledger_path.exists()

    def test_rate_limit_blocks_second_vent(self, ledger_path):
        execute_vent("First vent", None, None)
        msg = execute_vent("Second vent", None, None)
        assert "rate limit" in msg.content.lower()
        # Only first vent recorded
        entries = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        assert len(entries) == 1
        assert entries[0]["message"] == "First vent"

    def test_rate_limit_resets_after_hook(self, ledger_path):
        execute_vent("First vent", None, None)
        # Simulate step_pre hook firing between turns
        list(_reset_vent_limit(None))  # type: ignore[arg-type]
        msg = execute_vent("Second vent after reset", None, None)
        assert "recorded" in msg.content.lower()
        entries = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        assert len(entries) == 2

    def test_entry_jsonl_format(self, ledger_path):
        execute_vent("Line 1", None, None)
        list(_reset_vent_limit(None))  # type: ignore[arg-type]
        execute_vent("Line 2", None, None)
        lines = ledger_path.read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # must be valid JSON

    def test_ledger_appends_across_calls(self, ledger_path):
        """Each vent appends; earlier entries are not overwritten."""
        for i in range(3):
            list(_reset_vent_limit(None))  # type: ignore[arg-type]
            execute_vent(f"Vent {i}", None, None)
        entries = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        assert len(entries) == 3
        assert [e["message"] for e in entries] == ["Vent 0", "Vent 1", "Vent 2"]


class TestVentProvenance:
    @pytest.mark.parametrize(
        ("neutral", "legacy", "expected"),
        [
            ("neutral-session", "legacy-session", "neutral-session"),
            (None, "legacy-session", "legacy-session"),
            ("  ", " legacy-session ", "legacy-session"),
            (" neutral-session ", None, "neutral-session"),
            (None, None, None),
        ],
    )
    def test_launcher_session_identity(
        self, ledger_path, monkeypatch, neutral, legacy, expected
    ):
        monkeypatch.delenv("GPTME_SESSION_ID", raising=False)
        for key, value in (("AGENT_SESSION_ID", neutral), ("BOB_SESSION_ID", legacy)):
            monkeypatch.delenv(key, raising=False)
            if value is not None:
                monkeypatch.setenv(key, value)
        execute_vent("Stuck on import resolution", None, None)
        entry = json.loads(ledger_path.read_text())
        assert entry.get("session_id") == expected
        assert entry["harness"] == "gptme"
        if expected is None:
            assert "session_id" not in entry

    def test_gptme_session_identity_has_precedence(self, ledger_path, monkeypatch):
        monkeypatch.setenv("GPTME_SESSION_ID", "native-session")
        monkeypatch.setenv("AGENT_SESSION_ID", "neutral-session")
        monkeypatch.setenv("BOB_SESSION_ID", "legacy-session")
        execute_vent("Stuck on import resolution", None, None)
        assert json.loads(ledger_path.read_text())["session_id"] == "native-session"

    def test_runtime_model(self, ledger_path, monkeypatch):
        from gptme.llm.models import ModelMeta

        model = ModelMeta(provider="openai", model="gpt-test", context=8192)
        monkeypatch.setattr("gptme.llm.models.get_default_model", lambda: model)
        execute_vent("Stuck on import resolution", None, None)
        entry = json.loads(ledger_path.read_text())
        assert entry["model"] == "openai/gpt-test"

    def test_unset_runtime_model(self, ledger_path, monkeypatch):
        monkeypatch.setattr("gptme.llm.models.get_default_model", lambda: None)
        execute_vent("Stuck on import resolution", None, None)
        entry = json.loads(ledger_path.read_text())
        assert entry["harness"] == "gptme"
        assert "model" not in entry

    def test_repeated_vents_preserve_one_session(self, ledger_path, monkeypatch):
        monkeypatch.delenv("GPTME_SESSION_ID", raising=False)
        monkeypatch.setenv("AGENT_SESSION_ID", "same-session")
        for _ in range(2):
            _vent_this_turn.set(False)
            execute_vent("Stuck on import resolution", None, None)
        entries = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        assert len(entries) == 2
        assert {entry["session_id"] for entry in entries} == {"same-session"}


class TestParseResolutionOwner:
    def test_no_tag_returns_none(self):
        msg, owner = _parse_resolution_owner("Just a plain message with no tag")
        assert msg == "Just a plain message with no tag"
        assert owner is None

    @pytest.mark.parametrize(
        "owner", ["self", "tooling", "operator", "upstream", "architectural"]
    )
    def test_valid_owners(self, owner):
        msg, parsed = _parse_resolution_owner(f"Blocked on X\nOwner: {owner}")
        assert msg == "Blocked on X"
        assert parsed == owner

    def test_case_insensitive_keyword_and_value(self):
        msg, owner = _parse_resolution_owner("Stuck\nOWNER: Tooling")
        assert msg == "Stuck"
        assert owner == "tooling"

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("Type0", "operator"),
            ("Type1", "self"),
            ("Type2a", "tooling"),
            ("Type2b", "architectural"),
        ],
    )
    def test_deprecated_type_aliases(self, label, expected):
        msg, owner = _parse_resolution_owner(f"Old style vent\nType: {label}")
        assert msg == "Old style vent"
        assert owner == expected

    def test_parenthetical_note_stripped(self):
        msg, owner = _parse_resolution_owner(
            "Need a key\nOwner: tooling (missing API key)"
        )
        assert msg == "Need a key"
        assert owner == "tooling"

    @pytest.mark.parametrize(
        "trailer", ["tooling.", "tooling!", "operator,", "self;", "upstream?"]
    )
    def test_trailing_punctuation_tolerated(self, trailer):
        # Agents often end the tag line with sentence punctuation; the owner
        # should still be captured rather than silently dropped.
        expected = trailer.strip(".,;:!?")
        msg, owner = _parse_resolution_owner(f"Blocked on X\nOwner: {trailer}")
        assert msg == "Blocked on X"
        assert owner == expected

    def test_internal_hyphen_not_misparsed(self):
        # Stripping terminal punctuation must not turn "self-evident" into "self".
        msg, owner = _parse_resolution_owner("Stuck\nOwner: self-evident")
        assert msg == "Stuck\nOwner: self-evident"
        assert owner is None

    def test_resolution_keyword_and_equals_separator(self):
        msg, owner = _parse_resolution_owner("Stuck\nResolution = upstream")
        assert msg == "Stuck"
        assert owner == "upstream"

    def test_invalid_owner_kept_in_message(self):
        # An unrecognized value is not silently dropped — it stays in the message.
        msg, owner = _parse_resolution_owner("Stuck\nOwner: banana")
        assert msg == "Stuck\nOwner: banana"
        assert owner is None

    def test_owner_only_yields_empty_message(self):
        msg, owner = _parse_resolution_owner("Owner: tooling")
        assert msg == ""
        assert owner == "tooling"


class TestExecuteVentWithOwner:
    def test_records_resolution_owner(self, ledger_path):
        execute_vent("Blocked: need a credential\nOwner: operator", None, None)
        entry = json.loads(ledger_path.read_text().strip())
        assert entry["message"] == "Blocked: need a credential"
        assert entry["resolution_owner"] == "operator"

    def test_no_owner_omits_field(self, ledger_path):
        execute_vent("Plain vent without a tag", None, None)
        entry = json.loads(ledger_path.read_text().strip())
        assert "resolution_owner" not in entry

    def test_owner_only_no_description_not_recorded(self, ledger_path):
        msg = execute_vent("Owner: tooling", None, None)
        assert "no message" in msg.content.lower()
        assert not ledger_path.exists()
