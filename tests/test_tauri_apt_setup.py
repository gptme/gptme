"""Guard the shared Tauri apt setup without installing packages on the host."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/setup-tauri-apt/action.yml"


def test_tauri_apt_setup_is_shared_and_bounded() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/tauri.yml").read_text())
    expected = {"lint", "test", "e2e", "build", "build-sidecar", "release"}
    found = set()
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            assert "apt-get" not in step.get("run", ""), name
            if step.get("uses", "").endswith("/.github/actions/setup-tauri-apt"):
                found.add(name)
                assert step["timeout-minutes"] == 8
    assert found == expected


def test_old_tag_build_loads_action_from_workflow_revision() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/tauri.yml").read_text())
    for name in ("lint", "test", "e2e", "build", "build-sidecar", "release"):
        steps = workflow["jobs"][name]["steps"]
        install = next(
            step
            for step in steps
            if step.get("uses", "").endswith("/.github/actions/setup-tauri-apt")
        )
        # The source checkout may be an old tag with no local action at all.
        source = next(
            step for step in steps if step.get("uses") == "actions/checkout@v7"
        )
        assert source["with"]["ref"] == "${{ inputs.tag || github.ref }}"
        action_checkout = next(
            step for step in steps if step.get("name") == "Check out workflow actions"
        )
        assert action_checkout["with"]["ref"] == "${{ github.workflow_sha }}"
        assert action_checkout["with"]["path"] == ".workflow-actions"
        assert action_checkout["with"]["persist-credentials"] is False
        assert (
            action_checkout["with"]["sparse-checkout"]
            == ".github/actions/setup-tauri-apt"
        )
        assert steps.index(source) < steps.index(action_checkout) < steps.index(install)
        assert action_checkout.get("if") == install.get("if")
        assert install["uses"] == "./.workflow-actions/.github/actions/setup-tauri-apt"


def test_tauri_apt_cache_rotates_with_stable_restore_prefix() -> None:
    action = yaml.safe_load(ACTION.read_text())
    steps = action["runs"]["steps"]
    cache = next(step for step in steps if step.get("uses") == "actions/cache@v6")
    prefix = cache["with"]["restore-keys"].strip()
    assert prefix.endswith("${{ steps.packages.outputs.hash }}-")
    assert cache["with"]["key"] == prefix + "${{ steps.packages.outputs.day }}"
    assert "date -u +%Y-%m-%d" in steps[0]["run"]


def test_tauri_apt_cache_and_download_bounds() -> None:
    action = yaml.safe_load(ACTION.read_text())
    steps = action["runs"]["steps"]
    cache = next(step for step in steps if step.get("uses") == "actions/cache@v6")
    assert cache["with"]["path"] == "/var/cache/apt/archives/*.deb"
    assert "steps.packages.outputs.hash" in cache["with"]["key"]
    assert "runner.arch" in cache["with"]["key"]
    commands = "\n".join(step.get("run", "") for step in steps)
    apt_lines = [line for line in commands.splitlines() if "sudo apt-get" in line]
    assert len(apt_lines) == 2
    for line in apt_lines:
        assert "Acquire::Retries=5" in line
        assert "Acquire::http::Timeout=30" in line
    assert "APT::Update::Error-Mode=any" in apt_lines[0]
    # Ubuntu's docker-clean hook otherwise deletes the very archives we cache.
    assert "/etc/apt/apt.conf.d/docker-clean" in commands
    assert "Keep-Downloaded-Packages=true" in commands
    # The runner user must be able to restore archives before apt runs as root.
    prepare = next(i for i, step in enumerate(steps) if "chown" in step.get("run", ""))
    assert prepare < steps.index(cache)


@pytest.mark.parametrize(
    ("packages", "extra_packages"),
    [
        ("python3-dev build-essential libffi-dev", "xvfb webkit2gtk-driver"),
        ("python3-dev\nbuild-essential\tlibffi-dev", "xvfb\nwebkit2gtk-driver"),
        ("python3-dev\r\nbuild-essential libffi-dev", "xvfb\r\nwebkit2gtk-driver\n"),
    ],
)
@pytest.mark.parametrize("update_status", [0, 100])
def test_tauri_apt_executes_real_script(
    tmp_path: Path, update_status: int, packages: str, extra_packages: str
) -> None:
    action = yaml.safe_load(ACTION.read_text())
    install = action["runs"]["steps"][-1]["run"]
    calls = tmp_path / "calls"
    # Stub sudo, not the script: no host modifications or network requests.
    sudo = tmp_path / "sudo"
    sudo.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'if [[ "$1" == apt-get && " $* " == *" update "* ]]; then\n'
        '  exit "$UPDATE_STATUS"\n'
        "fi\n"
    )
    sudo.chmod(0o755)
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", install],
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "UPDATE_STATUS": str(update_status),
            "PACKAGES": packages,
            "EXTRA_PACKAGES": extra_packages,
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == update_status, result.stderr
    commands = calls.read_text().splitlines()
    installs = [line for line in commands if " install -y " in line]
    if update_status:
        assert not installs  # Failed/partial index refresh must not continue.
    else:
        assert len(installs) == 1
        assert installs[0].endswith(
            "install -y python3-dev build-essential libffi-dev xvfb webkit2gtk-driver"
        )


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="Preparation uses Linux /etc/os-release and GNU sha256sum",
)
def test_tauri_apt_hash_includes_all_lines(tmp_path: Path) -> None:
    action = yaml.safe_load(ACTION.read_text())
    prepare = action["runs"]["steps"][0]["run"]
    sudo = tmp_path / "sudo"
    sudo.write_text("#!/usr/bin/env bash\nexit 0\n")
    sudo.chmod(0o755)

    def package_hash(packages: str, extras: str) -> str:
        output = tmp_path / "outputs"
        output.write_text("")
        subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", prepare],
            env={
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(output),
                "PACKAGES": packages,
                "EXTRA_PACKAGES": extras,
            },
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return dict(line.split("=", 1) for line in output.read_text().splitlines())[
            "hash"
        ]

    expected = package_hash("python3-dev build-essential", "xvfb webkit2gtk-driver")
    assert (
        package_hash("python3-dev\nbuild-essential", "xvfb\nwebkit2gtk-driver")
        == expected
    )
    assert (
        package_hash("python3-dev\r\nbuild-essential", "xvfb\r\nwebkit2gtk-driver")
        == expected
    )
    assert package_hash("python3-dev build-essential", "xvfb\npatchelf") != expected


def test_action_changes_trigger_all_platforms() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/tauri.yml").read_text())
    # PyYAML's YAML 1.1 loader interprets the GitHub Actions `on` key as True.
    events = workflow[True]
    action_path = ".github/actions/setup-tauri-apt/**"
    for event in ("push", "pull_request"):
        assert action_path in events[event]["paths"]
    changes = workflow["jobs"]["changes"]
    filter_step = next(step for step in changes["steps"] if step.get("id") == "filter")
    filters = yaml.safe_load(filter_step["with"]["filters"])
    # With predicate-quantifier=every, two positive paths in one filter never
    # match a single file. Action edits need their own OR-ed filter.
    assert filters["apt"] == [action_path]
    assert "steps.filter.outputs.apt == 'true'" in changes["outputs"]["tauri"]
