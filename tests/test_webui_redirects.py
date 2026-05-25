from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REDIRECTS_PATH = ROOT / "webui" / "public" / "_redirects"


def _load_redirect_rules() -> set[tuple[str, str, str]]:
    rules: set[tuple[str, str, str]] = set()
    for raw_line in REDIRECTS_PATH.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        source, destination, status = line.split()
        rules.add((source, destination, status))
    return rules


def test_webui_cloudflare_pages_redirects_cover_spa_deep_links():
    assert REDIRECTS_PATH.exists(), (
        "webui/public/_redirects is required for hosted deep links"
    )

    rules = _load_redirect_rules()
    expected_rules = {
        ("/chat", "/", "200"),
        ("/chat/*", "/", "200"),
        ("/tasks", "/", "200"),
        ("/tasks/*", "/", "200"),
        ("/agents", "/", "200"),
        ("/workspaces", "/", "200"),
        ("/history", "/", "200"),
        ("/external-sessions", "/", "200"),
        ("/workspace/*", "/", "200"),
    }
    missing = expected_rules - rules
    assert not missing, (
        f"missing Cloudflare Pages deep-link redirects: {sorted(missing)}"
    )


def test_webui_redirects_do_not_swallow_unknown_api_paths():
    rules = _load_redirect_rules()
    assert ("/*", "/index.html", "200") not in rules
    assert all(not source.startswith("/api") for source, _, _ in rules), (
        "API routes should fall through to the hosted 404 page"
    )
