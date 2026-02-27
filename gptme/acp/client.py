"""ACP Client implementation for gptme.

This module allows gptme to act as an ACP **client**, spawning other
ACP-compatible agents (including gptme itself via ``gptme-acp``) as
isolated subprocesses.

Primary use-cases
-----------------
1. **Server-side session isolation** – the gptme HTTP server can spawn one
   ``gptme-acp`` process per session so that each session has its own working
   directory and process state.  This avoids the process-wide ``os.chdir``
   hack currently in ``api_v2_sessions.py`` (tracked in issue #1486).

2. **Multi-harness subagents** – the subagent tool can optionally talk to any
   ACP-compatible harness (Claude Code, Cursor, …) instead of always forking
   gptme itself.

Typical usage
-------------
::

    async with GptmeAcpClient(workspace=Path("/project")) as client:
        session_id = await client.new_session()
        result = await client.prompt(session_id, "summarise this codebase")
        print(result.stop_reason)  # PromptResponse.stop_reason
"""

from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

logger = logging.getLogger(__name__)

# Lazy ACP imports — keep the module importable even without the optional dep.


def _check_acp() -> None:
    """Raise RuntimeError if the ``acp`` package is not installed."""
    try:
        import acp  # noqa: F401  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "The 'acp' package is required for ACP client support. "
            "Install with: pip install 'gptme[acp]'"
        ) from exc


# ---------------------------------------------------------------------------
# Minimal Client implementation
# ---------------------------------------------------------------------------


