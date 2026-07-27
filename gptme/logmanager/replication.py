"""Optional S3-compatible replication for session event logs.

Phase 2 of the session event log durability feature.  Off by default;
configure via ``[session_event_log_replication]`` in ``config.toml``.

Architecture:

- ``ReplicationBackend`` — protocol (interface) any backend must satisfy.
- ``FakeBackend`` — in-process fake for tests.
- ``S3Backend`` — uploads to an S3-compatible store via lazy ``boto3`` import.
- ``ReplicationWorker`` — process-local background thread; debounces writes
  and retries on transient failure.  Wired into ``LogManager._write_event_log``
  when replication is enabled.

Remote recovery:

- ``recover_from_remote(conv_id, backend, logdir_fn)`` downloads the remote
  ``events.jsonl`` into a temp dir and delegates to
  ``eventlog.recover_messages()`` for message reconstruction.
"""

from __future__ import annotations

import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# ── backend protocol ─────────────────────────────────────────────────────────


@runtime_checkable
class ReplicationBackend(Protocol):
    """Interface a replication backend must satisfy."""

    def upload(self, key: str, local_path: Path) -> None:
        """Upload *local_path* under *key* in the remote store.

        Must be idempotent (a retry replaces the same object).
        Raise on non-transient error; the caller handles retries.
        """
        ...

    def download(self, key: str, dest: Path) -> bool:
        """Download the object at *key* to *dest*.

        Returns ``True`` on success, ``False`` if the object does not exist.
        Raises on non-transient errors.
        """
        ...


# ── fake backend (for tests) ─────────────────────────────────────────────────


