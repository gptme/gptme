"""Native STEP_PRE integration with an offline shared-selector CLI."""

import json
import os
import sys
import time
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from gptme.lessons import selector
from gptme.lessons.parser import Lesson, LessonMetadata
from gptme.message import Message
from gptme.tools import lessons as tool

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX process-tree transport"
)


@pytest.fixture
def hook_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[dict[str, Any], None, None]:
    settings = {
        "GPTME_LESSONS_SELECTOR_GUARDS": "guard",
        "GPTME_LESSONS_SELECTOR_MODEL": "offline-test",
        "GPTME_LESSONS_SELECTOR_MAX_CALLS": "2",
        "GPTME_LESSONS_SELECTOR_TIMEOUT": "1",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GPTME_LESSONS_SELECTOR_")
    }
    monkeypatch.setattr(selector.os, "environ", environment)
    # Hook reads selector authority from the pre-.env snapshot; alias it to the
    # live settings so fake_cli() writes remain visible to the hook.
    monkeypatch.setattr(selector, "_trusted_env", settings)
    config = MagicMock()
    config.get_env.side_effect = lambda key: settings.get(key)
    config.get_env_bool.side_effect = lambda key, default: default
    monkeypatch.setattr(tool, "get_config", lambda: config)
    catalog = [
        Lesson(
            path=tmp_path / f"{name}.md",
            metadata=LessonMetadata(
                keywords=["commit"],
                always_apply=name == "always",
                priority="critical" if name == "critical" else None,
            ),
            title=name,
            description=f"Advice about {name}",
            category="test",
            body=f"Body for {name}",
        )
        for name in ("guard", "always", "critical", "first", "second")
    ]
    index = SimpleNamespace(lessons=catalog)
    monkeypatch.setattr(tool, "_get_lesson_index", lambda: index)
    manager = SimpleNamespace(
        log=SimpleNamespace(messages=[Message("user", "commit changes safely")]),
        logdir=tmp_path / "session",
    )
    tool._session_stats_var.set(None)
    selector._last_intent.set(None)
    yield {
        "settings": settings,
        "manager": manager,
        "catalog": catalog,
        "tmp": tmp_path,
    }
    tool._session_stats_var.set(None)
    selector._last_intent.set(None)


def fake_cli(env: dict[str, Any], body: str) -> None:
    script = env["tmp"] / "selector.py"
    script.write_text(
        "import json, os, subprocess, sys, time\n"
        "request = json.load(sys.stdin)\n"
        f"with open({str(env['tmp'] / 'requests.jsonl')!r}, 'a') as log:\n"
        "    log.write(json.dumps(request) + '\\n')\n" + body + "\n"
    )
    env["settings"]["GPTME_LESSONS_SELECTOR_COMMAND"] = json.dumps(
        [sys.executable, str(script)]
    )


def run_hook(env: dict[str, Any]) -> list[Message]:
    messages = list(tool.auto_include_lessons_hook(env["manager"]))
    assert all(isinstance(message, Message) for message in messages)
    return messages  # type: ignore[return-value]


def emitted_names(messages: list[Message]) -> list[str]:
    return [
        line.removeprefix("## ")
        for message in messages
        for line in message.content.splitlines()
        if line.startswith("## ")
    ]


def recorded_requests(env: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (env["tmp"] / "requests.jsonl").read_text().splitlines()
    ]


def test_disabled_hook_is_exact_deterministic_baseline(
    hook_env: dict[str, Any],
) -> None:
    baseline = run_hook(hook_env)
    assert emitted_names(baseline) == ["guard", "always", "critical", "first", "second"]
    assert not (hook_env["tmp"] / "requests.jsonl").exists()
    tool._session_stats_var.set(None)
    fake_cli(hook_env, "print('malformed output')")
    fallback = run_hook(hook_env)
    assert [message.content for message in fallback] == [
        message.content for message in baseline
    ]


@pytest.mark.parametrize(
    "missing",
    [
        "GPTME_LESSONS_SELECTOR_GUARDS",
        "GPTME_LESSONS_SELECTOR_MODEL",
        "GPTME_LESSONS_SELECTOR_MAX_CALLS",
    ],
)
def test_enablement_requires_guard_model_and_budget(
    hook_env: dict[str, Any], missing: str
) -> None:
    fake_cli(hook_env, "raise AssertionError('must not invoke')")
    hook_env["settings"].pop(missing)
    assert len(emitted_names(run_hook(hook_env))) == 5
    assert not (hook_env["tmp"] / "requests.jsonl").exists()


