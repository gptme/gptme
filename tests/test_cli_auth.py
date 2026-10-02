"""Tests for the `gptme-auth login` device flow."""

from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from gptme.cli.auth import main

AUTHORIZE = {
    "device_code": "dev123",
    "user_code": "ABCD-1234",
    "verification_uri": "https://example.test/device",
    "expires_in": 900,
    "interval": 5,
}


def _resp(status: int = 200, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.raise_for_status.return_value = None
    return r


def _login(posts: list):
    """Run `login` with the given sequence of requests.post results/exceptions."""
    runner = CliRunner()
    with (
        patch("gptme.cli.auth.requests.post", side_effect=posts) as post,
        patch("gptme.cli.auth.time.sleep") as sleep,
        patch("gptme.cli.auth.webbrowser.open"),
        patch("gptme.llm.llm_gptme._save_token") as save,
    ):
        result = runner.invoke(
            main,
            ["login", "--url", "https://svc.test", "--no-browser"],
        )
    return result, post, sleep, save


def test_login_success_saves_token():
    result, _, _, save = _login(
        [
            _resp(200, AUTHORIZE),
            _resp(200, {"access_token": "tok", "sub": "me"}),
        ]
    )
    assert result.exit_code == 0, result.output
    assert "Authorization successful" in result.output
    save.assert_called_once()


def test_pending_then_success():
    result, _, sleep, save = _login(
        [
            _resp(200, AUTHORIZE),
            _resp(400, {"error": "authorization_pending"}),
            _resp(200, {"access_token": "tok"}),
        ]
    )
    assert result.exit_code == 0, result.output
    assert sleep.call_count == 2
    save.assert_called_once()


def test_slow_down_bumps_interval():
    result, _, sleep, _ = _login(
        [
            _resp(200, AUTHORIZE),
            _resp(400, {"error": "slow_down"}),
            _resp(200, {"access_token": "tok"}),
        ]
    )
    assert result.exit_code == 0, result.output
    assert [c.args[0] for c in sleep.call_args_list] == [5, 10]


@pytest.mark.parametrize(
    ("error", "message"),
    [("access_denied", "denied"), ("expired_token", "expired")],
)
def test_terminal_poll_errors_exit_nonzero(error, message):
    result, _, _, save = _login([_resp(200, AUTHORIZE), _resp(400, {"error": error})])
    assert result.exit_code == 1
    assert message in result.output
    save.assert_not_called()


@pytest.mark.parametrize(
    "exc", [requests.exceptions.ConnectionError, requests.exceptions.Timeout]
)
def test_transient_poll_error_is_retried(exc):
    result, post, _, save = _login(
        [
            _resp(200, AUTHORIZE),
            exc("blip"),
            _resp(200, {"access_token": "tok"}),
        ]
    )
    assert result.exit_code == 0, result.output
    assert post.call_count == 3
    save.assert_called_once()


def test_authorize_timeout_exits_cleanly():
    result, _, _, _ = _login([requests.exceptions.Timeout("slow")])
    assert result.exit_code == 1
    assert "Timed out" in result.output
    assert not isinstance(result.exception, requests.exceptions.Timeout)


def test_persistent_network_error_stops_at_deadline_and_reports_cause():
    """Repeated transient failures end at the device-code deadline, nonzero, with the cause."""
    clock = {"t": 0.0}

    def fake_sleep(seconds):
        clock["t"] += seconds

    posts = [_resp(200, AUTHORIZE)] + [
        requests.exceptions.ConnectionError("net down")
    ] * 500
    runner = CliRunner()
    with (
        patch("gptme.cli.auth.requests.post", side_effect=posts) as post,
        patch("gptme.cli.auth.time.sleep", side_effect=fake_sleep),
        patch("gptme.cli.auth.time.monotonic", side_effect=lambda: clock["t"]),
        patch("gptme.cli.auth.webbrowser.open"),
        patch("gptme.llm.llm_gptme._save_token") as save,
    ):
        result = runner.invoke(
            main, ["login", "--url", "https://svc.test", "--no-browser"]
        )
    assert result.exit_code == 1
    assert "Timed out" in result.output
    assert "net down" in result.output
    # 900s deadline / 5s interval = 180 polls (+1 authorize call)
    assert post.call_count == 181
    save.assert_not_called()
