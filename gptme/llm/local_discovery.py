"""Probe well-known local OpenAI-compatible providers.

gptme should "just work" with a local Ollama or LM Studio instance. This module
detects those servers by hitting their real OpenAI-compatible ``/v1/models``
endpoint — not by checking whether a port is open, and not by guessing native
APIs such as Ollama's ``/api/tags``.

Candidates (loopback only):

- Ollama:    ``http://127.0.0.1:11434/v1/models``
- LM Studio: ``http://127.0.0.1:1234/v1/models``

A probe is a GET of that URL with a short timeout. Success requires HTTP 200
and an OpenAI models-list JSON body (``{"data": [{"id": ...}, ...]}``). Every
other outcome is returned with a reason so callers never silently drop a
candidate.

This module never writes config. Listing is opt-in display; persisting a
provider is ``gptme providers add``.

Disable probes with ``GPTME_NO_LOCAL_DISCOVERY=1`` (CI / hermetic tests).
"""

from __future__ import annotations

import errno
import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException
from typing import Literal
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 0.5
MAX_BODY_BYTES = 65_536
USER_AGENT = "gptme-local-discovery/1.0"

ProbeStatus = Literal["up", "down", "incompatible", "auth_required", "error"]


@dataclass(frozen=True)
class LocalProviderCandidate:
    """A well-known local OpenAI-compatible server to probe."""

    name: str
    display_name: str
    base_url: str
    hint: str

    @property
    def models_url(self) -> str:
        """The real OpenAI-compat models list URL (``{base_url}/models``)."""
        return self.base_url.rstrip("/") + "/models"


LOCAL_PROVIDER_CANDIDATES: tuple[LocalProviderCandidate, ...] = (
    LocalProviderCandidate(
        name="ollama",
        display_name="Ollama",
        base_url="http://127.0.0.1:11434/v1",
        hint="Install from https://ollama.com and run `ollama serve`",
    ),
    LocalProviderCandidate(
        name="lmstudio",
        display_name="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        hint="Open LM Studio → Local Server → Start",
    ),
)

