import importlib.util
import io
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_model_freshness.py"
_SPEC = importlib.util.spec_from_file_location("check_model_freshness", _SCRIPT)
assert _SPEC and _SPEC.loader
check_model_freshness = importlib.util.module_from_spec(_SPEC)
# dataclasses resolve annotations via sys.modules, so register before exec
sys.modules[_SPEC.name] = check_model_freshness
_SPEC.loader.exec_module(check_model_freshness)


def test_globally_live_model_not_reported_stale_when_api_key_set(monkeypatch, tmp_path):
    # Regression: an authenticated /v1/models request returns a catalog filtered
    # by the account's provider/data-policy settings, so globally-live models were
    # reported as stale. The check must use the public catalog even if a key is set.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    src = tmp_path / "gptme"
    (src / "llm/models").mkdir(parents=True)
    (src / "llm/models/data.py").write_text('MODEL = "openrouter/z-ai/glm-5"\n')

    seen = []

    def fake_urlopen(req, timeout):
        seen.append(req)
        # Account-filtered view hides the model; the public catalog lists it.
        ids = [] if req.get_header("Authorization") else ["z-ai/glm-5"]
        return io.BytesIO(json.dumps({"data": [{"id": i} for i in ids]}).encode())

    monkeypatch.setattr(check_model_freshness.urllib.request, "urlopen", fake_urlopen)

    results = check_model_freshness.run_checks(src)

    assert [(r.model_id, r.is_live) for r in results] == [("z-ai/glm-5", True)]
    assert len(seen) == 1
    assert seen[0].get_header("Authorization") is None