class FakeBackend:
    """In-process fake backend for unit tests."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self.upload_calls: list[tuple[str, Path]] = []
        self.download_calls: list[tuple[str, Path]] = []
        self.fail_upload: bool = False

    def upload(self, key: str, local_path: Path) -> None:
        self.upload_calls.append((key, local_path))
        if self.fail_upload:
            raise OSError("simulated upload failure")
        self._store[key] = local_path.read_bytes()

    def download(self, key: str, dest: Path) -> bool:
        self.download_calls.append((key, dest))
        if key not in self._store:
            return False
        dest.write_bytes(self._store[key])
        return True


# ── S3 backend ───────────────────────────────────────────────────────────────


class S3Backend:
    """S3-compatible backend using a lazy ``boto3`` import.

    Credential resolution follows the standard AWS provider chain:
    environment variables, shared credentials file, instance metadata, etc.
    Do not store credentials in gptme config.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "events/",
        endpoint_url: str | None = None,
        region: str | None = None,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.rstrip("/")
        self._endpoint_url = endpoint_url
        self._region = region
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3 is required for S3 replication. "
                    "Install it with: pip install boto3"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
            if self._region:
                kwargs["region_name"] = self._region
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _object_key(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    def upload(self, key: str, local_path: Path) -> None:
        client = self._get_client()
        object_key = self._object_key(key)
        logger.debug("Uploading event log to s3://%s/%s", self._bucket, object_key)
        client.upload_file(str(local_path), self._bucket, object_key)

    def download(self, key: str, dest: Path) -> bool:
        client = self._get_client()
        object_key = self._object_key(key)
        try:
            client.download_file(self._bucket, object_key, str(dest))
            return True
        except client.exceptions.ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code in ("404", "NoSuchKey"):
                return False
            raise


# ── object key helpers ────────────────────────────────────────────────────────


def _event_log_key(conversation_id: str) -> str:
    """Remote object key for a conversation's event log."""
    return f"{conversation_id}/events.jsonl"


# ── background replication worker ─────────────────────────────────────────────


class ReplicationWorker:
    """Process-local background worker for best-effort event log replication.

    Debounces repeated writes for the same logdir, then uploads the full
    ``events.jsonl`` file.  Upload failures log a warning and retry with
    capped exponential backoff.

    The worker does **not** block the main session write path.
    """

    _DEFAULT_DEBOUNCE_S: float = 0.5  # seconds
    _MAX_RETRIES: int = 3
    _DEFAULT_RETRY_BACKOFF: float = 2.0  # seconds (doubles each retry)

    def __init__(
        self,
        backend: ReplicationBackend,
        debounce_s: float = _DEFAULT_DEBOUNCE_S,
        max_retries: int = _MAX_RETRIES,
        retry_backoff: float = _DEFAULT_RETRY_BACKOFF,
    ) -> None:
        self._backend = backend
        self._debounce_s = debounce_s
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

        self._pending: dict[str, Path] = {}  # logdir_key → logdir Path
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._stopped = False

        self._thread = threading.Thread(
            target=self._run,
            name="gptme-replication-worker",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, logdir: Path, conversation_id: str) -> None:
        """Schedule *logdir* for replication.  Repeated calls are debounced."""
        key = conversation_id
        with self._lock:
            self._pending[key] = logdir
        self._event.set()

    def stop(self, timeout: float = 5.0) -> None:
        """Request shutdown and wait for the worker thread to exit."""
        self._stopped = True
        self._event.set()
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            self._event.wait()
            self._event.clear()
            # Debounce: sleep only when not stopping so we still flush on exit
            if not self._stopped:
                time.sleep(self._debounce_s)
            self._flush()
            if self._stopped:
                break

    def _flush(self) -> None:
        with self._lock:
            pending = dict(self._pending)
            self._pending.clear()

        for conv_id, logdir in pending.items():
            self._upload_with_retry(conv_id, logdir)

    def _upload_with_retry(self, conversation_id: str, logdir: Path) -> None:
        from . import eventlog

        local_path = logdir / eventlog.EVENT_LOG_NAME
        if not local_path.exists():
            return

        key = _event_log_key(conversation_id)
        delay = self._retry_backoff
        for attempt in range(1, self._max_retries + 1):
            try:
                self._backend.upload(key, local_path)
                logger.debug(
                    "Replicated event log for %s (attempt %d)", conversation_id, attempt
                )
                return
            except Exception as exc:
                if attempt == self._max_retries:
                    logger.warning(
                        "Event log replication failed for %s after %d attempts: %s",
                        conversation_id,
                        self._max_retries,
                        exc,
                    )
                    return
                logger.debug(
                    "Replication attempt %d for %s failed (%s); retrying in %.1fs",
                    attempt,
                    conversation_id,
                    exc,
                    delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 30.0)


# ── process-level singleton ───────────────────────────────────────────────────

_worker: ReplicationWorker | None = None
_worker_lock = threading.Lock()


def get_worker(
    backend: ReplicationBackend, debounce_ms: int = 500
) -> ReplicationWorker:
    """Return the process-level ReplicationWorker, creating it on first call."""
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = ReplicationWorker(backend, debounce_s=debounce_ms / 1000.0)
    return _worker


def reset_worker() -> None:
    """Stop and discard the process-level worker.  Used in tests only."""
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.stop()
            _worker = None


# ── remote recovery ───────────────────────────────────────────────────────────


def recover_from_remote(
    conversation_id: str,
    backend: ReplicationBackend,
) -> list[dict[str, Any]] | None:
    """Attempt to recover messages from the remote event log replica.

    Downloads the remote ``events.jsonl`` into a temp directory, then calls
    ``eventlog.recover_messages()`` to reconstruct the message list.

    Returns the recovered message dicts, or ``None`` if the remote object does
    not exist or recovery finds no events.
    """
    from . import eventlog

    key = _event_log_key(conversation_id)
    with tempfile.TemporaryDirectory(prefix="gptme-recovery-") as tmp:
        tmp_path = Path(tmp)
        dest = tmp_path / eventlog.EVENT_LOG_NAME
        try:
            found = backend.download(key, dest)
        except Exception as exc:
            logger.warning(
                "Remote recovery download failed for %s: %s", conversation_id, exc
            )
            return None

        if not found:
            logger.debug("No remote event log found for %s", conversation_id)
            return None

        messages = eventlog.recover_messages(tmp_path)
        if messages is None:
            logger.warning(
                "Remote event log for %s contained no events", conversation_id
            )
            return None

        logger.info(
            "Remote recovery: reconstructed %d messages for %s",
            len(messages),
            conversation_id,
        )
        return messages
