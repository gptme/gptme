"""Tests for workspace handling in server config endpoints.

- PUT /api/v2/conversations/<id> (create) accepts an explicit client-supplied
  workspace: this is the documented override used by the webui workspace
  picker (which sends "." or an absolute path for every new conversation).
  An authorized API client can already run arbitrary shell commands through
  the agent, so containment at creation adds no security boundary.
- PATCH /api/v2/conversations/<id>/config (update) remains contained: a
  workspace that escapes the conversation's logdir is rejected with 400.
"""

import shutil
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

pytestmark = [pytest.mark.timeout(15)]


def _conv_id() -> str:
    return f"test-ws-containment-{uuid4().hex[:8]}"


class TestPutWorkspaceOverride:
    """PUT /api/v2/conversations/<id> must accept explicit workspace overrides."""

    def test_default_atlog_workspace_is_accepted(self, client: FlaskClient):
        """No explicit workspace (defaults to @log) must succeed."""
        resp = client.put(
            f"/api/v2/conversations/{_conv_id()}",
            json={"prompt": "none"},
        )
        assert resp.status_code == 200

    def test_explicit_atlog_workspace_is_accepted(self, client: FlaskClient):
        """Explicit @log workspace must succeed."""
        resp = client.put(
            f"/api/v2/conversations/{_conv_id()}",
            json={"prompt": "none", "config": {"chat": {"workspace": "@log"}}},
        )
        assert resp.status_code == 200

    def test_explicit_external_workspace_is_accepted(
        self, client: FlaskClient, tmp_path: Path
    ):
        """An explicit absolute workspace outside the logdir must succeed
        and be reflected in the conversation config (webui workspace picker)."""
        cid = _conv_id()
        resp = client.put(
            f"/api/v2/conversations/{cid}",
            json={"prompt": "none", "config": {"chat": {"workspace": str(tmp_path)}}},
        )
        assert resp.status_code == 200

        config = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        assert Path(config["chat"]["workspace"]).resolve() == tmp_path.resolve()

    def test_relative_dot_workspace_is_accepted(self, client: FlaskClient):
        """workspace='.' (sent by the webui for every new conversation by
        default) resolves against the server cwd and must succeed."""
        resp = client.put(
            f"/api/v2/conversations/{_conv_id()}",
            json={"prompt": "none", "config": {"chat": {"workspace": "."}}},
        )
        assert resp.status_code == 200


class TestPatchWorkspaceContainment:
    """PATCH /api/v2/conversations/<id>/config must reject workspaces outside logdir."""

    def _create_conv(self, client: FlaskClient) -> str:
        cid = _conv_id()
        resp = client.put(
            f"/api/v2/conversations/{cid}",
            json={"prompt": "none"},
        )
        assert resp.status_code == 200
        return cid

    def test_atlog_workspace_patch_is_accepted(self, client: FlaskClient):
        """PATCH with @log workspace must succeed."""
        cid = self._create_conv(client)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"workspace": "@log"}},
        )
        assert resp.status_code == 200

    def test_absolute_escape_patch_is_rejected(self, client: FlaskClient):
        """PATCH with workspace=/etc must be 400."""
        cid = self._create_conv(client)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"workspace": "/etc"}},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "workspace" in data["error"].lower()

    def test_absolute_tmp_escape_patch_is_rejected(self, client: FlaskClient):
        """PATCH with workspace=/tmp/evil must be 400."""
        cid = self._create_conv(client)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"workspace": "/tmp/evil"}},
        )
        assert resp.status_code == 400

    def test_relative_escape_patch_is_rejected(self, client: FlaskClient):
        """PATCH with relative workspace that resolves outside logdir must be 400."""
        cid = self._create_conv(client)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"workspace": "."}},
        )
        assert resp.status_code == 400

    def test_roundtrip_of_persisted_external_workspace_is_accepted(
        self, client: FlaskClient, tmp_path: Path
    ):
        """PATCH echoing the already-persisted external workspace must succeed.

        The webui settings dialog sends the full config (including the
        unchanged workspace) on every save; conversations legitimately have
        external workspaces (CLI-created, or PUT with explicit workspace).
        Only *redirecting* the workspace outside logdir is rejected."""
        cid = _conv_id()
        resp = client.put(
            f"/api/v2/conversations/{cid}",
            json={"prompt": "none", "config": {"chat": {"workspace": str(tmp_path)}}},
        )
        assert resp.status_code == 200

        # Settings save round-trips the persisted workspace alongside the change
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"workspace": str(tmp_path), "model": "gpt-4"}},
        )
        assert resp.status_code == 200, resp.get_json()

        config = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        assert config["chat"]["model"] == "gpt-4"

    def test_changing_to_different_external_workspace_is_rejected(
        self, client: FlaskClient, tmp_path: Path
    ):
        """PATCH *changing* the workspace to a different external path must be 400."""
        cid = _conv_id()
        resp = client.put(
            f"/api/v2/conversations/{cid}",
            json={"prompt": "none", "config": {"chat": {"workspace": str(tmp_path)}}},
        )
        assert resp.status_code == 200

        other = tmp_path.parent / f"{tmp_path.name}-other"
        other.mkdir(exist_ok=True)
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"workspace": str(other)}},
        )
        assert resp.status_code == 400

    def test_no_config_change_on_escape_patch(self, client: FlaskClient):
        """A rejected workspace PATCH must not modify the existing config."""
        cid = self._create_conv(client)

        # Read the original workspace
        orig = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        orig_workspace = orig.get("chat", {}).get("workspace")

        # Attempt to escape
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"workspace": "/etc"}},
        )
        assert resp.status_code == 400

        # Config must be unchanged
        after = client.get(f"/api/v2/conversations/{cid}/config").get_json()
        assert after.get("chat", {}).get("workspace") == orig_workspace


class TestPatchWithoutWorkspaceKey:
    """Regression: PATCH without 'workspace' key on conversation with
    externally-set workspace should succeed (not block config updates).

    When workspace is absent from the PATCH body, from_dict infers it from the
    existing logdir/workspace path (which may legitimately point outside logdir
    for CLI-created conversations). The containment check must only fire when
    workspace was explicitly in the request body.
    """

    def _create_conv(self, client: FlaskClient) -> str:
        cid = _conv_id()
        resp = client.put(
            f"/api/v2/conversations/{cid}",
            json={"prompt": "none"},
        )
        assert resp.status_code == 200
        return cid

    def test_patch_without_workspace_succeeds_on_external_workspace(
        self, client: FlaskClient
    ):
        """PATCH without workspace must succeed even when logdir/workspace
        points outside logdir (simulating a CLI-created conversation)."""
        cid = self._create_conv(client)

        # Get the logdir path from the API response
        conv = client.get(f"/api/v2/conversations/{cid}").get_json()
        assert conv is not None
        logdir = conv["logdir"]

        # Create an external workspace directory
        external_ws = Path("/tmp/test-external-ws-regression")
        external_ws.mkdir(parents=True, exist_ok=True)

        # Replace logdir/workspace with symlink pointing outside logdir
        ws_path = Path(logdir) / "workspace"
        if ws_path.is_dir():
            shutil.rmtree(ws_path)
        ws_path.symlink_to(str(external_ws))

        # PATCH without workspace key → must succeed (200), not 400
        resp = client.patch(
            f"/api/v2/conversations/{cid}/config",
            json={"chat": {"model": "gpt-4"}},
        )
        assert resp.status_code == 200, (
            f"PATCH without workspace returned {resp.status_code}: {resp.get_json()}"
        )

        # Cleanup
        shutil.rmtree(external_ws, ignore_errors=True)