# Hosts we will actually probe. 0.0.0.0 is the unspecified/any address,
# not loopback, so `_probe_one` refuses it.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Extra names treated as the same endpoint when matching config. Users
# often copy a bind address (`0.0.0.0`) into `base_url`.
_LOOPBACK_CONFIG_ALIASES = _LOOPBACK_HOSTS | {"0.0.0.0"}


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of probing one local provider candidate."""

    candidate: LocalProviderCandidate
    status: ProbeStatus
    reason: str
    models: tuple[str, ...] = ()
    configured_as: str | None = None
    elapsed_s: float = 0.0

    @property
    def is_up(self) -> bool:
        return self.status == "up"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.candidate.name,
            "display_name": self.candidate.display_name,
            "base_url": self.candidate.base_url,
            "models_url": self.candidate.models_url,
            "status": self.status,
            "reason": self.reason,
            "models": list(self.models),
            "configured_as": self.configured_as,
            "hint": self.candidate.hint,
        }


FetchFn = Callable[[str, float], tuple[int, bytes]]


def local_discovery_disabled() -> bool:
    """Return True when GPTME_NO_LOCAL_DISCOVERY disables probing."""
    raw = os.environ.get("GPTME_NO_LOCAL_DISCOVERY", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def discover_local_providers(
    *,
    candidates: Sequence[LocalProviderCandidate] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    configured: Sequence[object] | None = None,
    fetch: FetchFn | None = None,
) -> list[DiscoveryResult]:
    """Probe well-known local providers and return a result for each candidate.

    Every candidate is represented in the result list. Failures are never
    dropped — ``status`` and ``reason`` explain why a probe did not count as
    a live OpenAI-compatible provider.
    """
    if local_discovery_disabled():
        logger.debug("local provider discovery disabled via GPTME_NO_LOCAL_DISCOVERY")
        return []

    to_probe = (
        tuple(candidates) if candidates is not None else LOCAL_PROVIDER_CANDIDATES
    )
    if not to_probe:
        return []

    fetch_fn = fetch or _http_get
    configured_index = _index_configured(configured or ())

    if len(to_probe) == 1:
        return [_probe_one(to_probe[0], timeout, fetch_fn, configured_index)]

    results: list[DiscoveryResult | None] = [None] * len(to_probe)
    with ThreadPoolExecutor(max_workers=len(to_probe)) as pool:
        futs = {
            pool.submit(_probe_one, cand, timeout, fetch_fn, configured_index): i
            for i, cand in enumerate(to_probe)
        }
        for fut, idx in futs.items():
            results[idx] = fut.result()
    return [r for r in results if r is not None]


def _probe_one(
    candidate: LocalProviderCandidate,
    timeout: float,
    fetch: FetchFn,
    configured_index: dict[tuple[str, str, int, str], str],
) -> DiscoveryResult:
    url = candidate.models_url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not _is_loopback_host(host):
        result = DiscoveryResult(
            candidate=candidate,
            status="error",
            reason=f"refusing to probe non-loopback host {host!r}",
        )
        _log_result(result)
        return result

    t0 = time.monotonic()
    try:
        status_code, body = fetch(url, timeout)
    except TimeoutError:
        result = DiscoveryResult(
            candidate=candidate,
            status="down",
            reason=f"timeout after {timeout:.1f}s probing {url}",
            elapsed_s=time.monotonic() - t0,
            configured_as=_match_configured(candidate, configured_index),
        )
        _log_result(result)
        return result
    except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError) as e:
        result = DiscoveryResult(
            candidate=candidate,
            status="down",
            reason=f"not running ({_short_oserror(e)})",
            elapsed_s=time.monotonic() - t0,
            configured_as=_match_configured(candidate, configured_index),
        )
        _log_result(result)
        return result
    except OSError as e:
        # Connection refused often arrives as OSError on some platforms.
        reason = _short_oserror(e)
        status: ProbeStatus = "down" if _looks_like_unreachable(e) else "error"
        result = DiscoveryResult(
            candidate=candidate,
            status=status,
            reason=reason,
            elapsed_s=time.monotonic() - t0,
            configured_as=_match_configured(candidate, configured_index),
        )
        _log_result(result)
        return result
    except HTTPException as e:
        result = DiscoveryResult(
            candidate=candidate,
            status="error",
            reason=f"HTTP error probing {url}: {e}",
            elapsed_s=time.monotonic() - t0,
            configured_as=_match_configured(candidate, configured_index),
        )
        _log_result(result)
        return result

    elapsed = time.monotonic() - t0
    configured_as = _match_configured(candidate, configured_index)

    if status_code in {401, 403}:
        result = DiscoveryResult(
            candidate=candidate,
            status="auth_required",
            reason=f"HTTP {status_code} from {url} (reachable, auth required)",
            elapsed_s=elapsed,
            configured_as=configured_as,
        )
        _log_result(result)
        return result

    if status_code != 200:
        result = DiscoveryResult(
            candidate=candidate,
            status="error",
            reason=f"HTTP {status_code} from {url}",
            elapsed_s=elapsed,
            configured_as=configured_as,
        )
        _log_result(result)
        return result

    models, parse_error = _parse_openai_models(body)
    if parse_error:
        result = DiscoveryResult(
            candidate=candidate,
            status="incompatible",
            reason=f"{url} is not an OpenAI-compatible /v1/models endpoint: {parse_error}",
            elapsed_s=elapsed,
            configured_as=configured_as,
        )
        _log_result(result)
        return result

    result = DiscoveryResult(
        candidate=candidate,
        status="up",
        reason="ok",
        models=models,
        elapsed_s=elapsed,
        configured_as=configured_as,
    )
    _log_result(result)
    return result


def _http_get(url: str, timeout: float) -> tuple[int, bytes]:
    """GET ``url`` without following redirects. Loopback-only by construction."""
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise OSError("missing host")
    try:
        port = parsed.port
    except ValueError as e:
        raise OSError(f"invalid port in {url}") from e
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    if parsed.scheme != "http":
        raise OSError(f"unsupported scheme {parsed.scheme!r} (http only)")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    conn = HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request(
            "GET",
            path,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        resp = conn.getresponse()
        body = resp.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            raise OSError(f"response body exceeds {MAX_BODY_BYTES} bytes")
        return resp.status, body
    finally:
        conn.close()


def _parse_openai_models(body: bytes) -> tuple[tuple[str, ...], str | None]:
    """Return (model ids, error). Error is set when the body is not OpenAI-shaped."""
    if not body.strip():
        return (), "empty body"
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as e:
        return (), f"not UTF-8 ({e})"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        return (), f"not JSON ({e.msg})"
    if not isinstance(payload, dict):
        return (), f"JSON {type(payload).__name__} is not an object"
    data = payload.get("data")
    if not isinstance(data, list):
        # Deliberately reject Ollama's native {"models": [...]} /api/tags shape.
        if isinstance(payload.get("models"), list):
            return (), "native Ollama /api/tags shape, not OpenAI /v1/models"
        return (), "missing OpenAI 'data' list"
    ids: list[str] = []
    for item in data:
        if isinstance(item, str) and item:
            ids.append(item)
        elif isinstance(item, dict):
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id:
                ids.append(model_id)
            else:
                return (), f"data item missing valid 'id' field: {item!r}"
        else:
            return (), f"data item is not a model object: {type(item).__name__}"
    return tuple(ids), None


def _index_configured(
    configured: Sequence[object],
) -> dict[tuple[str, str, int, str], str]:
    index: dict[tuple[str, str, int, str], str] = {}
    for item in configured:
        name = getattr(item, "name", None)
        base_url = getattr(item, "base_url", None)
        if not isinstance(name, str) or not isinstance(base_url, str):
            continue
        key = _endpoint_key(base_url)
        if key is not None:
            index[key] = name
    return index


def _match_configured(
    candidate: LocalProviderCandidate, index: dict[tuple[str, str, int, str], str]
) -> str | None:
    key = _endpoint_key(candidate.base_url)
    if key is None:
        return None
    return index.get(key)


def _endpoint_key(url: str) -> tuple[str, str, int, str] | None:
    """Return a canonical (scheme, host, port, path) key for endpoint identity matching."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    scheme = parsed.scheme.lower()
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    canonical = "loopback" if _is_loopback_alias(host) else host
    path = parsed.path.rstrip("/") or "/"
    return scheme, canonical, port, path


