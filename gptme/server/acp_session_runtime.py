"""ACP-backed session runtime for gptme server.

This module provides a small lifecycle wrapper around :class:`gptme.acp.client.GptmeAcpClient`
for running one ACP subprocess per server-side conversation/session.

The ACP stdio transport (subprocess pipes, reader tasks and the
``spawn_agent_process`` async context) is bound to the event loop that created
it. Server steps each run in their own short-lived ``asyncio.run`` loop, so the
runtime owns a dedicated, long-lived event loop thread and executes all ACP
client work there. Callers on any loop simply ``await`` the runtime's methods;
the work is marshalled onto the runtime loop and the result is awaited back.

It is intentionally decoupled from the current V2 SSE/tool-confirmation loop so it can be
introduced incrementally.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    import concurrent.futures
    from collections.abc import Awaitable, Callable, Coroutine
    from pathlib import Path

from gptme.acp.client import GptmeAcpClient

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


# How long the runtime loop waits for cancelled tasks and async generators to
# unwind before the loop is closed anyway. A task that swallows
# ``CancelledError`` must not keep the loop thread alive forever.
_SHUTDOWN_GRACE_S = 5.0


def _run_until_complete_bounded(
    loop: asyncio.AbstractEventLoop,
    awaitable: Awaitable[Any],
    timeout: float,
    what: str,
) -> None:
    """Run ``awaitable`` on ``loop`` for at most ``timeout`` seconds.

    ``run_until_complete`` has no timeout of its own, and ``asyncio.wait_for``
    still awaits the cancelled future, so neither bounds a coroutine that
    swallows ``CancelledError``. ``asyncio.wait`` returns on the timeout and
    leaves the task behind.
    """
    task = asyncio.ensure_future(awaitable, loop=loop)
    loop.run_until_complete(asyncio.wait({task}, timeout=timeout))
    if not task.done():
        logger.warning(
            "ACP runtime loop shutdown timed out after %.1fs waiting for %s",
            timeout,
            what,
        )
        task.cancel()
    elif not task.cancelled() and task.exception() is not None:
        # asyncio.wait never raises, so report the failure the caller would
        # otherwise never see.
        logger.warning(
            "ACP runtime loop shutdown failed while waiting for %s",
            what,
            exc_info=task.exception(),
        )


class _LoopCalls:
    """Book-keeping for calls submitted to one runtime loop.

    Once ``closed`` is set no new call is accepted, and every call still
    pending when the loop shuts down is cancelled, so a caller waiting on it
    is released instead of hanging on a future nobody will complete.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.futures: set[concurrent.futures.Future[Any]] = set()
        self.closed = False

    def close_and_cancel(self) -> None:
        with self.lock:
            self.closed = True
            leftovers = list(self.futures)
            self.futures.clear()
        for fut in leftovers:
            fut.cancel()

    def discard(self, fut: concurrent.futures.Future[Any]) -> None:
        with self.lock:
            self.futures.discard(fut)


