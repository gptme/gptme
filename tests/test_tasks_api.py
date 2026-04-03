"""Tests for the tasks API module (gptme/server/tasks_api.py).

Tests the pure data logic (Task, status progression, save/load)
and the Flask API endpoints for task management.
"""

import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from gptme.server.tasks_api import (
    Task,
    TaskStatus,
    _find_git_workspace,
    determine_task_status,
    is_status_progression,
    list_tasks,
    load_task,
    save_task,
)

# ── Task dataclass ──────────────────────────────────────────────────


class TestTaskDataclass:
    """Tests for the Task dataclass."""

    def test_create_minimal(self):
        task = Task(
            id="task-1",
            content="Fix the bug",
            created_at="2026-01-01T00:00:00Z",
            status="pending",
            target_type="stdout",
        )
        assert task.id == "task-1"
        assert task.content == "Fix the bug"
        assert task.status == "pending"
        assert task.target_type == "stdout"
        assert task.conversation_ids == []
        assert task.metadata == {}
        assert task.archived is False
        assert task.target_repo is None

    def test_create_full(self):
        task = Task(
            id="task-2",
            content="Create PR",
            created_at="2026-01-01T00:00:00Z",
            status="active",
            target_type="pr",
            target_repo="owner/repo",
            conversation_ids=["conv-1", "conv-2"],
            metadata={"priority": "high"},
            archived=False,
        )
        assert task.target_repo == "owner/repo"
        assert len(task.conversation_ids) == 2
        assert task.metadata["priority"] == "high"

    def test_asdict_roundtrip(self):
        task = Task(
            id="task-3",
            content="Test roundtrip",
            created_at="2026-01-01T00:00:00Z",
            status="pending",
            target_type="stdout",
            metadata={"key": "value"},
        )
        data = asdict(task)
        restored = Task(**data)
        assert restored.id == task.id
        assert restored.content == task.content
        assert restored.metadata == task.metadata


# ── is_status_progression ───────────────────────────────────────────


class TestIsStatusProgression:
    """Tests for status transition validation."""

    def test_pending_to_active(self):
        assert is_status_progression("pending", "active")

    def test_pending_to_completed(self):
        assert is_status_progression("pending", "completed")

    def test_pending_to_failed(self):
        assert is_status_progression("pending", "failed")

    def test_active_to_completed(self):
        assert is_status_progression("active", "completed")

    def test_active_to_failed(self):
        assert is_status_progression("active", "failed")

    def test_active_to_pending(self):
        assert is_status_progression("active", "pending")

    def test_completed_no_regression(self):
        assert not is_status_progression("completed", "pending")
        assert not is_status_progression("completed", "active")
        assert not is_status_progression("completed", "failed")

    def test_failed_to_pending_retry(self):
        assert is_status_progression("failed", "pending")

    def test_failed_no_other_progression(self):
        assert not is_status_progression("failed", "active")
        assert not is_status_progression("failed", "completed")

    def test_same_status_not_progression(self):
        assert not is_status_progression("pending", "pending")
        assert not is_status_progression("active", "active")
        assert not is_status_progression("completed", "completed")
        assert not is_status_progression("failed", "failed")

    def test_unknown_status_returns_false(self):
        assert not is_status_progression("unknown", "active")  # type: ignore[arg-type]
        assert not is_status_progression("pending", "unknown")  # type: ignore[arg-type]


# ── determine_task_status ───────────────────────────────────────────