def _is_loopback_host(host: str) -> bool:
    return host.lower() in _LOOPBACK_HOSTS


def _is_loopback_alias(host: str) -> bool:
    return host.lower() in _LOOPBACK_CONFIG_ALIASES


def _looks_like_unreachable(exc: OSError) -> bool:
    if isinstance(exc, ConnectionRefusedError | ConnectionResetError | BrokenPipeError):
        return True
    err = getattr(exc, "errno", None)
    if err in {
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.ECONNABORTED,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
    }:
        return True
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "connection refused",
            "connect call failed",
            "name or service not known",
            "nodename nor servname",
            "network is unreachable",
        )
    )


def _short_oserror(exc: BaseException) -> str:
    msg = str(exc).strip() or type(exc).__name__
    # urllib/http.client wrap "Connection refused" in longer strings.
    if "connection refused" in msg.lower():
        return "connection refused"
    if "timed out" in msg.lower():
        return "timeout"
    return msg


def _log_result(result: DiscoveryResult) -> None:
    name = result.candidate.name
    url = result.candidate.models_url
    if result.status == "up":
        logger.info(
            "local provider %s up at %s (%d models)",
            name,
            url,
            len(result.models),
        )
    elif result.status == "incompatible":
        logger.warning("local provider %s ignored: %s", name, result.reason)
    else:
        logger.info(
            "local provider %s not listed as available: %s", name, result.reason
        )
