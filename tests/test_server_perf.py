"""Performance gate: GET /api/v2/conversations with 100+ seeded conversations.

Regression guard for O(N) filesystem scan on the conversations list endpoint.
With the partial-cache-update fix (#2934), a cold scan is O(N) but subsequent
warm reads are O(1) (cache hit). Both are bounded here.
"""

import json
import math
import time

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient

N_CONVERSATIONS = 100
N_SAMPLES = 20
COLD_SCAN_LIMIT_MS = 500.0  # generous upper bound for 100-conversation cold scan
# Warm p95 must be < 75% of cold scan time.  Environment-agnostic: cache hit
# is O(1) so warm << cold.  Broken cache → warm ≈ cold (ratio approaches 1.0).
WARM_TO_COLD_RATIO_MAX = 0.75


def _seed_conversations(tmp_path, n: int) -> None:
    """Write n conversation directories under tmp_path."""
    msg = json.dumps(
        {"role": "user", "content": "hello", "timestamp": "2026-01-01T00:00:00"}
    )
    for i in range(n):
        conv_dir = tmp_path / f"perf-conv-{i:04d}"
        conv_dir.mkdir()
        (conv_dir / "conversation.jsonl").write_text(msg + "\n")


@pytest.mark.slow
def test_conversations_list_cold_scan_under_500ms(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Cold GET /api/v2/conversations with 100 conversations must finish < 500ms.

    Catches catastrophic regressions where a cold scan becomes O(N^2) or hits
    a pathological code path (e.g., opening every file multiple times).
    """
    import gptme.server.api_v2 as api_v2_module

    _seed_conversations(tmp_path, N_CONVERSATIONS)
    monkeypatch.setattr("gptme.logmanager.conversations.get_logs_dir", lambda: tmp_path)
    api_v2_module._invalidate_conversations_cache()

    start = time.perf_counter()
    resp = client.get("/api/v2/conversations")
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == N_CONVERSATIONS, (
        f"expected {N_CONVERSATIONS} conversations, got {len(data)}"
    )
    assert elapsed_ms < COLD_SCAN_LIMIT_MS, (
        f"cold scan took {elapsed_ms:.1f}ms > {COLD_SCAN_LIMIT_MS}ms limit "
        f"({N_CONVERSATIONS} conversations)"
    )


@pytest.mark.slow
def test_conversations_list_warm_cache_faster_than_cold(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Warm GET /api/v2/conversations p95 must be substantially faster than a cold scan.

    Measures both cold (O(N) filesystem scan) and warm (O(1) cache hit) latencies
    in the same test and asserts warm_p95 < cold * WARM_TO_COLD_RATIO_MAX.  This is
    environment-agnostic: the ratio catches a broken cache (warm ≈ cold) regardless
    of how fast or slow the CI machine is.  The pre-#2934 regression pattern had
    every message POST trigger _invalidate_conversations_cache(), making warm ≈ cold.
    """
    import gptme.server.api_v2 as api_v2_module

    _seed_conversations(tmp_path, N_CONVERSATIONS)
    monkeypatch.setattr("gptme.logmanager.conversations.get_logs_dir", lambda: tmp_path)
    api_v2_module._invalidate_conversations_cache()

    # Cold scan — O(N) filesystem scan, fills the cache
    start = time.perf_counter()
    resp = client.get("/api/v2/conversations")
    cold_ms = (time.perf_counter() - start) * 1000
    assert resp.status_code == 200
    assert len(resp.get_json()) == N_CONVERSATIONS

    # Warm reads — O(1) cache hits
    latencies: list[float] = []
    for _ in range(N_SAMPLES):
        start = time.perf_counter()
        resp = client.get("/api/v2/conversations")
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert resp.status_code == 200
        latencies.append(elapsed_ms)

    latencies.sort()
    p95_index = math.ceil(N_SAMPLES * 0.95) - 1
    p95 = latencies[p95_index]

    warm_limit_ms = cold_ms * WARM_TO_COLD_RATIO_MAX
    assert p95 < warm_limit_ms, (
        f"warm p95={p95:.1f}ms ≥ {WARM_TO_COLD_RATIO_MAX:.0%} of cold={cold_ms:.1f}ms "
        f"(limit={warm_limit_ms:.1f}ms). Cache may not be working. "
        f"Sorted warm samples (ms): {[round(x, 1) for x in latencies]}"
    )
