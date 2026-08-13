"""Tests for gptme-util status command."""

from __future__ import annotations

import json

from click.testing import CliRunner

import gptme.cli.cmd_status as cmd_status
from gptme.cli.cmd_status import (
    _pr_queue_display,
    _session_id,
    _strip_markdown,
    build_table_document,
    status,
)
from gptme.cli.util import main as util_main
from gptme.status_provider import StatusProvider

# ── fixtures / helpers ─────────────────────────────────────────────────


class _FakeProvider:
    """A minimal StatusProvider test double."""

    name = "test"

    def collect(self) -> dict:
        return {"test_key": "test_value", "test_count": 42}

    def narrative_sections(self) -> list[str]:
        return ["## Test Section\n\n- item from provider"]


def _noop_providers() -> list:
    """Return an empty provider list to suppress entry-point loading in tests."""
    return []


# ── core output tests ──────────────────────────────────────────────────


def test_status_output_contains_expected_sections():
    """Verify the status output contains always-present sections."""
    runner = CliRunner()
    result = runner.invoke(status)
    assert result.exit_code == 0, result.output
    assert "# gptme Status" in result.output
    assert "## Active Work" in result.output
    assert "## Disk" in result.output


def test_status_invoked_via_util_subcommand():
    """Verify gptme-util status dispatches correctly."""
    runner = CliRunner()
    result = runner.invoke(util_main, ["status"])
    assert result.exit_code == 0, result.output
    assert "# gptme Status" in result.output


def test_status_write_to_file(tmp_path):
    """Verify --write creates a file at the repo root equivalent."""
    runner = CliRunner()
    output_file = tmp_path / "handoff.md"
    result = runner.invoke(status, ["-o", str(output_file)])
    assert result.exit_code == 0, result.output
    assert output_file.exists()
    content = output_file.read_text()
    assert "# gptme Status" in content
    assert "## Active Work" in content


def test_status_no_markdown():
    """Verify --no-markdown strips heading markers from output."""
    runner = CliRunner()
    result = runner.invoke(status, ["--no-markdown"])
    assert result.exit_code == 0, result.output
    assert "# gptme Status" not in result.output
    assert "gptme Status" in result.output


def test_status_agent_name_from_env(monkeypatch):
    """Verify GPTME_AGENT_NAME env var is reflected in the header."""
    monkeypatch.setenv("GPTME_AGENT_NAME", "TestAgent")
    runner = CliRunner()
    result = runner.invoke(status)
    assert result.exit_code == 0, result.output
    assert "TestAgent" in result.output


