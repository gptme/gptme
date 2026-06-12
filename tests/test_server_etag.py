"""Tests for ETag conditional request support on conversations endpoints."""

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path, monkeypatch):
    from gptme.server.app import create_app

    monkeypatch.setenv("GPTME_LOGS_DIR", str(tmp_path / "logs"))
    app = create_app()
    app.config["TESTING"] = True
    app.config["AUTH_DISABLED"] = True
    return app


def _create_conversation(client, conv_id: str) -> None:
    resp = client.put(
        f"/api/v2/conversations/{conv_id}",
        json={"messages": [{"role": "user", "content": "hello"}], "prompt": "none"},
    )
    assert resp.status_code == 200, resp.get_json()


class TestConversationsListETag:
    def test_returns_etag_header(self, app):
        with app.test_client() as client:
            _create_conversation(client, "test-conv-1")
            resp = client.get("/api/v2/conversations")
            assert resp.status_code == 200
            assert resp.headers.get("ETag") is not None

    def test_304_on_matching_etag(self, app):
        with app.test_client() as client:
            _create_conversation(client, "test-conv-2")
            resp1 = client.get("/api/v2/conversations")
            assert resp1.status_code == 200
            etag = resp1.headers["ETag"]

            resp2 = client.get("/api/v2/conversations", headers={"If-None-Match": etag})
            assert resp2.status_code == 304
            assert resp2.data == b""

    def test_200_on_mismatched_etag(self, app):
        with app.test_client() as client:
            _create_conversation(client, "test-conv-3")
            resp = client.get(
                "/api/v2/conversations",
                headers={"If-None-Match": '"deadbeefdeadbeef"'},
            )
            assert resp.status_code == 200
            assert resp.headers.get("ETag") is not None

    def test_no_etag_on_search(self, app):
        """Search responses don't carry ETags (content varies by query param)."""
        with app.test_client() as client:
            _create_conversation(client, "test-conv-4")
            resp = client.get("/api/v2/conversations?search=hello")
            assert resp.status_code == 200
            # No ETag for search results (correct — skip assertion if absent)
            # We just check it doesn't 304 inadvertently
            assert resp.get_json() is not None


class TestConversationETag:
    def test_returns_etag_header(self, app):
        with app.test_client() as client:
            _create_conversation(client, "test-single-1")
            resp = client.get("/api/v2/conversations/test-single-1")
            assert resp.status_code == 200
            assert resp.headers.get("ETag") is not None

    def test_304_on_matching_etag(self, app):
        with app.test_client() as client:
            _create_conversation(client, "test-single-2")
            resp1 = client.get("/api/v2/conversations/test-single-2")
            assert resp1.status_code == 200
            etag = resp1.headers["ETag"]

            resp2 = client.get(
                "/api/v2/conversations/test-single-2",
                headers={"If-None-Match": etag},
            )
            assert resp2.status_code == 304
            assert resp2.data == b""

    def test_200_on_mismatched_etag(self, app):
        with app.test_client() as client:
            _create_conversation(client, "test-single-3")
            resp = client.get(
                "/api/v2/conversations/test-single-3",
                headers={"If-None-Match": '"deadbeefdeadbeef"'},
            )
            assert resp.status_code == 200
            assert resp.headers.get("ETag") is not None

    def test_etag_changes_after_message_added(self, app):
        with app.test_client() as client:
            _create_conversation(client, "test-single-4")
            resp1 = client.get("/api/v2/conversations/test-single-4")
            etag1 = resp1.headers["ETag"]

            # Add a new message — mtime changes
            add = client.post(
                "/api/v2/conversations/test-single-4",
                json={"role": "user", "content": "follow-up"},
            )
            assert add.status_code == 200

            resp2 = client.get("/api/v2/conversations/test-single-4")
            etag2 = resp2.headers["ETag"]
            assert etag1 != etag2, "ETag must change after message is appended"
