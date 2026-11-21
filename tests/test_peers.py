"""Real durable queue tests for local peer messages."""

import os
from collections.abc import Generator
from pathlib import Path
from uuid import UUID

import pytest

from gptme.peers import list_peers, send_peer_message
from gptme.prompt_queue import (
    drain_peer_prompts,
    drain_prompt_queue,
    drain_steer_prompts,
    get_prompt_queue_path,
    queue_prompt,
    requeue_prompts,
)


@pytest.fixture
def conversations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    sender, target = tmp_path / "sender", tmp_path / "target"
    sender.mkdir()
    target.mkdir()
    return sender, target


@pytest.mark.parametrize("steer", [True, False])
def test_peer_roundtrip(conversations: tuple[Path, Path], steer: bool) -> None:
    sender, target = conversations
    nonce = send_peer_message(
        "target", "ready", sender_logdir=sender, branch="review", steer=steer
    )
    assert str(UUID(nonce)) == nonce
    assert drain_prompt_queue(target) == []
    assert drain_steer_prompts(target) == []
    assert drain_peer_prompts(target) == []
    (message,) = drain_peer_prompts(target, branch="review")
    assert "not a human instruction" in message.content
    assert "not authenticated" in message.content
    assert "'sender'" in message.content
    assert message.metadata is not None
    assert message.metadata["peer_sender"] == "sender"
    assert message.metadata["peer_sender_pid"] == os.getpid()
    assert message.metadata["peer_message_id"] == nonce
    assert message.to_dict()["metadata"] == message.metadata
    assert requeue_prompts(target, [message]) == 1
    assert drain_prompt_queue(target, branch="review") == []
    (again,) = drain_peer_prompts(target, branch="review")
    assert again.metadata == message.metadata
    assert again.content == message.content
    assert drain_peer_prompts(target, branch="review") == []


def test_branch_and_peer_filters(conversations: tuple[Path, Path]) -> None:
    sender, target = conversations
    queue_prompt(target, "/command")
    queue_prompt(target, "skill", steer=True, skill_invocation_id="skill")
    queue_prompt(target, "other", branch="other")
    queue_prompt(target, "other steer", branch="other", steer=True)
    send_peer_message("target", "hi", sender_logdir=sender)
    assert len(drain_peer_prompts(target)) == 1
    assert [m.content for m in drain_prompt_queue(target)] == ["/command"]
    (skill,) = drain_steer_prompts(target)
    assert skill.metadata == {"steer": True, "skill_invocation_id": "skill"}
    assert [m.content for m in drain_prompt_queue(target, branch="other")] == ["other"]
    (steer,) = drain_steer_prompts(target, branch="other")
    requeue_prompts(target, [steer])
    assert drain_steer_prompts(target) == []
    assert len(drain_steer_prompts(target, branch="other")) == 1


@pytest.mark.parametrize(
    "name", ["", ".", "..", "../target", "/target", "a/b", "a\\b", "a\n"]
)
def test_invalid_id(conversations: tuple[Path, Path], name: str) -> None:
    sender, target = conversations
    with pytest.raises(ValueError, match="conversation|Peer message"):
        send_peer_message(name, "no", sender_logdir=sender)
    assert not get_prompt_queue_path(target).exists()


@pytest.mark.parametrize("text", ["", " ", "a" * 16385, "é" * 8193])
def test_payload_limit(conversations: tuple[Path, Path], text: str) -> None:
    sender, target = conversations
    with pytest.raises(ValueError, match="conversation|Peer message"):
        send_peer_message("target", text, sender_logdir=sender)
    assert not get_prompt_queue_path(target).exists()


def test_self_path_and_size(conversations: tuple[Path, Path]) -> None:
    sender, target = conversations
    with pytest.raises(ValueError, match="same conversation"):
        send_peer_message("sender", "no", sender_logdir=sender)
    with pytest.raises(FileNotFoundError):
        send_peer_message("missing", "no", sender_logdir=sender)
    with pytest.raises(ValueError, match="conversation|Peer message"):
        send_peer_message("target", "no", branch="../escape", sender_logdir=sender)
    alias = target.parent / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="direct conversation"):
        send_peer_message("alias", "no", sender_logdir=sender)
    send_peer_message("target", "é" * 8192, sender_logdir=sender)
    assert len(drain_peer_prompts(target)) == 1


