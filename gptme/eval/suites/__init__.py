import importlib
import logging
import pkgutil
from pathlib import Path

from ..types import EvalSpec

logger = logging.getLogger(__name__)

# Explicitly registered suites (non-practical, stable)
from .basic import tests as tests_basic
from .browser import tests as tests_browser
from .init_projects import tests as tests_init_projects

suites: dict[str, list[EvalSpec]] = {
    "basic": tests_basic,
    "init_projects": tests_init_projects,
    "browser": tests_browser,
}

# Auto-discover all other suite modules in this package.
# Any module that exports a `tests` list of EvalSpec dicts is registered.
# This makes adding new eval suites as simple as creating a new file.
_package_dir = Path(__file__).parent
_explicit = {"basic", "browser", "init_projects", "__init__"}

for _info in sorted(
    pkgutil.iter_modules([str(_package_dir)]),
    key=lambda m: (
        # Sort practical suites numerically: practical, practical2, ..., practical14
        int(m.name.removeprefix("practical") or "1")
        if m.name.startswith("practical")
        else 0,
        m.name,
    ),
):
    if _info.name in _explicit:
        continue
    try:
        _mod = importlib.import_module(f".{_info.name}", __package__)
    except Exception:
        logger.warning(
            "Failed to import eval suite module %s", _info.name, exc_info=True
        )
        continue
    _tests = getattr(_mod, "tests", None)
    if _tests is not None and isinstance(_tests, list):
        suites[_info.name] = _tests
    else:
        logger.debug("Skipping %s: no 'tests' list found", _info.name)


tests: list[EvalSpec] = [test for suite in suites.values() for test in suite]


def _check_no_duplicate_names() -> None:
    """Guard against duplicate test names (silently shadowed by dict comprehension).

    Raises ValueError on import if any two suites share a test name.
    Regression guard for cce683d25 (write-tests name collision).
    """
    seen: dict[str, str] = {}
    for suite_name, suite_tests in suites.items():
        for test in suite_tests:
            name = test["name"]
            if name in seen:
                raise ValueError(
                    f"Duplicate eval test name '{name}' in suite '{suite_name}' "
                    f"(already defined in '{seen[name]}')"
                )
            seen[name] = suite_name


_check_no_duplicate_names()

tests_map: dict[str, EvalSpec] = {test["name"]: test for test in tests}

tests_default_ids: list[str] = [
    "hello",
    "hello-patch",
    "hello-ask",
    "prime100",
    "init-git",
]
tests_default: list[EvalSpec] = [tests_map[test_id] for test_id in tests_default_ids]
