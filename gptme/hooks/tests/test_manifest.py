"""Tests for the tool-call manifest writer hook."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from gptme.hooks import HookType
from gptme.hooks.manifest import (
    _record_content_hash,
    register_manifest_hooks,
    verify_manifest_chain,
)
from gptme.hooks.registry import HookRegistry
from gptme.hooks.types import ToolExecutePostData, ToolExecutePreData
from gptme.message import Message
from gptme.tools.base import ToolUse

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _run_hook(registry: HookRegistry, hook_type: HookType, data: object) -> list:
    return list(registry.trigger(hook_type, data))


def _run_tool_call(
    registry: HookRegistry,
    seq: int,
) -> None:
    """Helper: execute pre and post hooks for one tool call."""
    tool_use = ToolUse(tool="shell", args=[f"echo seq{seq}"], content=None)
    pre_data = ToolExecutePreData(tool_use=tool_use)
    _run_hook(registry, HookType.TOOL_EXECUTE_PRE, pre_data)
    result_msg = Message("system", f"output{seq}\n")
    post_data = ToolExecutePostData(tool_use=tool_use, result_msgs=(result_msg,))
    _run_hook(registry, HookType.TOOL_EXECUTE_POST, post_data)


@pytest.fixture()
def manifest_dir(tmp_path: Path) -> Path:
    return tmp_path / "manifests"


@pytest.fixture()
def registry(manifest_dir: Path) -> Iterator[HookRegistry]:
    import gptme.hooks.registry as _mod

    reg = HookRegistry()
    _mod.set_registry(reg)
    register_manifest_hooks(manifest_dir)
    yield reg
    _mod.set_registry(HookRegistry())


class TestManifestHooks:
    def test_manifest_dir_created(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        assert manifest_dir.exists()

    def test_pre_hook_writes_file(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        tool_use = ToolUse(tool="shell", args=["echo hi"], content=None)
        data = ToolExecutePreData(tool_use=tool_use)
        _run_hook(registry, HookType.TOOL_EXECUTE_PRE, data)

        files = list(manifest_dir.glob("*-pre.json"))
        assert len(files) == 1
        record = json.loads(files[0].read_text())
        assert record["tool"] == "shell"
        assert record["phase"] == "pre"
        assert record["sequence"] == 1
        assert "args_hash" in record
        assert record["args_hash"].startswith("sha256:")
        assert "hash" in record
        assert record["hash"].startswith("sha256:")
        # Genesis record: no predecessor.
        assert record["prev_hash"] is None

    def test_post_hook_writes_file(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        tool_use = ToolUse(tool="shell", args=["echo hi"], content=None)
        result_msg = Message("system", "hi\n")
        pre_data = ToolExecutePreData(tool_use=tool_use)
        _run_hook(registry, HookType.TOOL_EXECUTE_PRE, pre_data)

        post_data = ToolExecutePostData(tool_use=tool_use, result_msgs=(result_msg,))
        _run_hook(registry, HookType.TOOL_EXECUTE_POST, post_data)

        files = list(manifest_dir.glob("*-post.json"))
        assert len(files) == 1
        record = json.loads(files[0].read_text())
        assert record["tool"] == "shell"
        assert record["phase"] == "post"
        assert "result_hash" in record
        assert record["result_hash"].startswith("sha256:")
        assert "hash" in record
        assert record["hash"].startswith("sha256:")
        # Post-record links to its pre-record.
        assert record["prev_hash"] is not None
        assert record["prev_hash"].startswith("sha256:")

    def test_sequence_increments_per_tool_call(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        for i in range(3):
            tool_use = ToolUse(tool="read", args=[f"file{i}.py"], content=None)
            data = ToolExecutePreData(tool_use=tool_use)
            _run_hook(registry, HookType.TOOL_EXECUTE_PRE, data)

        pre_files = sorted(manifest_dir.glob("*-pre.json"))
        assert len(pre_files) == 3
        seqs = [json.loads(f.read_text())["sequence"] for f in pre_files]
        assert seqs == [1, 2, 3]

    def test_no_files_when_tool_use_none(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        data = ToolExecutePreData(tool_use=None)
        _run_hook(registry, HookType.TOOL_EXECUTE_PRE, data)
        assert list(manifest_dir.glob("*.json")) == []

    def test_hash_chain_integrity(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        """A full session chain (3 tool calls) verifies clean."""
        _run_tool_call(registry, 1)
        _run_tool_call(registry, 2)
        _run_tool_call(registry, 3)

        errors = verify_manifest_chain(manifest_dir)
        assert errors == [], f"Chain verification failed: {errors}"

    def test_hash_chain_pre_to_post_linking(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        """Post-record's prev_hash must equal its pre-record's hash."""
        _run_tool_call(registry, 1)

        pre_file = list(manifest_dir.glob("*-pre.json"))[0]
        post_file = list(manifest_dir.glob("*-post.json"))[0]
        pre_rec = json.loads(pre_file.read_text())
        post_rec = json.loads(post_file.read_text())

        assert post_rec["prev_hash"] == pre_rec["hash"]

    def test_hash_chain_cross_call_linking(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        """Next pre-record's prev_hash must equal previous post-record's hash."""
        _run_tool_call(registry, 1)
        _run_tool_call(registry, 2)

        pre_files = sorted(manifest_dir.glob("*-pre.json"))
        post_files = sorted(manifest_dir.glob("*-post.json"))

        pre1 = json.loads(pre_files[0].read_text())
        post1 = json.loads(post_files[0].read_text())
        pre2 = json.loads(pre_files[1].read_text())

        assert pre1["prev_hash"] is None  # genesis
        assert post1["prev_hash"] == pre1["hash"]
        assert pre2["prev_hash"] == post1["hash"]

    def test_record_hash_self_consistency(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        """Each record's hash field matches a recomputation of its content."""
        _run_tool_call(registry, 1)

        for fpath in manifest_dir.glob("*.json"):
            rec = json.loads(fpath.read_text())
            stored = rec.pop("hash")
            computed = _record_content_hash(rec)
            assert stored == computed, f"{fpath.name}: hash mismatch"


