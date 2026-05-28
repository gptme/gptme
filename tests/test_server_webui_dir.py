"""Tests for serving a custom web UI directory from gptme-server.

Coverage for gptme/gptme#2612: a self-hoster can point gptme-server at the
modern React webui build via ``--webui-dir`` / ``GPTME_WEBUI_DIR`` instead of
the bundled legacy static UI.
"""

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)
pytest.importorskip(
    "flask_cors", reason="flask-cors not installed, install server extras (-E server)"
)


def test_default_static_folder_is_legacy_bundle():
    from gptme.server.app import create_app, static_path

    app = create_app()
    assert app.static_folder == str(static_path)


def test_webui_dir_arg_overrides_static_folder(tmp_path):
    from gptme.server.app import create_app

    (tmp_path / "index.html").write_text("<html>modern</html>")
    app = create_app(webui_dir=tmp_path)

    assert app.static_folder == str(tmp_path)
    with app.test_client() as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"modern" in resp.data


def test_webui_dir_env_var_overrides_static_folder(tmp_path, monkeypatch):
    from gptme.server.app import create_app

    (tmp_path / "index.html").write_text("<html>env-modern</html>")
    monkeypatch.setenv("GPTME_WEBUI_DIR", str(tmp_path))
    app = create_app()

    assert app.static_folder == str(tmp_path)


def test_webui_dir_arg_takes_precedence_over_env(tmp_path, monkeypatch):
    from gptme.server.app import create_app

    arg_dir = tmp_path / "arg"
    env_dir = tmp_path / "env"
    arg_dir.mkdir()
    env_dir.mkdir()
    monkeypatch.setenv("GPTME_WEBUI_DIR", str(env_dir))
    app = create_app(webui_dir=arg_dir)

    assert app.static_folder == str(arg_dir)


def test_missing_webui_dir_raises(tmp_path):
    from gptme.server.app import create_app

    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        create_app(webui_dir=missing)


def test_computer_route_falls_back_to_index_for_custom_webui(tmp_path):
    """Custom webui builds (React/Vite) don't ship computer.html; /computer must
    serve index.html so client-side routing handles it instead of 404-ing."""
    from gptme.server.app import create_app

    (tmp_path / "index.html").write_text("<html>spa</html>")
    # Intentionally no computer.html in tmp_path
    app = create_app(webui_dir=tmp_path)

    with app.test_client() as client:
        resp = client.get("/computer")
        assert resp.status_code == 200
        assert b"spa" in resp.data


def test_spa_catch_all_serves_index_for_unknown_paths(tmp_path):
    """Unknown deep-link paths (e.g. /settings, /conversations/xyz) should
    return index.html when a custom webui dir is configured."""
    from gptme.server.app import create_app

    (tmp_path / "index.html").write_text("<html>spa</html>")
    app = create_app(webui_dir=tmp_path)

    with app.test_client() as client:
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert b"spa" in resp.data

        resp = client.get("/conversations/some-id")
        assert resp.status_code == 200
        assert b"spa" in resp.data


def test_spa_catch_all_serves_actual_asset_files(tmp_path):
    """Existing static assets (JS/CSS/images) must be served as-is, not
    replaced with index.html, when the custom webui dir is active."""
    from gptme.server.app import create_app

    (tmp_path / "index.html").write_text("<html>spa</html>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "main.js").write_text("console.log('hi')")
    app = create_app(webui_dir=tmp_path)

    with app.test_client() as client:
        resp = client.get("/assets/main.js")
        assert resp.status_code == 200
        assert b"console.log" in resp.data
