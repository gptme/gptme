from ..types import EvalSpec
from .basic import tests as tests_basic
from .browser import tests as tests_browser
from .init_projects import tests as tests_init_projects
from .practical2 import tests as tests_practical2
from .practical import tests as tests_practical

suites: dict[str, list[EvalSpec]] = {
    "basic": tests_basic,
    "init_projects": tests_init_projects,
    "browser": tests_browser,
    "practical2": tests_practical2,
    "practical": tests_practical,
}

tests: list[EvalSpec] = [test for suite in suites.values() for test in suite]
tests_map: dict[str, EvalSpec] = {test["name"]: test for test in tests}

tests_default_ids: list[str] = [
    "hello",
    "hello-patch",
    "hello-ask",
    "prime100",
    "init-git",
]
tests_default: list[EvalSpec] = [tests_map[test_id] for test_id in tests_default_ids]
