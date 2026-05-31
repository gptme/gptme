"""Tests for the panel registry API endpoints (ErikBjare/bob#830 Phase 3b).

Covers:
- src allowlist validation (_is_allowed_src)
- sandbox token filtering and dangerous-combination removal (_resolve_sandbox)
- panel hint parsing from message metadata (panels_from_messages)
- list endpoint, including empty-response and error handling
"""

from typing import Any
from unittest import mock
from uuid import uuid4

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from gptme.server.panels_api import (  # fmt: skip
    _is_allowed_src,
    _resolve_sandbox,
    panels_from_messages,
)

pytestmark = [pytest.mark.timeout(10)]


# ============================================================
# Unit tests — src allowlist
# ============================================================


class TestIsAllowedSrc:
    @pytest.mark.parametrize(
        "src",
        [
            "/demos/my-panel/",
            "/panel.html",
            "http://localhost:8080/ui",
            "http://127.0.0.1:3000/",
            "http://[::1]/panel",
        ],
    )
    def test_allowed(self, src: str):
        assert _is_allowed_src(src)

    @pytest.mark.parametrize(
        "src",
        [
            "https://evil.example.com/xss",
            "//evil.example.com/xss",
            "/\\evil",
            "",
            "   ",
            123,
            None,
        ],
    )
    def test_rejected(self, src: Any):
        assert not _is_allowed_src(src)


# ============================================================
# Unit tests — sandbox token filtering
# ============================================================


class TestResolveSandbox:
    def test_filters_unknown_tokens(self):
        result = _resolve_sandbox(["allow-scripts", "allow-popups", "allow-modals"])
        assert result == ["allow-scripts"]

    def test_drops_same_origin_when_scripts_present(self):
        result = _resolve_sandbox(["allow-scripts", "allow-same-origin"])
        assert "allow-same-origin" not in result
        assert "allow-scripts" in result

    def test_keeps_same_origin_without_scripts(self):
        result = _resolve_sandbox(["allow-same-origin", "allow-forms"])
        assert "allow-same-origin" in result

    def test_deduplicates(self):
        result = _resolve_sandbox(["allow-scripts", "allow-scripts", "allow-forms"])
        assert result.count("allow-scripts") == 1

    def test_non_list_returns_empty(self):
        assert _resolve_sandbox(None) == []
        assert _resolve_sandbox("allow-scripts") == []


# ============================================================
# Integration tests — panels_from_messages
# ============================================================


def _make_manager(messages: list[dict]) -> Any:
    """Build a minimal mock LogManager with the given messages."""
    log_entries = []
    for m in messages:
        msg = mock.MagicMock()
        msg.metadata = m.get("metadata")
        log_entries.append(msg)
    manager = mock.MagicMock()
    manager.log = log_entries
    return manager


