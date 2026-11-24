"""The interrupt drain must wait for the interrupted cell's own idle.

Hermetic (no ipykernel): a fake client replays an iopub queue that still holds
an ``idle`` from an earlier request, which ``_exec_silent`` leaves behind
because it reads only the shell channel. Stopping on that stale idle returns
before the SIGINT is handled, so the interrupt lands in the next cell.
"""

import queue

from gptme.tools.python_kernel import KernelBackend


def _status(state: str, parent: str) -> dict:
    return {
        "msg_type": "status",
        "content": {"execution_state": state},
        "parent_header": {"msg_id": parent},
    }


class _FakeClient:
    def __init__(self, msgs: list[dict]) -> None:
        self._q: queue.Queue[dict] = queue.Queue()
        for m in msgs:
            self._q.put(m)

    def get_iopub_msg(self, timeout: float) -> dict:
        return self._q.get(timeout=timeout)

    def remaining(self) -> int:
        return self._q.qsize()


def _backend(client: _FakeClient, last_msg_id: str) -> KernelBackend:
    kb = KernelBackend.__new__(KernelBackend)  # skip the bridge/kernel startup
    kb._kc = client
    kb._last_execute_msg_id = last_msg_id
    return kb


def test_drain_skips_stale_idle_from_earlier_request() -> None:
    stale_idle = _status("idle", "setup-msg")
    interrupt_error = {
        "msg_type": "error",
        "content": {"ename": "KeyboardInterrupt"},
        "parent_header": {"msg_id": "cell-msg"},
    }
    cell_idle = _status("idle", "cell-msg")
    next_msg = _status("busy", "next-cell")
    client = _FakeClient([stale_idle, interrupt_error, cell_idle, next_msg])

    _backend(client, "cell-msg")._drain_until_idle(grace=2.0)

    # Consumed through the interrupted cell's idle, and no further.
    assert client.remaining() == 1


def test_drain_stops_at_grace_without_matching_idle() -> None:
    client = _FakeClient([_status("idle", "other")])
    _backend(client, "cell-msg")._drain_until_idle(grace=0.2)
    assert client.remaining() == 0