class TestManifestTamperDetection:
    """Regression tests: tampering with the manifest chain must be detectable."""

    def test_tampered_content_breaks_self_hash(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        """Modifying a record's tool field invalidates its own hash."""
        _run_tool_call(registry, 1)
        _run_tool_call(registry, 2)

        # Tamper with seq=1 pre-record.
        pre_files = sorted(manifest_dir.glob("*-pre.json"))
        rec = json.loads(pre_files[0].read_text())
        rec["tool"] = "tampered_tool"
        pre_files[0].write_text(json.dumps(rec, indent=2))

        errors = verify_manifest_chain(manifest_dir)
        assert any("hash mismatch" in e for e in errors)

    def test_tampered_prev_hash_breaks_chain(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        """Modifying a record's prev_hash breaks the chain link."""
        _run_tool_call(registry, 1)
        _run_tool_call(registry, 2)

        # Tamper with seq=2 pre-record's prev_hash.
        pre_files = sorted(manifest_dir.glob("*-pre.json"))
        rec = json.loads(pre_files[1].read_text())
        rec["prev_hash"] = "sha256:deadbeef"
        # Must also fix the self-hash so only the chain-link check catches it.
        rec["hash"] = _record_content_hash(rec)
        pre_files[1].write_text(json.dumps(rec, indent=2))

        errors = verify_manifest_chain(manifest_dir)
        assert any("prev_hash mismatch" in e for e in errors)

    def test_deleted_record_breaks_chain(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        """Deleting a record from the middle of the chain is detected."""
        _run_tool_call(registry, 1)
        _run_tool_call(registry, 2)
        _run_tool_call(registry, 3)

        # Delete seq=2 pre-record.
        pre_files = sorted(manifest_dir.glob("*-pre.json"))
        pre_files[1].unlink()

        errors = verify_manifest_chain(manifest_dir)
        assert any("missing pre record" in e and "2" in e for e in errors)

    def test_inserted_record_breaks_chain(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        """An extra (forged) record is detected."""
        _run_tool_call(registry, 1)

        # Forge an extra pre-record with a fake hash chain.
        forged: dict = {
            "session_id": "attacker",
            "model": "evil",
            "sequence": 2,
            "tool": "malicious",
            "args_hash": "sha256:aaaaaaaa",
            "timestamp": "2020-01-01T00:00:00+00:00",
            "phase": "pre",
            "prev_hash": "sha256:bbbbbbbb",
            "hash": "sha256:cccccccc",
        }
        (manifest_dir / "attacker-0002-malicious-pre.json").write_text(
            json.dumps(forged, indent=2)
        )

        errors = verify_manifest_chain(manifest_dir)
        assert len(errors) > 0

    def test_empty_dir_passes(self, manifest_dir: Path) -> None:
        """An empty manifest directory verifies clean."""
        errors = verify_manifest_chain(manifest_dir)
        assert errors == []

    def test_missing_post_record_detected(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        """A pre-record without its post counterpart is flagged."""
        _run_tool_call(registry, 1)
        _run_tool_call(registry, 2)

        # Delete seq=2 post-record.
        post_files = sorted(manifest_dir.glob("*-post.json"))
        post_files[1].unlink()

        errors = verify_manifest_chain(manifest_dir)
        assert any("missing post record" in e and "2" in e for e in errors)