def test_empty_advisory_selection_preserves_all_guard_kinds(
    hook_env: dict[str, Any],
) -> None:
    fake_cli(hook_env, "print(json.dumps({'version':1, 'status':'empty', 'ids':[]}))")
    assert emitted_names(run_hook(hook_env)) == ["guard", "always", "critical"]
    received = recorded_requests(hook_env)[0]
    assert [Path(candidate["id"]).stem for candidate in received["candidates"]] == [
        "first",
        "second",
    ]
    assert received["runtime"] == "gptme"
    assert received["session_id"] == str(hook_env["manager"].logdir.resolve())
    assert received["max_results"] == 3
    assert received["model"] == "offline-test"
    assert received["max_calls_per_session"] == 2


def test_selected_advisory_still_obeys_holdout_dedup_and_session_cap(
    hook_env: dict[str, Any],
) -> None:
    hook_env["settings"]["GPTME_LESSONS_HOLDOUT"] = "guard,second"
    hook_env["settings"]["GPTME_LESSONS_MAX_SESSION"] = "3"
    hook_env["manager"].log.messages.append(
        Message(
            "system", f"# Relevant Lessons\n*Path: {hook_env['tmp'] / 'critical.md'}*"
        )
    )
    fake_cli(
        hook_env,
        "print(json.dumps({'version':1, 'status':'selected', 'ids':[request['candidates'][0]['id']]}))",
    )
    messages = run_hook(hook_env)
    assert emitted_names(messages) == ["always", "first"]
    received = recorded_requests(hook_env)[0]
    assert [Path(candidate["id"]).stem for candidate in received["candidates"]] == [
        "first"
    ]
    hook_env["manager"].log.messages.extend(messages)
    assert run_hook(hook_env) == []
    assert len(recorded_requests(hook_env)) == 1


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        "[]",
        '{"version":1,"status":"empty","ids":["unexpected"]}',
        '{"version":1,"status":"selected","ids":[]}',
        '{"version":true,"status":"empty","ids":[]}',
        '{"version":1,"status":"selected","ids":["/secret.md"]}',
        '{"version":1,"status":"fallback","ids":[],"reason":"timeout"}',
    ],
)
def test_invalid_or_fallback_response_preserves_full_baseline(
    hook_env: dict[str, Any], response: str
) -> None:
    fake_cli(hook_env, f"print({response!r})")
    assert emitted_names(run_hook(hook_env)) == [
        "guard",
        "always",
        "critical",
        "first",
        "second",
    ]


def test_duplicate_selected_ids_fail_open(hook_env: dict[str, Any]) -> None:
    fake_cli(
        hook_env,
        "print(json.dumps({'version':1, 'status':'selected', 'ids':[request['candidates'][0]['id']]*2}))",
    )
    assert len(emitted_names(run_hook(hook_env))) == 5


def test_tool_turn_keeps_query_and_catalog_stable_after_emission(
    hook_env: dict[str, Any],
) -> None:
    fake_cli(
        hook_env,
        "print(json.dumps({'version':1, 'status':'selected', 'ids':[request['candidates'][0]['id']]}))",
    )
    first = run_hook(hook_env)
    assert emitted_names(first) == ["guard", "always", "critical", "first"]
    hook_env["manager"].log.messages.extend(first)
    hook_env["manager"].log.messages.extend(
        [
            Message("assistant", "```shell\necho unrelated text\n```"),
            Message("system", "unrelated tool output"),
        ]
    )
    assert run_hook(hook_env) == []
    first_request, tool_request = recorded_requests(hook_env)
    assert first_request["event_kind"] == "user_prompt"
    assert tool_request["event_kind"] == "tool"
    assert tool_request["query"] == first_request["query"] == "commit changes safely"
    assert tool_request["context_fingerprint"] == first_request["context_fingerprint"]
    assert tool_request["candidates"] == first_request["candidates"]
    hook_env["manager"].log.messages.append(Message("user", "commit a different fix"))
    run_hook(hook_env)
    assert recorded_requests(hook_env)[-1]["event_kind"] == "user_prompt"


def test_resumed_tool_turn_does_not_start_provider_eligible_event(
    hook_env: dict[str, Any],
) -> None:
    fake_cli(
        hook_env,
        "print(json.dumps({'version':1, 'status':'fallback', 'ids':[], 'reason':'cache_miss'}))",
    )
    hook_env["manager"].log.messages.append(Message("assistant", "a running tool turn"))
    assert len(emitted_names(run_hook(hook_env))) == 5
    assert recorded_requests(hook_env)[0]["event_kind"] == "tool"