class TestPanelsFromMessages:
    def test_empty_log_returns_empty(self):
        manager = _make_manager([])
        assert panels_from_messages(manager) == []

    def test_no_panel_hints_key(self):
        manager = _make_manager([{"metadata": {"artifacts": []}}])
        assert panels_from_messages(manager) == []

    def test_valid_panel_hint(self):
        manager = _make_manager(
            [
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "id": "my-panel",
                                "kind": "iframe",
                                "title": "My Panel",
                                "src": "/demos/my-panel/",
                                "sandbox": ["allow-scripts"],
                            }
                        ]
                    }
                }
            ]
        )
        panels = panels_from_messages(manager)
        assert len(panels) == 1
        assert panels[0].id == "my-panel"
        assert panels[0].title == "My Panel"
        assert panels[0].src == "/demos/my-panel/"
        assert panels[0].sandbox == ["allow-scripts"]
        assert panels[0].message_index == 0

    def test_disallowed_src_is_dropped(self):
        manager = _make_manager(
            [
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "id": "evil",
                                "kind": "iframe",
                                "title": "Evil",
                                "src": "https://evil.example.com",
                                "sandbox": [],
                            }
                        ]
                    }
                }
            ]
        )
        assert panels_from_messages(manager) == []

    def test_duplicate_id_first_wins(self):
        manager = _make_manager(
            [
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "id": "panel-1",
                                "kind": "iframe",
                                "title": "First",
                                "src": "/first/",
                                "sandbox": [],
                            }
                        ]
                    }
                },
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "id": "panel-1",
                                "kind": "iframe",
                                "title": "Second",
                                "src": "/second/",
                                "sandbox": [],
                            }
                        ]
                    }
                },
            ]
        )
        panels = panels_from_messages(manager)
        assert len(panels) == 1
        assert panels[0].title == "First"

    def test_non_iframe_kind_skipped(self):
        manager = _make_manager(
            [
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "id": "webrtc-panel",
                                "kind": "webrtc",
                                "title": "Video",
                                "src": "/video/",
                                "sandbox": [],
                            }
                        ]
                    }
                }
            ]
        )
        assert panels_from_messages(manager) == []

    def test_sandbox_dangerous_combo_stripped(self):
        manager = _make_manager(
            [
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "id": "p",
                                "kind": "iframe",
                                "title": "P",
                                "src": "/p/",
                                "sandbox": ["allow-scripts", "allow-same-origin"],
                            }
                        ]
                    }
                }
            ]
        )
        panels = panels_from_messages(manager)
        assert "allow-same-origin" not in panels[0].sandbox
        assert "allow-scripts" in panels[0].sandbox

    def test_bootstrap_forwarded(self):
        manager = _make_manager(
            [
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "id": "p",
                                "kind": "iframe",
                                "title": "P",
                                "src": "/p/",
                                "sandbox": [],
                                "bootstrap": {"token": "abc123"},
                            }
                        ]
                    }
                }
            ]
        )
        panels = panels_from_messages(manager)
        assert panels[0].bootstrap == {"token": "abc123"}

    def test_opaque_origin_warning_on_server_relative_with_scripts(self):
        """Server-relative src + allow-scripts sandbox emits a warning."""
        manager = _make_manager(
            [
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "id": "p",
                                "kind": "iframe",
                                "title": "P",
                                "src": "/p/",
                                "sandbox": ["allow-scripts"],
                            }
                        ]
                    }
                }
            ]
        )
        panels = panels_from_messages(manager)
        assert len(panels) == 1
        assert len(panels[0].warnings) == 1
        assert "opaque origin" in panels[0].warnings[0]

    def test_no_warning_for_localhost_absolute_src(self):
        """localhost absolute URL with allow-scripts gets no warning."""
        manager = _make_manager(
            [
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "id": "p",
                                "kind": "iframe",
                                "title": "P",
                                "src": "http://localhost:8080/p/",
                                "sandbox": ["allow-scripts"],
                            }
                        ]
                    }
                }
            ]
        )
        panels = panels_from_messages(manager)
        assert panels[0].warnings == []

    def test_no_warning_for_server_relative_without_scripts(self):
        """Server-relative src without allow-scripts gets no warning."""
        manager = _make_manager(
            [
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "id": "p",
                                "kind": "iframe",
                                "title": "P",
                                "src": "/p/",
                                "sandbox": ["allow-forms"],
                            }
                        ]
                    }
                }
            ]
        )
        panels = panels_from_messages(manager)
        assert panels[0].warnings == []

    def test_allow_attribute_stripped_with_warning(self):
        """The `allow` (Permissions-Policy) attribute is never forwarded; a warning is emitted."""
        manager = _make_manager(
            [
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "id": "p",
                                "kind": "iframe",
                                "title": "P",
                                "src": "/p/",
                                "sandbox": [],
                                "allow": "camera; microphone; geolocation",
                            }
                        ]
                    }
                }
            ]
        )
        panels = panels_from_messages(manager)
        assert len(panels) == 1
        assert panels[0].allow is None
        assert any("allow" in w.lower() for w in panels[0].warnings)

    def test_allow_none_is_fine(self):
        """No `allow` key in the hint is fine — no warning emitted."""
        manager = _make_manager(
            [
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "id": "p",
                                "kind": "iframe",
                                "title": "P",
                                "src": "/p/",
                                "sandbox": [],
                            }
                        ]
                    }
                }
            ]
        )
        panels = panels_from_messages(manager)
        assert panels[0].allow is None
        assert panels[0].warnings == []

    def test_missing_id_skipped(self):
        manager = _make_manager(
            [
                {
                    "metadata": {
                        "panel_hints": [
                            {
                                "kind": "iframe",
                                "title": "No ID",
                                "src": "/p/",
                                "sandbox": [],
                            }
                        ]
                    }
                }
            ]
        )
        assert panels_from_messages(manager) == []


# ============================================================
# Integration tests — Flask endpoint
# ============================================================


@pytest.fixture()
def app():
    pytest.importorskip("flask")
    from gptme.server.app import create_app

    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def conversation_id():
    return f"test-{uuid4().hex[:8]}"


class TestListPanelsEndpoint:
    def test_unknown_conversation_returns_404(self, client):
        resp = client.get(
            "/api/v2/conversations/nonexistent-conv-xyz/panels",
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code == 404

    def test_empty_panels(self, client, tmp_path, conversation_id, monkeypatch):
        from gptme.logmanager import LogManager

        log_dir = tmp_path / conversation_id
        log_dir.mkdir()
        (log_dir / "conversation.jsonl").write_text("")

        def _fake_load(conv_id, lock=True):
            manager = mock.MagicMock()
            manager.log = []
            manager.logdir = log_dir
            return manager

        monkeypatch.setattr(LogManager, "load", staticmethod(_fake_load))
        resp = client.get(
            f"/api/v2/conversations/{conversation_id}/panels",
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == {"panels": []}
