"""Tests for the tool-call manifest writer hook."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

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


class TestManifestVerifierRobustness:
    """Regression tests for verifier edge-cases (Greptile P1/P2 findings)."""

    def test_non_object_json_does_not_crash(self, manifest_dir: Path) -> None:
        """A file containing valid but non-object JSON returns an error, not AttributeError."""
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "bad-0001-shell-pre.json").write_text(json.dumps([]))
        (manifest_dir / "bad-0001-shell-post.json").write_text(json.dumps("text"))

        errors = verify_manifest_chain(manifest_dir)
        assert len(errors) == 2
        assert all("expected JSON object" in e for e in errors)

    def test_non_positive_sequence_rejected(self, manifest_dir: Path) -> None:
        """Records with zero or negative sequence numbers are rejected."""
        manifest_dir.mkdir(parents=True, exist_ok=True)
        for seq_val, fname in [
            (0, "sess-0000-shell-pre.json"),
            (-1, "sess--001-shell-pre.json"),
        ]:
            rec = {
                "session_id": "sess",
                "sequence": seq_val,
                "tool": "shell",
                "phase": "pre",
            }
            (manifest_dir / fname).write_text(json.dumps(rec))

        errors = verify_manifest_chain(manifest_dir)
        assert len(errors) == 2
        assert all("invalid sequence number" in e for e in errors)

    def test_duplicate_sequence_reported(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        """Two sessions sharing a manifest dir produce duplicate-sequence errors."""
        _run_tool_call(registry, 1)

        # Inject a second pre-record with the same seq=1 from a different session.
        dup: dict = {
            "session_id": "other-session",
            "model": "unknown",
            "sequence": 1,
            "tool": "shell",
            "args_hash": "sha256:aabbccdd",
            "timestamp": "2020-01-01T00:00:00+00:00",
            "phase": "pre",
            "prev_hash": None,
            "hash": "sha256:deadbeef",
        }
        (manifest_dir / "other-session-0001-shell-pre.json").write_text(
            json.dumps(dup, indent=2)
        )

        errors = verify_manifest_chain(manifest_dir)
        assert any("duplicate" in e and "1" in e for e in errors)

    def test_large_sequence_does_not_exhaust_resources(
        self, manifest_dir: Path
    ) -> None:
        """A crafted record with a huge sequence number completes fast."""
        manifest_dir.mkdir(parents=True, exist_ok=True)
        # Sequence 1 (valid) plus a forged record with a huge sequence.
        for seq_val, fname in [
            (1, "sess-0001-shell-pre.json"),
            (10_000_000, "evil-9999-shell-pre.json"),
        ]:
            rec = {
                "session_id": "sess",
                "sequence": seq_val,
                "tool": "shell",
                "phase": "pre",
                "prev_hash": None,
                "hash": "sha256:aabbccdd",
            }
            (manifest_dir / fname).write_text(json.dumps(rec))

        t0 = time.monotonic()
        errors = verify_manifest_chain(manifest_dir)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"verify took {elapsed:.2f}s — range-loop DoS likely"
        assert len(errors) > 0  # gap and missing-post errors expected

    def test_failed_write_does_not_advance_chain_tail(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        """If a write fails the chain tail is not advanced, so the next record links correctly."""
        import gptme.hooks.manifest as _mod

        _run_tool_call(registry, 1)

        # Capture what prev_hash[0] looks like after a clean write.
        post_files = sorted(manifest_dir.glob("*-post.json"))
        post_rec = json.loads(post_files[0].read_text())
        expected_tail_hash = post_rec["hash"]

        # Simulate a failed pre-write for tool call 2 (OSError on the pre record).
        original = _mod._write_record

        calls: list[bool] = []

        def _failing_once(mdir, fname, record):
            if fname.endswith("-pre.json") and len(calls) == 0:
                calls.append(False)
                # Update hash on record (as original would) but return False.
                record["hash"] = _mod._record_content_hash(record)
                return False
            return original(mdir, fname, record)

        with patch.object(_mod, "_write_record", side_effect=_failing_once):
            _run_tool_call(registry, 2)

        # The post-record of call 2 should link to the tail after call 1's post,
        # NOT to the hash of the failed pre-record that was never written.
        post_files2 = sorted(manifest_dir.glob("*-post.json"))
        # There should only be the post from call 1 (call 2's pre failed, so
        # prev_hash for the post will still be the tail from call 1).
        post2 = json.loads(post_files2[-1].read_text())
        # The post-record's prev_hash must not be None (it links to the last
        # successful write, which is post of call 1).
        assert post2.get("sequence") == 2
        assert post2["prev_hash"] == expected_tail_hash
