"""Conversation metadata sidecar and config-PATCH model validation (gptme/gptme#5187)."""

from uuid import uuid4

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

pytestmark = [pytest.mark.timeout(15)]


def _create_conv(client: FlaskClient) -> str:
    cid = f"conv-metadata-{uuid4().hex[:8]}"
    resp = client.put(f"/api/v2/conversations/{cid}", json={"prompt": "none"})
    assert resp.status_code == 200
    return cid


def _list_item(client: FlaskClient, cid: str) -> dict:
    items = client.get("/api/v2/conversations?limit=1000").get_json()
    return next(item for item in items if item["id"] == cid)


class TestMetadata:
    @pytest.mark.parametrize(
        ("contents", "error_match"),
        [
            (b'description = "unterminated', "Unexpected end of file"),
            (b"\xff", "invalid start byte"),
        ],
    )
    def test_patch_preserves_unparseable_sidecar(self, tmp_path, contents, error_match):
        from gptme.logmanager.metadata import read_metadata, update_metadata

        sidecar = tmp_path / "metadata.toml"
        sidecar.write_bytes(contents)
        # List reads stay tolerant, but edits must not replace unreadable data.
        assert read_metadata(tmp_path).starred is False
        with pytest.raises(ValueError, match=error_match):
            update_metadata(tmp_path, {"starred": True})
        assert sidecar.read_bytes() == contents
        assert not list(tmp_path.glob(".metadata-*"))

    def test_patch_preserves_temporarily_unreadable_sidecar(
        self, tmp_path, monkeypatch
    ):
        from pathlib import Path

        from gptme.logmanager.metadata import read_metadata, update_metadata

        sidecar = tmp_path / "metadata.toml"
        contents = b'description = "keep me"\ntags = ["work"]\n'
        sidecar.write_bytes(contents)
        real_read = Path.read_text

        def unreadable(path, *args, **kwargs):
            if path == sidecar:
                raise PermissionError("temporarily unreadable metadata")
            return real_read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", unreadable)
        assert read_metadata(tmp_path).tags == []
        with pytest.raises(PermissionError):
            update_metadata(tmp_path, {"starred": True})
        assert sidecar.read_bytes() == contents
        assert not list(tmp_path.glob(".metadata-*"))

    def test_patch_preserves_unknown_and_mistyped_sidecar_fields(self, tmp_path):
        """An unrelated edit must not drop keys this version does not know."""
        from gptme.logmanager.metadata import METADATA_FILENAME, update_metadata

        sidecar = tmp_path / METADATA_FILENAME
        sidecar.write_text(
            'color = "red"\ntags = "work"\ndescription = "keep me"\n',
            encoding="utf-8",
        )
        meta = update_metadata(tmp_path, {"starred": True})

        assert meta.starred is True
        assert meta.description == "keep me"
        # `tags = "work"` is mistyped, so it reads as the default list...
        assert meta.tags == []
        text = sidecar.read_text(encoding="utf-8")
        # ...but an unrelated edit must not overwrite it or drop `color`.
        assert 'tags = "work"' in text
        assert 'color = "red"' in text

    def test_patch_clears_field_by_removing_the_key(self, tmp_path):
        from gptme.logmanager.metadata import METADATA_FILENAME, update_metadata

        sidecar = tmp_path / METADATA_FILENAME
        sidecar.write_text('starred = true\ndescription = "x"\n', encoding="utf-8")
        meta = update_metadata(tmp_path, {"description": None})

        assert meta.description is None
        assert "description" not in sidecar.read_text(encoding="utf-8")

    def test_patch_unreadable_sidecar_is_409_json(self, client: FlaskClient):
        from gptme.dirs import get_logs_dir
        from gptme.logmanager.metadata import METADATA_FILENAME

        cid = _create_conv(client)
        sidecar = get_logs_dir() / cid / METADATA_FILENAME
        sidecar.write_bytes(b'description = "unterminated')
        resp = client.patch(
            f"/api/v2/conversations/{cid}/metadata", json={"starred": True}
        )
        assert resp.status_code == 409, resp.data
        assert "error" in resp.get_json()
        # The unreadable file is left untouched.
        assert sidecar.read_bytes() == b'description = "unterminated'

    def test_defaults(self, client: FlaskClient):
        cid = _create_conv(client)
        resp = client.get(f"/api/v2/conversations/{cid}/metadata")
        assert resp.status_code == 200
        assert resp.get_json() == {
            "starred": False,
            "description": None,
            "tags": [],
            "pinned_order": None,
        }

    def test_list_schema_preserves_metadata(self, client: FlaskClient):
        from gptme.server.openapi_docs import ConversationListItem

        cid = _create_conv(client)
        metadata = {
            "starred": True,
            "description": "project notes",
            "tags": ["work"],
            "pinned_order": 2,
        }
        assert (
            client.patch(
                f"/api/v2/conversations/{cid}/metadata", json=metadata
            ).status_code
            == 200
        )
        item = ConversationListItem.model_validate(_list_item(client, cid)).model_dump()
        assert {key: item.get(key) for key in metadata} == metadata

    def test_non_ascii_text_round_trips_and_lone_surrogates_are_400(
        self, client: FlaskClient
    ):
        from gptme.dirs import get_logs_dir
        from gptme.logmanager.metadata import METADATA_FILENAME

        cid = _create_conv(client)
        url = f"/api/v2/conversations/{cid}/metadata"
        resp = client.patch(url, json={"description": "日本語", "tags": ["🚀"]})
        assert resp.status_code == 200, resp.get_json()
        sidecar = get_logs_dir() / cid / METADATA_FILENAME
        assert "日本語" in sidecar.read_bytes().decode("utf-8")
        assert client.get(url).get_json()["tags"] == ["🚀"]

        # JSON can carry a lone surrogate that no file encoding can write.
        resp = client.patch(
            url,
            data='{"description": "\\ud800"}',
            content_type="application/json",
        )
        assert resp.status_code == 400, resp.get_json()

    def test_star_persists_and_shows_in_list(self, client: FlaskClient):
        cid = _create_conv(client)
        # Warm the list cache first: starring must invalidate it.
        assert _list_item(client, cid)["starred"] is False

        resp = client.patch(
            f"/api/v2/conversations/{cid}/metadata", json={"starred": True}
        )
        assert resp.status_code == 200
        assert resp.get_json()["starred"] is True

        assert client.get(f"/api/v2/conversations/{cid}/metadata").get_json()["starred"]
        assert _list_item(client, cid)["starred"] is True

    def test_partial_update_and_null_clears(self, client: FlaskClient):
        cid = _create_conv(client)
        url = f"/api/v2/conversations/{cid}/metadata"
        client.patch(url, json={"starred": True, "tags": ["a", "b"]})
        client.patch(url, json={"description": "hello"})
        data = client.get(url).get_json()
        assert data["starred"] is True
        assert data["tags"] == ["a", "b"]
        assert data["description"] == "hello"

        client.patch(url, json={"description": None, "tags": None})
        data = client.get(url).get_json()
        assert data["description"] is None
        assert data["tags"] == []
        assert data["starred"] is True

    @pytest.mark.parametrize("method", ["get", "patch"])
    def test_invalid_conversation_id_is_400(self, client: FlaskClient, method):
        cid = "c" * 300
        url = f"/api/v2/conversations/{cid}/metadata"
        if method == "get":
            resp = client.get(url)
        else:
            resp = client.patch(url, json={"starred": True})
        assert resp.status_code == 400
        assert "conversation_id" in resp.get_json()["error"]

    def test_metadata_does_not_touch_chat_config(self, client: FlaskClient):
        cid = _create_conv(client)
        before = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        resp = client.patch(
            f"/api/v2/conversations/{cid}/metadata", json={"starred": True}
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["starred"] is True
        after = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        assert before == after

    @pytest.mark.parametrize(
        "body",
        [
            {"starred": "yes"},
            {"tags": "a"},
            {"tags": ["a", 1]},
            {"pinned_order": True},
            {"description": 3},
            {"bogus": 1},
            ["starred"],
        ],
    )
    def test_bad_input_is_400(self, client: FlaskClient, body):
        cid = _create_conv(client)
        resp = client.patch(f"/api/v2/conversations/{cid}/metadata", json=body)
        assert resp.status_code == 400

    def test_null_unstars(self, client: FlaskClient):
        cid = _create_conv(client)
        url = f"/api/v2/conversations/{cid}/metadata"
        client.patch(url, json={"starred": True})
        resp = client.patch(url, json={"starred": None})
        assert resp.status_code == 200
        assert resp.get_json()["starred"] is False

    def test_folder_without_conversation_is_404(self, client: FlaskClient):
        from gptme.dirs import get_logs_dir

        cid = f"empty-{uuid4().hex[:8]}"
        (get_logs_dir() / cid).mkdir(parents=True)
        assert client.get(f"/api/v2/conversations/{cid}/metadata").status_code == 404
        resp = client.patch(
            f"/api/v2/conversations/{cid}/metadata", json={"starred": True}
        )
        assert resp.status_code == 404
        assert not (get_logs_dir() / cid / "metadata.toml").exists()

    def test_list_etag_tells_null_from_none_text(self, client: FlaskClient):
        cid = _create_conv(client)
        url = f"/api/v2/conversations/{cid}/metadata"
        client.patch(url, json={"description": "None"})
        etag = client.get("/api/v2/conversations?limit=1000").headers["ETag"]
        client.patch(url, json={"description": None})
        resp = client.get(
            "/api/v2/conversations?limit=1000", headers={"If-None-Match": etag}
        )
        assert resp.status_code == 200
        assert _list_item(client, cid)["description"] is None

    def test_scan_racing_an_edit_is_not_cached(self, client: FlaskClient, monkeypatch):
        """A list scan that overlaps an invalidation must not refill the cache."""
        import gptme.server.api_v2 as api_v2

        _create_conv(client)
        api_v2._invalidate_conversations_cache()
        real_scan = api_v2.get_user_conversations

        def scan_with_concurrent_edit(*args, **kwargs):
            api_v2._invalidate_conversations_cache()  # e.g. a star landing
            yield from real_scan(*args, **kwargs)

        monkeypatch.setattr(api_v2, "get_user_conversations", scan_with_concurrent_edit)
        assert client.get("/api/v2/conversations?limit=1000").status_code == 200
        assert api_v2._conversations_cache is None

        monkeypatch.setattr(api_v2, "get_user_conversations", real_scan)
        client.get("/api/v2/conversations?limit=1000")
        assert api_v2._conversations_cache is not None

    @pytest.mark.parametrize("rebuild", [False, True])
    def test_partial_refresh_cannot_restore_cache_after_metadata_edit(
        self, client: FlaskClient, monkeypatch: pytest.MonkeyPatch, rebuild: bool
    ):
        import gptme.server.api_v2 as api_v2

        active = _create_conv(client)
        other = _create_conv(client)
        assert _list_item(client, other)["starred"] is False
        real_read = api_v2.get_conversation_meta_direct

        def read_with_concurrent_edit(*args, **kwargs):
            updated = real_read(*args, **kwargs)
            # The partial refresh is doing filesystem I/O while another request
            # edits metadata and optionally rebuilds the full cache.
            resp = client.patch(
                f"/api/v2/conversations/{other}/metadata", json={"starred": True}
            )
            assert resp.status_code == 200
            if rebuild:
                assert _list_item(client, other)["starred"] is True
            return updated

        monkeypatch.setattr(
            api_v2, "get_conversation_meta_direct", read_with_concurrent_edit
        )
        api_v2._update_conversation_in_cache(active)
        if not rebuild:
            assert api_v2._conversations_cache is None
        assert _list_item(client, other)["starred"] is True

    def test_unknown_conversation_is_404_without_allocating_lock(
        self, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
    ):
        from unittest.mock import Mock

        from gptme.server.session_models import SessionManager

        cid = f"missing-{uuid4().hex[:8]}"
        lock = Mock(wraps=SessionManager.conversation_lock)
        monkeypatch.setattr(SessionManager, "conversation_lock", lock)
        assert client.get(f"/api/v2/conversations/{cid}/metadata").status_code == 404
        resp = client.patch(
            f"/api/v2/conversations/{cid}/metadata", json={"starred": True}
        )
        assert resp.status_code == 404
        lock.assert_not_called()
        assert cid not in SessionManager._conversation_locks

    def test_new_sidecar_uses_the_process_umask(self, tmp_path):
        """A new sidecar must be readable like config.toml, not forced to 0600."""
        import os
        import stat

        from gptme.logmanager.metadata import METADATA_FILENAME, update_metadata

        previous = os.umask(0)
        os.umask(previous)
        try:
            update_metadata(tmp_path, {"starred": True})
        finally:
            os.umask(previous)

        mode = stat.S_IMODE((tmp_path / METADATA_FILENAME).stat().st_mode)
        assert mode == 0o666 & ~previous

    def test_edit_preserves_existing_sidecar_permissions(self, tmp_path):
        import stat

        from gptme.logmanager.metadata import METADATA_FILENAME, update_metadata

        sidecar = tmp_path / METADATA_FILENAME
        sidecar.write_text("starred = false\n", encoding="utf-8")
        sidecar.chmod(0o640)

        update_metadata(tmp_path, {"starred": True})

        assert stat.S_IMODE(sidecar.stat().st_mode) == 0o640

    @pytest.mark.parametrize(
        "patch",
        [
            {"description": 1},
            {"tags": "work"},
            {"tags": ["work", 2]},
            {"pinned_order": "1"},
            {"pinned_order": True},
        ],
    )
    def test_invalid_patch_is_rejected(self, tmp_path, patch):
        from gptme.logmanager.metadata import update_metadata

        with pytest.raises(ValueError, match="must be"):
            update_metadata(tmp_path, patch)
        assert not list(tmp_path.glob(".metadata-*"))

    def test_strict_read_reraises_an_unreadable_sidecar(self, tmp_path):
        import tomlkit

        from gptme.logmanager.metadata import METADATA_FILENAME, read_metadata

        (tmp_path / METADATA_FILENAME).write_bytes(b'description = "unterminated')

        with pytest.raises(tomlkit.exceptions.TOMLKitError):
            read_metadata(tmp_path, strict=True)

    def test_failed_write_cleans_up_the_staged_file(self, tmp_path, monkeypatch):
        from pathlib import Path

        from gptme.logmanager import metadata

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(metadata.os, "replace", boom)

        with pytest.raises(OSError, match="disk full"):
            metadata.update_metadata(tmp_path, {"starred": True})

        assert not list(Path(tmp_path).glob(".metadata-*"))
        assert not (tmp_path / metadata.METADATA_FILENAME).exists()


class TestConfigPatchModelValidation:
    def test_unknown_provider_is_400(self, client: FlaskClient):
        cid = _create_conv(client)
        before = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"model": "nonexistent-provider/zzz"}},
        )
        assert resp.status_code == 400
        assert "nonexistent-provider" in resp.get_json()["error"]
        after = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        assert after == before

    def test_unknown_bare_model_is_400(self, client: FlaskClient):
        cid = _create_conv(client)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"model": "zzz-not-a-model"}},
        )
        assert resp.status_code == 400
        assert "zzz-not-a-model" in resp.get_json()["error"]

    def test_model_is_saved_trimmed(self, client: FlaskClient):
        cid = _create_conv(client)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"model": " openai/gpt-4o "}},
        )
        assert resp.status_code == 200, resp.get_json()
        config = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        assert config["chat"]["model"] == "openai/gpt-4o"

    @pytest.mark.parametrize(
        "model", ["openai/gpt-4o", "openai/some-future-model", "gpt-4o"]
    )
    def test_routable_models_are_accepted(self, client: FlaskClient, model: str):
        cid = _create_conv(client)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config", json={"chat": {"model": model}}
        )
        assert resp.status_code == 200, resp.get_json()

    @pytest.mark.parametrize("model", ["nonexistent-provider/zzz", "zzz-not-a-model"])
    def test_creation_rejects_unroutable_model(self, client: FlaskClient, model: str):
        from gptme.dirs import get_logs_dir

        cid = f"conv-metadata-{uuid4().hex[:8]}"
        resp = client.put(
            f"/api/v2/conversations/{cid}",
            json={"prompt": "none", "config": {"chat": {"model": model}}},
        )
        assert resp.status_code == 400, resp.get_json()
        assert not (get_logs_dir() / cid).exists()

    def test_creation_saves_trimmed_routable_model(self, client: FlaskClient):
        cid = f"conv-metadata-{uuid4().hex[:8]}"
        resp = client.put(
            f"/api/v2/conversations/{cid}",
            json={
                "prompt": "none",
                "config": {"chat": {"model": " openai/some-future-model "}},
            },
        )
        assert resp.status_code == 200, resp.get_json()
        config = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        assert config["chat"]["model"] == "openai/some-future-model"

    def test_echoing_a_stored_unknown_model_is_accepted(self, client: FlaskClient):
        """A settings save that echoes the stored model must not 400 because
        that model has since become unknown; only a change is validated."""
        import tomlkit

        from gptme.dirs import get_logs_dir

        cid = _create_conv(client)
        config_path = get_logs_dir() / cid / "config.toml"
        doc = tomlkit.parse(config_path.read_text())
        doc.setdefault("chat", tomlkit.table())["model"] = "retired-provider/old"
        config_path.write_text(tomlkit.dumps(doc))

        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"model": "retired-provider/old", "stream": False}},
        )
        assert resp.status_code == 200, resp.get_json()
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"model": "other-retired/zzz"}},
        )
        assert resp.status_code == 400

    def test_retired_model_cannot_return_after_a_concurrent_change(
        self, client: FlaskClient, monkeypatch
    ):
        """The unchanged-model check must read the saved model under the same
        lock that writes it: a save that replaced the retired model while this
        request waited must not let the echo put it back unchecked."""
        import contextlib

        import tomlkit

        from gptme.dirs import get_logs_dir
        from gptme.server.session_models import SessionManager

        cid = _create_conv(client)
        config_path = get_logs_dir() / cid / "config.toml"

        def set_model(model: str) -> None:
            doc = tomlkit.parse(config_path.read_text())
            doc.setdefault("chat", tomlkit.table())["model"] = model
            config_path.write_text(tomlkit.dumps(doc))

        set_model("retired-provider/old")
        real_lock = SessionManager.conversation_lock

        @contextlib.contextmanager
        def lock_after_concurrent_save(conversation_id: str):
            # Another save lands while this request waits for the lock.
            set_model("openai/gpt-4o")
            with real_lock(conversation_id):
                yield

        monkeypatch.setattr(
            SessionManager, "conversation_lock", lock_after_concurrent_save
        )
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"model": "retired-provider/old"}},
        )
        assert resp.status_code == 400, resp.get_json()

    def test_saved_effort_on_a_retired_model_does_not_block_other_edits(
        self, client: FlaskClient
    ):
        """Neither the model nor the effort changes, so the saved pair is kept
        rather than revalidated against a model that is no longer known."""
        import tomlkit

        from gptme.dirs import get_logs_dir

        cid = _create_conv(client)
        config_path = get_logs_dir() / cid / "config.toml"
        doc = tomlkit.parse(config_path.read_text())
        chat = doc.setdefault("chat", tomlkit.table())
        chat["model"] = "retired-provider/old"
        chat["reasoning_effort"] = "high"
        config_path.write_text(tomlkit.dumps(doc))

        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"name": "renamed"}},
        )
        assert resp.status_code == 200, resp.get_json()

    @pytest.mark.parametrize("echo_model", [False, True])
    def test_rename_keeps_padded_saved_model_and_effort(
        self, client: FlaskClient, echo_model: bool
    ):
        """Before #5187 PATCH saved the model untrimmed; a padded stored model
        is still the unchanged pair, so a rename must not revalidate it."""
        import tomlkit

        from gptme.dirs import get_logs_dir

        cid = _create_conv(client)
        config_path = get_logs_dir() / cid / "config.toml"
        doc = tomlkit.parse(config_path.read_text())
        chat = doc.setdefault("chat", tomlkit.table())
        chat["model"] = "retired-provider/old "
        chat["reasoning_effort"] = "high"
        config_path.write_text(tomlkit.dumps(doc))

        patch = {"name": "renamed"}
        if echo_model:
            patch["model"] = "retired-provider/old "
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": patch},
        )
        assert resp.status_code == 200, resp.get_json()

    def test_effort_is_rechecked_when_the_resolved_model_changes(
        self, client: FlaskClient, monkeypatch
    ):
        """A workspace switch can change the inherited MODEL without touching
        the saved model or effort; the effort must be checked against it."""
        import tomlkit

        from gptme.dirs import get_logs_dir
        from gptme.llm.models import effort
        from gptme.server import api_v2

        cid = _create_conv(client)
        config_path = get_logs_dir() / cid / "config.toml"
        doc = tomlkit.parse(config_path.read_text())
        doc.setdefault("chat", tomlkit.table())["reasoning_effort"] = "high"
        config_path.write_text(tomlkit.dumps(doc))

        # The inherited MODEL depends on the workspace: o3 in the current one,
        # gpt-4o in ws-b. Keyed on the config, not on call order, so the test
        # fails if either lookup ignores the workspace the PATCH switches to.
        new_workspace = get_logs_dir() / cid / "ws-b"
        new_workspace.mkdir()
        monkeypatch.setattr(
            api_v2,
            "_effective_model_for_effort_validation",
            lambda cfg: (
                "openai/gpt-4o"
                if cfg.workspace.resolve() == new_workspace.resolve()
                else "openai/o3"
            ),
        )

        def reject(model, level):
            raise ValueError(f"{model.full} does not support effort {level}")

        monkeypatch.setattr(effort, "validate_reasoning_effort", reject)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"workspace": str(new_workspace)}},
        )
        assert resp.status_code == 400, resp.get_json()
        assert "gpt-4o" in resp.get_json()["error"]

    def test_provider_prefix_without_model_name_is_rejected(self):
        from gptme.server.api_v2 import _validate_conversation_model

        with pytest.raises(ValueError, match="model name after the provider"):
            _validate_conversation_model("openai/")

    def test_patch_model_when_conversation_has_no_config(self, client: FlaskClient):
        """A logdir without config.toml has no saved model to compare against.

        `_saved_model_and_effort` returns the "nothing saved" tuple instead of
        raising, so a PATCH still validates the requested model.
        """
        from gptme.dirs import get_logs_dir

        cid = _create_conv(client)
        (get_logs_dir() / cid / "config.toml").unlink()
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"model": "openai/gpt-4o-mini"}},
        )
        assert resp.status_code == 200, resp.get_json()

    def test_empty_and_bare_provider_models_are_accepted(self):
        from gptme.server.api_v2 import _validate_conversation_model

        # An empty model means "unset"; nothing to validate.
        _validate_conversation_model("   ")
        # A bare provider name selects that provider's default model.
        _validate_conversation_model("openai")

    def test_saved_model_lookup_failure_is_not_fatal(self, monkeypatch):
        """A config.toml that fails to load must not break a settings save."""
        from gptme.config.chat import ChatConfig
        from gptme.dirs import get_logs_dir
        from gptme.server import api_v2

        cid = f"conv-broken-config-{uuid4().hex[:8]}"
        logdir = get_logs_dir() / cid
        logdir.mkdir(parents=True)
        (logdir / "config.toml").write_text("")

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(ChatConfig, "from_logdir", staticmethod(boom))
        assert api_v2._saved_model_and_effort(cid) == (None, None, None)
