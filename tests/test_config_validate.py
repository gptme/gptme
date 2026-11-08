"""Tests for gptme.config.validate — read-only config corruption detector."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import tomllib

if TYPE_CHECKING:
    from pathlib import Path

from gptme.config.validate import (
    detect_config_problems,
    fix_providers_corruption,
)

# ---------------------------------------------------------------------------
# Fixtures: corrupt and clean config contents
# ---------------------------------------------------------------------------

CORRUPT_PROVIDERS_TOML = """\
providers = [name = "fixture"
base_url = "http://127.0.0.1:37453/v1"
default_model = "fixture-model"
]
"""

UNRELATED_SETTING = 'model = "openai/gpt-4o-mini"\n'

CLEAN_PROVIDERS_TOML = """\
[[providers]]
name = "fixture"
base_url = "http://127.0.0.1:37453/v1"
default_model = "fixture-model"
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, content: str) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(content, encoding="utf-8")
    return cfg


def _make_token(tmp_path: Path, data: object) -> Path:
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    token = auth_dir / "gptme-cloud-aabbccdd1234.json"
    token.write_text(json.dumps(data), encoding="utf-8")
    return token


# ---------------------------------------------------------------------------
# detect_config_problems
# ---------------------------------------------------------------------------


class TestDetectClean:
    """Clean config → no problems."""

    def test_empty_root(self, tmp_path: Path) -> None:
        problems = detect_config_problems(config_root=tmp_path)
        assert problems == []

    def test_valid_providers(self, tmp_path: Path) -> None:
        _make_config(tmp_path, CLEAN_PROVIDERS_TOML)
        problems = detect_config_problems(config_root=tmp_path)
        assert problems == []

    def test_valid_token(self, tmp_path: Path) -> None:
        _make_token(tmp_path, {"access_token": "tok", "expires_at": 9999999999})
        problems = detect_config_problems(config_root=tmp_path)
        assert problems == []


class TestDetectProvidersCorruption:
    """providers-not-array pattern (gptme/gptme#4806)."""

    def test_detected(self, tmp_path: Path) -> None:
        _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        problems = detect_config_problems(config_root=tmp_path)
        names = [p.name for p in problems]
        assert "providers-not-array" in names

    def test_repair_cmd_present(self, tmp_path: Path) -> None:
        _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        problems = detect_config_problems(config_root=tmp_path)
        p = next(x for x in problems if x.name == "providers-not-array")
        assert p.repair_cmd  # non-empty
        assert p.repair_cmd.lower().startswith(("gptme", "rm"))

    def test_no_false_positive_on_valid_toml_parse_error(self, tmp_path: Path) -> None:
        # A parse error without the providers = [ pattern should not raise.
        _make_config(tmp_path, "this is not [ valid toml !\n")
        problems = detect_config_problems(config_root=tmp_path)
        # Should not contain providers-not-array
        assert not any(p.name == "providers-not-array" for p in problems)


class TestDetectAuthCorruption:
    """auth-cache-invalid pattern (gptme/gptme#4797)."""

    def test_list_root(self, tmp_path: Path) -> None:
        _make_token(tmp_path, ["not", "an", "object"])
        problems = detect_config_problems(config_root=tmp_path)
        names = [p.name for p in problems]
        assert "auth-cache-invalid" in names

    def test_null_root(self, tmp_path: Path) -> None:
        _make_token(tmp_path, None)
        problems = detect_config_problems(config_root=tmp_path)
        names = [p.name for p in problems]
        assert "auth-cache-invalid" in names

    def test_repair_cmd_present(self, tmp_path: Path) -> None:
        _make_token(tmp_path, ["x"])
        problems = detect_config_problems(config_root=tmp_path)
        p = next(x for x in problems if x.name == "auth-cache-invalid")
        assert p.repair_cmd

    def test_dict_root_is_clean(self, tmp_path: Path) -> None:
        _make_token(tmp_path, {"access_token": "tok"})
        problems = detect_config_problems(config_root=tmp_path)
        assert not any(p.name == "auth-cache-invalid" for p in problems)


# ---------------------------------------------------------------------------
# fix_providers_corruption
# ---------------------------------------------------------------------------


class TestFixProvidersCorruption:
    """fix_providers_corruption repairs in place and leaves a backup."""

    def test_parses_after_fix(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        fix_providers_corruption(cfg)
        tomllib.loads(cfg.read_text(encoding="utf-8"))  # must not raise

    def test_providers_is_list_of_tables(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        fix_providers_corruption(cfg)
        parsed = tomllib.loads(cfg.read_text(encoding="utf-8"))
        providers = parsed.get("providers")
        assert isinstance(providers, list)
        assert all(isinstance(p, dict) for p in providers)

    def test_unrelated_setting_preserved(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        fix_providers_corruption(cfg)
        parsed = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert parsed.get("model") == "openai/gpt-4o-mini"

    def test_backup_left_behind(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        bak = fix_providers_corruption(cfg)
        assert bak.exists()
        assert ".bak" in bak.name

    def test_backup_contains_original(self, tmp_path: Path) -> None:
        original = UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML
        cfg = _make_config(tmp_path, original)
        bak = fix_providers_corruption(cfg)
        assert bak.read_text(encoding="utf-8") == original

    def test_detect_no_longer_flags_after_fix(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        fix_providers_corruption(cfg)
        problems = detect_config_problems(config_root=tmp_path)
        assert not any(p.name == "providers-not-array" for p in problems)

    def test_no_unrecognised_block_leaves_file_unchanged(self, tmp_path: Path) -> None:
        # A clean config should not be modified (backup is still written).
        content = CLEAN_PROVIDERS_TOML
        cfg = _make_config(tmp_path, content)
        fix_providers_corruption(cfg)
        assert cfg.read_text(encoding="utf-8") == content
