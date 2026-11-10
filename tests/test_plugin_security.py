"""Unit tests for the pre-import plugin security scanner.

These cover the scanner's own boundaries: what counts as one statement, and how
far an editable install's importable source reaches. Discovery-level behaviour
(does ``ep.load()`` run?) is covered in ``test_unified_plugins.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gptme.plugins.security import PluginSecurityScan, scan_plugin_entry_point


def _entry_point(module: str, dist: object) -> SimpleNamespace:
    return SimpleNamespace(
        name=module.rsplit(".", 1)[-1],
        module=module,
        value=f"{module}:plugin",
        dist=dist,
    )


def _editable_distribution(source_dir: Path) -> SimpleNamespace:
    """Distribution metadata a pip/uv editable install exposes: shim, no RECORD."""

    def read_text(name: str) -> str:
        if name == "direct_url.json":
            return json.dumps(
                {"url": source_dir.as_uri(), "dir_info": {"editable": True}}
            )
        raise FileNotFoundError(name)

    return SimpleNamespace(name="thirdparty-plugin", files=None, read_text=read_text)


def _wheel_distribution(root: Path, relative: str) -> SimpleNamespace:
    return SimpleNamespace(
        name="thirdparty-plugin",
        files=[Path(relative)],
        locate_file=lambda path: root / path,
    )


def _scan_source_file(
    tmp_path: Path, source: str, name: str = "pkg"
) -> PluginSecurityScan | None:
    """Scan ``source`` as a single module shipped in a wheel."""
    relative = f"{name}/__init__.py"
    module = tmp_path / relative
    module.parent.mkdir(parents=True)
    module.write_text(source, encoding="utf-8")
    return scan_plugin_entry_point(
        _entry_point(name, _wheel_distribution(tmp_path, relative))
    )


def test_standalone_editable_module_scans_sibling_payload(tmp_path):
    """A standalone editable module must not hide an unscanned sibling.

    ``import payload`` resolves beside the module because the editable install
    put that directory on ``sys.path``; the sibling is executed by ``ep.load()``
    and has to be scanned too.
    """
    (tmp_path / "plugin.py").write_text("import payload\n", encoding="utf-8")
    (tmp_path / "payload.py").write_text(
        "data = open('~/.ssh/id_rsa').read()\n", encoding="utf-8"
    )

    scan = scan_plugin_entry_point(
        _entry_point("plugin", _editable_distribution(tmp_path))
    )

    assert scan is not None
    assert scan.entry_point_scanned
    assert [f.category for f in scan.findings] == ["credential-harvest"]
    assert scan.findings[0].path.endswith("payload.py")


def test_clean_standalone_editable_module_is_verified(tmp_path):
    """Sibling coverage must not turn a clean editable plugin into a rejection."""
    (tmp_path / "plugin.py").write_text("import payload\n", encoding="utf-8")
    (tmp_path / "payload.py").write_text("VALUE = 42\n", encoding="utf-8")

    scan = scan_plugin_entry_point(
        _entry_point("plugin", _editable_distribution(tmp_path))
    )

    assert scan is not None
    assert scan.entry_point_scanned
    assert scan.findings == ()


def test_nested_editable_layout_is_resolved(tmp_path):
    """``package_dir={"": "lib"}`` puts the import root below the project root."""
    package = tmp_path / "lib" / "my_plugin"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")

    scan = scan_plugin_entry_point(
        _entry_point("my_plugin", _editable_distribution(tmp_path))
    )

    assert scan is not None
    assert scan.entry_point_scanned
    assert scan.findings == ()


def test_package_submodule_scans_its_package(tmp_path):
    """An entry point that is a submodule still scans the whole package."""
    package = tmp_path / "src" / "my_plugin"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text("from . import creds\n", encoding="utf-8")
    (package / "creds.py").write_text(
        "secret = open('~/.ssh/id_rsa').read()\n", encoding="utf-8"
    )

    scan = scan_plugin_entry_point(
        _entry_point("my_plugin.cli", _editable_distribution(tmp_path))
    )

    assert scan is not None
    assert scan.entry_point_scanned
    assert [f.category for f in scan.findings] == ["credential-harvest"]


@pytest.mark.parametrize("layout", ["", "src", "lib"])
@pytest.mark.parametrize("payload", ["__init__.py", "sibling.py"])
def test_nested_entry_point_scans_top_level_package(tmp_path, layout, payload):
    """Parent initializers and siblings remain in a nested module's scan."""
    package = tmp_path / layout / "my_plugin"
    nested = package / "sub"
    nested.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (nested / "__init__.py").write_text("", encoding="utf-8")
    (nested / "cli.py").write_text("VALUE = 42\n", encoding="utf-8")
    (package / payload).write_text(
        "secret = open('~/.ssh/id_rsa').read()\n", encoding="utf-8"
    )

    scan = scan_plugin_entry_point(
        _entry_point("my_plugin.sub.cli", _editable_distribution(tmp_path))
    )

    assert scan is not None
    assert scan.entry_point_scanned
    assert [f.category for f in scan.findings] == ["credential-harvest"]
    assert scan.findings[0].path == str(package / payload)


def test_persistence_pattern_does_not_span_statements(tmp_path):
    """Help text naming a service must not make an unrelated call look like one.

    ``subprocess.run(['echo', 'ready'])`` executes echo; the service-install
    string lives in a later statement. A DOTALL pattern that joined them
    rejected a plugin whose only command was benign.
    """
    scan = _scan_source_file(
        tmp_path,
        "import subprocess\n"
        "subprocess.run(['echo', 'ready'])\n"
        "HELP = 'systemctl enable example'\n",
    )

    assert scan is not None
    assert scan.findings == ()
    assert scan.entry_point_scanned


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.run('systemctl enable evil', shell=True)\n",
        # argv list form: separators are commas, not whitespace
        "import subprocess\nsubprocess.run(['systemctl', 'enable', 'evil'])\n",
        "import os\nos.system('crontab -e')\n",
        # Explicit continuation before the opening bracket must also join.
        "import subprocess\nsubprocess.run \\\n('systemctl enable evil', shell=True)\n",
        "import subprocess\nsubprocess.\\\nrun('systemctl enable evil', shell=True)\n",
        # the call itself spans lines
        "import subprocess\nsubprocess.run(\n    ['systemctl',\n     'enable', 'evil']\n)\n",
    ],
)
def test_persistence_pattern_still_matches_the_call(tmp_path, source):
    """Confining a match to one statement must not lose real detections."""
    scan = _scan_source_file(tmp_path, source)

    assert scan is not None
    assert [f.category for f in scan.findings] == ["persistence"]


def test_credential_pattern_matches_across_lines_within_a_statement(tmp_path):
    """A split expression is still one statement, so it cannot evade the scan."""
    scan = _scan_source_file(
        tmp_path,
        "import os\nsecret = open(\n    os.path.expanduser('~/.ssh/id_rsa')\n).read()\n",
    )

    assert scan is not None
    assert [f.category for f in scan.findings] == ["credential-harvest"]
