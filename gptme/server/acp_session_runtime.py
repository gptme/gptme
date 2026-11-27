"""ACP-backed session runtime for gptme server.

This module provides a small lifecycle wrapper around :class:`gptme.acp.client.GptmeAcpClient`
for running one ACP subprocess per server-side conversation/session.

It is intentionally decoupled from the current V2 SSE/tool-confirmation loop so it can be
introduced incrementally.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from pathlib import Path

from gptme.acp.client import GptmeAcpClient

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def extract_text_from_prompt_response(resp: Any) -> str:
    """Extract text content from an ACP prompt response.

    The ACP response shape can vary slightly between adapters/versions. This helper tries
    common structures conservatively and returns a best-effort text payload.
    """
    # Common shape: resp.output -> list[TextContentBlock]
    output = getattr(resp, "output", None)
    if output is None and isinstance(resp, dict):
        output = resp.get("output")

    if isinstance(output, list):
        chunks: list[str] = []
        for block in output:
            if isinstance(block, dict):
                text = block.get("text")
            else:
                text = getattr(block, "text", None)
            if isinstance(text, str):
                chunks.append(text)
        if chunks:
            return "".join(chunks)

    # Fallbacks for looser adapters
    text = getattr(resp, "text", None)
    if isinstance(text, str):
        return text

    if isinstance(resp, dict):
        txt = resp.get("text")
        if isinstance(txt, str):
            return txt

    return ""


class AcpSessionRuntime:
    """Manage one ACP subprocess-backed session for a server conversation."""

    def __init__(
        self,
        workspace: Path,
        *,
        command: str = "gptme-acp",
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        auto_confirm: bool = True,
        model: str | None = None,
        on_update: Any | None = None,
    ) -> None:
        self.workspace = workspace
        self.command = command
        self.extra_args = extra_args or []
        self.env = env
        self.auto_confirm = auto_confirm
        self.model = model
        self._on_update = on_update

        self._client: GptmeAcpClient | None = None
        self._session_id: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._loop_lock = threading.Lock()
        self._closing = False
        self._close_future: Future[None] | None = None
        self._lifecycle_lock: asyncio.Lock | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def start(self) -> None:
        """Start ACP subprocess and create ACP session if not already started."""
        await self._submit(self._start_on_loop)

    async def _submit(
        self, operation: Callable[[], Coroutine[Any, Any, _T]]
    ) -> _T:
        with self._loop_lock:
            if self._closing:
                raise RuntimeError("ACP runtime is closing")
            loop = self._ensure_loop_unlocked()
            future = asyncio.run_coroutine_threadsafe(operation(), loop)
        return await asyncio.wrap_future(future)

    async def _start_on_loop(self) -> None:
        """Start or reuse the ACP client on the runtime's dedicated loop."""
        async with self._get_lifecycle_lock():
            if self._client is not None and self._session_id is not None:
                return

            client = GptmeAcpClient(
                workspace=self.workspace,
                command=self.command,
                extra_args=self.extra_args,
                env=self.env,
                auto_confirm=self.auto_confirm,
                on_update=self._on_update,
            )
            self._client = client
            try:
                await client.__aenter__()
                session_id = await client.new_session(cwd=self.workspace)
                if self.model:
                    try:
                        await client.set_session_model(
                            session_id=session_id, model_id=self.model
                        )
                    except NotImplementedError:
                        logger.debug(
                            "ACP client does not support set_session_model; "
                            "falling back to ACP-side model resolution"
                        )
                    except Exception:
                        logger.exception(
                            "Failed to set ACP session model %s for session %s",
                            self.model,
                            session_id,
                        )
                        raise
                self._session_id = session_id
            except BaseException as startup_error:
                try:
                    await client.__aexit__(
                        type(startup_error),
                        startup_error,
                        startup_error.__traceback__,
                    )
                except BaseException:
                    logger.exception("Failed to close ACP client after startup failure")
                finally:
                    if self._client is client:
                        self._client = None
                        self._session_id = None
                raise
            logger.debug(
                "Started ACP runtime "
                "(command=%s, workspace=%s, session_id=%s, model=%s)",
                self.command,
                self.workspace,
                self._session_id,
                self.model,
            )

    def _get_lifecycle_lock(self) -> asyncio.Lock:
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        return self._lifecycle_lock

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            return self._ensure_loop_unlocked()

    def _ensure_loop_unlocked(self) -> asyncio.AbstractEventLoop:
        if self._loop_thread is not None and self._loop_thread.is_alive():
            if self._loop is not None and self._loop.is_running():
                return self._loop
            raise RuntimeError("ACP event loop worker is not running")

        self._loop_ready.clear()
        loop = asyncio.new_event_loop()
        self._loop = loop

        def run_loop() -> None:
            asyncio.set_event_loop(loop)
            loop.call_soon(self._loop_ready.set)
            try:
                loop.run_forever()
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                loop.close()

        thread = threading.Thread(
            target=run_loop,
            name=f"gptme-acp-{id(self)}",
            daemon=True,
        )
        self._loop_thread = thread
        thread.start()
        if not self._loop_ready.wait(timeout=5) or not loop.is_running():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
            thread.join(timeout=5)
            if not thread.is_alive():
                self._loop = None
                self._loop_thread = None
                self._loop_ready.clear()
            raise RuntimeError("Failed to initialize ACP event loop")
        return loop

    def _stop_loop(self) -> None:
        with self._loop_lock:
            loop = self._loop
            thread = self._loop_thread
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                logger.debug("ACP event loop stopped before stop could be scheduled")
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                logger.warning("ACP event loop worker did not stop within 5 seconds")
                return
        with self._loop_lock:
            if self._loop is loop and self._loop_thread is thread:
                self._loop = None
                self._loop_thread = None
                self._loop_ready.clear()

    def set_on_update(self, on_update: Any | None) -> None:
        """Set/update callback for ACP session_update notifications."""
        self._on_update = on_update
        if self._client is not None:
            self._client.set_on_update(on_update)

    async def prompt(
        self, message: str, max_tokens: int | None = None
    ) -> tuple[str, Any]:
        """Send prompt to ACP session and return extracted text + raw response."""
        return await self._submit(
            lambda: self._prompt_on_loop(message, max_tokens=max_tokens)
        )

    async def _prompt_on_loop(
        self, message: str, max_tokens: int | None = None
    ) -> tuple[str, Any]:
        if self._client is None or self._session_id is None:
            await self._start_on_loop()

        if self._client is None or self._session_id is None:
            raise RuntimeError(
                "ACP runtime failed to initialize: "
                f"client={'connected' if self._client else 'None'}, "
                f"session_id={self._session_id!r}"
            )

        resp = await self._client.prompt(
            self._session_id, message, max_tokens=max_tokens
        )
        text = extract_text_from_prompt_response(resp)
        return text, resp

    @property
    def process_pid(self) -> int | None:
        """Return the PID of the ACP subprocess, if available."""
        if self._client is None:
            return None
        proc = getattr(self._client, "_process", None)
        if proc is None:
            return None
        return getattr(proc, "pid", None)

    def is_subprocess_alive(self) -> bool:
        """Check if the ACP subprocess is still running.

        Returns False if the subprocess has exited or was never started.
        """
        if self._client is None:
            return False
        proc = getattr(self._client, "_process", None)
        if proc is None:
            return False
        # subprocess.Popen.poll() returns None if still running
        poll = getattr(proc, "poll", None)
        if callable(poll):
            return poll() is None
        # Fallback: check returncode directly
        returncode = getattr(proc, "returncode", None)
        return returncode is None

    def terminate_subprocess_sync(self, timeout: float = 3.0) -> None:
        """Terminate the ACP subprocess and stop its worker loop synchronously."""
        with self._loop_lock:
            self._closing = True
            client = self._client
        proc = getattr(client, "_process", None) if client is not None else None
        try:
            poll = getattr(proc, "poll", None)
            if proc is not None and (not callable(poll) or poll() is None):
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
        except Exception:
            logger.exception("Failed to terminate ACP subprocess")
        finally:
            with self._loop_lock:
                close_future = self._close_future
                loop = self._loop
                if close_future is None:
                    if loop is not None and loop.is_running():
                        close_future = asyncio.run_coroutine_threadsafe(
                            self._close_on_loop(), loop
                        )
                    else:
                        close_future = Future()
                        close_future.set_result(None)
                        self._client = None
                        self._session_id = None
                    self._close_future = close_future
            try:
                close_future.result(timeout=timeout + 2.0)
            except FutureTimeoutError:
                logger.warning("Timed out closing ACP runtime on its event loop")
                close_future.cancel()
            except Exception:
                logger.exception("Failed to close ACP runtime on its event loop")
            finally:
                if loop is None or not loop.is_running():
                    with self._loop_lock:
                        self._client = None
                        self._session_id = None
                self._stop_loop()

    async def close(self) -> None:
        """Close ACP subprocess/session. Safe to call multiple times."""
        with self._loop_lock:
            self._closing = True
            loop = self._loop
            close_future = self._close_future
            if close_future is None:
                if loop is not None and loop.is_running():
                    close_future = asyncio.run_coroutine_threadsafe(
                        self._close_on_loop(), loop
                    )
                else:
                    close_future = Future()
                    close_future.set_result(None)
                self._close_future = close_future
        try:
            if loop is not None and loop.is_running():
                await asyncio.wrap_future(close_future)
            elif self._client is not None:
                self.terminate_subprocess_sync()
            else:
                await asyncio.wrap_future(close_future)
        finally:
            self._stop_loop()

    async def _close_on_loop(self) -> None:
        async with self._get_lifecycle_lock():
            client = self._client
            try:
                if client is not None:
                    await client.__aexit__(None, None, None)
            finally:
                if self._client is client:
                    self._client = None
                    self._session_id = None

    async def __aenter__(self) -> AcpSessionRuntime:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()
