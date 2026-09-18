"""Tests for ModelSelectionTrace durability: LogManager persistence and attestation embedding."""

from __future__ import annotations

import errno
import json
import logging
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.message import Message
from gptme.model_attestation import (
    ModelSelectionTrace,
    create_selection_trace,
    set_selection_trace,
)


def make_trace(
    requested: str = "anthropic/claude-sonnet-4-6",
    resolved: str = "anthropic/claude-sonnet-4-6",
    source_kind: str = "cli",
    transport: str = "anthropic",
    backend: str = "anthropic",
) -> ModelSelectionTrace:
    return create_selection_trace(
        requested_model=requested,
        resolved_model=resolved,
        source_kind=source_kind,  # type: ignore[arg-type]
        source_value=requested,
        transport_provider=transport,
        backend_provider=backend,
    )


# ---------------------------------------------------------------------------
# LogManager trace persistence
# ---------------------------------------------------------------------------


class TestLogManagerTracePersistence:
    """Round-trip: LogManager.write() persists trace, read_model_trace() restores it."""

    def test_write_model_trace_creates_file(self, tmp_path: Path) -> None:
        from gptme.logmanager.manager import LogManager

        trace = make_trace()
        set_selection_trace(trace)

        lm = LogManager(logdir=tmp_path, lock=False)
        lm.write()

        trace_file = tmp_path / "model_selection_trace.json"
        assert trace_file.exists(), "model_selection_trace.json must be written"

    def test_write_model_trace_no_file_when_no_trace(self, tmp_path: Path) -> None:
        from gptme.logmanager.manager import LogManager

        set_selection_trace(None)

        lm = LogManager(logdir=tmp_path, lock=False)
        lm.write()

        trace_file = tmp_path / "model_selection_trace.json"
        assert not trace_file.exists(), "no trace file when context has no trace"

    def test_read_model_trace_round_trips_fields(self, tmp_path: Path) -> None:
        from gptme.logmanager.manager import LogManager

        original = make_trace(
            requested="openrouter/anthropic/claude-sonnet-4-6",
            resolved="openrouter/anthropic/claude-sonnet-4-6",
            source_kind="api_request",
            transport="openrouter",
            backend="anthropic",
        )
        set_selection_trace(original)

        lm = LogManager(logdir=tmp_path, lock=False)
        lm.write()

        # Re-read from a fresh manager instance (no active ContextVar)
        set_selection_trace(None)
        lm2 = LogManager(logdir=tmp_path, lock=False)
        restored = lm2.read_model_trace()

        assert restored is not None
        assert restored.schema == original.schema
        # Narrow both selection fields for mypy
        assert original.selection is not None
        assert restored.selection is not None
        orig_sel = original.selection
        rest_sel = restored.selection
        assert rest_sel.requested_model == orig_sel.requested_model
        assert rest_sel.resolved_model == orig_sel.resolved_model
        assert rest_sel.transport_provider == orig_sel.transport_provider
        assert rest_sel.backend_provider == orig_sel.backend_provider
        assert rest_sel.source.kind == orig_sel.source.kind
        assert restored.identity is not None
        assert restored.identity.attestation_level == "selection_only"

    def test_read_model_trace_returns_none_when_file_absent(
        self, tmp_path: Path
    ) -> None:
        from gptme.logmanager.manager import LogManager

        lm = LogManager(logdir=tmp_path, lock=False)
        assert lm.read_model_trace() is None

    def test_trace_file_is_valid_json(self, tmp_path: Path) -> None:
        from gptme.logmanager.manager import LogManager

        set_selection_trace(make_trace())
        lm = LogManager(logdir=tmp_path, lock=False)
        lm.write()

        trace_path = tmp_path / "model_selection_trace.json"
        data = json.loads(trace_path.read_text())
        assert data["schema"] == "gptme.model-attestation/v0"
        assert "selection" in data
        assert "identity" in data

    def test_trace_write_replaces_file_atomically(self, tmp_path: Path) -> None:
        from gptme.logmanager.manager import LogManager

        trace_path = tmp_path / "model_selection_trace.json"
        trace_path.write_text("old trace\n")
        set_selection_trace(make_trace())
        lm = LogManager(logdir=tmp_path, lock=False)

        with patch.object(Path, "replace", autospec=True) as replace:
            result = lm.write_model_trace()

        replace.assert_called_once()
        temp_path, destination = replace.call_args.args
        assert temp_path.parent == tmp_path
        assert temp_path.name.startswith(".model_selection_trace.json.")
        assert temp_path.suffix == ".tmp"
        assert destination == trace_path
        assert result == trace_path
        assert trace_path.read_text() == "old trace\n"
        assert not temp_path.exists()

    def test_sync_fsyncs_conversation_trace_and_directory(self, tmp_path: Path) -> None:
        from gptme.logmanager.manager import LogManager

        set_selection_trace(make_trace())
        lm = LogManager(logdir=tmp_path, lock=False)

        synced: set[tuple[int, int]] = set()

        def record(fd: int) -> None:
            info = os.fstat(fd)
            synced.add((info.st_dev, info.st_ino))

        with patch.object(os, "fsync", side_effect=record):
            lm.write(sync=True)

        paths = [lm.logfile, tmp_path / "model_selection_trace.json"]
        if os.name != "nt":
            paths.append(tmp_path)
        for path in paths:
            info = path.stat()
            assert (info.st_dev, info.st_ino) in synced

    def test_model_trace_syncs_temporary_file_before_replacement(
        self, tmp_path: Path
    ) -> None:
        from gptme.logmanager.manager import LogManager

        set_selection_trace(make_trace())
        lm = LogManager(logdir=tmp_path, lock=False)
        order: list[str] = []
        real_fsync = os.fsync
        real_replace = Path.replace

        def record_sync(fd: int) -> None:
            order.append("sync")
            real_fsync(fd)

        def record_replace(source: Path, target: Path) -> Path:
            order.append("replace")
            return real_replace(source, target)

        with (
            patch.object(os, "fsync", side_effect=record_sync),
            patch.object(Path, "replace", autospec=True, side_effect=record_replace),
        ):
            lm.write_model_trace()

        assert order == ["sync", "replace"]

    @pytest.mark.skipif(os.name == "nt", reason="No portable directory fsync")
    def test_directory_io_error_does_not_acknowledge_save(self, tmp_path: Path) -> None:
        """A real I/O error means the write did not land: fail the barrier."""
        from gptme.logmanager.manager import LogManager

        set_selection_trace(make_trace())
        lm = LogManager(
            [Message("user", "persist me", quiet=True)],
            logdir=tmp_path,
            lock=False,
        )
        real_fsync = os.fsync

        def reject_directory(fd: int) -> None:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, "directory fsync failed")
            real_fsync(fd)

        lm.write()
        with (
            patch.object(os, "fsync", side_effect=reject_directory),
            pytest.raises(OSError, match="directory fsync failed"),
        ):
            lm.write(sync=True)

        assert lm.logfile.exists()
        assert (tmp_path / "model_selection_trace.json").exists()

    @pytest.mark.skipif(os.name == "nt", reason="No portable directory fsync")
    def test_unsupported_directory_fsync_does_not_fail_save(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Filesystems without a directory barrier must not kill the turn.

        Some network, overlay and FUSE mounts reject fsync on a directory fd.
        The transcript is written and synced either way; only the namespace
        guarantee is weaker, which is worth a warning and nothing more.
        """
        from gptme.logmanager.manager import LogManager

        set_selection_trace(make_trace())
        lm = LogManager(
            [Message("user", "persist me", quiet=True)],
            logdir=tmp_path,
            lock=False,
        )
        real_fsync = os.fsync

        def reject_directory(fd: int) -> None:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EINVAL, "fsync not supported for this file")
            real_fsync(fd)

        lm.write()
        with (
            patch.object(os, "fsync", side_effect=reject_directory),
            caplog.at_level(logging.WARNING),
        ):
            lm.write(sync=True)  # must not raise

        assert lm.logfile.exists()
        assert (tmp_path / "model_selection_trace.json").exists()
        assert "No directory barrier available" in caplog.text


# ---------------------------------------------------------------------------
# Attestation embedding
# ---------------------------------------------------------------------------


FAKE_COMMIT = "a" * 40


def _build_payload(trace: ModelSelectionTrace | None) -> dict:
    """Build an attestation payload with git calls mocked out."""
    from unittest.mock import patch

    import gptme.attestation as att_mod

    set_selection_trace(trace)
    with (
        patch.object(att_mod, "get_workspace_commit", return_value=FAKE_COMMIT),
        patch.object(att_mod, "resolve_workspace_root", return_value=Path("/fake")),
        patch.object(
            att_mod, "get_model_identity", return_value=("test/model", "test")
        ),
        patch.object(att_mod, "get_agent_id", return_value="bob@host"),
        patch.object(att_mod, "get_session_id", return_value="sess-123"),
    ):
        payload = att_mod._build_payload(
            workspace_commit=FAKE_COMMIT,
            output_type="text",
            content_hash="sha256:abc",
            output_path=None,
            url=None,
        )
    return payload


class TestAttestationTraceEmbedding:
    """attestation.py embeds ModelSelectionTrace when active, omits it gracefully when absent."""

    def test_attestation_includes_selection_trace_when_active(self) -> None:
        trace = make_trace()
        payload = _build_payload(trace)

        assert "selection_trace" in payload["model"], (
            "model.selection_trace must be present when trace is active"
        )
        st = payload["model"]["selection_trace"]
        assert st["schema"] == "gptme.model-attestation/v0"
        assert st["selection"]["requested_model"] == "anthropic/claude-sonnet-4-6"
        assert st["selection"]["transport_provider"] == "anthropic"
        assert st["identity"]["attestation_level"] == "selection_only"

    def test_attestation_omits_selection_trace_when_none(self) -> None:
        payload = _build_payload(None)

        assert "selection_trace" not in payload["model"], (
            "model.selection_trace must be absent when no trace is active"
        )
        assert "id" in payload["model"]
        assert "provider" in payload["model"]

    def test_attestation_model_block_backward_compat(self) -> None:
        """Legacy fields (id, provider) are always present regardless of trace."""
        for trace in [make_trace(), None]:
            payload = _build_payload(trace)
            assert "id" in payload["model"]
            assert "provider" in payload["model"]