def test_strip_markdown_removes_headings():
    """Unit-test the _strip_markdown helper."""
    doc = (
        "# Heading\n\nSome **bold** text and `code`.\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    )
    plain = _strip_markdown(doc)
    assert "# Heading" not in plain
    assert "Heading" in plain
    assert "**bold**" not in plain
    assert "bold" in plain
    assert "`code`" not in plain
    assert "code" in plain


def test_status_format_table():
    """Verify --format table outputs a markdown table with core fields."""
    runner = CliRunner()
    result = runner.invoke(status, ["--format", "table"])
    assert result.exit_code == 0, result.output
    assert "| Field | Value |" in result.output
    assert "| session_id |" in result.output
    assert "| last_commit |" in result.output
    assert "| disk_usage |" in result.output


def test_status_format_table_via_util():
    """Verify gptme-util status --format table dispatches correctly."""
    runner = CliRunner()
    result = runner.invoke(util_main, ["status", "--format", "table"])
    assert result.exit_code == 0, result.output
    assert "| Field | Value |" in result.output
    assert "| session_id |" in result.output


# ── JSON output tests ──────────────────────────────────────────────────


def test_status_json_core_fields(monkeypatch):
    """Verify --json emits core status fields without Bob-specific data."""
    monkeypatch.setattr(cmd_status, "_recent_commits", lambda n=3: ["abc123 Fix"])
    monkeypatch.setattr(cmd_status, "_session_id", lambda: "session-1")
    monkeypatch.setattr(cmd_status, "_git_root", lambda: None)
    monkeypatch.setattr(cmd_status, "_disk_usage", lambda _path=None: "1G / 2G (50%)")
    monkeypatch.setattr(cmd_status, "load_providers", lambda: [])

    result = CliRunner().invoke(status, ["--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "timestamp" in data
    assert "T" in data.pop("timestamp")
    assert data == {
        "session_id": "session-1",
        "recent_commits": ["abc123 Fix"],
        "disk_usage": "1G / 2G (50%)",
    }


def test_status_json_no_bob_fields_by_default(monkeypatch):
    """Verify Bob-specific fields are absent from core JSON output."""
    monkeypatch.setattr(cmd_status, "_recent_commits", lambda n=3: [])
    monkeypatch.setattr(cmd_status, "_session_id", lambda: "session-y")
    monkeypatch.setattr(cmd_status, "_git_root", lambda: None)
    monkeypatch.setattr(cmd_status, "_disk_usage", lambda _path=None: "1G / 2G (50%)")
    monkeypatch.setattr(cmd_status, "load_providers", lambda: [])

    result = CliRunner().invoke(status, ["--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    for bob_key in (
        "services",
        "dead_timers",
        "blockers",
        "ready_tasks",
        "active_tasks",
        "journal_entries",
        "pr_queue",
    ):
        assert bob_key not in data, (
            f"Bob-specific field '{bob_key}' present in core JSON output"
        )


def test_status_json_via_util(monkeypatch):
    """Verify gptme-util status exposes the --json flag (deterministic)."""
    monkeypatch.setattr(cmd_status, "_recent_commits", lambda n=3: [])
    monkeypatch.setattr(cmd_status, "_session_id", lambda: "session-util")
    monkeypatch.setattr(cmd_status, "_git_root", lambda: None)
    monkeypatch.setattr(cmd_status, "_disk_usage", lambda _path=None: "1G / 2G (50%)")
    monkeypatch.setattr(cmd_status, "load_providers", lambda: [])

    result = CliRunner().invoke(util_main, ["status", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, dict)
    assert data["session_id"] == "session-util"
    assert "timestamp" in data


def test_status_json_with_output_file(tmp_path, monkeypatch):
    """Verify --json -o path writes valid JSON with expected schema to a file."""
    monkeypatch.setattr(cmd_status, "_recent_commits", lambda n=3: [])
    monkeypatch.setattr(cmd_status, "_session_id", lambda: "session-x")
    monkeypatch.setattr(cmd_status, "_git_root", lambda: None)
    monkeypatch.setattr(cmd_status, "_disk_usage", lambda _path=None: "1G / 2G (50%)")
    monkeypatch.setattr(cmd_status, "load_providers", lambda: [])

    out_file = tmp_path / "status.json"
    result = CliRunner().invoke(status, ["--json", "-o", str(out_file)])

    assert result.exit_code == 0, result.output
    assert out_file.exists()
    data = json.loads(out_file.read_text())
    assert data["session_id"] == "session-x"
    assert "timestamp" in data
    assert "recent_commits" in data
    assert "disk_usage" in data


def test_status_json_write_with_output_path(tmp_path, monkeypatch):
    """Verify --json --write -o path is accepted and writes JSON."""
    monkeypatch.setattr(cmd_status, "_recent_commits", lambda n=3: [])
    monkeypatch.setattr(cmd_status, "_session_id", lambda: "session-w")
    monkeypatch.setattr(cmd_status, "_git_root", lambda: None)
    monkeypatch.setattr(cmd_status, "_disk_usage", lambda _path=None: "1G / 2G (50%)")
    monkeypatch.setattr(cmd_status, "load_providers", lambda: [])

    out_file = tmp_path / "out.json"
    result = CliRunner().invoke(status, ["--json", "--write", "-o", str(out_file)])

    assert result.exit_code == 0, result.output
    assert out_file.exists()
    data = json.loads(out_file.read_text())
    assert data["session_id"] == "session-w"
    assert "timestamp" in data


def test_status_json_rejects_rendering_options():
    """Verify JSON cannot be combined with presentation-only options."""
    runner = CliRunner()
    for args in (
        ["--json", "--no-markdown"],
        ["--json", "--format", "table"],
        ["--json", "--write"],  # --write without -o is rejected
    ):
        result = runner.invoke(status, args)
        assert result.exit_code == 2


# ── StatusProvider tests ───────────────────────────────────────────────


def test_status_provider_protocol():
    """Verify _FakeProvider satisfies the StatusProvider protocol."""
    provider = _FakeProvider()
    assert isinstance(provider, StatusProvider)
    assert provider.name == "test"
    assert provider.collect() == {"test_key": "test_value", "test_count": 42}
    sections = provider.narrative_sections()
    assert len(sections) == 1
    assert "## Test Section" in sections[0]


def test_status_json_merges_provider_fields(monkeypatch):
    """Verify provider collect() is merged into --json output."""
    monkeypatch.setattr(cmd_status, "_recent_commits", lambda n=3: [])
    monkeypatch.setattr(cmd_status, "_session_id", lambda: "session-p")
    monkeypatch.setattr(cmd_status, "_git_root", lambda: None)
    monkeypatch.setattr(cmd_status, "_disk_usage", lambda _path=None: "1G / 2G (50%)")
    monkeypatch.setattr(cmd_status, "load_providers", lambda: [_FakeProvider()])

    result = CliRunner().invoke(status, ["--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["test_key"] == "test_value"
    assert data["test_count"] == 42


def test_status_narrative_includes_provider_sections(monkeypatch):
    """Verify provider narrative_sections() are included in narrative output."""
    monkeypatch.setattr(cmd_status, "load_providers", lambda: [_FakeProvider()])

    result = CliRunner().invoke(status)
    assert result.exit_code == 0, result.output
    assert "## Test Section" in result.output
    assert "item from provider" in result.output


def test_status_table_includes_provider_fields(monkeypatch):
    """Verify provider collect() keys appear as rows in table format."""
    monkeypatch.setattr(cmd_status, "_session_id", lambda: "s")
    monkeypatch.setattr(cmd_status, "_recent_commits", lambda n=1: [])
    monkeypatch.setattr(cmd_status, "_git_root", lambda: None)
    monkeypatch.setattr(cmd_status, "_disk_usage", lambda _path=None: "0G / 0G (0%)")
    monkeypatch.setattr(cmd_status, "load_providers", lambda: [_FakeProvider()])

    result = CliRunner().invoke(status, ["--format", "table"])
    assert result.exit_code == 0, result.output
    assert "| test_key |" in result.output
    assert "test_value" in result.output


def test_status_broken_provider_does_not_crash(monkeypatch):
    """Verify a provider whose collect() raises does not crash the command."""

    class _BrokenProvider:
        name = "broken"

        def collect(self) -> dict:
            raise RuntimeError("provider is broken")

        def narrative_sections(self) -> list[str]:
            raise RuntimeError("provider is broken")

    monkeypatch.setattr(cmd_status, "load_providers", lambda: [_BrokenProvider()])

    result = CliRunner().invoke(status)
    assert result.exit_code == 0, result.output
    assert "# gptme Status" in result.output


def test_load_providers_skips_raising_name_provider(monkeypatch):
    """Verify load_providers() skips a provider whose name property raises.

    Runtime-checkable isinstance() only verifies attribute *presence*, not that
    property getters actually work.  A provider with a raising name property must
    be excluded so error-handler log lines in cmd_status.py never themselves raise.
    """
    import importlib.metadata as im

    class _RaisingNameProvider:
        """Structurally-conforming provider whose name property always raises."""

        @property
        def name(self) -> str:
            raise RuntimeError("name property is broken")

        def collect(self) -> dict:
            return {}

        def narrative_sections(self) -> list[str]:
            return []

    def _factory():
        return _RaisingNameProvider()

    class _FakeEP:
        name = "raising-name-provider"

        def load(self):
            return _factory

    original_ep = im.entry_points

    def _patched_ep(**kw):
        if kw.get("group") == "gptme.status_providers":
            return [_FakeEP()]
        return original_ep(**kw)

    monkeypatch.setattr(im, "entry_points", _patched_ep)
    from gptme.status_provider import load_providers

    providers = load_providers()
    # The raising-name provider must be silently dropped
    assert all(
        p.name == p.name for p in providers
    )  # all returned providers have safe names
    # And the raising provider itself must not be present
    for p in providers:
        assert not isinstance(p, _RaisingNameProvider)


def test_load_providers_returns_empty_when_none_installed():
    """Verify load_providers() returns an empty list when no providers installed."""
    from gptme.status_provider import load_providers

    providers = load_providers()
    # In the gptme package itself, no providers are registered.
    # If some are installed in this environment, they should still satisfy the protocol.
    assert isinstance(providers, list)
    for p in providers:
        assert isinstance(p, StatusProvider)


def test_load_providers_skips_malformed_factory_result(monkeypatch):
    """Verify load_providers() skips a factory that returns a non-provider."""
    import gptme.status_provider as sp_mod

    # A factory that returns None instead of a StatusProvider
    def _bad_factory():
        return None

    class _FakeEP:
        name = "bad-provider"

        def load(self):
            return _bad_factory

    monkeypatch.setattr(
        sp_mod,
        "entry_points",
        lambda **_kw: [_FakeEP()],
        raising=False,
    )
    # Patch entry_points in the module namespace it's imported into
    import importlib.metadata as im

    original_ep = im.entry_points

    def _patched_ep(**kw):
        if kw.get("group") == "gptme.status_providers":
            return [_FakeEP()]
        return original_ep(**kw)

    monkeypatch.setattr(im, "entry_points", _patched_ep)
    from gptme.status_provider import load_providers

    providers = load_providers()
    # The bad factory result must be silently dropped
    assert all(isinstance(p, StatusProvider) for p in providers)


def test_status_json_provider_unserializable_value_does_not_crash(monkeypatch):
    """Verify non-JSON-serializable provider values don't crash --json output."""
    from datetime import datetime, timezone
    from pathlib import Path

    class _UnserializableProvider:
        name = "unser"

        def collect(self) -> dict:
            return {
                "a_datetime": datetime.now(timezone.utc),  # not JSON-serializable
                "a_path": Path("/tmp"),  # not JSON-serializable
            }

        def narrative_sections(self) -> list[str]:
            return []

    monkeypatch.setattr(cmd_status, "_recent_commits", lambda n=3: [])
    monkeypatch.setattr(cmd_status, "_session_id", lambda: "session-u")
    monkeypatch.setattr(cmd_status, "_git_root", lambda: None)
    monkeypatch.setattr(cmd_status, "_disk_usage", lambda _path=None: "1G / 2G (50%)")
    monkeypatch.setattr(
        cmd_status, "load_providers", lambda: [_UnserializableProvider()]
    )

    result = CliRunner().invoke(status, ["--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    # Values should be coerced to strings, not cause a crash
    assert "a_datetime" in data
    assert "a_path" in data
    assert isinstance(data["a_datetime"], str)
    assert isinstance(data["a_path"], str)


def test_status_json_provider_cannot_overwrite_core_keys(monkeypatch):
    """Verify provider collect() cannot overwrite reserved core fields."""

    class _EvilProvider:
        name = "evil"

        def collect(self) -> dict:
            return {
                "session_id": "HIJACKED",
                "disk_usage": "HIJACKED",
                "extra_key": "allowed",
            }

        def narrative_sections(self) -> list[str]:
            return []

    monkeypatch.setattr(cmd_status, "_recent_commits", lambda n=3: [])
    monkeypatch.setattr(cmd_status, "_session_id", lambda: "real-session")
    monkeypatch.setattr(cmd_status, "_git_root", lambda: None)
    monkeypatch.setattr(cmd_status, "_disk_usage", lambda _path=None: "1G / 2G (50%)")
    monkeypatch.setattr(cmd_status, "load_providers", lambda: [_EvilProvider()])

    result = CliRunner().invoke(status, ["--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    # Core keys must NOT be overwritten
    assert data["session_id"] == "real-session"
    assert data["disk_usage"] == "1G / 2G (50%)"
    # Non-core key from provider is allowed through
    assert data["extra_key"] == "allowed"


# ── formatting helpers ─────────────────────────────────────────────────


def test_pr_queue_display():
    """Verify _pr_queue_display formats count/cap and at-limit suffix correctly."""
    assert _pr_queue_display(2, None) == "2"
    assert _pr_queue_display(2, 10) == "2/10"
    assert _pr_queue_display(10, 10) == "10/10 ⚠ at limit"
    assert _pr_queue_display(11, 10) == "11/10 ⚠ at limit"


def test_status_format_narrative_is_default():
    """Verify default format is narrative (not table)."""
    runner = CliRunner()
    result = runner.invoke(status)
    assert result.exit_code == 0, result.output
    assert "## Active Work" in result.output
    assert "| Field | Value |" not in result.output


def test_session_id_from_env(monkeypatch):
    """Verify _session_id reads from environment variables."""
    monkeypatch.setenv("GPTME_SESSION_ID", "abc123")
    assert _session_id() == "abc123"
    monkeypatch.delenv("GPTME_SESSION_ID")
    monkeypatch.setenv("BOB_SESSION_ID", "def456")
    assert _session_id() == "def456"


def test_session_id_fallback(monkeypatch):
    """Verify _session_id returns 'none' when no env var is set."""
    for key in (
        "GPTME_SESSION_ID",
        "BOB_SESSION_ID",
        "SESSION_ID",
        "GIT_COMMITTER_SESSION_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    assert _session_id() == "none"


def test_build_table_document_structure():
    """Verify build_table_document produces a markdown table with core fields."""
    doc = build_table_document(providers=[])
    lines = doc.splitlines()
    assert any("| Field | Value |" in line for line in lines)
    assert any("| session_id |" in line for line in lines)
    assert any("| last_commit |" in line for line in lines)
    assert any("| disk_usage |" in line for line in lines)


def test_build_table_document_with_provider():
    """Verify provider fields appear as table rows."""
    doc = build_table_document(providers=[_FakeProvider()])
    assert "| test_key |" in doc
    assert "test_value" in doc


def test_build_table_document_core_key_collision():
    """Provider cannot inject a row that duplicates a core table field."""

    class _CoreClobberProvider:
        name = "clobber"

        def collect(self) -> dict:
            # session_id and disk_usage are core fields rendered by core itself.
            return {
                "session_id": "INJECTED",
                "disk_usage": "INJECTED",
                "safe_key": "ok",
            }

        def narrative_sections(self) -> list:
            return []

    doc = build_table_document(providers=[_CoreClobberProvider()])
    # The core fields must appear exactly once (no duplicate rows).
    assert doc.count("| session_id |") == 1, "session_id should appear exactly once"
    assert doc.count("| disk_usage |") == 1, "disk_usage should appear exactly once"
    assert "INJECTED" not in doc, "provider must not overwrite core table values"
    # Non-conflicting key still appears.
    assert "| safe_key |" in doc


def test_build_table_document_cross_provider_collision():
    """Later provider cannot add a row that duplicates an earlier provider's key."""

    class _FirstProvider:
        name = "first"

        def collect(self) -> dict:
            return {"shared_key": "from_first"}

        def narrative_sections(self) -> list:
            return []

    class _SecondProvider:
        name = "second"

        def collect(self) -> dict:
            return {"shared_key": "from_second", "unique_key": "ok"}

        def narrative_sections(self) -> list:
            return []

    doc = build_table_document(providers=[_FirstProvider(), _SecondProvider()])
    # Only the first provider's value should appear; no duplicate rows.
    assert doc.count("| shared_key |") == 1, "shared_key should appear exactly once"
    assert "from_first" in doc
    assert "from_second" not in doc
    assert "| unique_key |" in doc
