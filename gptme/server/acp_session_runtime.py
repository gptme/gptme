"""ACP-backed session runtime for gptme server.

This module provides a small lifecycle wrapper around :class:`gptme.acp.client.GptmeAcpClient`
for running one ACP subprocess per server-side conversation/session.

It is intentionally decoupled from the current V2 SSE/tool-confirmation loop so it can be
introduced incrementally.
"""

from __future__ import annotations

import logging
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
    ) -> None:
        self.workspace = workspace
        self.command = command
        self.extra_args = extra_args or []
        self.env = env
        self.auto_confirm = auto_confirm

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
        )
        await client.__aenter__()
        try:
            session_id = await client.new_session(cwd=self.workspace)
        except Exception:
            await client.__aexit__(None, None, None)
            raise
        self._client = client
        self._session_id = session_id
        logger.debug(
            "Started ACP runtime (command=%s, workspace=%s, session_id=%s)",
            self.command,
            self.workspace,
            self._session_id,
        )

    async def prompt(self, message: str) -> tuple[str, Any]:
        """Send prompt to ACP session and return extracted text + raw response."""
        if self._client is None or self._session_id is None:
            await self.start()

        if self._client is None or self._session_id is None:
            raise RuntimeError("ACP runtime failed to initialize")

        resp = await self._client.prompt(self._session_id, message)
        text = extract_text_from_prompt_response(resp)
        return text, resp

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
