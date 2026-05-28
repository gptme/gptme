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
