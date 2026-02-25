"""
SWE-bench constants adapted for the current swebench package API.

The swebench library restructured its constants in newer versions (multi-language support).
This module provides backward-compatible wrappers.
"""

from collections import defaultdict

from swebench.harness.constants import (
    MAP_REPO_TO_REQS_PATHS,
    MAP_REPO_VERSION_TO_SPECS_PY,
)
from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
from swebench.harness.log_parsers.python import parse_log_pytest

# Build MAP_REPO_TO_TEST_FRAMEWORK from new API
# Old API had repo -> test_framework_cmd
# New API has repo -> version -> specs with "test_cmd" field
_test_framework_map: dict[str, str | dict[str, str]] = {}
for _repo, _versions in MAP_REPO_VERSION_TO_SPECS_PY.items():
    _cmds: dict[str, str] = {}
    for _ver, _specs in _versions.items():
        if "test_cmd" in _specs:
            _cmds[_ver] = _specs["test_cmd"]
    if len(set(_cmds.values())) == 1:
        _test_framework_map[_repo] = next(iter(_cmds.values()))
    elif _cmds:
        _test_framework_map[_repo] = _cmds

# Build MAP_VER_TO_INSTALL from new API (repo -> version -> install specs)
_ver_to_install_map: dict[str, dict[str, dict]] = {}
for _repo, _versions in MAP_REPO_VERSION_TO_SPECS_PY.items():
    _ver_to_install_map[_repo] = {}
    for _ver, _specs in _versions.items():
        _ver_to_install_map[_repo][_ver] = _specs


MAP_VERSION_TO_INSTALL_PLACEHOLDER = {
    "0.0": {
        "python": "3.9",
        "pip_packages": [
            "pytest",
            "cython",
            "distro",
            "pytest-cov",
            "pytest-xdist",
            "pytest-mock",
            "pytest-asyncio",
            "pytest-bdd",
            "pytest-benchmark",
            "pytest-randomly",
            "responses",
            "mock",
            "hypothesis",
            "freezegun",
            "trustme",
            "requests-mock",
            "requests",
            "tomlkit",
        ],
        "install": "pip install --force-reinstall -e .; pip install -e .[test]; pip install -e .[testing]; pip install -e .[tests]; pip install -e .[dev]; pip install -e .[dev-dependencies]",
    }
}
MAP_REPO_TO_REQS_PATHS_PLACEHOLDER = [
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "requirements_test.txt",
    "requirements_dev.txt",
]
TEST_PYTEST_WO_DEPRECATION = "pytest"


MAP_REPO_TO_REQS_PATHS = defaultdict(
    lambda: MAP_REPO_TO_REQS_PATHS_PLACEHOLDER, MAP_REPO_TO_REQS_PATHS
)
MAP_REPO_TO_TEST_FRAMEWORK = defaultdict(
    lambda: TEST_PYTEST_WO_DEPRECATION, _test_framework_map
)
MAP_VER_TO_INSTALL = defaultdict(
    lambda: MAP_VERSION_TO_INSTALL_PLACEHOLDER, _ver_to_install_map
)
MAP_REPO_TO_PARSER = defaultdict(lambda: parse_log_pytest, MAP_REPO_TO_PARSER)
