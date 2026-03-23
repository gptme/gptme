from ..types import EvalSpec
from .basic import tests as tests_basic
from .browser import tests as tests_browser
from .init_projects import tests as tests_init_projects
from .practical import tests as tests_practical
from .practical2 import tests as tests_practical2
from .practical3 import tests as tests_practical3
from .practical4 import tests as tests_practical4
from .practical5 import tests as tests_practical5
from .practical6 import tests as tests_practical6
from .practical7 import tests as tests_practical7
from .practical8 import tests as tests_practical8
from .practical9 import tests as tests_practical9
from .practical10 import tests as tests_practical10
from .practical11 import tests as tests_practical11
from .practical12 import tests as tests_practical12
from .practical13 import tests as tests_practical13
from .practical14 import tests as tests_practical14

suites: dict[str, list[EvalSpec]] = {
    "basic": tests_basic,
    "init_projects": tests_init_projects,
    "browser": tests_browser,
    "practical": tests_practical,
    "practical2": tests_practical2,
    "practical3": tests_practical3,
    "practical4": tests_practical4,
    "practical5": tests_practical5,
    "practical6": tests_practical6,
    "practical7": tests_practical7,
    "practical8": tests_practical8,
    "practical9": tests_practical9,
    "practical10": tests_practical10,
    "practical11": tests_practical11,
    "practical12": tests_practical12,
    "practical13": tests_practical13,
    "practical14": tests_practical14,
}

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
