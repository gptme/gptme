from collections import defaultdict
from swebench.harness.constants import (
    MAP_REPO_TO_REQS_PATHS,
    MAP_REPO_TO_TEST_FRAMEWORK,
    MAP_VERSION_TO_INSTALL,
)
from swebench.harness.log_parsers import parse_log_pytest, MAP_REPO_TO_PARSER

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
TEST_PYTEST_WO_DEPRECATION = (
    # "pytest --no-header -rA --tb=no  -p no:cacheprovider -W ignore::DeprecationWarning"
    "pytest"
)


MAP_REPO_TO_REQS_PATHS = defaultdict(
    lambda: MAP_REPO_TO_REQS_PATHS_PLACEHOLDER, MAP_REPO_TO_REQS_PATHS
)
MAP_REPO_TO_TEST_FRAMEWORK = defaultdict(
    lambda: TEST_PYTEST_WO_DEPRECATION, MAP_REPO_TO_TEST_FRAMEWORK
)
MAP_VER_TO_INSTALL = defaultdict(
    lambda: MAP_VERSION_TO_INSTALL_PLACEHOLDER, MAP_VERSION_TO_INSTALL
)
MAP_REPO_TO_PARSER = defaultdict(lambda: parse_log_pytest, MAP_REPO_TO_PARSER)