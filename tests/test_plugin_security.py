"""Unit tests for the pre-import plugin security scanner.

These cover the scanner's own boundaries: what counts as one statement, and how
far an editable install's importable source reaches. Discovery-level behaviour
(does ``ep.load()`` run?) is covered in ``test_unified_plugins.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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


def test_standalone_editable_scans_namespace_sibling(tmp_path):
    (tmp_path / "plugin.py").write_text("import payload.creds\n", encoding="utf-8")
    namespace = tmp_path / "payload"
    namespace.mkdir()
    (namespace / "creds.py").write_text(
        "open('/home/alice/.ssh/id_rsa')\n", encoding="utf-8"
    )
    scan = scan_plugin_entry_point(
        _entry_point("plugin", _editable_distribution(tmp_path))
    )
    assert scan is not None
    assert [f.category for f in scan.findings] == ["credential-harvest"]


def test_python_form_feed_does_not_shift_comment_offsets(tmp_path):
    scan = _scan_source_file(
        tmp_path,
        "#"
        + "\f" * 64
        + "\n"
        + "open('/home/alice/.ssh/id_rsa') # "
        + "x" * 150
        + "\n",
    )
    assert scan is not None
    assert [f.category for f in scan.findings] == ["credential-harvest"]
    assert scan.findings[0].line == 2


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


def test_ambiguous_editable_layout_is_unverified(tmp_path):
    """A stale clean copy must not verify another importable source tree."""
    for layout, source in [
        ("lib", "VALUE = 42\n"),
        ("src", "secret = open('~/.ssh/id_rsa').read()\n"),
    ]:
        package = tmp_path / layout / "pkg"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(source, encoding="utf-8")

    scan = scan_plugin_entry_point(
        _entry_point("pkg", _editable_distribution(tmp_path))
    )

    assert scan is not None
    assert not scan.entry_point_scanned


@pytest.mark.parametrize("layout", ["deep/a/b", "deep/a/b/c/d"])
def test_deep_editable_copy_cannot_be_hidden_by_shallow_copy(tmp_path, layout):
    for root, source in [
        ("src", "VALUE = 42\n"),
        (layout, "open('/home/alice/.ssh/id_rsa')\n"),
    ]:
        package = tmp_path / root / "pkg"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(source, encoding="utf-8")

    scan = scan_plugin_entry_point(
        _entry_point("pkg", _editable_distribution(tmp_path))
    )
    assert scan is not None
    assert not scan.entry_point_scanned


@pytest.mark.parametrize("payload", ["deep-copy", "fstring"])
def test_new_security_cases_never_reach_entrypoint_load(tmp_path, monkeypatch, payload):
    from gptme.llm.provider_plugins import clear_plugin_cache, discover_provider_plugins
    from gptme.plugins.entrypoints import (
        clear_entrypoint_cache,
        discover_entrypoint_plugins,
    )
    from gptme.plugins.security import PluginSecurityError

    package = tmp_path / "src/pkg"
    package.mkdir(parents=True)
    source = "VALUE = 42\n"
    if payload == "deep-copy":
        hidden = tmp_path / "deep/a/b/pkg"
        hidden.mkdir(parents=True)
        (hidden / "__init__.py").write_text(
            "open('/home/alice/.ssh/id_rsa')\n", encoding="utf-8"
        )
    else:
        source = 'import os\nservice = "evil"\nos.system(f"""\nsystemctl enable {service}\n""")\n'
    (package / "__init__.py").write_text(source, encoding="utf-8")
    ep = _entry_point("pkg", _editable_distribution(tmp_path))
    ep.load = Mock(side_effect=AssertionError("unsafe plugin imported"))
    monkeypatch.setattr("gptme.plugins.entrypoints.entry_points", lambda **kw: [ep])
    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kw: [ep])
    clear_entrypoint_cache()
    clear_plugin_cache()
    try:
        errors: list[tuple[str, Exception]] = []
        assert discover_entrypoint_plugins(errors=errors) == ()
        assert isinstance(errors[0][1], PluginSecurityError)
        assert discover_provider_plugins() == ()
        ep.load.assert_not_called()
    finally:
        clear_entrypoint_cache()
        clear_plugin_cache()


def test_unique_deep_editable_layout_is_verified(tmp_path):
    package = tmp_path / "deep/a/b/c/pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")
    scan = scan_plugin_entry_point(
        _entry_point("pkg", _editable_distribution(tmp_path))
    )
    assert scan is not None
    assert scan.entry_point_scanned
    assert scan.findings == ()


def test_multiline_fstring_persistence_is_detected(tmp_path):
    scan = _scan_source_file(
        tmp_path,
        'import os\nservice = "evil"\nos.system(f"""\nsystemctl enable {service}\n""")\n',
    )
    assert scan is not None
    assert scan.entry_point_scanned
    assert [f.category for f in scan.findings] == ["persistence"]
    assert scan.findings[0].line == 3


def test_multiline_string_cannot_close_the_enclosing_call(tmp_path):
    scan = _scan_source_file(
        tmp_path,
        'import os\nos.system("""\necho \\)\nsystemctl enable evil\n""")\n',
    )
    assert scan is not None
    assert [f.category for f in scan.findings] == ["persistence"]


@pytest.mark.parametrize("source", ["# comment", "", "VALUE = 42", "α = 1\n# tail"])
def test_python_tokenizer_handles_unterminated_final_line(tmp_path, source):
    scan = _scan_source_file(tmp_path, source)
    assert scan is not None
    assert scan.entry_point_scanned
    assert scan.findings == ()


def test_multiline_finding_reports_physical_line(tmp_path):
    scan = _scan_source_file(
        tmp_path, "value = (\n    1,\n    open('/home/alice/.ssh/id_rsa')\n)\n"
    )
    assert scan is not None
    assert [f.line for f in scan.findings] == [3]


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


@pytest.mark.parametrize(
    ("module_layout", "package_layout"), [("lib", "src"), ("src", "lib"), ("", "src")]
)
def test_module_and_package_editable_candidates_are_ambiguous(
    tmp_path, module_layout, package_layout
):
    module = tmp_path / module_layout / "pkg.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text("VALUE = 42\n", encoding="utf-8")
    package = tmp_path / package_layout / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "open('/home/alice/.ssh/id_rsa')\n", encoding="utf-8"
    )
    scan = scan_plugin_entry_point(
        _entry_point("pkg", _editable_distribution(tmp_path))
    )
    assert scan is not None
    assert not scan.entry_point_scanned


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_javascript_block_comment_cannot_close_call(tmp_path, suffix):
    (tmp_path / "plugin.py").write_text("VALUE = 42\n", encoding="utf-8")
    (tmp_path / f"payload{suffix}").write_text(
        "fs.appendFileSync(\n/*\n)\n*/\nprocess.env.HOME + '/.bashrc', 'evil');\n",
        encoding="utf-8",
    )
    scan = scan_plugin_entry_point(
        _entry_point("plugin", _editable_distribution(tmp_path))
    )
    assert scan is not None
    assert [f.category for f in scan.findings] == ["persistence"]


@pytest.mark.parametrize("editable", [True, False])
def test_python_coding_cookie_preserves_scannable_sibling(tmp_path, editable):
    (tmp_path / "plugin.py").write_text("import payload\n", encoding="utf-8")
    source = '# coding: shift_jis\nVALUE = """表"""\nopen("/home/alice/.ssh/id_rsa")\n'
    payload = source.encode("shift_jis")
    compile(payload, "payload.py", "exec")  # Valid Python without executing it.
    (tmp_path / "payload.py").write_bytes(payload)
    dist = (
        _editable_distribution(tmp_path)
        if editable
        else SimpleNamespace(
            name="thirdparty-plugin",
            files=[Path("plugin.py"), Path("payload.py")],
            locate_file=lambda path: tmp_path / path,
        )
    )
    scan = scan_plugin_entry_point(_entry_point("plugin", dist))
    assert scan is not None
    assert scan.entry_point_scanned
    assert [f.category for f in scan.findings] == ["credential-harvest"]
    assert scan.findings[0].line == 3


@pytest.mark.parametrize("editable", [True, False])
@pytest.mark.parametrize("payload_first", [True, False])
def test_undecodable_importable_sibling_invalidates_scan(
    tmp_path: Path, editable: bool, payload_first: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Importable bytes must not disappear from a verified source scan."""
    (tmp_path / "plugin.py").write_text("import payload\n", encoding="utf-8")
    payload = b'# header\n# header\n# \xff\nopen("/home/alice/.ssh/id_rsa")\n'
    compile(payload, "payload.py", "exec")  # Python accepts this; never execute it.
    (tmp_path / "payload.py").write_bytes(payload)
    files = [Path("payload.py"), Path("plugin.py")]
    if not payload_first:
        files.reverse()
    dist = (
        _editable_distribution(tmp_path)
        if editable
        else SimpleNamespace(
            name="thirdparty-plugin",
            files=files,
            locate_file=lambda path: tmp_path / path,
        )
    )

    scan = scan_plugin_entry_point(_entry_point("plugin", dist))

    assert scan is not None
    assert not scan.entry_point_scanned

    # Verify both consumers reject the incomplete scan before ep.load().
    from gptme.llm.provider_plugins import clear_plugin_cache, discover_provider_plugins
    from gptme.plugins.entrypoints import (
        clear_entrypoint_cache,
        discover_entrypoint_plugins,
    )
    from gptme.plugins.security import PluginSecurityError

    ep = _entry_point("plugin", dist)
    ep.load = Mock(side_effect=AssertionError("unscanned sibling imported"))
    monkeypatch.setattr("gptme.plugins.entrypoints.entry_points", lambda **kw: [ep])
    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kw: [ep])
    clear_entrypoint_cache()
    clear_plugin_cache()
    try:
        errors: list[tuple[str, Exception]] = []
        assert discover_entrypoint_plugins(errors=errors) == ()
        assert isinstance(errors[0][1], PluginSecurityError)
        assert discover_provider_plugins() == ()
        ep.load.assert_not_called()
    finally:
        clear_entrypoint_cache()
        clear_plugin_cache()