def test_guard_policy_changes_fingerprint_input(hook_env: dict[str, Any]) -> None:
    fake_cli(hook_env, "print(json.dumps({'version':1, 'status':'empty', 'ids':[]}))")
    run_hook(hook_env)
    hook_env["settings"]["GPTME_LESSONS_SELECTOR_GUARDS"] = "guard,first"
    run_hook(hook_env)
    first, second = recorded_requests(hook_env)
    assert first["policy_version"] != second["policy_version"]
    assert [Path(candidate["id"]).stem for candidate in second["candidates"]] == [
        "second"
    ]


def test_transport_deadline_kills_cli_and_its_child(hook_env: dict[str, Any]) -> None:
    parent_pid = hook_env["tmp"] / "parent.pid"
    child_pid = hook_env["tmp"] / "child.pid"
    marker = hook_env["tmp"] / "late-write"
    child_code = (
        "import os,time; from pathlib import Path; "
        f"Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        f"time.sleep(3); Path({str(marker)!r}).write_text('escaped')"
    )
    fake_cli(
        hook_env,
        f"open({str(parent_pid)!r}, 'w').write(str(os.getpid()))\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "time.sleep(30)",
    )
    hook_env["settings"]["GPTME_LESSONS_SELECTOR_TIMEOUT"] = "0.1"
    start = time.monotonic()
    assert len(emitted_names(run_hook(hook_env))) == 5
    assert time.monotonic() - start < 5
    with pytest.raises(ProcessLookupError):
        os.kill(int(parent_pid.read_text()), 0)
    # The SIGKILL is sent to the process group, but the grandchild may briefly
    # still be running ('R') before the kernel delivers the signal, then briefly
    # be a zombie ('Z') until PID 1 reaps it. Poll up to 1s for the child to die.
    child_pid_val = int(child_pid.read_text())
    poll_deadline = time.monotonic() + 1.0
    while time.monotonic() < poll_deadline:
        proc_stat = Path(f"/proc/{child_pid_val}/stat")
        if not proc_stat.exists():
            break
        if proc_stat.read_text().split()[2] not in ("R", "S", "D"):
            break
        time.sleep(0.05)
    proc_stat = Path(f"/proc/{child_pid_val}/stat")
    if proc_stat.exists():
        assert proc_stat.read_text().split()[2] == "Z"
    assert not marker.exists()


@pytest.mark.parametrize("timeout", ["nan", "inf", "-1", "31"])
def test_invalid_timeout_never_spawns_transport(
    hook_env: dict[str, Any], timeout: str
) -> None:
    fake_cli(hook_env, "raise AssertionError('must not invoke')")
    hook_env["settings"]["GPTME_LESSONS_SELECTOR_TIMEOUT"] = timeout
    assert len(emitted_names(run_hook(hook_env))) == 5
    assert not (hook_env["tmp"] / "requests.jsonl").exists()


def test_response_read_is_bounded(hook_env: dict[str, Any]) -> None:
    fake_cli(hook_env, "print('x' * (1024 * 1024 + 1))")
    assert len(emitted_names(run_hook(hook_env))) == 5


def test_selector_cannot_materialize_a_rejected_stub(hook_env: dict[str, Any]) -> None:
    hook_env["catalog"][-1].is_stub = True
    fake_cli(hook_env, "print(json.dumps({'version':1, 'status':'empty', 'ids':[]}))")
    assert emitted_names(run_hook(hook_env)) == ["guard", "always", "critical"]


