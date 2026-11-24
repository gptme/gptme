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

    @pytest.mark.parametrize(
        "model", ["openai/gpt-4o", "openai/some-future-model", "gpt-4o"]
    )
    def test_routable_models_are_accepted(self, client: FlaskClient, model: str):
        cid = _create_conv(client)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config", json={"chat": {"model": model}}
        )
        assert resp.status_code == 200, resp.get_json()
