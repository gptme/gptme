"""Exercise complete native CI collection through offline API pages."""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

from gptme.tools import gh

SHA = "a" * 40


def run(index: int, status: str = "completed", conclusion: str = "success") -> dict:
    return {
        "id": index,
        "name": f"check-{index}",
        "status": status,
        "conclusion": conclusion,
    }


@pytest.mark.parametrize("operation", ["status", "checks"])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("count", [30, 31, 102])
def test_all_pages_observed(operation: str, explicit: bool, count: int) -> None:
    runs = [run(i) for i in range(count)]
    runs[-1]["conclusion"] = "failure"
    endpoints: list[str] = []

    def transport(command: list[str], **kwargs: object) -> SimpleNamespace:
        endpoint = command[2]
        endpoints.append(endpoint)
        data: object
        if "/pulls/" in endpoint:
            data = {"head": {"sha": SHA}}
        else:
            query = parse_qs(urlparse(endpoint).query)
            size = int(query.get("per_page", [30])[0])
            page = int(query.get("page", [1])[0])
            data = {
                "total_count": count,
                "check_runs": runs[(page - 1) * size : page * size],
            }
        return SimpleNamespace(stdout=json.dumps(data))

    suffix = f" {SHA}" if explicit else ""
    with patch.object(gh.subprocess, "run", side_effect=transport):
        text = "\n".join(
            m.content
            for m in gh.execute_gh(
                None, None, {"command": f"pr {operation} o/r#1{suffix}"}
            )
        )
    assert f"check-{count - 1}" in text
    assert "All checks passed" not in text
    assert all(SHA in endpoint for endpoint in endpoints if "/commits/" in endpoint)


@pytest.mark.parametrize("operation", ["status", "checks"])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize(
    "bad_page", ["empty", "repeat", "json", "transport", "missing", "shape", "count"]
)
def test_incomplete_pages_fail_closed(
    operation: str, explicit: bool, bad_page: str
) -> None:
    requests = 0

    def transport(command: list[str], **kwargs: object) -> SimpleNamespace:
        nonlocal requests
        if "/pulls/" in command[2]:
            return SimpleNamespace(stdout=json.dumps({"head": {"sha": SHA}}))
        requests += 1
        query = parse_qs(urlparse(command[2]).query)
        page = int(query.get("page", [1])[0])
        data: object
        if page == 1:
            data = {"total_count": 102, "check_runs": [run(i) for i in range(100)]}
        elif bad_page == "transport":
            raise subprocess.CalledProcessError(1, command)
        elif bad_page == "json":
            return SimpleNamespace(stdout="not json")
        else:
            data = {
                "empty": {"total_count": 102, "check_runs": []},
                "repeat": {
                    "total_count": 102,
                    "check_runs": [run(i) for i in range(100)],
                },
                "missing": {"check_runs": [run(100), run(101)]},
                "shape": {"total_count": 102, "check_runs": [None]},
                "count": {"total_count": 100, "check_runs": [run(100), run(101)]},
            }[bad_page]
        return SimpleNamespace(stdout=json.dumps(data))

    suffix = f" {SHA}" if explicit else ""
    with patch.object(gh.subprocess, "run", side_effect=transport):
        text = "\n".join(
            m.content
            for m in gh.execute_gh(
                None, None, {"command": f"pr {operation} o/r#1{suffix}"}
            )
        )
    assert "Error" in text
    assert "All checks passed" not in text
    assert requests <= 3


def test_page_two_pending_polls_same_head_until_success() -> None:
    polls = 0
    heads = 0

    def transport(command: list[str], **kwargs: object) -> SimpleNamespace:
        nonlocal polls, heads
        endpoint = command[2]
        if "/pulls/" in endpoint:
            heads += 1
            return SimpleNamespace(
                stdout=json.dumps({"head": {"sha": SHA if heads == 1 else "changed"}})
            )
        assert SHA in endpoint
        query = parse_qs(urlparse(endpoint).query)
        page = int(query.get("page", [1])[0])
        size = int(query.get("per_page", [30])[0])
        if page == 1:
            polls += 1
        runs = [run(i) for i in range(101)]
        if polls < 3:
            runs[-1]["status"] = "in_progress"
            runs[-1]["conclusion"] = None
        data = {
            "total_count": len(runs),
            "check_runs": runs[(page - 1) * size : page * size],
        }
        return SimpleNamespace(stdout=json.dumps(data))

    with (
        patch.object(gh.subprocess, "run", side_effect=transport),
        patch.object(gh, "time", wraps=gh.time) as clock,
    ):
        clock.sleep.return_value = None
        text = "\n".join(
            m.content for m in gh.execute_gh(None, None, {"command": "pr checks o/r#1"})
        )
    assert heads == 1
    assert polls == 3
    assert "in progress" in text
    assert "All checks passed! (101 checks)" in text
    clock.sleep.assert_called_once_with(10)


@pytest.mark.parametrize("operation", ["status", "checks"])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"total_count": -1, "check_runs": []},
        {"total_count": True, "check_runs": []},
        {"total_count": 1, "check_runs": {}},
        {"total_count": 1, "check_runs": [{"id": "1"}]},
        {"total_count": 0, "check_runs": [run(1)]},
    ],
)
def test_malformed_first_page_is_not_success(
    operation: str, explicit: bool, data: object
) -> None:
    def transport(command: list[str], **kwargs: object) -> SimpleNamespace:
        value = {"head": {"sha": SHA}} if "/pulls/" in command[2] else data
        return SimpleNamespace(stdout=json.dumps(value))

    suffix = f" {SHA}" if explicit else ""
    with patch.object(gh.subprocess, "run", side_effect=transport):
        text = "\n".join(
            m.content
            for m in gh.execute_gh(
                None, None, {"command": f"pr {operation} o/r#1{suffix}"}
            )
        )
    assert "Error" in text
    assert "All checks passed" not in text


@pytest.mark.parametrize("operation", ["status", "checks"])
@pytest.mark.parametrize("explicit", [False, True])
def test_empty_listing_keeps_no_checks_behavior(operation: str, explicit: bool) -> None:
    def transport(command: list[str], **kwargs: object) -> SimpleNamespace:
        data: dict[str, object]
        if "/pulls/" in command[2]:
            data = {"head": {"sha": SHA}}
        else:
            data = {"total_count": 0, "check_runs": []}
        return SimpleNamespace(stdout=json.dumps(data))

    suffix = f" {SHA}" if explicit else ""
    with patch.object(gh.subprocess, "run", side_effect=transport):
        text = "\n".join(
            m.content
            for m in gh.execute_gh(
                None, None, {"command": f"pr {operation} o/r#1{suffix}"}
            )
        )
    assert "No checks found" in text
    assert "All checks passed" not in text
