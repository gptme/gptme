"""Tests for the gptme-server --webui-dir / GPTME_WEBUI_DIR override.

Coverage for serving a custom (modern) web UI directory from gptme-server
instead of the legacy webui bundled in the package.
"""

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)


def test_webui_dir_serves_custom_index(tmp_path):
    from gptme.server.app import create_app

    (tmp_path / "index.html").write_text("<title>modern webui</title>")

    app = create_app(webui_dir=str(tmp_path))
    with app.test_client() as client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert b"modern webui" in resp.data


def test_webui_dir_missing_index_raises(tmp_path):
    from gptme.server.app import create_app

    with pytest.raises(ValueError, match="index.html"):
        create_app(webui_dir=str(tmp_path))


def test_default_uses_bundled_static():
    from gptme.server.app import create_app, static_path

    app = create_app()
    assert app.static_folder == str(static_path)
