"""Tests for local OpenAI-compatible provider auto-discovery.

Probes hit a real HTTP server on loopback so we assert the actual
``/v1/models`` path and payload shape, not a mocked URL guess.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from gptme.config.models import ProviderConfig
from gptme.llm.local_discovery import (
    LOCAL_PROVIDER_CANDIDATES,
    LocalProviderCandidate,
    _endpoint_key,
    discover_local_providers,
)


class _ModelsHandler(BaseHTTPRequestHandler):
    """Configurable /v1/models fixture. Class attrs set per test."""

    status: int = 200
    body: bytes = b""
    seen_paths: list[str] = []
    delay_s: float = 0.0

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.delay_s:
            import time

            time.sleep(self.delay_s)
        path = self.path.split("?", 1)[0]
        type(self).seen_paths.append(path)
        if path != "/v1/models":
            self.send_error(404)
            return
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        if self.body:
            self.wfile.write(self.body)


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    base = f"http://127.0.0.1:{port}/v1"
    return server, base


def _candidate(base_url: str, name: str = "ollama") -> LocalProviderCandidate:
    return LocalProviderCandidate(
        name=name,
        display_name=name,
        base_url=base_url,
        hint="test",
    )


def _openai_models_body(*ids: str) -> bytes:
    return json.dumps(
        {
            "object": "list",
            "data": [{"id": model_id, "object": "model"} for model_id in ids],
        }
    ).encode()


@pytest.fixture
def handler_cls():
    class H(_ModelsHandler):
        status = 200
        body = _openai_models_body("llama3.2:3b", "mistral:7b")
        seen_paths: list[str] = []
        delay_s = 0.0

    return H


def test_candidates_point_at_v1_models() -> None:
    """Hardcoded probes must hit /v1/models, not native APIs like /api/tags."""
    by_name = {c.name: c for c in LOCAL_PROVIDER_CANDIDATES}
    assert by_name["ollama"].models_url == "http://127.0.0.1:11434/v1/models"
    assert by_name["lmstudio"].models_url == "http://127.0.0.1:1234/v1/models"
    assert "/api/tags" not in by_name["ollama"].models_url


def test_discovers_openai_compat_models(handler_cls) -> None:
    server, base = _serve(handler_cls)
    try:
        results = discover_local_providers(
            candidates=[_candidate(base)],
            timeout=1.0,
        )
    finally:
        server.shutdown()

    assert len(results) == 1
    result = results[0]
    assert result.status == "up"
    assert result.models == ("llama3.2:3b", "mistral:7b")
    assert result.reason == "ok"
    assert handler_cls.seen_paths == ["/v1/models"]
    assert result.candidate.models_url.endswith("/v1/models")


def test_reports_connection_refused_instead_of_dropping() -> None:
    # Keep the socket bound-but-not-listening so the port stays reserved for
    # the duration of the probe, preventing any other process from binding to
    # it and causing spurious non-refused results.
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}/v1"

    try:
        results = discover_local_providers(
            candidates=[_candidate(base, name="lmstudio")],
            timeout=0.3,
        )
    finally:
        sock.close()

    assert len(results) == 1
    assert results[0].status == "down"
    assert (
        "connection refused" in results[0].reason.lower()
        or "not running" in results[0].reason.lower()
    )
    assert results[0].models == ()


def test_rejects_native_ollama_tags_shape(handler_cls) -> None:
    """A 200 body that looks like /api/tags is incompatible, not a hit."""
    handler_cls.body = json.dumps(
        {"models": [{"name": "llama3.2:3b", "size": 1}]}
    ).encode()
    server, base = _serve(handler_cls)
    try:
        results = discover_local_providers(
            candidates=[_candidate(base)],
            timeout=1.0,
        )
    finally:
        server.shutdown()

    assert results[0].status == "incompatible"
    assert "api/tags" in results[0].reason or "data" in results[0].reason
    assert results[0].models == ()
    assert handler_cls.seen_paths == ["/v1/models"]


def test_rejects_html_on_known_port(handler_cls) -> None:
    handler_cls.body = b"<html>not a model server</html>"
    server, base = _serve(handler_cls)
    try:
        results = discover_local_providers(
            candidates=[_candidate(base)],
            timeout=1.0,
        )
    finally:
        server.shutdown()

    assert results[0].status == "incompatible"
    assert "JSON" in results[0].reason


def test_auth_required_is_not_silent(handler_cls) -> None:
    handler_cls.status = 401
    handler_cls.body = b'{"error":"unauthorized"}'
    server, base = _serve(handler_cls)
    try:
        results = discover_local_providers(
            candidates=[_candidate(base)],
            timeout=1.0,
        )
    finally:
        server.shutdown()

    assert results[0].status == "auth_required"
    assert "401" in results[0].reason


def test_http_404_is_reported(handler_cls) -> None:
    handler_cls.status = 404
    handler_cls.body = b"not found"
    server, base = _serve(handler_cls)
    try:
        results = discover_local_providers(
            candidates=[_candidate(base)],
            timeout=1.0,
        )
    finally:
        server.shutdown()

    assert results[0].status == "error"
    assert "404" in results[0].reason


def test_marks_already_configured_loopback_alias(handler_cls) -> None:
    server, base = _serve(handler_cls)
    try:
        # Config uses localhost; probe uses 127.0.0.1 — same endpoint.
        port = int(base.rsplit(":", 1)[1].split("/")[0])
        configured = [
            ProviderConfig(
                name="my-ollama",
                base_url=f"http://localhost:{port}/v1",
            )
        ]
        results = discover_local_providers(
            candidates=[_candidate(base, name="ollama")],
            configured=configured,
            timeout=1.0,
        )
    finally:
        server.shutdown()

    assert results[0].status == "up"
    assert results[0].configured_as == "my-ollama"


def test_env_disable_returns_empty(monkeypatch, handler_cls) -> None:
    monkeypatch.setenv("GPTME_NO_LOCAL_DISCOVERY", "1")
    server, base = _serve(handler_cls)
    try:
        results = discover_local_providers(
            candidates=[_candidate(base)],
            timeout=1.0,
        )
    finally:
        server.shutdown()
    assert results == []
    assert handler_cls.seen_paths == []


def test_refuses_non_loopback_host() -> None:
    results = discover_local_providers(
        candidates=[_candidate("http://example.com:11434/v1", name="remote-ollama")],
        timeout=0.2,
    )
    assert results[0].status == "error"
    assert "non-loopback" in results[0].reason


def test_up_with_empty_model_list_still_counts(handler_cls) -> None:
    handler_cls.body = json.dumps({"object": "list", "data": []}).encode()
    server, base = _serve(handler_cls)
    try:
        results = discover_local_providers(
            candidates=[_candidate(base)],
            timeout=1.0,
        )
    finally:
        server.shutdown()
    assert results[0].status == "up"
    assert results[0].models == ()


def test_case_distinct_paths_are_not_equal() -> None:
    # HTTP paths are case-sensitive; /V1 and /v1 are different resources.
    key_lower = _endpoint_key("http://localhost:11434/v1")
    key_upper = _endpoint_key("http://localhost:11434/V1")
    assert key_lower is not None
    assert key_upper is not None
    assert key_lower != key_upper


def test_default_candidates_include_ollama_and_lmstudio() -> None:
    names = [c.name for c in LOCAL_PROVIDER_CANDIDATES]
    assert names == ["ollama", "lmstudio"]


def test_endpoint_key_malformed_port_returns_none() -> None:
    # A TOML config typo like base_url="http://localhost:abc/v1" must not crash
    # _index_configured (and therefore gptme providers list) with a ValueError.
    assert _endpoint_key("http://localhost:abc/v1") is None
    assert _endpoint_key("http://127.0.0.1:notaport/v1") is None


def test_refuses_unspecified_address() -> None:
    # 0.0.0.0 is a bind address, not loopback. Probe must refuse it; config
    # matching still treats it as the same endpoint as 127.0.0.1.
    results = discover_local_providers(
        candidates=[_candidate("http://0.0.0.0:11434/v1", name="wildcard")],
        timeout=0.2,
    )
    assert results[0].status == "error"
    assert "non-loopback" in results[0].reason
    assert "0.0.0.0" in results[0].reason


def test_zero_addr_config_matches_loopback() -> None:
    key_loop = _endpoint_key("http://127.0.0.1:11434/v1")
    key_zero = _endpoint_key("http://0.0.0.0:11434/v1")
    key_local = _endpoint_key("http://localhost:11434/v1")
    assert key_loop is not None
    assert key_loop == key_zero == key_local


def test_malformed_candidate_port_does_not_crash() -> None:
    # Custom candidates are public API. A typo in the port must return an
    # error result, not propagate ValueError out of gptme providers list.
    results = discover_local_providers(
        candidates=[_candidate("http://127.0.0.1:abc/v1", name="bad-port")],
        timeout=0.2,
    )
    assert len(results) == 1
    assert results[0].status == "error"
    assert "invalid port" in results[0].reason.lower()
