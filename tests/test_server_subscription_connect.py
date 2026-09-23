"""Tests for POST /api/v2/user/subscription-connect and its status endpoint."""

import os
import threading
import time
import unittest.mock

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

pytestmark = [pytest.mark.timeout(15)]

TOKEN = "test-sub-connect-token"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client with auth enabled and a known token."""
    monkeypatch.setenv("GPTME_SERVER_TOKEN", TOKEN)
    monkeypatch.setenv("GPTME_DISABLE_AUTH", "")

    import gptme.server.api_v2 as api_mod
    import gptme.server.auth as auth_mod

    # Reset auth state
    auth_mod._server_token = None
    auth_mod._auth_enabled = True
    auth_mod.init_auth("127.0.0.1", display=False)

    # Clear any stale task state from other tests
    with api_mod._subscription_tasks_lock:
        api_mod._subscription_tasks.clear()

    from gptme.server.app import create_app

    app = create_app(host="127.0.0.1")
    app.config["TESTING"] = True

    return app.test_client()


def auth_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_start_subscription_connect_unknown_provider(client: FlaskClient):
    resp = client.post(
        "/api/v2/user/subscription-connect",
        json={"provider": "not-a-real-provider"},
        headers=auth_headers(),
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "error" in data


def test_start_subscription_connect_no_body(client: FlaskClient):
    resp = client.post(
        "/api/v2/user/subscription-connect",
        headers=auth_headers(),
    )
    assert resp.status_code == 400


def test_start_subscription_connect_returns_task_id(client: FlaskClient):
    """Starting a valid provider returns a pending task immediately."""
    # Mock the OAuth function so it blocks until we release it, then succeeds.
    barrier = threading.Event()

    def _fake_openai_oauth(on_url_ready=None):
        barrier.wait(timeout=5)  # block until test releases

    with unittest.mock.patch(
        "gptme.llm.llm_openai_subscription.oauth_authenticate",
        side_effect=_fake_openai_oauth,
    ):
        resp = client.post(
            "/api/v2/user/subscription-connect",
            json={"provider": "openai-subscription"},
            headers=auth_headers(),
        )
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["status"] == "pending"
    assert "task_id" in data
    task_id = data["task_id"]

    # Status endpoint should return pending before OAuth completes
    status_resp = client.get(
        f"/api/v2/user/subscription-connect/{task_id}",
        headers=auth_headers(),
    )
    assert status_resp.status_code == 200
    status_data = status_resp.get_json()
    assert status_data["status"] == "pending"
    assert status_data["provider"] == "openai-subscription"

    # Let the background thread finish
    barrier.set()


def test_subscription_connect_success(client: FlaskClient):
    """OAuth success → task transitions to connected."""

    def _fast_openai_oauth(on_url_ready=None):
        pass  # immediate success

    with unittest.mock.patch(
        "gptme.llm.llm_openai_subscription.oauth_authenticate",
        side_effect=_fast_openai_oauth,
    ):
        resp = client.post(
            "/api/v2/user/subscription-connect",
            json={"provider": "openai-subscription"},
            headers=auth_headers(),
        )
    assert resp.status_code == 202
    task_id = resp.get_json()["task_id"]

    # Poll until connected (the background thread is very fast in this mock)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status_resp = client.get(
            f"/api/v2/user/subscription-connect/{task_id}",
            headers=auth_headers(),
        )
        data = status_resp.get_json()
        if data["status"] != "pending":
            break
        time.sleep(0.05)

    assert data["status"] == "connected"
    assert data["provider"] == "openai-subscription"
    assert data["model"] is not None
    assert data["error"] is None


def test_subscription_connect_error(client: FlaskClient):
    """OAuth failure → task transitions to error with message."""

    def _failing_oauth(on_url_ready=None):
        raise RuntimeError("Port 1455 is in use")

    with unittest.mock.patch(
        "gptme.llm.llm_openai_subscription.oauth_authenticate",
        side_effect=_failing_oauth,
    ):
        resp = client.post(
            "/api/v2/user/subscription-connect",
            json={"provider": "openai-subscription"},
            headers=auth_headers(),
        )
    task_id = resp.get_json()["task_id"]

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        data = client.get(
            f"/api/v2/user/subscription-connect/{task_id}",
            headers=auth_headers(),
        ).get_json()
        if data["status"] != "pending":
            break
        time.sleep(0.05)

    assert data["status"] == "error"
    assert "Port 1455" in data["error"]


def test_subscription_connect_grok(client: FlaskClient):
    """Grok subscription provider flow."""

    def _fake_grok_oauth(on_url_ready=None):
        pass

    with unittest.mock.patch(
        "gptme.llm.llm_grok_subscription.oauth_authenticate",
        side_effect=_fake_grok_oauth,
    ):
        resp = client.post(
            "/api/v2/user/subscription-connect",
            json={"provider": "grok-subscription"},
            headers=auth_headers(),
        )
    assert resp.status_code == 202
    task_id = resp.get_json()["task_id"]

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        data = client.get(
            f"/api/v2/user/subscription-connect/{task_id}",
            headers=auth_headers(),
        ).get_json()
        if data["status"] != "pending":
            break
        time.sleep(0.05)

    assert data["status"] == "connected"
    assert "grok" in data["model"]


def test_subscription_connect_openrouter(client: FlaskClient, monkeypatch):
    """OpenRouter OAuth → key saved to env."""

    def _fake_openrouter_oauth(on_url_ready=None):
        return "sk-or-v1-testkey"

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with (
        unittest.mock.patch(
            "gptme.llm.llm_openrouter_subscription.oauth_get_api_key",
            side_effect=_fake_openrouter_oauth,
        ),
        unittest.mock.patch("gptme.server.api_v2.set_config_value"),
    ):
        resp = client.post(
            "/api/v2/user/subscription-connect",
            json={"provider": "openrouter"},
            headers=auth_headers(),
        )
    assert resp.status_code == 202
    task_id = resp.get_json()["task_id"]

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        data = client.get(
            f"/api/v2/user/subscription-connect/{task_id}",
            headers=auth_headers(),
        ).get_json()
        if data["status"] != "pending":
            break
        time.sleep(0.05)

    assert data["status"] == "connected"
    assert os.environ.get("OPENROUTER_API_KEY") == "sk-or-v1-testkey"


def test_subscription_connect_persists_default_model(client: FlaskClient):
    """Successful OAuth persists models.default so a fresh install can start."""

    with (
        unittest.mock.patch(
            "gptme.llm.llm_openai_subscription.oauth_authenticate",
        ),
        unittest.mock.patch(
            "gptme.server.api_v2._persist_default_model",
            return_value=False,
        ) as mock_persist,
    ):
        resp = client.post(
            "/api/v2/user/subscription-connect",
            json={"provider": "openai-subscription"},
            headers=auth_headers(),
        )
    assert resp.status_code == 202
    task_id = resp.get_json()["task_id"]

    deadline = time.monotonic() + 5
    data = None
    while time.monotonic() < deadline:
        data = client.get(
            f"/api/v2/user/subscription-connect/{task_id}",
            headers=auth_headers(),
        ).get_json()
        if data["status"] != "pending":
            break
        time.sleep(0.05)

    assert data is not None
    assert data["status"] == "connected"
    assert data["model"] == "openai-subscription/gpt-5.2"
    mock_persist.assert_called()
    assert mock_persist.call_args.args[0] == "openai-subscription/gpt-5.2"


def test_start_subscription_connect_reuses_pending_task(client: FlaskClient):
    """A second POST for the same in-flight provider reuses the task instead of spawning another thread."""
    barrier = threading.Event()

    def _block_oauth(on_url_ready=None):
        barrier.wait(timeout=5)

    with unittest.mock.patch(
        "gptme.llm.llm_openai_subscription.oauth_authenticate",
        side_effect=_block_oauth,
    ):
        first = client.post(
            "/api/v2/user/subscription-connect",
            json={"provider": "openai-subscription"},
            headers=auth_headers(),
        )
        second = client.post(
            "/api/v2/user/subscription-connect",
            json={"provider": "openai-subscription"},
            headers=auth_headers(),
        )
    try:
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.get_json()["task_id"] == second.get_json()["task_id"]
    finally:
        barrier.set()


def test_reuse_or_create_is_atomic_under_concurrency():
    """Concurrent create/reuse must leave exactly one pending task per provider."""
    import gptme.server.api_v2 as api_mod

    with api_mod._subscription_tasks_lock:
        api_mod._subscription_tasks.clear()

    n_threads = 8
    barrier = threading.Barrier(n_threads)
    results: list[tuple[str, bool]] = []
    errors: list[BaseException] = []

    def _race() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(
                api_mod._reuse_or_create_subscription_task("openai-subscription")
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_race) for _ in range(n_threads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(results) == n_threads
    task_ids = {task_id for task_id, _created in results}
    assert len(task_ids) == 1
    assert sum(1 for _task_id, created in results if created) == 1
    with api_mod._subscription_tasks_lock:
        pending = [
            task
            for task in api_mod._subscription_tasks.values()
            if task.get("provider") == "openai-subscription"
            and task.get("status") == "pending"
        ]
    assert len(pending) == 1


def test_status_unknown_task(client: FlaskClient):
    resp = client.get(
        "/api/v2/user/subscription-connect/nonexistent-task",
        headers=auth_headers(),
    )
    assert resp.status_code == 404


def test_start_requires_auth(client: FlaskClient):
    resp = client.post(
        "/api/v2/user/subscription-connect",
        json={"provider": "openai-subscription"},
    )
    assert resp.status_code == 401


def test_status_requires_auth(client: FlaskClient):
    resp = client.get("/api/v2/user/subscription-connect/some-task")
    assert resp.status_code == 401


def test_headless_server_keeps_oauth_flow_alive(client: FlaskClient, monkeypatch):
    """Headless servers expose the OAuth URL without aborting the PKCE flow."""
    import gptme.server.api_v2 as api_mod

    monkeypatch.setattr(api_mod, "_is_headless_server", lambda: True)

    fake_url = "https://auth.example.com/oauth?code_challenge=test"
    barrier = threading.Event()
    skip_browser = threading.Event()

    def _fake_oauth(on_url_ready=None):
        if on_url_ready:
            result = on_url_ready(fake_url)
            if result is False:
                skip_browser.set()
        barrier.wait(timeout=5)

    try:
        with unittest.mock.patch(
            "gptme.llm.llm_openai_subscription.oauth_authenticate",
            side_effect=_fake_oauth,
        ):
            resp = client.post(
                "/api/v2/user/subscription-connect",
                json={"provider": "openai-subscription"},
                headers=auth_headers(),
            )
        assert resp.status_code == 202
        task_id = resp.get_json()["task_id"]

        deadline = time.monotonic() + 5
        data = None
        while time.monotonic() < deadline:
            data = client.get(
                f"/api/v2/user/subscription-connect/{task_id}",
                headers=auth_headers(),
            ).get_json()
            if data.get("oauth_url") == fake_url:
                break
            time.sleep(0.05)

        assert data is not None
        assert data["status"] == "pending"
        assert data.get("oauth_url") == fake_url
        assert skip_browser.is_set()
    finally:
        barrier.set()


def test_oauth_url_exposed_in_status(client: FlaskClient, monkeypatch):
    """oauth_url is populated in the task status once the PKCE URL is built."""
    import gptme.server.api_v2 as api_mod

    # Ensure headless check never fires (this test runs on CI without a display)
    monkeypatch.setattr(api_mod, "_is_headless_server", lambda: False)

    barrier = threading.Event()
    url_set = threading.Event()
    fake_url = "https://auth.openai.com/authorize?code_challenge=abc"

    def _slow_oauth(on_url_ready=None):
        if on_url_ready:
            on_url_ready(fake_url)
        url_set.set()
        barrier.wait(timeout=5)  # hold until test is done checking

    with unittest.mock.patch(
        "gptme.llm.llm_openai_subscription.oauth_authenticate",
        side_effect=_slow_oauth,
    ):
        resp = client.post(
            "/api/v2/user/subscription-connect",
            json={"provider": "openai-subscription"},
            headers=auth_headers(),
        )
    task_id = resp.get_json()["task_id"]

    # Wait until the background thread has called on_url_ready
    url_set.wait(timeout=5)
    time.sleep(0.05)  # small grace period for the task dict update

    status_resp = client.get(
        f"/api/v2/user/subscription-connect/{task_id}",
        headers=auth_headers(),
    )
    data = status_resp.get_json()
    assert data["oauth_url"] == fake_url

    barrier.set()


def _clear_headless_env(monkeypatch) -> None:
    for key in (
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "BROWSER",
        "CI",
        "GPTME_HEADLESS",
        "SSH_CONNECTION",
        "SSH_CLIENT",
        "SESSIONNAME",
    ):
        monkeypatch.delenv(key, raising=False)


def test_is_headless_linux_without_display(monkeypatch):
    import sys

    import gptme.server.api_v2 as api_mod

    _clear_headless_env(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    assert api_mod._is_headless_server() is True


def test_is_headless_linux_with_display(monkeypatch):
    import sys

    import gptme.server.api_v2 as api_mod

    _clear_headless_env(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    assert api_mod._is_headless_server() is False


def test_is_headless_ci_on_macos(monkeypatch):
    import sys

    import gptme.server.api_v2 as api_mod

    _clear_headless_env(monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("CI", "true")
    assert api_mod._is_headless_server() is True


def test_is_headless_ssh_on_macos_without_display(monkeypatch):
    import sys

    import gptme.server.api_v2 as api_mod

    _clear_headless_env(monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 22 5.6.7.8 22")
    assert api_mod._is_headless_server() is True


def test_is_headless_windows_service(monkeypatch):
    import sys

    import gptme.server.api_v2 as api_mod

    _clear_headless_env(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SESSIONNAME", "Services")
    assert api_mod._is_headless_server() is True


def test_is_not_headless_windows_console(monkeypatch):
    import sys

    import gptme.server.api_v2 as api_mod

    _clear_headless_env(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SESSIONNAME", "Console")
    assert api_mod._is_headless_server() is False


def test_browser_env_overrides_ci_headless(monkeypatch):
    import sys

    import gptme.server.api_v2 as api_mod

    _clear_headless_env(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("BROWSER", "firefox")
    assert api_mod._is_headless_server() is False


def test_openrouter_callback_server_closed_when_url_ready_raises(monkeypatch):
    """Raising from on_url_ready must still shut down the OpenRouter callback server."""
    import gptme.llm.llm_openrouter_subscription as or_mod

    class FakeHTTPServer:
        def __init__(self, *_args, **_kwargs):
            self.shutdown_calls = 0
            self.close_calls = 0

        def serve_forever(self):
            return None

        def shutdown(self):
            self.shutdown_calls += 1

        def server_close(self):
            self.close_calls += 1

    servers: list[FakeHTTPServer] = []

    def _factory(*args, **kwargs):
        server = FakeHTTPServer(*args, **kwargs)
        servers.append(server)
        return server

    monkeypatch.setattr(or_mod.http.server, "HTTPServer", _factory)

    def _boom(_url: str) -> None:
        raise RuntimeError("abort oauth")

    with pytest.raises(RuntimeError, match="abort oauth"):
        or_mod.oauth_get_api_key(on_url_ready=_boom)

    assert len(servers) == 1
    assert servers[0].shutdown_calls == 1
    assert servers[0].close_calls == 1
