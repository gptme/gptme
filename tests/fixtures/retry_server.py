"""Opt-in web UI Retry integration server; real persistence, no paid inference.

Run from the repository with server extras installed:
  GPTME_RETRY_TEST_ROOT=/tmp/gptme-retry-test uv run --extra server python3 tests/fixtures/retry_server.py
Then from webui/:
  GPTME_RETRY_TEST_ROOT=/tmp/gptme-retry-test \
  GPTME_RETRY_TEST_SERVER=http://127.0.0.1:5791 npm test -- --runInBand \
  src/hooks/__tests__/useConversation.test.tsx -t 'real server'

Only the LLM boundary is replaced. HTTP, sessions, generation error handling,
and LogManager persistence run normally. Bind to loopback; auth is disabled.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch


def main() -> None:
    """Start a loopback-only fixture with an isolated log root."""
    root = Path(os.environ["GPTME_RETRY_TEST_ROOT"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["GPTME_LOGS_HOME"] = str(root / "logs")
    os.environ["GPTME_DISABLE_AUTH"] = "true"

    from gptme.server.app import create_app

    def fake_stream(*args: Any, **kwargs: Any) -> Iterator[str]:
        """Fail or succeed deterministically without calling an LLM provider."""
        if (root / "step_mode.txt").read_text().strip() == "fail":
            raise RuntimeError("simulated generation failure (stub)")
        yield "Stub assistant reply (no paid inference)."

    def fake_chat_complete(*args: Any, **kwargs: Any) -> tuple[str, dict]:
        """Use the controlled boundary for non-streaming and recovery calls."""
        return "".join(fake_stream()), {}

    (root / "step_mode.txt").write_text("fail")
    with (
        patch("gptme.server.session_step._stream", fake_stream),
        patch("gptme.server.session_step._chat_complete", fake_chat_complete),
    ):
        create_app().run(host="127.0.0.1", port=5791, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
