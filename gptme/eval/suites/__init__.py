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


def _suite_sort_key(m: pkgutil.ModuleInfo) -> tuple[int, str]:
    """Sort key: practical suites sorted numerically, others grouped at 0.

    Numeric practical suites (practical, practical2, ...) sort first among
    practical suites (keys 1..N). Non-numeric practical* names (e.g.
    practical_bonus) sort after all numeric ones (key 10000). All other
    suites sort before practical suites (key 0).
    """
    if m.name.startswith("practical"):
        suffix = m.name.removeprefix("practical")
        if not suffix or suffix.isdigit():
            return (int(suffix) if suffix else 1, m.name)
        # non-numeric practical* — sort after all numeric practical suites
        return (10000, m.name)
    return (0, m.name)


def _discover_suites() -> None:
    """Auto-discover and register suite modules from this package directory.

    Wrapped in a function to avoid leaking loop variables into module namespace.
    """
    for info in sorted(pkgutil.iter_modules([str(_package_dir)]), key=_suite_sort_key):
        if info.name in _explicit:
            continue
        try:
            mod = importlib.import_module(f".{info.name}", __package__)
        except Exception:
            logger.warning(
                "Failed to import eval suite module %s", info.name, exc_info=True
            )
            continue
        mod_tests = getattr(mod, "tests", None)
        if mod_tests is not None and isinstance(mod_tests, list):
            suites[info.name] = mod_tests
        else:
            logger.debug("Skipping %s: no 'tests' list found", info.name)


_discover_suites()


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
