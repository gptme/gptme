"""Tests for the artifact registry API endpoints (ErikBjare/bob#830 Phase 1).

Covers:
- kind classification from extension/MIME
- computed-on-read artifact derivation from attachments
- list and detail endpoints, including error handling
"""

import io
from pathlib import Path
from typing import cast
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


# ============================================================
# Phase 2: tool/plugin-declared artifacts (message metadata)
# ============================================================

from gptme.logmanager import LogManager  # fmt: skip
from gptme.message import Message, MessageMetadata  # fmt: skip
from gptme.server.artifacts_api import derive_artifacts  # fmt: skip


def _manager_with_messages(tmp_path, messages: list[Message]) -> LogManager:
    return LogManager(log=messages, logdir=tmp_path, lock=False)


class TestArtifactsFromMessages:
    def test_attachment_descriptor_sets_tool_provenance(self, tmp_path):
        msg = Message(
            "assistant",
            "made an image",
            metadata={
                "artifacts": [
                    {
                        "source_type": "attachment",
                        "path": "attachments/plot.png",
                        "tool": "python",
                    }
                ]
            },
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 1
        assert arts[0].kind == "image"
        assert arts[0].title == "plot.png"
        assert arts[0].provenance.tool == "python"
        assert arts[0].provenance.message_index == 0

    def test_tool_descriptor_overrides_attachment_scan(self, tmp_path):
        # A file on disk (Phase 1 scan) plus a message declaring the same path.
        att = tmp_path / "attachments"
        att.mkdir()
        (att / "plot.png").write_bytes(b"\x89PNG")
        msg = Message(
            "assistant",
            "made an image",
            metadata={
                "artifacts": [
                    {
                        "source_type": "attachment",
                        "path": "attachments/plot.png",
                        "tool": "python",
                    }
                ]
            },
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        # Deduped to one artifact; the tool-declared provenance wins.
        assert len(arts) == 1
        assert arts[0].provenance.tool == "python"
        assert arts[0].size == 4  # real file size still picked up

    def test_external_source(self, tmp_path):
        msg = Message(
            "assistant",
            "fetched a page",
            metadata={
                "artifacts": [
                    {
                        "source_type": "external",
                        "url": "https://example.com/report.pdf",
                        "tool": "browser",
                    }
                ]
            },
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 1
        assert arts[0].source.type == "external"
        assert arts[0].source.url == "https://example.com/report.pdf"
        assert arts[0].kind == "pdf"
        assert arts[0].size is None

    def test_kind_override_validated(self, tmp_path):
        # An invalid kind is ignored and reclassified; a valid one is honored.
        msg = Message(
            "assistant",
            "x",
            metadata={
                "artifacts": [
                    {"source_type": "workspace", "path": "out/app", "kind": "webapp"},
                    {"source_type": "workspace", "path": "data.csv", "kind": "bogus"},
                ]
            },
        )
        arts = {
            a.title: a
            for a in derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        }
        assert arts["app"].kind == "webapp"
        assert arts["data.csv"].kind == "dataset"  # reclassified from extension

    def test_malformed_descriptors_skipped(self, tmp_path):
        # Deliberately invalid descriptors (cast past the TypedDict) to prove
        # one bad entry never breaks the list.
        bad_metadata = cast(
            MessageMetadata,
            {
                "artifacts": [
                    "not-a-dict",
                    {"source_type": "attachment"},  # missing path
                    {"source_type": "external"},  # missing url
                    {"source_type": "bogus", "path": "x"},  # bad source type
                    {"source_type": "attachment", "path": "attachments/ok.txt"},
                ]
            },
        )
        msg = Message("assistant", "x", metadata=bad_metadata)
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert [a.title for a in arts] == ["ok.txt"]

    def test_inline_duplicate_title_no_collision(self, tmp_path):
        # Two inline descriptors in the same message with the same (or absent)
        # title must not collide — desc_index makes their ids unique.
        msg = Message(
            "assistant",
            "x",
            metadata={
                "artifacts": [
                    {"source_type": "inline", "title": "result"},
                    {"source_type": "inline", "title": "result"},
                    {"source_type": "inline"},  # both title absent
                    {"source_type": "inline"},
                ]
            },
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 4
        assert len({a.id for a in arts}) == 4  # all ids distinct

    def test_target_id_filters_message_artifacts(self, tmp_path):
        msg = Message(
            "assistant",
            "x",
            metadata={
                "artifacts": [
                    {"source_type": "external", "url": "https://a.example/x.png"},
                    {"source_type": "external", "url": "https://b.example/y.png"},
                ]
            },
        )
        manager = _manager_with_messages(tmp_path, [msg])
        all_arts = derive_artifacts(manager)
        target = all_arts[0].id
        filtered = derive_artifacts(manager, target_id=target)
        assert len(filtered) == 1
        assert filtered[0].id == target


class TestToolWriteArtifacts:
    """Phase 3: workspace files created/modified by file-writing tool uses."""

    def test_save_creates_workspace_artifact(self, tmp_path):
        msg = Message(
            "assistant",
            "Saving:\n\n```save sub/factorial.py\ndef f():\n    return 1\n```\n",
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 1
        art = arts[0]
        assert art.source.type == "workspace"
        assert art.source.path == "sub/factorial.py"
        assert art.title == "factorial.py"
        assert art.provenance.tool == "save"  # created
        assert art.provenance.message_index == 0

    def test_patch_marks_modified(self, tmp_path):
        msg = Message(
            "assistant",
            "```patch app.py\n<<<<<<< ORIGINAL\na\n=======\nb\n>>>>>>> UPDATED\n```\n",
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 1
        assert arts[0].provenance.tool == "patch"  # modified, not created

    def test_append_marks_modified(self, tmp_path):
        msg = Message("assistant", "```append notes.md\nmore text\n```\n")
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 1
        assert arts[0].title == "notes.md"
        assert arts[0].provenance.tool == "append"  # modified, not created

    def test_metadata_descriptor_overrides_tool_write(self, tmp_path):
        # A metadata-declared workspace artifact for the same path wins (richer
        # provenance), so the file isn't listed twice.
        msg = Message(
            "assistant",
            "```save app.py\nx = 1\n```\n",
            metadata={
                "artifacts": [
                    {"source_type": "workspace", "path": "app.py", "tool": "custom"}
                ]
            },
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 1  # not duplicated
        assert arts[0].provenance.tool == "custom"  # metadata wins

    def test_save_then_patch_dedups_as_created(self, tmp_path):
        msgs = [
            Message("assistant", "```save app.py\nx = 1\n```\n"),
            Message(
                "assistant",
                "```patch app.py\n<<<<<<< ORIGINAL\nx = 1\n=======\nx = 2\n>>>>>>> UPDATED\n```\n",
            ),
        ]
        arts = derive_artifacts(_manager_with_messages(tmp_path, msgs))
        assert len(arts) == 1
        assert arts[0].provenance.tool == "save"  # created wins over later modify
        assert arts[0].provenance.message_index == 0  # first touch

    def test_path_outside_workspace_skipped(self, tmp_path):
        msg = Message("assistant", "```save /etc/passwd\nhi\n```\n")
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert arts == []

    def test_user_message_tool_blocks_ignored(self, tmp_path):
        # Only assistant messages count (user examples shouldn't create artifacts).
        msg = Message("user", "```save evil.py\nx\n```\n")
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert arts == []

    def test_morph_marks_modified(self, tmp_path):
        msg = Message(
            "assistant",
            "```morph app.py\n// ... existing code ...\nFOO\n```\n",
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 1
        assert arts[0].title == "app.py"
        assert arts[0].provenance.tool == "morph"  # modified, not created
        assert arts[0].diff is None  # morph edits aren't diffable

    def test_patch_many_simple_form(self, tmp_path):
        msg = Message(
            "assistant",
            "```patch_many a.py b.py\n"
            "<<<<<<< ORIGINAL\n1\n=======\n2\n>>>>>>> UPDATED\n"
            "<<<<<<< ORIGINAL\n3\n=======\n4\n>>>>>>> UPDATED\n```\n",
        )
        arts = {
            a.title: a
            for a in derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        }
        assert set(arts) == {"a.py", "b.py"}
        assert arts["a.py"].provenance.tool == "patch_many"
        assert arts["a.py"].diff == "-1\n+2"
        assert arts["b.py"].diff == "-3\n+4"

    def test_patch_many_embedded_paths(self, tmp_path):
        msg = Message(
            "assistant",
            "```patch_many\n"
            "=== PATH: p.py ===\n"
            "<<<<<<< ORIGINAL\na\n=======\nb\n>>>>>>> UPDATED\n"
            "=== PATH: q.py ===\n"
            "<<<<<<< ORIGINAL\nc\n=======\nd\n>>>>>>> UPDATED\n```\n",
        )
        arts = {
            a.title: a
            for a in derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        }
        assert set(arts) == {"p.py", "q.py"}
        assert arts["p.py"].diff == "-a\n+b"
        assert arts["q.py"].diff == "-c\n+d"

    def test_xml_format_save(self, tmp_path):
        msg = Message(
            "assistant",
            '<tool-use>\n<save args="x.py">\nx = 1\n</save>\n</tool-use>',
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 1
        assert arts[0].title == "x.py"
        assert arts[0].provenance.tool == "save"

    def test_xml_format_patch_has_diff(self, tmp_path):
        msg = Message(
            "assistant",
            '<tool-use>\n<patch args="app.py">\n'
            "<<<<<<< ORIGINAL\na\n=======\nb\n>>>>>>> UPDATED\n"
            "</patch>\n</tool-use>",
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 1
        assert arts[0].provenance.tool == "patch"
        assert arts[0].diff == "-a\n+b"

    def test_toolcall_format_save(self, tmp_path):
        msg = Message(
            "assistant",
            '@save(toolu_01): {"path": "t.py", "content": "x = 1"}',
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 1
        assert arts[0].title == "t.py"
        assert arts[0].provenance.tool == "save"

    def test_toolcall_format_patch_many_has_diff(self, tmp_path):
        msg = Message(
            "assistant",
            '@patch_many(toolu_02): {"patches": [{"path": "a.py", '
            '"patch": "<<<<<<< ORIGINAL\\nx\\n=======\\ny\\n>>>>>>> UPDATED"}]}',
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 1
        assert arts[0].title == "a.py"
        assert arts[0].provenance.tool == "patch_many"
        assert arts[0].diff == "-x\n+y"

    def test_append_diff_is_additions(self, tmp_path):
        msg = Message("assistant", "```append notes.md\nline one\nline two\n```\n")
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert len(arts) == 1
        assert arts[0].diff == "+line one\n+line two"

    def test_toolcall_in_codeblock_ignored(self, tmp_path):
        # Tool-call syntax shown as an example inside a fenced block is not a
        # real write and must not produce a phantom artifact.
        msg = Message(
            "assistant",
            'Here is how to use save:\n\n```\n@save(toolu_01): {"path": "x.py", '
            '"content": "hi"}\n```\n',
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert arts == []

    def test_xml_bare_tool_name_arg_no_crash(self, tmp_path):
        # args="save" with no path must not raise; it's just skipped.
        msg = Message(
            "assistant", '<tool-use>\n<save args="save">\nx\n</save>\n</tool-use>'
        )
        arts = derive_artifacts(_manager_with_messages(tmp_path, [msg]))
        assert arts == []

    def test_created_file_has_no_diff(self, tmp_path):
        # save-then-patch stays "created"; created files show full content, no diff.
        msgs = [
            Message("assistant", "```save app.py\nx = 1\n```\n"),
            Message(
                "assistant",
                "```patch app.py\n<<<<<<< ORIGINAL\nx = 1\n=======\nx = 2\n>>>>>>> UPDATED\n```\n",
            ),
        ]
        arts = derive_artifacts(_manager_with_messages(tmp_path, msgs))
        assert len(arts) == 1
        assert arts[0].provenance.tool == "save"
        assert arts[0].diff is None
