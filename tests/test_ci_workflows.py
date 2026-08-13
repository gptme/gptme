from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _workflow(name: str):
    return yaml.safe_load((PROJECT_ROOT / ".github" / "workflows" / name).read_text())


def test_windows_ci_job_runs_focused_path_config_slice():
    workflow = _workflow("test.yml")
    job = workflow["jobs"]["test-windows"]

    assert job["runs-on"] == "windows-latest"

    install_step = next(
        step for step in job["steps"] if step.get("name") == "Install dependencies"
    )
    assert install_step["run"] == "poetry install"

    run_step = next(
        step
        for step in job["steps"]
        if step.get("name") == "Run Windows path/config smoke tests"
    )
    command = run_step["run"]

    assert "poetry run pytest -v" in command
    assert "tests/test_dirs.py" in command
    assert "tests/test_config.py::test_custom_tool_file_allowlist_preserved" in command
    assert "tests/test_config.py::test_custom_tool_file_mixed_allowlist" in command
    assert "tests/test_llm_openai.py::TestIsProxy" in command
    assert "tests/test_cli.py::test_help" in command
    assert "tests/test_cli.py::test_version" in command
    assert "make test" not in command
