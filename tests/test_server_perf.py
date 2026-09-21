"""Performance gate: GET /api/v2/conversations with 100+ seeded conversations.

Regression guard for O(N) filesystem scan on the conversations list endpoint.
With the partial-cache-update fix (#2934), a cold scan is O(N) but subsequent
warm reads are O(1) (cache hit). Both are bounded here.
"""

import json
import math
import tempfile
import time
from pathlib import Path

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient

N_CONVERSATIONS = 100
N_SMALL = 25
N_SAMPLES = 20
# Hang detector only. Absolute 500/600/650ms gates flaked on loaded GitHub
# runners (observed 740-844ms). 5s still fails a hung or truly pathological
# scan without encoding runner speed.
COLD_SCAN_CATASTROPHE_MS = 5000.0
# Linear size ratio is 4x; quadratic would be ~16x. 3x slack leaves room for
# Flask overhead and CI noise while still catching a superlinear scan.
COLD_SCAN_SCALE_SLACK = 3.0
# Warm p95 must be < 75% of cold scan time.  Environment-agnostic: cache hit
# is O(1) so warm << cold.  Broken cache → warm ≈ cold (ratio approaches 1.0).
WARM_TO_COLD_RATIO_MAX = 0.75


def _seed_conversations(tmp_path: Path, n: int) -> None:
    """Write n conversation directories under tmp_path."""
    msg = json.dumps(
        {"role": "user", "content": "hello", "timestamp": "2026-01-01T00:00:00"}
    )
    for i in range(n):
        conv_dir = tmp_path / f"perf-conv-{i:04d}"
        conv_dir.mkdir()
        (conv_dir / "conversation.jsonl").write_text(msg + "\n")


def _cold_list_conversations(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch, n: int
) -> tuple[float, int]:
    """Seed n conversations, invalidate cache, return (elapsed_ms, count)."""
    import gptme.server.api_v2 as api_v2_module

    with tempfile.TemporaryDirectory() as tmpdir:
        logs_dir = Path(tmpdir)
        _seed_conversations(logs_dir, n)
        monkeypatch.setattr(
            "gptme.logmanager.conversations.get_logs_dir", lambda: logs_dir
        )
        api_v2_module._invalidate_conversations_cache()
        start = time.perf_counter()
        resp = client.get("/api/v2/conversations")
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        return elapsed_ms, len(data)


@pytest.mark.slow
def test_conversations_list_cold_scan_is_near_linear(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold GET /api/v2/conversations should scale near-linearly with N.

    Replaces the old absolute `under_500ms` wall-clock gate. That assertion
    measured runner load, not scan complexity: CI bumped 500→600→650ms and
    still failed at 740-844ms on healthy master. Catch O(N^2)/pathology via
    size scaling plus a hang ceiling. The cache invariant lives in
    test_conversations_list_warm_cache_faster_than_cold.
    """
    small_ms, small_n = _cold_list_conversations(client, monkeypatch, N_SMALL)
    large_ms, large_n = _cold_list_conversations(client, monkeypatch, N_CONVERSATIONS)
    assert small_n == N_SMALL
    assert large_n == N_CONVERSATIONS, (
        f"expected {N_CONVERSATIONS} conversations, got {large_n}"
    )
    assert large_ms < COLD_SCAN_CATASTROPHE_MS, (
        f"cold scan of {N_CONVERSATIONS} took {large_ms:.1f}ms "
        f"> {COLD_SCAN_CATASTROPHE_MS:.0f}ms hang bound"
    )
    scale = large_ms / max(small_ms, 1.0)
    max_scale = (N_CONVERSATIONS / N_SMALL) * COLD_SCAN_SCALE_SLACK
    assert scale < max_scale, (
        f"cold scan scaled {scale:.1f}x from {N_SMALL}→{N_CONVERSATIONS} "
        f"({small_ms:.1f}ms → {large_ms:.1f}ms); near-linear expected "
        f"(<{max_scale:.0f}x). Scan may have gone superlinear."
    )


@pytest.mark.slow
def test_conversations_list_warm_cache_faster_than_cold(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Warm GET /api/v2/conversations p95 must be substantially faster than a cold scan.

    Measures both cold (O(N) filesystem scan) and warm (O(1) cache hit) latencies
    in the same test and asserts warm_p95 < cold * WARM_TO_COLD_RATIO_MAX.  This is
    environment-agnostic: the ratio catches a broken cache (warm ≈ cold) regardless
    of how fast or slow the CI machine is.  The pre-#2934 regression pattern had
    every message POST trigger _invalidate_conversations_cache(), making warm ≈ cold.
    """
    import gptme.server.api_v2 as api_v2_module

    with tempfile.TemporaryDirectory() as tmpdir:
        logs_dir = Path(tmpdir)
        _seed_conversations(logs_dir, N_CONVERSATIONS)
        monkeypatch.setattr(
            "gptme.logmanager.conversations.get_logs_dir", lambda: logs_dir
        )
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