class TestDetermineTaskStatus:
    """Tests for git-info-based task status determination."""

    def _make_task(self, status: "TaskStatus" = "pending") -> Task:
        return Task(
            id="task-test",
            content="Test task",
            created_at="2026-01-01T00:00:00Z",
            status=status,
            target_type="pr",
        )

    def test_pr_merged(self):
        task = self._make_task()
        git_info = {"pr_status": "MERGED", "pr_merged": True}
        assert determine_task_status(task, git_info) == "completed"

    def test_pr_merged_via_status_only(self):
        task = self._make_task()
        git_info = {"pr_status": "MERGED", "pr_merged": False}
        assert determine_task_status(task, git_info) == "completed"

    def test_pr_closed_without_merge(self):
        task = self._make_task()
        git_info = {"pr_status": "CLOSED", "pr_merged": False}
        assert determine_task_status(task, git_info) == "failed"

    def test_pr_open(self):
        task = self._make_task()
        git_info = {"pr_status": "OPEN", "pr_merged": False}
        assert determine_task_status(task, git_info) == "pending"

    def test_has_commits_no_pr(self):
        task = self._make_task()
        git_info = {"recent_commits": ["abc123 fix: something"]}
        assert determine_task_status(task, git_info) == "pending"

    def test_has_files_changed_no_pr(self):
        task = self._make_task()
        git_info = {"diff_stats": {"files_changed": 3}}
        assert determine_task_status(task, git_info) == "pending"

    def test_no_git_info(self):
        task = self._make_task()
        assert determine_task_status(task, None) == "pending"

    def test_empty_git_info(self):
        task = self._make_task()
        assert determine_task_status(task, {}) == "pending"

    def test_git_info_no_commits_no_pr(self):
        task = self._make_task()
        git_info = {
            "recent_commits": [],
            "diff_stats": {"files_changed": 0},
        }
        assert determine_task_status(task, git_info) == "pending"

    def test_preserves_status_on_error(self):
        task = self._make_task("active")
        # Pass malformed data: diff_stats=None causes AttributeError when
        # the function calls None.get("files_changed", 0), triggering the
        # except branch which returns task.status unchanged.
        result = determine_task_status(task, {"diff_stats": None})
        assert result == "active"  # Returns current task status on error


# ── _find_git_workspace ─────────────────────────────────────────────


class TestFindGitWorkspace:
    """Tests for git workspace discovery."""

    def test_no_target_repo(self, tmp_path: Path):
        task = Task(
            id="t1",
            content="test",
            created_at="2026-01-01T00:00:00Z",
            status="pending",
            target_type="stdout",
        )
        mock_manager = type("M", (), {"workspace": tmp_path})()
        assert _find_git_workspace(task, mock_manager) == tmp_path

    def test_target_repo_with_matching_subdir(self, tmp_path: Path):
        # Create a fake repo subdir with .git
        repo_dir = tmp_path / "repo-name"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        task = Task(
            id="t2",
            content="test",
            created_at="2026-01-01T00:00:00Z",
            status="pending",
            target_type="pr",
            target_repo="owner/repo-name",
        )
        mock_manager = type("M", (), {"workspace": tmp_path})()
        assert _find_git_workspace(task, mock_manager) == repo_dir

    def test_target_repo_no_matching_subdir(self, tmp_path: Path):
        task = Task(
            id="t3",
            content="test",
            created_at="2026-01-01T00:00:00Z",
            status="pending",
            target_type="pr",
            target_repo="owner/nonexistent",
        )
        mock_manager = type("M", (), {"workspace": tmp_path})()
        # Falls back to workspace root
        assert _find_git_workspace(task, mock_manager) == tmp_path

    def test_target_repo_subdir_exists_but_no_git(self, tmp_path: Path):
        # Directory exists but no .git
        repo_dir = tmp_path / "repo-name"
        repo_dir.mkdir()

        task = Task(
            id="t4",
            content="test",
            created_at="2026-01-01T00:00:00Z",
            status="pending",
            target_type="pr",
            target_repo="owner/repo-name",
        )
        mock_manager = type("M", (), {"workspace": tmp_path})()
        # Falls back to workspace root since no .git
        assert _find_git_workspace(task, mock_manager) == tmp_path

    def test_target_repo_no_slash(self, tmp_path: Path):
        task = Task(
            id="t5",
            content="test",
            created_at="2026-01-01T00:00:00Z",
            status="pending",
            target_type="pr",
            target_repo="just-repo-name",
        )
        mock_manager = type("M", (), {"workspace": tmp_path})()
        # No slash means the condition is skipped
        assert _find_git_workspace(task, mock_manager) == tmp_path


# ── save_task / load_task / list_tasks ──────────────────────────────


