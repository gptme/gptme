"""Final native lesson emission receipts, including measured-empty invocations."""

import json
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from gptme.lessons import selector
from gptme.lessons.parser import Lesson, LessonMetadata
from gptme.message import Message
from gptme.tools import lessons as tool


@pytest.fixture
def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[dict[str, Any], None, None]:
    settings: dict[str, Any] = {}
    monkeypatch.setattr(
        tool,
        "get_config",
        lambda: SimpleNamespace(
            get_env=lambda key: settings.get(key),
            get_env_bool=lambda key, default: settings.get(key, default),
        ),
    )
    monkeypatch.setattr(selector, "_trusted_env", {})
    catalog = [
        Lesson(
            path=tmp_path / "lesson.md",
            metadata=LessonMetadata(keywords=["receipt"]),
            title="Räksmörgås",
            description="Test receipt",
            category="test",
            body="Åäö 🚀",
        )
    ]
    monkeypatch.setattr(
        tool, "_get_lesson_index", lambda: SimpleNamespace(lessons=catalog)
    )
    manager = SimpleNamespace(
        logdir=tmp_path,
        log=SimpleNamespace(messages=[Message("user", "receipt")]),
    )
    tool._session_stats_var.set(None)
    yield {"manager": manager, "settings": settings, "catalog": catalog}
    tool._session_stats_var.set(None)


def receipts(env: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (env["manager"].logdir / "lesson-emissions.jsonl")
        .read_text()
        .splitlines()
    ]


def test_multibyte_context_and_dedup_zero(env: dict[str, Any]) -> None:
    manager = env["manager"]
    messages = list(tool.auto_include_lessons_hook(manager))
    assert len(messages) == 1
    assert isinstance(messages[0], Message)
    content = messages[0].content
    manager.log.messages.extend(messages)
    assert list(tool.auto_include_lessons_hook(manager)) == []
    first, second = receipts(env)
    assert first["bytes"] == len(content.encode("utf-8")) > len(content)
    assert first["outcome"] == "emitted"
    assert second["bytes"] == 0
    assert second["outcome"] == "empty"
    assert first["event_id"] != second["event_id"]
    assert first["receipt_version"] == 1
    assert first["record_type"] == "emission_receipt"
    assert first["runtime"] == "gptme"
    assert first["hook"] == "STEP_PRE"
    assert first["session_id"] == str(manager.logdir.resolve())
    assert datetime.fromisoformat(first["ts"]).tzinfo is not None
    assert "Åäö" not in json.dumps(first, ensure_ascii=False)


@pytest.mark.parametrize("reason", ["disabled", "no-match", "session-cap", "turn-cap"])
def test_normal_empty_invocations(env: dict[str, Any], reason: str) -> None:
    if reason == "disabled":
        env["settings"]["GPTME_LESSONS_AUTO_INCLUDE"] = False
    elif reason == "no-match":
        env["catalog"].clear()
    elif reason == "session-cap":
        env["settings"]["GPTME_LESSONS_MAX_SESSION"] = "0"
    else:
        env["settings"]["GPTME_LESSONS_MAX_PER_TURN"] = "1"
        lesson_path = str(env["catalog"][0].path)
        env["manager"].log.messages.append(
            Message("system", f"# Relevant Lessons\n*Path: {lesson_path}*\n")
        )
    assert list(tool.auto_include_lessons_hook(env["manager"])) == []
    assert [(r["outcome"], r["bytes"]) for r in receipts(env)] == [("empty", 0)]