def _terminate_asyncio_process_sync(proc: Any, timeout: float) -> None:
    """SIGTERM then SIGKILL a process whose event loop is unavailable.

    ``asyncio.subprocess.Process.wait()`` is a coroutine without a timeout, so
    a caller outside the owning loop has to reap by pid instead.
    """
    pid = getattr(proc, "pid", None)

    def _reaped(deadline: float) -> bool:
        if not isinstance(pid, int) or not hasattr(os, "WNOHANG"):
            return getattr(proc, "returncode", None) is not None
        while True:
            try:
                if os.waitpid(pid, os.WNOHANG)[0] != 0:
                    return True
            except ChildProcessError:
                # Not (or no longer) our child: reaped elsewhere.
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    try:
        proc.terminate()
    except ProcessLookupError:
        return
    if _reaped(time.monotonic() + timeout):
        return
    try:
        proc.kill()
    except ProcessLookupError:
        return
    _reaped(time.monotonic() + 1.0)


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

        # Dedicated event loop owning the ACP transport (see module docstring).
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._loop_lock = threading.Lock()
        self._loop_calls: _LoopCalls | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    # -- runtime event loop -------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Return the runtime's event loop, starting its thread if needed."""
        return self._ensure_loop_calls()[0]

    def _ensure_loop_calls(self) -> tuple[asyncio.AbstractEventLoop, _LoopCalls]:
        with self._loop_lock:
            loop = self._loop
            thread = self._loop_thread
            calls = self._loop_calls
            if (
                loop is not None
                and not loop.is_closed()
                and thread is not None
                and thread.is_alive()
                and calls is not None
                and not calls.closed
            ):
                return loop, calls

            if self._client is not None:
                # A live client is bound to the loop that just stopped; running
                # it from a fresh loop is exactly the cross-loop failure this
                # runtime exists to prevent.
                raise ConnectionError(
                    "ACP runtime transport is bound to a stopped loop; "
                    "the runtime must be recreated"
                )
            loop = asyncio.new_event_loop()
            calls = _LoopCalls()
            running = threading.Event()

            def _run_loop() -> None:
                asyncio.set_event_loop(loop)
                loop.call_soon(running.set)
                try:
                    loop.run_forever()
                finally:
                    try:
                        # Cancel in-flight calls so callers awaiting them via
                        # run_coroutine_threadsafe are released, not left hanging.
                        pending = asyncio.all_tasks(loop)
                        for task in pending:
                            task.cancel()
                        if pending:
                            _run_until_complete_bounded(
                                loop,
                                asyncio.gather(*pending, return_exceptions=True),
                                _SHUTDOWN_GRACE_S,
                                "cancelled tasks",
                            )
                        # Finalize the spawn_agent_process async generator (and
                        # any other) on its own loop, not via GC on another one.
                        _run_until_complete_bounded(
                            loop,
                            loop.shutdown_asyncgens(),
                            _SHUTDOWN_GRACE_S,
                            "async generators",
                        )
                    except Exception:
                        logger.debug(
                            "Failed to shut down ACP runtime async generators",
                            exc_info=True,
                        )
                    finally:
                        loop.close()
                        # Release callers whose call was accepted but never
                        # started before the loop stopped.
                        calls.close_and_cancel()

            thread = threading.Thread(
                target=_run_loop, daemon=True, name="acp-runtime-loop"
            )
            thread.start()
            running.wait(timeout=5.0)
            self._loop = loop
            self._loop_thread = thread
            self._loop_calls = calls
            return loop, calls

    def _stop_loop(self, join_timeout: float = 5.0) -> None:
        """Stop the runtime loop thread (no-op if not running)."""
        with self._loop_lock:
            loop = self._loop
            thread = self._loop_thread
            calls = self._loop_calls
            self._loop = None
            self._loop_thread = None
            self._loop_calls = None
        if calls is not None:
            # Reject new work and release accepted callers now, even if a
            # blocked callback prevents the loop thread from finishing. This
            # shares the submission lock, so no future can miss cancellation.
            calls.close_and_cancel()
        if loop is None or loop.is_closed():
            if calls is not None:
                calls.close_and_cancel()
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            # Loop closed concurrently.
            return
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=join_timeout)
        if (thread is None or not thread.is_alive()) and not loop.is_closed():
            # The thread that owned the loop is gone (or never ran), so nobody
            # else will close it; reclaim its selector and callbacks.
            try:
                loop.close()
            except RuntimeError:  # pragma: no cover - loop still running
                logger.debug("ACP runtime loop could not be closed", exc_info=True)

    async def _run_on_loop(
        self,
        func: Callable[..., Coroutine[Any, Any, _T]],
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """Run ``func(*args, **kwargs)`` on the runtime loop and await its result."""
        loop, calls = self._ensure_loop_calls()
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - always called from a coroutine
            current = None
        if current is loop:
            return await func(*args, **kwargs)
        coro = func(*args, **kwargs)
        with calls.lock:
            if calls.closed:
                coro.close()
                raise RuntimeError("ACP runtime is shutting down")
            try:
                fut = asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError:
                coro.close()
                raise
            calls.futures.add(fut)
        fut.add_done_callback(calls.discard)
        try:
            return await asyncio.wrap_future(fut)
        except asyncio.CancelledError:
            # A cancellation aimed at the caller's own task must propagate.
            # A cancellation of the *submitted call* means either the runtime
            # loop shut down underneath it or the call cancelled itself;
            # surface both as errors instead, so callers report a failed step
            # rather than dying with a BaseException that ``except Exception``
            # handlers never see.
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            if calls.closed or not loop.is_running():
                raise ConnectionError(
                    "ACP runtime loop stopped before the call completed"
                ) from None
            raise RuntimeError("ACP call was cancelled before it completed") from None

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Start ACP subprocess and create ACP session if not already started."""
        await self._run_on_loop(self._start_impl)

    async def _start_impl(self) -> None:
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
        await client.__aenter__()
        try:
            session_id = await client.new_session(cwd=self.workspace)
        except Exception:
            await client.__aexit__(None, None, None)
            raise
        self._client = client
        self._session_id = session_id
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
                await client.__aexit__(None, None, None)
                raise
        logger.debug(
            "Started ACP runtime (command=%s, workspace=%s, session_id=%s, model=%s)",
            self.command,
            self.workspace,
            self._session_id,
            self.model,
        )

    def set_on_update(self, on_update: Any | None) -> None:
        """Set/update callback for ACP session_update notifications."""
        self._on_update = on_update
        if self._client is not None:
            self._client.set_on_update(on_update)

    async def prompt(
        self, message: str, max_tokens: int | None = None
    ) -> tuple[str, Any]:
        """Send prompt to ACP session and return extracted text + raw response."""
        return await self._run_on_loop(self._prompt_impl, message, max_tokens)

    async def _prompt_impl(
        self, message: str, max_tokens: int | None
    ) -> tuple[str, Any]:
        if self._client is None or self._session_id is None:
            await self._start_impl()

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

    def terminate_subprocess_sync(
        self, timeout: float = 3.0, *, graceful: bool = True
    ) -> None:
        """Terminate the ACP subprocess synchronously.

        Safer than ``close()`` in atexit handlers where the caller's asyncio
        machinery may already be partially torn down. If the runtime loop is
        still alive, the client is first closed gracefully on that loop; on
        failure (or timeout) the subprocess is signalled directly, falling back
        to SIGKILL if SIGTERM does not succeed within *timeout* seconds.

        Pass ``graceful=False`` after a client exit has already failed: a
        context manager cannot be exited twice, and the second attempt can
        return normally without having cleaned anything up.
        """
        try:
            if self._client is None:
                return
            proc = getattr(self._client, "_process", None)
            if graceful and self._close_on_loop_sync(timeout):
                return
            # The graceful close did not finish: retire the loop before reaping
            # by pid, so its child watcher cannot race the synchronous waitpid
            # over the same child.
            self._stop_loop(join_timeout=timeout)
            if proc is None:
                return
            if not isinstance(proc, subprocess.Popen):
                _terminate_asyncio_process_sync(proc, timeout)
                return
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)  # reap zombie after SIGKILL
                except Exception:
                    logger.debug(
                        "Failed to kill ACP subprocess after SIGTERM timeout",
                        exc_info=True,
                    )
        finally:
            # Clear references so the runtime is left in a consistent state.
            # Mirrors what close() does asynchronously; prevents accidental reuse.
            self._client = None
            self._session_id = None
            self._stop_loop(join_timeout=1.0)

    def _close_on_loop_sync(self, timeout: float) -> bool:
        """Gracefully close the client on the live runtime loop.

        Returns True if the client was closed, False if the caller should fall
        back to signalling the subprocess directly.
        """
        with self._loop_lock:
            loop = self._loop
            thread = self._loop_thread
        if (
            loop is None
            or loop.is_closed()
            or thread is None
            or not thread.is_alive()
            or thread is threading.current_thread()
        ):
            return False
        try:
            fut = asyncio.run_coroutine_threadsafe(self._close_impl(), loop)
            fut.result(timeout=timeout)
            return True
        except (Exception, asyncio.CancelledError):
            # Name CancelledError explicitly: it is a BaseException where
            # ``concurrent.futures.CancelledError`` aliases ``asyncio``'s. A
            # concurrent _stop_loop cancels the scheduled close task, and the
            # caller must still fall back to signalling the subprocess.
            logger.debug(
                "Graceful ACP close on runtime loop failed; terminating subprocess",
                exc_info=True,
            )
            return False

    async def close(self) -> None:
        """Close ACP subprocess/session. Safe to call multiple times."""
        if self._client is not None:
            try:
                await self._run_on_loop(self._close_impl)
            except Exception:
                # The transport could not be closed cleanly (its loop is gone,
                # or __aexit__ raised). Signal the subprocess synchronously so
                # the close never leaves an orphan behind.
                logger.debug(
                    "Graceful ACP close failed; terminating subprocess",
                    exc_info=True,
                )
                self.terminate_subprocess_sync(graceful=False)
            finally:
                self._stop_loop()
        else:
            self._stop_loop()

    async def _close_impl(self) -> None:
        if self._client is None:
            return
        # Keep the handle until __aexit__ succeeds: if the transport is already
        # broken and the close raises, a later terminate_subprocess_sync() must
        # still be able to find and signal the subprocess.
        client = self._client
        await client.__aexit__(None, None, None)
        self._client = None
        self._session_id = None

    async def __aenter__(self) -> AcpSessionRuntime:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()