class TestTaskPersistence:
    """Tests for task save/load/list operations."""

    def test_save_and_load(self, tmp_path: Path):
        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
            task = Task(
                id="persist-1",
                content="Test persistence",
                created_at="2026-01-01T00:00:00Z",
                status="active",
                target_type="stdout",
                metadata={"key": "value"},
            )
            save_task(task)

            loaded = load_task("persist-1")
            assert loaded is not None
            assert loaded.id == "persist-1"
            assert loaded.content == "Test persistence"
            assert loaded.status == "active"
            assert loaded.metadata == {"key": "value"}

    def test_load_nonexistent(self, tmp_path: Path):
        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
            assert load_task("nonexistent") is None

    def test_save_creates_directory(self, tmp_path: Path):
        tasks_dir = tmp_path / "tasks"
        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tasks_dir):
            task = Task(
                id="mkdir-test",
                content="Test",
                created_at="2026-01-01T00:00:00Z",
                status="pending",
                target_type="stdout",
            )
            save_task(task)
            assert tasks_dir.exists()
            assert (tasks_dir / "mkdir-test.json").exists()

    def test_save_overwrites(self, tmp_path: Path):
        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
            task = Task(
                id="overwrite-test",
                content="Original",
                created_at="2026-01-01T00:00:00Z",
                status="pending",
                target_type="stdout",
            )
            save_task(task)

            task.content = "Updated"
            task.status = "active"
            save_task(task)

            loaded = load_task("overwrite-test")
            assert loaded is not None
            assert loaded.content == "Updated"
            assert loaded.status == "active"

    def test_list_tasks_empty(self, tmp_path: Path):
        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
            assert list_tasks() == []

    def test_list_tasks_nonexistent_dir(self, tmp_path: Path):
        nonexistent = tmp_path / "nonexistent"
        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=nonexistent):
            assert list_tasks() == []

    def test_list_tasks_multiple(self, tmp_path: Path):
        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
            for i in range(3):
                task = Task(
                    id=f"list-{i}",
                    content=f"Task {i}",
                    created_at=f"2026-01-0{i + 1}T00:00:00Z",
                    status="pending",
                    target_type="stdout",
                )
                save_task(task)

            tasks = list_tasks()
            assert len(tasks) == 3

    def test_list_tasks_sorted_by_created_at_desc(self, tmp_path: Path):
        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
            for i, date in enumerate(["2026-01-01", "2026-01-03", "2026-01-02"]):
                task = Task(
                    id=f"sort-{i}",
                    content=f"Task {i}",
                    created_at=f"{date}T00:00:00Z",
                    status="pending",
                    target_type="stdout",
                )
                save_task(task)

            tasks = list_tasks()
            assert tasks[0].created_at.startswith("2026-01-03")
            assert tasks[1].created_at.startswith("2026-01-02")
            assert tasks[2].created_at.startswith("2026-01-01")

    def test_load_corrupted_file(self, tmp_path: Path):
        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
            # Write invalid JSON
            (tmp_path / "bad.json").write_text("not valid json")
            assert load_task("bad") is None

    def test_load_missing_fields(self, tmp_path: Path):
        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
            # Write JSON with missing required fields
            (tmp_path / "incomplete.json").write_text(json.dumps({"id": "incomplete"}))
            assert load_task("incomplete") is None

    def test_list_ignores_non_json(self, tmp_path: Path):
        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
            task = Task(
                id="real-task",
                content="Real",
                created_at="2026-01-01T00:00:00Z",
                status="pending",
                target_type="stdout",
            )
            save_task(task)
            # Create non-JSON file
            (tmp_path / "readme.txt").write_text("ignore me")

            tasks = list_tasks()
            assert len(tasks) == 1
            assert tasks[0].id == "real-task"


# ── setup_task_workspace ────────────────────────────────────────────


class TestSetupTaskWorkspace:
    """Tests for workspace setup logic."""

    def test_no_target_repo(self, tmp_path: Path):
        from gptme.server.tasks_api import setup_task_workspace

        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
            workspace = setup_task_workspace("task-1")
            assert workspace.exists()
            assert workspace == tmp_path / "task-1" / "workspace"

    def test_invalid_target_repo_format(self, tmp_path: Path):
        from gptme.server.tasks_api import setup_task_workspace

        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
            # Invalid format (injection attempt)
            workspace = setup_task_workspace("task-2", "owner/repo; rm -rf /")
            # Should fall back to plain workspace
            assert workspace == tmp_path / "task-2" / "workspace"

    def test_valid_target_repo_clone_failure(self, tmp_path: Path):
        import subprocess as sp

        from gptme.server.tasks_api import setup_task_workspace

        with (
            patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path),
            patch(
                "subprocess.run",
                side_effect=sp.CalledProcessError(128, "git clone"),
            ),
        ):
            workspace = setup_task_workspace("task-3", "owner/repo")
            # Falls back to workspace directory
            assert workspace == tmp_path / "task-3" / "workspace"

    def test_valid_target_repo_already_cloned(self, tmp_path: Path):
        from gptme.server.tasks_api import setup_task_workspace

        with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
            # Pre-create the repo directory
            repo_path = tmp_path / "task-4" / "workspace" / "repo"
            repo_path.mkdir(parents=True)

            workspace = setup_task_workspace("task-4", "owner/repo")
            assert workspace == repo_path


