from types import SimpleNamespace

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip


def _set_openrouter_key(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.setattr(
        "gptme.config.get_config",
        lambda: SimpleNamespace(get_env=lambda key, default=None: value),
    )


def test_tts_endpoint_rejects_non_string_text(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    _set_openrouter_key(monkeypatch, "test-key")

    response = client.post("/api/v2/audio/speech", json={"text": 1})

    assert response.status_code == 400
    assert response.get_json() == {"error": "text must be a string"}


@pytest.mark.parametrize("field", ["model", "voice"])
def test_tts_endpoint_rejects_non_string_optional_fields(
    field: str, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    _set_openrouter_key(monkeypatch, "test-key")

    response = client.post(
        "/api/v2/audio/speech", json={"text": "Hello", field: ["bad"]}
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": f"{field} must be a string"}


def test_tts_endpoint_returns_400_when_api_key_missing(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    _set_openrouter_key(monkeypatch, None)

    response = client.post("/api/v2/audio/speech", json={"text": "Hello"})

    assert response.status_code == 400
    body = response.get_json()
    assert body is not None
    assert "OPENROUTER_API_KEY not configured" in body["error"]
    assert "gptme-tts" in body["error"]


def test_tts_endpoint_maps_provider_errors_to_bad_gateway(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    _set_openrouter_key(monkeypatch, "test-key")
    monkeypatch.setattr(
        "gptme.server.tts_api.requests.post",
        lambda *args, **kwargs: SimpleNamespace(
            ok=False,
            status_code=400,
            text="invalid voice",
        ),
    )

    response = client.post("/api/v2/audio/speech", json={"text": "Hello"})

    assert response.status_code == 502
    assert response.get_json() == {"error": "TTS provider error: 400"}
