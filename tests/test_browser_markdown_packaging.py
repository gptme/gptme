"""Frozen builds must retain the runtime Pandoc filter next to the browser module."""

import ast
import os
from pathlib import Path


def test_frozen_build_includes_browser_filter() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = root / "scripts/pyinstaller/gptme-server.spec"
    tree = ast.parse(spec.read_text())
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "datas"
            for target in node.targets
        )
    )
    namespace: dict[str, object] = {"project_root": root, "os": os}
    exec(
        compile(ast.Module(body=[assignment], type_ignores=[]), str(spec), "exec"),
        namespace,
    )
    asset = root / "gptme/tools/_browser_markdown.lua"
    assert asset.is_file()
    datas = namespace["datas"]
    assert isinstance(datas, list)
    assert (str(asset), "gptme/tools") in datas