# ── Flask API endpoints ─────────────────────────────────────────────


@pytest.fixture
def mock_auth():
    """Disable auth for testing."""
    import gptme.server.auth as auth_mod

    original = auth_mod._auth_enabled
    auth_mod._auth_enabled = False
    yield
    auth_mod._auth_enabled = original


@pytest.fixture
def app(tmp_path: Path, mock_auth):
    """Create a Flask app with the tasks API blueprint for testing."""
    import flask

    from gptme.server.tasks_api import tasks_api

    app = flask.Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(tasks_api)

    with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
        yield app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def sample_task(tmp_path: Path) -> Task:
    """Create and save a sample task."""
    with patch("gptme.server.tasks_api.get_tasks_dir", return_value=tmp_path):
        task = Task(
            id="sample-task",
            content="Sample task for testing",
            created_at="2026-01-15T10:30:00Z",
            status="pending",
            target_type="stdout",
        )
        save_task(task)
        return task


class TestTasksListAPI:
    """Tests for GET /api/v2/tasks."""

    def test_list_empty(self, client):
        resp = client.get("/api/v2/tasks")
        assert resp.status_code == 200
        assert resp.json == {"tasks": []}

    def test_list_with_tasks(self, client):
        for i in range(2):
            save_task(
                Task(
                    id=f"api-task-{i}",
                    content=f"Task {i}",
                    created_at=f"2026-01-0{i + 1}T00:00:00Z",
                    status="pending",
                    target_type="stdout",
                )
            )

        resp = client.get("/api/v2/tasks")
        assert resp.status_code == 200
        data = resp.json
        assert "tasks" in data
        assert len(data["tasks"]) == 2


class TestTasksGetAPI:
    """Tests for GET /api/v2/tasks/<task_id>."""

    def test_get_nonexistent(self, client):
        resp = client.get("/api/v2/tasks/nonexistent")
        assert resp.status_code == 404
        assert "error" in resp.json

    def test_get_existing(self, client, sample_task):
        resp = client.get(f"/api/v2/tasks/{sample_task.id}")
        assert resp.status_code == 200
        data = resp.json
        assert data["id"] == "sample-task"
        assert data["content"] == "Sample task for testing"


class TestTasksUpdateAPI:
    """Tests for PUT /api/v2/tasks/<task_id>."""

    def test_update_nonexistent(self, client):
        resp = client.put(
            "/api/v2/tasks/nonexistent",
            json={"content": "Updated"},
        )
        assert resp.status_code == 404

    def test_update_content(self, client, sample_task):
        resp = client.put(
            f"/api/v2/tasks/{sample_task.id}",
            json={"content": "Updated content"},
        )
        assert resp.status_code == 200

        # Verify persisted
        loaded = load_task(sample_task.id)
        assert loaded is not None
        assert loaded.content == "Updated content"

    def test_update_target_type(self, client, sample_task):
        resp = client.put(
            f"/api/v2/tasks/{sample_task.id}",
            json={"target_type": "pr", "target_repo": "owner/repo"},
        )
        assert resp.status_code == 200

        loaded = load_task(sample_task.id)
        assert loaded is not None
        assert loaded.target_type == "pr"
        assert loaded.target_repo == "owner/repo"

    def test_update_metadata_merges(self, client, sample_task):
        # First set metadata
        client.put(
            f"/api/v2/tasks/{sample_task.id}",
            json={"metadata": {"key1": "value1"}},
        )
        # Then update with new key
        client.put(
            f"/api/v2/tasks/{sample_task.id}",
            json={"metadata": {"key2": "value2"}},
        )

        loaded = load_task(sample_task.id)
        assert loaded is not None
        assert loaded.metadata["key1"] == "value1"
        assert loaded.metadata["key2"] == "value2"

    def test_update_no_json(self, client, sample_task):
        resp = client.put(
            f"/api/v2/tasks/{sample_task.id}",
            content_type="application/json",
        )
        assert resp.status_code == 400