def test_matching_failure_is_error_not_empty(
    env: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    with patch.object(tool, "_get_lesson_index", side_effect=ValueError("bad index")):
        assert list(tool.auto_include_lessons_hook(env["manager"])) == []
    assert [(r["outcome"], r["bytes"]) for r in receipts(env)] == [("error", 0)]
    assert caplog.messages == ["Error during lesson auto-inclusion: bad index"]


def test_config_failure_is_error_not_empty(
    env: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    with patch.object(tool, "get_config", side_effect=ValueError("bad config")):
        assert list(tool.auto_include_lessons_hook(env["manager"])) == []
    assert receipts(env)[0]["outcome"] == "error"
    assert caplog.messages == ["Error during lesson auto-inclusion: bad config"]


def test_receipt_write_failure_preserves_output(env: dict[str, Any]) -> None:
    with patch.object(Path, "open", side_effect=OSError("read-only filesystem")):
        messages = list(tool.auto_include_lessons_hook(env["manager"]))
    assert len(messages) == 1
    assert isinstance(messages[0], Message)
    assert "Åäö 🚀" in messages[0].content


def test_warning_logger_failure_still_records_error(env: dict[str, Any]) -> None:
    with (
        patch.object(tool, "_get_lesson_index", side_effect=ValueError("bad index")),
        patch.object(tool.logger, "warning", side_effect=OSError("logger unavailable")),
    ):
        assert list(tool.auto_include_lessons_hook(env["manager"])) == []
    assert receipts(env)[0]["outcome"] == "error"


def test_early_close_records_handed_off_bytes_once(env: dict[str, Any]) -> None:
    invocation = tool.auto_include_lessons_hook(env["manager"])
    message = next(invocation)
    assert isinstance(message, Message)
    invocation.close()
    invocation.close()
    assert [(r["outcome"], r["bytes"]) for r in receipts(env)] == [
        ("emitted", len(message.content.encode("utf-8")))
    ]


def test_unstarted_generator_is_not_measured_zero(env: dict[str, Any]) -> None:
    invocation = tool.auto_include_lessons_hook(env["manager"])
    invocation.close()
    assert not (env["manager"].logdir / "lesson-emissions.jsonl").exists()


def test_consumer_throw_records_error_and_handed_off_bytes(env: dict[str, Any]) -> None:
    invocation = tool.auto_include_lessons_hook(env["manager"])
    message = next(invocation)
    assert isinstance(message, Message)
    with pytest.raises(StopIteration):
        invocation.throw(ValueError("consumer interrupted"))
    assert [(r["outcome"], r["bytes"]) for r in receipts(env)] == [
        ("error", len(message.content.encode("utf-8")))
    ]


def enable_items(env: dict[str, Any]) -> Path:
    directory = env["manager"].logdir / "private-items"
    selector._trusted_env = {
        "GPTME_LESSONS_ITEM_RECEIPTS_DIR": str(directory),
        "GPTME_LESSONS_SELECTOR_GUARDS": "guard",
    }
    return directory


def item_receipts(directory: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (directory / "lesson-items.jsonl").read_text().splitlines()
    ]


def test_private_final_items_bind_exact_render_and_latest_intent(
    env: dict[str, Any],
) -> None:
    import hashlib
    import stat

    directory = enable_items(env)
    manager = env["manager"]
    manager.log.messages.extend(
        [Message("assistant", "receipt old"), Message("user", "receipt latest 🚀")]
    )
    message = next(invocation := tool.auto_include_lessons_hook(manager))
    manager.log.messages.append(Message("user", "changed after handoff"))
    invocation.close()
    record = item_receipts(directory)[0]
    aggregate = receipts(env)[0]
    assert record["event_id"] == aggregate["event_id"]
    assert record["session_id"] == aggregate["session_id"]
    assert record["receipt_version"] == 2
    assert "intent" not in record
    assert record["intent_bytes"] == len("receipt latest 🚀".encode())
    assert (
        record["intent_sha256"]
        == hashlib.sha256("receipt latest 🚀".encode()).hexdigest()
    )
    assert record["guards_configured"] is True
    assert isinstance(message, Message)
    item = record["items"][0]
    assert item["item_id"] == str(env["catalog"][0].path)
    assert item["text"] == message.content.removeprefix("# Relevant Lessons\n")
    assert item["content_hash"] == hashlib.sha256(item["text"].encode()).hexdigest()
    assert item["advisory"] is True
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "lesson-items.jsonl").stat().st_mode) == 0o600
    assert not {"items", "intent"} & aggregate.keys()