class _MinimalClient:
    """Minimal ACP Client callback handler.

    Satisfies the ``Client`` protocol required by ``ClientSideConnection``.
    Subclass or compose this to intercept session updates (e.g. to stream
    them over SSE in the gptme-server).
    """

    def __init__(
        self,
        on_update: Callable[[str, Any], None] | None = None,
        auto_confirm: bool = True,
    ) -> None:
        """
        Parameters
        ----------
        on_update:
            Optional callback invoked for every ``session_update`` notification
            from the agent.  Receives ``(session_id, update)`` where *update*
            is the raw ACP update object.
        auto_confirm:
            When ``True`` (default) all permission requests are auto-approved
            with an "allow_once" response.  Set to ``False`` to raise instead.
        """
        self._on_update = on_update
        self._auto_confirm = auto_confirm

    # -- ACP Client protocol ------------------------------------------------

    async def request_permission(
        self,
        options: list[Any],
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> Any:
        """Handle a permission request from the agent."""
        from acp.schema import (  # type: ignore[import-not-found]
            AllowedOutcome,
            DeniedOutcome,
            RequestPermissionResponse,
        )

        if not self._auto_confirm:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

        # Pick the first "allow" option offered by the agent.
        allow_option = next(
            (o for o in options if "allow" in str(getattr(o, "kind", "")).lower()),
            None,
        )
        if allow_option is not None:
            option_id: str = getattr(allow_option, "option_id", "") or str(allow_option)
        elif options:
            # Fallback: pick the first option whatever it is
            option_id = getattr(options[0], "option_id", "") or str(options[0])
        else:
            option_id = "allow_once"

        logger.debug(
            "ACP permission request for session %s: auto-approving (option=%s)",
            session_id,
            option_id,
        )
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=option_id)
        )

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        """Receive a streaming update from the agent."""
        logger.debug("ACP session_update [%s]: %r", session_id, update)
        if self._on_update is not None:
            self._on_update(session_id, update)

    async def write_text_file(
        self,
        content: str,
        path: str,
        session_id: str,
        **kwargs: Any,
    ) -> Any:
        """Write a text file on behalf of the agent (pass-through)."""
        from acp.schema import WriteTextFileResponse  # type: ignore[import-not-found]

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return WriteTextFileResponse()

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Read a text file on behalf of the agent (pass-through)."""
        from acp.schema import ReadTextFileResponse  # type: ignore[import-not-found]

        target = Path(path)
        if not target.exists():
            return ReadTextFileResponse(content="")
        text = target.read_text()
        if line is not None:
            lines = text.splitlines()
            start = line - 1
            if limit is not None:
                lines = lines[start : start + limit]
            else:
                lines = lines[start:]
            text = "\n".join(lines)
        elif limit is not None:
            text = "\n".join(text.splitlines()[:limit])
        return ReadTextFileResponse(content=text)

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        **kwargs: Any,
    ) -> Any:
        """Create a terminal (stub — not implemented in base client)."""
        from acp.schema import CreateTerminalResponse  # type: ignore[import-not-found]

        logger.warning(
            "ACP create_terminal called but not implemented in _MinimalClient; "
            "returning stub response."
        )
        return CreateTerminalResponse(terminal_id="stub-terminal")


# ---------------------------------------------------------------------------
# GptmeAcpClient
# ---------------------------------------------------------------------------


class GptmeAcpClient:
    """High-level ACP client for spawning gptme (or any ACP agent) as a subprocess.

    This is an async context manager.  On entry it spawns the agent process
    and performs ACP ``initialize``.  On exit it closes the connection and
    waits for the subprocess to finish.

    Parameters
    ----------
    workspace:
        Working directory for the spawned agent process.  Defaults to the
        current directory.
    command:
        Command to invoke the ACP agent.  Defaults to ``gptme-acp`` (gptme
        running in ACP server mode).  Can be any ACP-compatible harness.
    extra_args:
        Additional CLI arguments passed after *command*.
    env:
        Optional environment overrides for the subprocess.
    on_update:
        Optional callback forwarded to ``_MinimalClient.on_update``.
    auto_confirm:
        Forward to ``_MinimalClient.auto_confirm`` (default ``True``).
    client_factory:
        Advanced: supply a custom ``Client`` implementation instead of the
        built-in ``_MinimalClient``.
    """

    def __init__(
        self,
        workspace: Path | str | None = None,
        command: str = "gptme-acp",
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        on_update: Callable[[str, Any], None] | None = None,
        auto_confirm: bool = True,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.workspace = Path(workspace) if workspace else Path.cwd()
        self.command = command
        self.extra_args = extra_args or []
        self.env = env
        self._on_update = on_update
        self._auto_confirm = auto_confirm
        self._client_factory = client_factory

        # Set by __aenter__
        self._conn: Any = None
        self._process: Any = None
        self._ctx: Any = None

    # -- context manager ----------------------------------------------------

    async def __aenter__(self) -> GptmeAcpClient:
        _check_acp()
        from acp import PROTOCOL_VERSION  # type: ignore[import-not-found]
        from acp.stdio import spawn_agent_process  # type: ignore[import-not-found]

        if not shutil.which(self.command):
            raise FileNotFoundError(
                f"ACP agent command not found: {self.command!r}. "
                "Install gptme with ACP support: pip install 'gptme[acp]'"
            )

        client = (
            self._client_factory()
            if self._client_factory
            else _MinimalClient(
                on_update=self._on_update,
                auto_confirm=self._auto_confirm,
            )
        )

        self._ctx = spawn_agent_process(
            client,  # type: ignore[arg-type]
            self.command,
            *self.extra_args,
            cwd=self.workspace,
            env=self.env,
        )
        self._conn, self._process = await self._ctx.__aenter__()

        # Handshake
        await self._conn.initialize(protocol_version=PROTOCOL_VERSION)
        logger.debug(
            "ACP client connected to %s (pid=%s, workspace=%s)",
            self.command,
            getattr(self._process, "pid", "?"),
            self.workspace,
        )
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._ctx is not None:
            await self._ctx.__aexit__(*exc_info)
        self._conn = None
        self._process = None
        self._ctx = None

    # -- public API ---------------------------------------------------------

    async def new_session(
        self,
        cwd: str | Path | None = None,
        mcp_servers: list[Any] | None = None,
    ) -> str:
        """Create a new ACP session and return its session_id.

        Parameters
        ----------
        cwd:
            Working directory for the session (defaults to ``self.workspace``).
        mcp_servers:
            Optional list of MCP server configs to pass to the agent.
        """
        if self._conn is None:
            raise RuntimeError(
                "GptmeAcpClient is not connected; use as async context manager"
            )
        cwd = str(cwd or self.workspace)
        resp = await self._conn.new_session(
            cwd=cwd,
            mcp_servers=mcp_servers or [],
        )
        logger.debug("ACP new_session → session_id=%s", resp.session_id)
        return resp.session_id

    async def prompt(
        self,
        session_id: str,
        message: str,
    ) -> Any:
        """Send a prompt to the agent and wait for the response.

        Parameters
        ----------
        session_id:
            Session identifier returned by :meth:`new_session`.
        message:
            User message text to send.

        Returns
        -------
        ``acp.schema.PromptResponse`` with a ``stop_reason`` field.
        """
        if self._conn is None:
            raise RuntimeError(
                "GptmeAcpClient is not connected; use as async context manager"
            )
        from acp.schema import TextContentBlock  # type: ignore[import-not-found]

        prompt_content = [TextContentBlock(type="text", text=message)]
        resp = await self._conn.prompt(
            prompt=prompt_content,
            session_id=session_id,
        )
        logger.debug("ACP prompt → stop_reason=%s", getattr(resp, "stop_reason", "?"))
        return resp

    async def run(
        self,
        message: str,
        cwd: str | Path | None = None,
    ) -> Any:
        """Convenience method: create a session, send a prompt, return the result.

        This is a one-shot helper for callers that don't need fine-grained
        session control.
        """
        session_id = await self.new_session(cwd=cwd)
        return await self.prompt(session_id, message)


# ---------------------------------------------------------------------------
# Standalone helper (functional API)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def acp_client(
    workspace: Path | str | None = None,
    command: str = "gptme-acp",
    extra_args: list[str] | None = None,
    env: dict[str, str] | None = None,
    on_update: Callable[[str, Any], None] | None = None,
    auto_confirm: bool = True,
    client_factory: Callable[[], Any] | None = None,
) -> AsyncIterator[GptmeAcpClient]:
    """Async context manager shorthand for ``GptmeAcpClient``.

    Usage::

        async with acp_client(workspace=Path("/project")) as client:
            session_id = await client.new_session()
            result = await client.prompt(session_id, "hello")
    """
    async with GptmeAcpClient(
        workspace=workspace,
        command=command,
        extra_args=extra_args,
        env=env,
        on_update=on_update,
        auto_confirm=auto_confirm,
        client_factory=client_factory,
    ) as c:
        yield c
