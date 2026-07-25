"""End-to-end smoke test for the ACP agent.

Spawns ``gptme-acp`` as a subprocess, drives it through the ACP protocol
(initialize → session/new → session/prompt → tool call → file edit),
and verifies the file content actually changed on disk.

Uses gptme's own ``GptmeAcpClient`` / ``acp_client()`` helper, which in
turn uses the official ``agent-client-protocol`` library — not a hand-rolled
JSON-RPC client.  This validates that gptme's ACP agent works with the
canonical client implementation.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

pytest.importorskip(
    "acp",
    reason="agent-client-protocol not installed (pip install agent-client-protocol)",
)


@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.asyncio
async def test_acp_agent_e2e_file_edit():
    """Verify the ACP agent can read a file and modify it via a prompt.

    Spawns gptme as an ACP agent, creates a session, sends a prompt asking
    it to edit a file, then verifies the file was actually changed on disk.
    """
    from gptme.acp.client import acp_client

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test file
        test_file = Path(tmpdir) / "hello.txt"
        test_file.write_text("Hello, World!\n")

        env = {**os.environ, "GPTME_LOG_LEVEL": "WARNING"}

        async with acp_client(
            workspace=Path(tmpdir),
            command="gptme-acp",
            env=env,
            auto_confirm=True,
        ) as client:
            # Create a new session
            session_id = await client.new_session(cwd=tmpdir)
            assert session_id is not None
            assert isinstance(session_id, str)
            assert len(session_id) > 0

            # Send a prompt asking to edit the file
            await client.prompt(
                session_id,
                "Read hello.txt and append 'Goodbye!' to it on a new line. "
                "Then read it back to confirm.",
            )

        # Verify the file was actually modified with original content preserved and
        # new line appended (not just that "Goodbye!" appears somewhere).
        content = test_file.read_text()
        assert "Hello, World!" in content, (
            f"Expected original 'Hello, World!' to be preserved in file, got: {content!r}"
        )
        assert "Goodbye!" in content, (
            f"Expected 'Goodbye!' to be appended to file after ACP prompt, got: {content!r}"
        )
        assert content.index("Hello") < content.index("Goodbye"), (
            f"Expected 'Hello, World!' to appear before 'Goodbye!' in: {content!r}"
        )
