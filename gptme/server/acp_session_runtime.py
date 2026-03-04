"""ACP-backed session runtime for gptme server.

This module provides a small lifecycle wrapper around :class:`gptme.acp.client.GptmeAcpClient`
for running one ACP subprocess per server-side conversation/session.

It is intentionally decoupled from the current V2 SSE/tool-confirmation loop so it can be
introduced incrementally.
"""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from gptme.acp.client import GptmeAcpClient

logger = logging.getLogger(__name__)


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

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def start(self) -> None:
        """Start ACP subprocess and create ACP session if not already started."""
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

    async def prompt(self, message: str) -> tuple[str, Any]:
        """Send prompt to ACP session and return extracted text + raw response."""
        if self._client is None or self._session_id is None:
            await self.start()

        if self._client is None or self._session_id is None:
            raise RuntimeError("ACP runtime failed to initialize")

        resp = await self._client.prompt(self._session_id, message)
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
        """Terminate the ACP subprocess synchronously.

        Safer than ``close()`` in atexit handlers where the asyncio event loop
        may already be partially torn down.  Falls back to SIGKILL if SIGTERM
        does not succeed within *timeout* seconds.
        """
        if self._client is None:
            return
        proc = getattr(self._client, "_process", None)
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=1.0)  # reap zombie after SIGKILL
            except Exception:
                pass
        finally:
            # Clear references so the runtime is left in a consistent state.
            # Mirrors what close() does asynchronously; prevents accidental reuse.
            self._client = None
            self._session_id = None

    async def close(self) -> None:
        """Close ACP subprocess/session. Safe to call multiple times."""
        if self._client is None:
            return

        await self._client.__aexit__(None, None, None)
        self._client = None
        self._session_id = None

    async def __aenter__(self) -> AcpSessionRuntime:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()