def test_raw_regular_peer_not_consumed(conversations: tuple[Path, Path]) -> None:
    _, target = conversations
    queue_prompt(target, "raw peer", peer_sender="sender", peer_message_id="nonce")
    assert drain_prompt_queue(target) == []
    assert len(drain_peer_prompts(target)) == 1


def test_discovery_stale_pid(
    conversations: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from gptme.hooks.workspace_agents import AgentInfo

    sender, _ = conversations
    (sender / ".lock").write_text(str(os.getpid()))
    agent = AgentInfo(pid=os.getpid(), runtime="gptme", cwd=str(sender))
    monkeypatch.setattr(
        "gptme.hooks.workspace_agents.scan_agents", lambda **kwargs: [agent]
    )
    assert list_peers() == []


def test_same_workspace_processes_not_deduplicated(
    conversations: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from gptme.hooks import workspace_agents as discovery
    from gptme.logmanager import LogManager

    sender, target = conversations
    pids = [os.getpid() + 10000, os.getpid() + 10001]
    cwd = str(sender.parent)
    monkeypatch.setattr(
        discovery,
        "_get_all_cmdlines",
        lambda: {pid: ["gptme", "-m", "openai/test"] for pid in pids},
    )
    monkeypatch.setattr(
        discovery, "_get_process_cwds", lambda _: dict.fromkeys(pids, cwd)
    )
    monkeypatch.setattr(discovery, "_get_git_branch", lambda _: "shared")
    monkeypatch.setattr(discovery, "_get_process_timing", lambda _: (1, 1.0, "S"))
    monkeypatch.setattr(discovery, "_get_process_memory_mb", lambda _: 1.0)
    assert [a.pid for a in discovery.scan_agents(workspace=cwd)] == [pids[1]]
    assert [
        a.pid for a in discovery.scan_agents(workspace=cwd, deduplicate=False)
    ] == pids
    # Real held lifetime locks: PID contents alone must never count as proof.
    with (
        (sender / ".lock").open("w+") as first,
        (target / ".lock").open("w+") as second,
    ):
        for fd, pid in zip((first, second), pids, strict=True):
            fd.write(str(pid))
            fd.flush()
            LogManager._platform_lock(fd)
        try:
            peers = list_peers(workspace=cwd)
            assert [peer["pid"] for peer in peers] == pids
            assert [peer["conversation_id"] for peer in peers] == ["sender", "target"]
            assert all(peer["addressable"] is True for peer in peers)
        finally:
            LogManager._platform_unlock(first)
            LogManager._platform_unlock(second)


@pytest.fixture
def external_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    """A separate process holds a real conversation lock outside our logs root."""
    import subprocess
    import sys

    from gptme.hooks.workspace_agents import AgentInfo

    if not Path("/proc/self/fd").is_dir():
        pytest.skip("Cross-root process FD discovery requires procfs")
    target = tmp_path / "other-root" / "external"
    target.mkdir(parents=True)
    (target / "conversation.jsonl").touch()
    code = """
import os, sys
from pathlib import Path
from gptme.logmanager import LogManager
with (Path(sys.argv[1]) / '.lock').open('w+') as fd:
    fd.write(str(os.getpid()))
    fd.flush()
    LogManager._platform_lock(fd)
    print('ready', flush=True)
    sys.stdin.read()
"""
    with subprocess.Popen(
        [sys.executable, "-c", code, str(target)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "ready"
            agent = AgentInfo(pid=process.pid, runtime="gptme", cwd=str(tmp_path))
            monkeypatch.setattr(
                "gptme.hooks.workspace_agents.scan_agents", lambda **kwargs: [agent]
            )
            yield target
        finally:
            process.terminate()
            process.wait(timeout=10)


@pytest.mark.parametrize("steer", [True, False])
def test_cross_root_discovery_and_name_delivery(
    conversations: tuple[Path, Path], external_peer: Path, steer: bool
) -> None:
    sender, _ = conversations
    peers = list_peers()
    assert len(peers) == 1
    assert peers[0]["log_dir"] == str(external_peer)
    assert peers[0]["conversation_id"] == "external"
    assert peers[0]["addressable"] is True
    nonce = send_peer_message(
        "external", "cross-root", sender_logdir=sender, branch="review", steer=steer
    )
    assert drain_peer_prompts(external_peer) == []
    (message,) = drain_peer_prompts(external_peer, branch="review")
    assert message.metadata is not None
    assert message.metadata["peer_message_id"] == nonce


def test_cross_root_absolute_delivery(conversations: tuple[Path, Path]) -> None:
    sender, _ = conversations
    target = sender.parent.parent / "separate-root" / "sender"
    target.mkdir(parents=True)
    (target / "conversation.jsonl").touch()
    (sender / "conversation.jsonl").touch()
    # Identical basenames in distinct roots are not the same conversation.
    nonce = send_peer_message(str(target), "explicit path", sender_logdir=sender)
    (message,) = drain_peer_prompts(target)
    assert message.metadata is not None
    assert message.metadata["peer_message_id"] == nonce
    with pytest.raises(ValueError, match="same conversation"):
        send_peer_message(str(sender), "self", sender_logdir=sender)


@pytest.mark.parametrize(
    "kind", ["directory", "symlink", "parent-symlink", "traversal", "log-symlink"]
)
def test_explicit_target_validation(
    conversations: tuple[Path, Path], kind: str
) -> None:
    sender, target = conversations
    (target / "conversation.jsonl").touch()
    if kind == "directory":
        path = sender.parent / "not-a-conversation"
        path.mkdir()
    elif kind == "symlink":
        path = sender.parent / "alias"
        path.symlink_to(target, target_is_directory=True)
    elif kind == "parent-symlink":
        alias = sender.parent / "alias"
        alias.symlink_to(sender.parent, target_is_directory=True)
        path = alias / "target"
    elif kind == "traversal":
        path = target / ".." / "target"
    else:
        path = sender.parent / "log-alias"
        path.mkdir()
        (path / "conversation.jsonl").symlink_to(target / "conversation.jsonl")
    with pytest.raises(ValueError, match="conversation"):
        send_peer_message(str(path), "no", sender_logdir=sender)
    assert not get_prompt_queue_path(target).exists()


def test_ambiguous_external_name_and_local_precedence(
    conversations: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender, target = conversations
    peers = [
        {
            "conversation_id": "duplicate",
            "log_dir": str(sender.parent / root / "duplicate"),
            "addressable": True,
        }
        for root in ("run-a", "run-b")
    ]
    monkeypatch.setattr("gptme.peers.list_peers", lambda: peers)
    with pytest.raises(ValueError, match="Ambiguous"):
        send_peer_message("duplicate", "no", sender_logdir=sender)
    for peer in peers:
        peer["conversation_id"] = "target"
    send_peer_message("target", "local wins", sender_logdir=sender)
    assert len(drain_peer_prompts(target)) == 1


def test_proc_fd_open_but_unlocked_is_not_a_peer(
    conversations: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gptme.hooks.workspace_agents import AgentInfo

    target = tmp_path / "external-root" / "unlocked"
    target.mkdir(parents=True)
    agent = AgentInfo(pid=os.getpid(), runtime="gptme", cwd=str(tmp_path))
    monkeypatch.setattr(
        "gptme.hooks.workspace_agents.scan_agents", lambda **kwargs: [agent]
    )
    with (target / ".lock").open("w+") as fd:
        fd.write(str(os.getpid()))
        fd.flush()
        assert list_peers() == []


def test_local_discovery_survives_denied_procfs(
    conversations: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from gptme.hooks.workspace_agents import AgentInfo
    from gptme.logmanager import LogManager

    _, target = conversations
    pid = os.getpid()
    agent = AgentInfo(pid=pid, runtime="gptme", cwd=str(target.parent))
    monkeypatch.setattr(
        "gptme.hooks.workspace_agents.scan_agents", lambda **kwargs: [agent]
    )
    original_iterdir = Path.iterdir

    def iterdir(path: Path):
        if str(path).startswith("/proc/"):
            raise PermissionError("procfs denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    with (target / ".lock").open("w+") as fd:
        fd.write(str(pid))
        fd.flush()
        LogManager._platform_lock(fd)
        try:
            peers = list_peers()
            assert [peer["conversation_id"] for peer in peers] == ["target"]
        finally:
            LogManager._platform_unlock(fd)
