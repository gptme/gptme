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


def test_matching_failure_is_error_not_empty(env: dict[str, Any]) -> None:
    with patch.object(tool, "_get_lesson_index", side_effect=ValueError("bad index")):
        assert list(tool.auto_include_lessons_hook(env["manager"])) == []
    assert [(r["outcome"], r["bytes"]) for r in receipts(env)] == [("error", 0)]


def test_config_failure_is_error_not_empty(env: dict[str, Any]) -> None:
    with patch.object(tool, "get_config", side_effect=ValueError("bad config")):
        assert list(tool.auto_include_lessons_hook(env["manager"])) == []
    assert receipts(env)[0]["outcome"] == "error"


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