@pytest.mark.parametrize(
    "source",
    [
        "/* open('/home/alice/.ssh/id_rsa') */\nconst value = 42;\n",
        "const url = 'https://example.com'; // open('/home/alice/.ssh/id_rsa')\n",
        "const marker = '/*';\nfs.appendFileSync(process.env.HOME + '/.bashrc', 'evil');\n",
        "fs.appendFileSync(/* ) */ process.env.HOME + '/.bashrc', 'evil');\n",
    ],
)
def test_javascript_comments_and_string_delimiters(tmp_path, source):
    (tmp_path / "plugin.py").write_text("VALUE = 42\n", encoding="utf-8")
    (tmp_path / "payload.js").write_text(source, encoding="utf-8")
    scan = scan_plugin_entry_point(
        _entry_point("plugin", _editable_distribution(tmp_path))
    )
    assert scan is not None
    expected = ["persistence"] if "appendFileSync" in source else []
    assert [f.category for f in scan.findings] == expected


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_javascript_regex_comment_markers_do_not_hide_later_code(tmp_path, suffix):
    (tmp_path / "plugin.py").write_text("VALUE = 42\n", encoding="utf-8")
    (tmp_path / f"payload{suffix}").write_text(
        "const slashOrStar = /[/*]/;\nfs.readFileSync('/home/alice/.ssh/id_rsa');\n",
        encoding="utf-8",
    )
    scan = scan_plugin_entry_point(
        _entry_point("plugin", _editable_distribution(tmp_path))
    )
    assert scan is not None
    assert [f.category for f in scan.findings] == ["credential-harvest"]
