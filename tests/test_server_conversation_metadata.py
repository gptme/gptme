"""Conversation metadata sidecar and config-PATCH model validation (gptme/gptme#5187)."""

from uuid import uuid4

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

pytestmark = [pytest.mark.timeout(15)]


def _create_conv(client: FlaskClient) -> str:
    cid = f"conv-metadata-{uuid4().hex[:8]}"
    resp = client.put(f"/api/v2/conversations/{cid}", json={"prompt": "none"})
    assert resp.status_code == 200
    return cid


def _list_item(client: FlaskClient, cid: str) -> dict:
    items = client.get("/api/v2/conversations?limit=1000").get_json()
    return next(item for item in items if item["id"] == cid)


class TestMetadata:
    def test_defaults(self, client: FlaskClient):
        cid = _create_conv(client)
        resp = client.get(f"/api/v2/conversations/{cid}/metadata")
        assert resp.status_code == 200
        assert resp.get_json() == {
            "starred": False,
            "description": None,
            "tags": [],
            "pinned_order": None,
        }

    def test_star_persists_and_shows_in_list(self, client: FlaskClient):
        cid = _create_conv(client)
        # Warm the list cache first: starring must invalidate it.
        assert _list_item(client, cid)["starred"] is False

        resp = client.patch(
            f"/api/v2/conversations/{cid}/metadata", json={"starred": True}
        )
        assert resp.status_code == 200
        assert resp.get_json()["starred"] is True

        assert client.get(f"/api/v2/conversations/{cid}/metadata").get_json()["starred"]
        assert _list_item(client, cid)["starred"] is True

    def test_partial_update_and_null_clears(self, client: FlaskClient):
        cid = _create_conv(client)
        url = f"/api/v2/conversations/{cid}/metadata"
        client.patch(url, json={"starred": True, "tags": ["a", "b"]})
        client.patch(url, json={"description": "hello"})
        data = client.get(url).get_json()
        assert data["starred"] is True
        assert data["tags"] == ["a", "b"]
        assert data["description"] == "hello"

        client.patch(url, json={"description": None, "tags": None})
        data = client.get(url).get_json()
        assert data["description"] is None
        assert data["tags"] == []
        assert data["starred"] is True

    def test_metadata_does_not_touch_chat_config(self, client: FlaskClient):
        cid = _create_conv(client)
        before = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        client.patch(f"/api/v2/conversations/{cid}/metadata", json={"starred": True})
        after = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        assert before == after

    @pytest.mark.parametrize(
        "body",
        [
            {"starred": "yes"},
            {"tags": "a"},
            {"tags": ["a", 1]},
            {"pinned_order": True},
            {"description": 3},
            {"bogus": 1},
            ["starred"],
        ],
    )
    def test_bad_input_is_400(self, client: FlaskClient, body):
        cid = _create_conv(client)
        resp = client.patch(f"/api/v2/conversations/{cid}/metadata", json=body)
        assert resp.status_code == 400

    def test_null_unstars(self, client: FlaskClient):
        cid = _create_conv(client)
        url = f"/api/v2/conversations/{cid}/metadata"
        client.patch(url, json={"starred": True})
        resp = client.patch(url, json={"starred": None})
        assert resp.status_code == 200
        assert resp.get_json()["starred"] is False

    def test_folder_without_conversation_is_404(self, client: FlaskClient):
        from gptme.dirs import get_logs_dir

        cid = f"empty-{uuid4().hex[:8]}"
        (get_logs_dir() / cid).mkdir(parents=True)
        assert client.get(f"/api/v2/conversations/{cid}/metadata").status_code == 404
        resp = client.patch(
            f"/api/v2/conversations/{cid}/metadata", json={"starred": True}
        )
        assert resp.status_code == 404
        assert not (get_logs_dir() / cid / "metadata.toml").exists()

    def test_list_etag_tells_null_from_none_text(self, client: FlaskClient):
        cid = _create_conv(client)
        url = f"/api/v2/conversations/{cid}/metadata"
        client.patch(url, json={"description": "None"})
        etag = client.get("/api/v2/conversations?limit=1000").headers["ETag"]
        client.patch(url, json={"description": None})
        resp = client.get(
            "/api/v2/conversations?limit=1000", headers={"If-None-Match": etag}
        )
        assert resp.status_code == 200
        assert _list_item(client, cid)["description"] is None

    def test_scan_racing_an_edit_is_not_cached(self, client: FlaskClient, monkeypatch):
        """A list scan that overlaps an invalidation must not refill the cache."""
        import gptme.server.api_v2 as api_v2

        _create_conv(client)
        api_v2._invalidate_conversations_cache()
        real_scan = api_v2.get_user_conversations

        def scan_with_concurrent_edit(*args, **kwargs):
            api_v2._invalidate_conversations_cache()  # e.g. a star landing
            yield from real_scan(*args, **kwargs)

        monkeypatch.setattr(api_v2, "get_user_conversations", scan_with_concurrent_edit)
        assert client.get("/api/v2/conversations?limit=1000").status_code == 200
        assert api_v2._conversations_cache is None

        monkeypatch.setattr(api_v2, "get_user_conversations", real_scan)
        client.get("/api/v2/conversations?limit=1000")
        assert api_v2._conversations_cache is not None

    def test_unknown_conversation_is_404(self, client: FlaskClient):
        cid = f"missing-{uuid4().hex[:8]}"
        assert client.get(f"/api/v2/conversations/{cid}/metadata").status_code == 404
        resp = client.patch(
            f"/api/v2/conversations/{cid}/metadata", json={"starred": True}
        )
        assert resp.status_code == 404


class TestConfigPatchModelValidation:
    def test_unknown_provider_is_400(self, client: FlaskClient):
        cid = _create_conv(client)
        before = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"model": "nonexistent-provider/zzz"}},
        )
        assert resp.status_code == 400
        assert "nonexistent-provider" in resp.get_json()["error"]
        after = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        assert after == before

    def test_unknown_bare_model_is_400(self, client: FlaskClient):
        cid = _create_conv(client)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"model": "zzz-not-a-model"}},
        )
        assert resp.status_code == 400
        assert "zzz-not-a-model" in resp.get_json()["error"]

    def test_model_is_saved_trimmed(self, client: FlaskClient):
        cid = _create_conv(client)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"model": " openai/gpt-4o "}},
        )
        assert resp.status_code == 200, resp.get_json()
        config = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        assert config["chat"]["model"] == "openai/gpt-4o"

    @pytest.mark.parametrize(
        "model", ["openai/gpt-4o", "openai/some-future-model", "gpt-4o"]
    )
    def test_routable_models_are_accepted(self, client: FlaskClient, model: str):
        cid = _create_conv(client)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config", json={"chat": {"model": model}}
        )
        assert resp.status_code == 200, resp.get_json()
