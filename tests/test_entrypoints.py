"""Console scripts must import without their extras installed.

Every ``[project.scripts]`` entry point is imported by Python *before* any
gptme code runs.  If the module it names reaches an optional dependency at
module scope, the script dies with ``ModuleNotFoundError`` instead of the
project's own "install the extras" message -- which is what gptme/gptme#290
reported for ``gptme-server``.

These tests run the imports in a subprocess with every optional dependency
blocked, so they fail on a fully-provisioned CI machine too.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

project_root = Path(__file__).parent.parent
pyproject = tomllib.loads((project_root / "pyproject.toml").read_text())

SCRIPTS: dict[str, str] = pyproject["project"]["scripts"]


def _optional_distributions() -> list[str]:
    """Distribution names declared ``optional = true`` in pyproject."""
    deps = pyproject["tool"]["poetry"]["dependencies"]
    return [
        name
        for name, spec in deps.items()
        if isinstance(spec, dict) and spec.get("optional")
    ]


def _optional_modules() -> list[str]:
    """Top-level import names provided *only* by optional distributions."""
    from importlib.metadata import packages_distributions

    optional = {d.lower().replace("-", "_") for d in _optional_distributions()}
    provided = packages_distributions()

    modules = set()
    for module, dists in provided.items():
        normalized = {d.lower().replace("-", "_") for d in dists}
        # skip modules a required distribution also provides (e.g. pydantic)
        if normalized <= optional:
            modules.add(module)
    # distributions that are not installed here have no packages_distributions
    # entry, so fall back to the normalized distribution name
    installed = {d for dists in provided.values() for d in dists}
    installed = {d.lower().replace("-", "_") for d in installed}
    modules |= optional - installed
    return sorted(modules)


_PROBE = """
import importlib, json, sys

BLOCKED = set(json.loads(sys.argv[1]))


class _Blocker:
    def find_module(self, name, path=None):
        return None

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


for name in list(sys.modules):
    if name.split(".")[0] in BLOCKED:
        del sys.modules[name]
sys.meta_path.insert(0, _Blocker())

failures = {}
for script, target in json.loads(sys.argv[2]).items():
    module, _, attr = target.partition(":")
    try:
        getattr(importlib.import_module(module), attr)
    except BaseException as e:  # noqa: BLE001 - report, don't raise
        failures[script] = f"{type(e).__name__}: {e}"
print(json.dumps(failures))
"""


@pytest.fixture(scope="module")
def import_failures() -> dict[str, str]:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _PROBE,
            json.dumps(_optional_modules()),
            json.dumps(SCRIPTS),
        ],
        capture_output=True,
        text=True,
        cwd=project_root,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_entrypoint_imports_without_extras(script, import_failures):
    """A console script must import with no optional dependency installed."""
    assert script not in import_failures, (
        f"`{script}` ({SCRIPTS[script]}) reaches an optional dependency at "
        f"module scope: {import_failures[script]}. Import it inside the "
        f"command instead, so the extras check can report it."
    )
