"""Tests for workspace path containment in server config endpoints.

Covers the security fix for path traversal via client-supplied workspace in:
- PUT /api/v2/conversations/<id>  (create conversation with config)
- PATCH /api/v2/conversations/<id>/config  (update conversation config)

Any workspace that escapes the conversation's logdir must be rejected with 400.
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


class TestPutWorkspaceContainment:
    """PUT /api/v2/conversations/<id> must reject workspaces outside logdir."""

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

    def test_absolute_escape_is_rejected(self, client: FlaskClient):
        """workspace pointing outside logdir (absolute path) must be 400."""
        resp = client.put(
            f"/api/v2/conversations/{_conv_id()}",
            json={"prompt": "none", "config": {"chat": {"workspace": "/etc"}}},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "workspace" in data["error"].lower()

    def test_absolute_tmp_escape_is_rejected(self, client: FlaskClient):
        """workspace pointing to /tmp (outside logdir) must be 400."""
        resp = client.put(
            f"/api/v2/conversations/{_conv_id()}",
            json={"prompt": "none", "config": {"chat": {"workspace": "/tmp/evil"}}},
        )
        assert resp.status_code == 400

    def test_relative_escape_via_cwd_is_rejected(self, client: FlaskClient):
        """Relative workspace that resolves outside logdir must be 400.

        A bare relative path like '.' resolves to CWD (the server process
        directory), which is almost certainly outside the logdir.
        """
        resp = client.put(
            f"/api/v2/conversations/{_conv_id()}",
            json={"prompt": "none", "config": {"chat": {"workspace": "."}}},
        )
        # The CWD of the test process is not within the logdir, so this should 400.
        # (If by some coincidence CWD is a subdirectory of logdir, the test would
        # incorrectly pass, but that cannot happen in practice.)
        assert resp.status_code == 400

    def test_no_conversation_created_on_escape(self, client: FlaskClient):
        """Rejected workspace must not create the conversation (no side effects)."""
        cid = _conv_id()
        resp = client.put(
            f"/api/v2/conversations/{cid}",
            json={"prompt": "none", "config": {"chat": {"workspace": "/etc"}}},
        )
        assert resp.status_code == 400

        # The conversation must not exist after the failed PUT
        check = client.get(f"/api/v2/conversations/{cid}")
        assert check.status_code == 404


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