class TestTasksArchiveAPI:
    """Tests for POST /api/v2/tasks/<task_id>/archive and /unarchive."""

    def test_archive_task(self, client, sample_task):
        resp = client.post(f"/api/v2/tasks/{sample_task.id}/archive")
        assert resp.status_code == 200

        loaded = load_task(sample_task.id)
        assert loaded is not None
        assert loaded.archived is True

    def test_archive_already_archived(self, client, sample_task):
        sample_task.archived = True
        save_task(sample_task)

        resp = client.post(f"/api/v2/tasks/{sample_task.id}/archive")
        assert resp.status_code == 400
        assert "already archived" in resp.json["error"]

    def test_archive_nonexistent(self, client):
        resp = client.post("/api/v2/tasks/nonexistent/archive")
        assert resp.status_code == 404

    def test_unarchive_task(self, client, sample_task):
        sample_task.archived = True
        save_task(sample_task)

        resp = client.post(f"/api/v2/tasks/{sample_task.id}/unarchive")
        assert resp.status_code == 200

        loaded = load_task(sample_task.id)
        assert loaded is not None
        assert loaded.archived is False

    def test_unarchive_not_archived(self, client, sample_task):
        resp = client.post(f"/api/v2/tasks/{sample_task.id}/unarchive")
        assert resp.status_code == 400
        assert "not archived" in resp.json["error"]

    def test_unarchive_nonexistent(self, client):
        resp = client.post("/api/v2/tasks/nonexistent/unarchive")
        assert resp.status_code == 404


class TestTasksCreateAPI:
    """Tests for POST /api/v2/tasks."""

    def test_create_missing_content(self, client):
        resp = client.post("/api/v2/tasks", json={"target_type": "stdout"})
        assert resp.status_code == 400
        assert "content" in resp.json["error"]

    def test_create_no_json(self, client):
        # Sending application/json content-type with no body → 400
        resp = client.post("/api/v2/tasks", content_type="application/json")
        assert resp.status_code == 400

    def test_create_happy_path(self, client):
        # Mock create_task_conversation to avoid real logdir/workspace creation.
        # Mock get_task_info to avoid LogManager trying to load the fake conv ID.
        mock_info = {
            "id": "task-mock",
            "content": "Write tests for the module",
            "status": "pending",
            "target_type": "stdout",
            "conversation_ids": ["conv-mock-0"],
            "archived": False,
            "target_repo": None,
            "metadata": {},
            "created_at": "2026-01-01T00:00:00Z",
        }
        with (
            patch(
                "gptme.server.tasks_api.create_task_conversation",
                return_value="conv-mock-0",
            ),
            patch(
                "gptme.server.tasks_api.get_task_info",
                return_value=mock_info,
            ),
        ):
            resp = client.post(
                "/api/v2/tasks",
                json={"content": "Write tests for the module"},
            )
        assert resp.status_code == 201
        data = resp.json
        assert data["content"] == "Write tests for the module"
        assert data["status"] == "pending"
        assert "conv-mock-0" in data["conversation_ids"]

    def test_create_with_target_repo(self, client):
        with (
            patch(
                "gptme.server.tasks_api.create_task_conversation",
                return_value="conv-mock-0",
            ),
            patch(
                "gptme.server.tasks_api.get_task_info",
                side_effect=lambda t: {**asdict(t)},
            ),
        ):
            resp = client.post(
                "/api/v2/tasks",
                json={
                    "content": "Fix the bug",
                    "target_type": "pr",
                    "target_repo": "owner/repo",
                },
            )
        assert resp.status_code == 201
        data = resp.json
        assert data["target_type"] == "pr"
        assert data["target_repo"] == "owner/repo"

    def test_create_persists_task(self, client):
        with (
            patch(
                "gptme.server.tasks_api.create_task_conversation",
                return_value="conv-mock-0",
            ),
            patch(
                "gptme.server.tasks_api.get_task_info",
                side_effect=lambda t: {**asdict(t)},
            ),
        ):
            resp = client.post(
                "/api/v2/tasks",
                json={"content": "Persist me"},
            )
        assert resp.status_code == 201
        task_id = resp.json["id"]

        # Verify persisted via GET (get_task_info is NOT mocked here, so it will
        # try to load the log, fail gracefully, and return the saved task data)
        get_resp = client.get(f"/api/v2/tasks/{task_id}")
        assert get_resp.status_code == 200
        assert get_resp.json["content"] == "Persist me"