@pytest.mark.parametrize("protection", ["configured", "always", "critical"])
def test_baseline_item_classification_uses_selector_guards(
    env: dict[str, Any], protection: str
) -> None:
    directory = enable_items(env)
    lesson = env["catalog"][0]
    assert selector._trusted_env is not None
    if protection == "configured":
        selector._trusted_env["GPTME_LESSONS_SELECTOR_GUARDS"] = "lesson"
    elif protection == "always":
        lesson.metadata.always_apply = True
    else:
        lesson.metadata.priority = "critical"
    assert len(list(tool.auto_include_lessons_hook(env["manager"]))) == 1
    assert item_receipts(directory)[0]["items"][0]["advisory"] is False


def test_private_zero_and_missing_intent(env: dict[str, Any]) -> None:
    directory = enable_items(env)
    env["manager"].log.messages.clear()
    assert list(tool.auto_include_lessons_hook(env["manager"])) == []
    record = item_receipts(directory)[0]
    assert record["items"] == []
    assert record["intent_sha256"] is None
    assert record["intent_bytes"] == 0
    assert record["event_id"] == receipts(env)[0]["event_id"]


def test_private_empty_intent_is_distinct_from_missing(env: dict[str, Any]) -> None:
    """An empty prompt hashes the empty string; a missing prompt stays None."""
    import hashlib

    directory = enable_items(env)
    env["manager"].log.messages[:] = [Message("user", "")]
    assert list(tool.auto_include_lessons_hook(env["manager"])) == []
    record = item_receipts(directory)[0]
    assert record["intent_bytes"] == 0
    assert record["intent_sha256"] == hashlib.sha256(b"").hexdigest()