def test_repository_env_cannot_enable_execution_or_spending(
    hook_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cli(hook_env, "raise AssertionError('repository authorized execution')")
    malicious = dict(hook_env["settings"])
    for key in list(hook_env["settings"]):
        if key.startswith("GPTME_LESSONS_SELECTOR_"):
            del hook_env["settings"][key]
    config = MagicMock()
    config.get_env.side_effect = lambda key: malicious.get(key)
    config.get_env_bool.side_effect = lambda key, default: default
    monkeypatch.setattr(tool, "get_config", lambda: config)
    assert len(emitted_names(run_hook(hook_env))) == 5
    assert not (hook_env["tmp"] / "requests.jsonl").exists()


def test_workspace_env_cannot_enable_selector_command(
    hook_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cli(hook_env, "raise AssertionError('must not invoke')")
    # Simulate a repository .env that sets selector keys after startup capture.
    monkeypatch.setattr(selector, "_trusted_env", {})
    assert len(emitted_names(run_hook(hook_env))) == 5
    assert not (hook_env["tmp"] / "requests.jsonl").exists()


def test_identical_new_user_message_can_refresh_selection(
    hook_env: dict[str, Any],
) -> None:
    fake_cli(hook_env, "print(json.dumps({'version':1, 'status':'empty', 'ids':[]}))")
    run_hook(hook_env)
    hook_env["manager"].log.messages.append(Message("user", "commit changes safely"))
    run_hook(hook_env)
    assert [request["event_kind"] for request in recorded_requests(hook_env)] == [
        "user_prompt",
        "user_prompt",
    ]


def test_command_is_stopped_while_output_exceeds_limit(
    hook_env: dict[str, Any],
) -> None:
    completed = hook_env["tmp"] / "output-completed"
    fake_cli(
        hook_env,
        "for _ in range(512): os.write(1, b'x' * 4096)\n"
        f"open({str(completed)!r}, 'w').write('wrote 2MiB')",
    )
    assert len(emitted_names(run_hook(hook_env))) == 5
    assert not completed.exists()


def test_deeply_nested_response_preserves_guards_and_advisory_baseline(
    hook_env: dict[str, Any],
) -> None:
    fake_cli(hook_env, "print('[' * 10000 + '0' + ']' * 10000)")
    assert len(emitted_names(run_hook(hook_env))) == 5


def test_deeply_nested_command_configuration_preserves_baseline(
    hook_env: dict[str, Any],
) -> None:
    hook_env["settings"]["GPTME_LESSONS_SELECTOR_COMMAND"] = (
        "[" * 10000 + "0" + "]" * 10000
    )
    assert len(emitted_names(run_hook(hook_env))) == 5


def test_real_execution_setup_cannot_grant_selector_from_workspace_dotenv(
    hook_env: dict[str, Any],
) -> None:
    import subprocess

    fake_cli(hook_env, "raise AssertionError('untrusted dotenv command executed')")
    workspace = hook_env["tmp"]
    hook_env["settings"]["GPTME_LESSONS_ITEM_RECEIPTS_DIR"] = str(
        workspace / "private-items"
    )
    (workspace / ".env").write_text(
        "\n".join(
            f"{key}='{value}'"
            for key, value in hook_env["settings"].items()
            if key.startswith("GPTME_LESSONS_SELECTOR_")
            or key == "GPTME_LESSONS_ITEM_RECEIPTS_DIR"
        )
    )
    code = """
import os, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from gptme.config import Config, UserConfig
from gptme.executor import prepare_execution_environment
from gptme.lessons import selector
from gptme.lessons.parser import Lesson, LessonMetadata
from gptme.message import Message
from gptme.tools import lessons
workspace = Path(sys.argv[1])
assert all(value is None for value in selector._trusted_env.values())
config = Config(user=UserConfig())
with patch('gptme.executor.Config.from_workspace', return_value=config), \\
     patch('gptme.executor.init_tools', return_value=[]), \\
     patch('gptme.executor.init_hooks'), \\
     patch('gptme.executor._register_core_compaction_hook'):
    prepare_execution_environment(workspace)
assert os.environ.get('GPTME_LESSONS_SELECTOR_COMMAND')
catalog = [Lesson(workspace / (name + '.md'), LessonMetadata(keywords=['commit']),
                  name, name, 'test', name) for name in ('guard', 'advisory')]
manager = SimpleNamespace(logdir=workspace, log=SimpleNamespace(messages=[Message('user', 'commit')]))
with patch.object(lessons, '_get_lesson_index', return_value=SimpleNamespace(lessons=catalog)), \\
     patch.object(lessons, 'get_config', return_value=config):
    emitted = list(lessons.auto_include_lessons_hook(manager))
assert len(emitted) == 1
assert '## guard' in emitted[0].content and '## advisory' in emitted[0].content
assert not (workspace / 'requests.jsonl').exists()
assert os.environ.get('GPTME_LESSONS_ITEM_RECEIPTS_DIR')
assert not (workspace / 'private-items').exists()
"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GPTME_LESSONS_SELECTOR_")
        and key != "GPTME_LESSONS_ITEM_RECEIPTS_DIR"
    }
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    subprocess.run(
        [sys.executable, "-c", code, str(workspace)],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
