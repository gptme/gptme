"""Tests for the artifact registry API endpoints (ErikBjare/bob#830 Phase 1).

Covers:
- kind classification from extension/MIME
- computed-on-read artifact derivation from attachments
- list and detail endpoints, including error handling
"""

import io
from pathlib import Path
from uuid import uuid4

import pytest

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

from gptme.server.artifacts_api import (  # fmt: skip
    _artifact_id,
    classify_kind,
)

pytestmark = [pytest.mark.timeout(10)]


# ============================================================
# Unit tests for kind classification
# ============================================================


class TestClassifyKind:
    @pytest.mark.parametrize(
        ("name", "mime", "expected"),
        [
            ("hero.png", "image/png", "image"),
            ("clip.mp3", "audio/mpeg", "audio"),
            ("demo.mp4", "video/mp4", "video"),
            ("report.pdf", "application/pdf", "pdf"),
            ("page.html", "text/html", "html"),
            ("notes.md", "text/markdown", "markdown"),
            ("change.diff", "text/x-diff", "diff"),
            ("change.patch", None, "diff"),
            ("data.csv", "text/csv", "dataset"),
            ("data.json", "application/json", "dataset"),
            ("plain.txt", "text/plain", "other"),
            ("blob.bin", "application/octet-stream", "binary"),
            ("mystery", None, "other"),
        ],
    )
    def test_classify(self, name, mime, expected):
        assert classify_kind(Path(name), mime) == expected

    def test_extension_wins_over_ambiguous_mime(self):
        # .md is sometimes guessed as text/plain; extension must still win
        assert classify_kind(Path("notes.md"), "text/plain") == "markdown"


class TestArtifactId:
    def test_stable_and_prefixed(self):
        a = _artifact_id("attachments/hero.png")
        b = _artifact_id("attachments/hero.png")
        assert a == b
        assert a.startswith("art_")

    def test_distinct_paths_distinct_ids(self):
        assert _artifact_id("attachments/a.png") != _artifact_id("attachments/b.png")


# ============================================================
# Integration tests for the endpoints
# ============================================================


def _create_conv(client: FlaskClient) -> str:
    convname = f"test-artifacts-{uuid4().hex[:8]}"
    resp = client.put(f"/api/v2/conversations/{convname}", json={"prompt": "Test."})
    assert resp.status_code == 200
    return convname


def _upload(client: FlaskClient, conv_id: str, content: bytes, name: str) -> None:
    resp = client.post(
        f"/api/v2/conversations/{conv_id}/workspace/upload",
        data={"file": (io.BytesIO(content), name)},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200


class TestListArtifactsEndpoint:
    def test_empty_conversation_returns_empty_list(self, client: FlaskClient):
        conv_id = _create_conv(client)
        resp = client.get(f"/api/v2/conversations/{conv_id}/artifacts")
        assert resp.status_code == 200
        assert resp.get_json() == {"artifacts": []}

    def test_uploaded_file_becomes_artifact(self, client: FlaskClient):
        conv_id = _create_conv(client)
        _upload(client, conv_id, b"\x89PNG fake", "hero.png")

        resp = client.get(f"/api/v2/conversations/{conv_id}/artifacts")
        assert resp.status_code == 200
        artifacts = resp.get_json()["artifacts"]
        assert len(artifacts) == 1
        art = artifacts[0]
        assert art["kind"] == "image"
        assert art["title"] == "hero.png"
        assert art["source"] == {
            "type": "attachment",
            "path": "attachments/hero.png",
            "url": None,
        }
        assert art["preview"]["type"] == "image"
        assert art["id"].startswith("art_")
        action_types = {a["type"] for a in art["actions"]}
        assert {"download", "open_workspace", "open_panel"} <= action_types

    def test_multiple_files_sorted_by_name(self, client: FlaskClient):
        conv_id = _create_conv(client)
        _upload(client, conv_id, b"a", "zebra.txt")
        _upload(client, conv_id, b"b", "alpha.md")

        resp = client.get(f"/api/v2/conversations/{conv_id}/artifacts")
        artifacts = resp.get_json()["artifacts"]
        titles = [a["title"] for a in artifacts]
        assert titles == ["alpha.md", "zebra.txt"]

    def test_nonexistent_conversation_returns_404(self, client: FlaskClient):
        resp = client.get("/api/v2/conversations/does-not-exist-xyz/artifacts")
        assert resp.status_code == 404


class TestGetArtifactEndpoint:
    def test_detail_roundtrip(self, client: FlaskClient):
        conv_id = _create_conv(client)
        _upload(client, conv_id, b"data", "report.pdf")

        listed = client.get(f"/api/v2/conversations/{conv_id}/artifacts").get_json()
        artifact_id = listed["artifacts"][0]["id"]

        resp = client.get(f"/api/v2/conversations/{conv_id}/artifacts/{artifact_id}")
        assert resp.status_code == 200
        detail = resp.get_json()
        assert detail["id"] == artifact_id
        assert detail["kind"] == "pdf"
        assert detail["title"] == "report.pdf"

    def test_unknown_artifact_returns_404(self, client: FlaskClient):
        conv_id = _create_conv(client)
        resp = client.get(f"/api/v2/conversations/{conv_id}/artifacts/art_deadbeef0000")
        assert resp.status_code == 404
