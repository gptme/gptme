"""Tests for project-config shell trust gate (TOFU)."""

from gptme.config.trust import (
    _record,
    check_project_shell_trust,
    compute_shell_hash,
    is_trusted,
)

# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def test_compute_shell_hash_stable():
    """Same commands always produce the same hash."""
    h1 = compute_shell_hash("scripts/context.sh", ["echo start"])
    h2 = compute_shell_hash("scripts/context.sh", ["echo start"])
    assert h1 == h2


def test_compute_shell_hash_hook_order_canonical():
    """Hook order is canonicalised (sorted) so insertion order doesn't matter."""
    h1 = compute_shell_hash(None, ["b.sh", "a.sh"])
    h2 = compute_shell_hash(None, ["a.sh", "b.sh"])
    assert h1 == h2


def test_compute_shell_hash_prefix():
    """Hash string starts with ``sha256:``."""
    h = compute_shell_hash("cmd", [])
    assert h.startswith("sha256:")


def test_compute_shell_hash_distinct_commands():
    """Different commands produce different hashes."""
    h1 = compute_shell_hash("cmd1", [])
    h2 = compute_shell_hash("cmd2", [])
    assert h1 != h2


def test_compute_shell_hash_empty_is_not_nonempty():
    h1 = compute_shell_hash(None, [])
    h2 = compute_shell_hash("echo hi", [])
    assert h1 != h2


# ---------------------------------------------------------------------------
# Trust DB
# ---------------------------------------------------------------------------


def test_is_trusted_unknown_hash(tmp_path, monkeypatch):
    """An unknown hash is not trusted."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert not is_trusted("sha256:nonexistent")


def test_record_and_is_trusted(tmp_path, monkeypatch):
    """Recording an approved hash makes is_trusted return True."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    h = compute_shell_hash("echo hello", [])
    _record(h, approved=True, workspace=tmp_path)
    assert is_trusted(h)


def test_record_denied_is_not_trusted(tmp_path, monkeypatch):
    """Recording a denied hash does not mark it as trusted."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    h = compute_shell_hash("rm -rf /", [])
    _record(h, approved=False, workspace=tmp_path)
    assert not is_trusted(h)


# ---------------------------------------------------------------------------
# check_project_shell_trust — non-interactive (the common automated path)
# ---------------------------------------------------------------------------


def test_no_shell_is_always_trusted(tmp_path, monkeypatch):
    """A project config with no shell commands is trusted without prompting."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = check_project_shell_trust(None, [], tmp_path, interactive=False)
    assert result is True


def test_non_interactive_denied_without_prior_trust(tmp_path, monkeypatch, capsys):
    """In non-interactive mode an unrecognised hash is denied and logs a warning."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = check_project_shell_trust(
        "scripts/context.sh", [], tmp_path, interactive=False
    )
    assert result is False


def test_non_interactive_approved_when_previously_trusted(tmp_path, monkeypatch):
    """In non-interactive mode a previously-approved hash is trusted."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cmd = "scripts/context.sh"
    h = compute_shell_hash(cmd, [])
    _record(h, approved=True, workspace=tmp_path)

    result = check_project_shell_trust(cmd, [], tmp_path, interactive=False)
    assert result is True


def test_non_interactive_denied_when_hash_changed(tmp_path, monkeypatch):
    """A previously-approved hash does not cover a modified command."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old_cmd = "scripts/context.sh"
    h_old = compute_shell_hash(old_cmd, [])
    _record(h_old, approved=True, workspace=tmp_path)

    # Command changed → new hash → not trusted yet
    result = check_project_shell_trust(
        "scripts/CHANGED.sh", [], tmp_path, interactive=False
    )
    assert result is False


# ---------------------------------------------------------------------------
# check_project_shell_trust — trust-all env override
# ---------------------------------------------------------------------------


def test_trust_all_env_bypasses_check(tmp_path, monkeypatch):
    """GPTME_TRUST_PROJECT_SHELL=1 skips the gate entirely."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("GPTME_TRUST_PROJECT_SHELL", "1")
    result = check_project_shell_trust(
        "rm -rf /", ["danger.sh"], tmp_path, interactive=False
    )
    assert result is True


def test_trust_all_env_true_string(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("GPTME_TRUST_PROJECT_SHELL", "true")
    result = check_project_shell_trust("cmd", [], tmp_path, interactive=False)
    assert result is True


# ---------------------------------------------------------------------------
# check_project_shell_trust — interactive path (mocked input)
# ---------------------------------------------------------------------------


def test_interactive_approve_stores_hash(tmp_path, monkeypatch):
    """Approving in interactive mode stores the hash and returns True."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda: "y")

    cmd = "scripts/context.sh"
    result = check_project_shell_trust(cmd, [], tmp_path, interactive=True)
    assert result is True
    assert is_trusted(compute_shell_hash(cmd, []))


def test_interactive_deny_stores_hash_as_denied(tmp_path, monkeypatch):
    """Denying in interactive mode stores the hash as denied and returns False."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda: "n")

    cmd = "scripts/evil.sh"
    result = check_project_shell_trust(cmd, [], tmp_path, interactive=True)
    assert result is False
    assert not is_trusted(compute_shell_hash(cmd, []))


def test_interactive_eof_denies(tmp_path, monkeypatch):
    """EOF on input (e.g. piped /dev/null) defaults to deny."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def raise_eof():
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    result = check_project_shell_trust("cmd", [], tmp_path, interactive=True)
    assert result is False


def test_interactive_prompt_shown_once(tmp_path, monkeypatch):
    """On second call with same hash, no re-prompt is needed (already trusted)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    calls = []

    def record_and_approve():
        calls.append(1)
        return "y"

    monkeypatch.setattr("builtins.input", record_and_approve)
    cmd = "scripts/context.sh"

    check_project_shell_trust(cmd, [], tmp_path, interactive=True)
    check_project_shell_trust(cmd, [], tmp_path, interactive=True)

    # Input was only called once; second call used the stored approval
    assert len(calls) == 1


def test_hooks_and_context_cmd_hashed_together(tmp_path, monkeypatch):
    """context_cmd and hook commands are hashed as a unit; change either → new hash."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    h1 = compute_shell_hash("ctx.sh", ["hook.sh"])
    h2 = compute_shell_hash("ctx.sh", [])
    h3 = compute_shell_hash(None, ["hook.sh"])
    assert h1 != h2
    assert h1 != h3
    assert h2 != h3
