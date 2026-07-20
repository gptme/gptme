"""Tests for the tool-call manifest writer hook."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from gptme.hooks import HookType
from gptme.hooks.manifest import register_manifest_hooks
from gptme.hooks.registry import HookRegistry
from gptme.hooks.types import ToolExecutePostData, ToolExecutePreData
from gptme.message import Message
from gptme.tools.base import ToolUse

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _run_hook(registry: HookRegistry, hook_type: HookType, data: object) -> list:
    return list(registry.trigger(hook_type, data))


@pytest.fixture()
def manifest_dir(tmp_path: Path) -> Path:
    return tmp_path / "manifests"


@pytest.fixture()
def registry(manifest_dir: Path) -> Iterator[HookRegistry]:
    import gptme.hooks.registry as _mod

    reg = HookRegistry()
    _mod.set_registry(reg)
    register_manifest_hooks(manifest_dir)
    yield reg
    _mod.set_registry(HookRegistry())


class TestManifestHooks:
    def test_manifest_dir_created(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        assert manifest_dir.exists()

    def test_pre_hook_writes_file(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        tool_use = ToolUse(tool="shell", args=["echo hi"], content=None)
        data = ToolExecutePreData(tool_use=tool_use)
        _run_hook(registry, HookType.TOOL_EXECUTE_PRE, data)

        files = list(manifest_dir.glob("*-pre.json"))
        assert len(files) == 1
        record = json.loads(files[0].read_text())
        assert record["tool"] == "shell"
        assert record["phase"] == "pre"
        assert record["sequence"] == 1
        assert "args_hash" in record
        assert record["args_hash"].startswith("sha256:")

    def test_post_hook_writes_file(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        tool_use = ToolUse(tool="shell", args=["echo hi"], content=None)
        result_msg = Message("system", "hi\n")
        pre_data = ToolExecutePreData(tool_use=tool_use)
        _run_hook(registry, HookType.TOOL_EXECUTE_PRE, pre_data)

        post_data = ToolExecutePostData(tool_use=tool_use, result_msgs=(result_msg,))
        _run_hook(registry, HookType.TOOL_EXECUTE_POST, post_data)

        files = list(manifest_dir.glob("*-post.json"))
        assert len(files) == 1
        record = json.loads(files[0].read_text())
        assert record["tool"] == "shell"
        assert record["phase"] == "post"
        assert "result_hash" in record
        assert record["result_hash"].startswith("sha256:")

    def test_sequence_increments_per_tool_call(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        for i in range(3):
            tool_use = ToolUse(tool="read", args=[f"file{i}.py"], content=None)
            data = ToolExecutePreData(tool_use=tool_use)
            _run_hook(registry, HookType.TOOL_EXECUTE_PRE, data)

        pre_files = sorted(manifest_dir.glob("*-pre.json"))
        assert len(pre_files) == 3
        seqs = [json.loads(f.read_text())["sequence"] for f in pre_files]
        assert seqs == [1, 2, 3]

    def test_no_files_when_tool_use_none(
        self, manifest_dir: Path, registry: HookRegistry
    ) -> None:
        data = ToolExecutePreData(tool_use=None)
        _run_hook(registry, HookType.TOOL_EXECUTE_PRE, data)
        assert list(manifest_dir.glob("*.json")) == []
