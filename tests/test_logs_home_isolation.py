"""Log isolation must stay per-test without scanning the growing pytest root."""

import inspect
import os
from pathlib import Path

import pytest
from conftest import isolate_logs_home

from gptme.dirs import get_logs_dir


def test_logs_home_fixture_is_autouse(request: pytest.FixtureRequest) -> None:
    assert "isolate_logs_home" in request.fixturenames


def test_logs_home_allocation_does_not_scan_numbered_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    tmp_path: Path,
) -> None:
    def reject_numbered_allocation(*args: object, **kwargs: object) -> None:
        pytest.fail("Per-test log allocation must not scan numbered directories")

    monkeypatch.setattr(tmp_path_factory, "mktemp", reject_numbered_allocation)
    allocate = inspect.unwrap(isolate_logs_home)
    allocate(monkeypatch, tmp_path_factory)
    first = get_logs_dir()
    (first / "prior-test-log").mkdir()

    allocate(monkeypatch, tmp_path_factory)
    second = get_logs_dir()
    assert second != first
    assert second.is_dir()
    assert list(second.iterdir()) == []
    assert not first.is_relative_to(tmp_path)
    assert not second.is_relative_to(tmp_path)
    assert first.is_relative_to(tmp_path_factory.getbasetemp())
    assert second.is_relative_to(tmp_path_factory.getbasetemp())

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "explicit-override"))
    assert get_logs_dir() == Path(os.environ["GPTME_LOGS_HOME"])