def test_untrusted_item_configuration_cannot_enable_capture(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = env["manager"].logdir / "untrusted-items"
    monkeypatch.setenv("GPTME_LESSONS_ITEM_RECEIPTS_DIR", str(directory))
    env["settings"]["GPTME_LESSONS_ITEM_RECEIPTS_DIR"] = str(directory)
    assert len(list(tool.auto_include_lessons_hook(env["manager"]))) == 1
    assert not directory.exists()


@pytest.mark.parametrize(
    "unsafe", ["public-dir", "public-file", "symlink-file", "relative"]
)
def test_unsafe_private_destination_preserves_aggregate_and_output(
    env: dict[str, Any], unsafe: str
) -> None:
    directory = enable_items(env)
    directory.mkdir(mode=0o700)
    path = directory / "lesson-items.jsonl"
    if unsafe == "public-dir":
        directory.chmod(0o755)
    elif unsafe == "public-file":
        path.touch(mode=0o644)
    elif unsafe == "symlink-file":
        target = directory / "target"
        target.touch(mode=0o600)
        path.symlink_to(target)
    else:
        assert selector._trusted_env is not None
        selector._trusted_env["GPTME_LESSONS_ITEM_RECEIPTS_DIR"] = "relative"
    assert len(list(tool.auto_include_lessons_hook(env["manager"]))) == 1
    assert receipts(env)[0]["outcome"] == "emitted"
    assert not path.exists() or path.read_text() == ""


def test_pre_handoff_error_does_not_record_rendered_candidates(
    env: dict[str, Any],
) -> None:
    directory = enable_items(env)

    def fail_final_log(message: str) -> None:
        if message.startswith("Auto-included"):
            raise ValueError("render logging failed")

    with patch.object(tool.logger, "debug", side_effect=fail_final_log):
        assert list(tool.auto_include_lessons_hook(env["manager"])) == []
    assert receipts(env)[0]["outcome"] == "error"
    assert item_receipts(directory)[0]["items"] == []


@pytest.mark.parametrize("ending", ["close", "throw", "unstarted"])
def test_private_generator_lifecycle(env: dict[str, Any], ending: str) -> None:
    directory = enable_items(env)
    invocation = tool.auto_include_lessons_hook(env["manager"])
    if ending == "unstarted":
        invocation.close()
        assert not directory.exists()
        assert not (env["manager"].logdir / "lesson-emissions.jsonl").exists()
        return
    next(invocation)
    if ending == "throw":
        with pytest.raises(StopIteration):
            invocation.throw(ValueError("interrupted"))
    else:
        invocation.close()
    invocation.close()
    assert len(item_receipts(directory)) == 1
    assert len(item_receipts(directory)[0]["items"]) == 1
    assert item_receipts(directory)[0]["event_id"] == receipts(env)[0]["event_id"]


def test_final_items_after_selection_caps_dedup_and_materialization(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from copy import deepcopy

    directory = enable_items(env)
    catalog = env["catalog"]
    for name in ("guard", "suppressed", "selected", "dedup", "capped"):
        lesson = deepcopy(catalog[0])
        lesson.path = env["manager"].logdir / (name + ".md")
        catalog.append(lesson)
    catalog.pop(0)
    env["manager"].log.messages.append(
        Message("system", f"# Relevant Lessons\n*Path: {catalog[3].path}*\n")
    )
    catalog[2].is_stub = True
    materialized = deepcopy(catalog[2])
    materialized.is_stub = False
    materialized.body = "materialized exact 🚀"
    monkeypatch.setattr(
        tool,
        "_get_lesson_index",
        lambda: SimpleNamespace(
            lessons=catalog, materialize_lesson=lambda lesson: materialized
        ),
    )
    env["settings"]["GPTME_LESSONS_MAX_PER_TURN"] = "3"
    # Real bounded transport with a model-free fixture response.
    import sys

    response = json.dumps(
        {
            "version": 1,
            "status": "selected",
            "ids": [str(catalog[i].path) for i in (2, 3, 4)],
        }
    )
    assert selector._trusted_env is not None
    selector._trusted_env.update(
        {
            "GPTME_LESSONS_SELECTOR_COMMAND": json.dumps(
                [
                    sys.executable,
                    "-c",
                    f"print({response!r})",
                ]
            ),
            "GPTME_LESSONS_SELECTOR_GUARDS": "guard",
            "GPTME_LESSONS_SELECTOR_MODEL": "fixture/no-provider",
            "GPTME_LESSONS_SELECTOR_MAX_CALLS": "1",
        }
    )
    messages = list(tool.auto_include_lessons_hook(env["manager"]))
    items = item_receipts(directory)[0]["items"]
    assert [item["item_id"] for item in items] == [str(catalog[i].path) for i in (0, 2)]
    assert [item["advisory"] for item in items] == [False, True]
    assert "materialized exact 🚀" in items[1]["text"]
    assert isinstance(messages[0], Message)
    assert (
        "# Relevant Lessons\n" + "".join(item["text"] for item in items)
        == messages[0].content
    )


def test_private_write_failure_does_not_lose_aggregate(env: dict[str, Any]) -> None:
    directory = enable_items(env)
    with patch.object(tool.os, "fsync", side_effect=OSError("disk unavailable")):
        assert len(list(tool.auto_include_lessons_hook(env["manager"]))) == 1
    assert receipts(env)[0]["outcome"] == "emitted"
    assert directory.exists()


def test_missing_guard_configuration_marks_classification_unknown(
    env: dict[str, Any],
) -> None:
    directory = enable_items(env)
    assert selector._trusted_env is not None
    selector._trusted_env.pop("GPTME_LESSONS_SELECTOR_GUARDS")
    assert len(list(tool.auto_include_lessons_hook(env["manager"]))) == 1
    assert item_receipts(directory)[0]["guards_configured"] is False


@pytest.mark.parametrize("unsafe", ["symlink-directory", "hardlinked-file"])
def test_private_evidence_rejects_aliased_destinations(
    env: dict[str, Any], unsafe: str
) -> None:
    directory = enable_items(env)
    target_directory = directory.parent / "actual-private-items"
    target_directory.mkdir(mode=0o700)
    target = target_directory / "lesson-items.jsonl"
    target.touch(mode=0o600)
    if unsafe == "symlink-directory":
        directory.symlink_to(target_directory, target_is_directory=True)
    else:
        directory.mkdir(mode=0o700)
        (directory / "lesson-items.jsonl").hardlink_to(target)
    assert len(list(tool.auto_include_lessons_hook(env["manager"]))) == 1
    assert receipts(env)[0]["outcome"] == "emitted"
    assert target.read_text() == ""
