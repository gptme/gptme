"""Real-kernel raster delivery through execute_python, persistence and providers."""

import base64
import io
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip("jupyter_client", reason="requires gptme[kernel]")
pytest.importorskip("ipykernel", reason="requires gptme[kernel]")
Image = pytest.importorskip("PIL.Image", reason="requires Pillow")

from gptme.logmanager import Log, LogManager
from gptme.message import Message
from gptme.tools import get_tool, init_tools
from gptme.tools import python as py_mod
from gptme.tools.python_kernel import KernelBackend


@pytest.fixture
def raster_kernel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[LogManager]:
    """Use a real, isolated kernel and keep every generated file under tmp_path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("GPTME_IPYTHON_BACKEND", "kernel")
    monkeypatch.setattr(py_mod, "_IPYTHON_BACKEND", "kernel")
    monkeypatch.setenv("GPTME_SANDBOX", "none")
    # The running interpreter has the dependencies checked above. Do not select
    # an unrelated project venv or bootstrap dependencies through uv.
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setattr(py_mod, "_kernel_mode_cache", None)
    monkeypatch.setattr(py_mod, "_kernel_mode_cache_key", None)
    monkeypatch.setattr(py_mod, "_kernel_mode_frozen", False)
    monkeypatch.setattr(py_mod, "registered_functions", {})
    monkeypatch.setattr(py_mod, "registered_function_tools", {})
    backend = KernelBackend()
    monkeypatch.setattr("gptme.tools.python_kernel.get_backend", lambda: backend)
    with (
        LogManager.preserve_current(),
        LogManager(logdir=tmp_path / "conversation", lock=False) as manager,
    ):
        try:
            yield manager
        finally:
            backend.shutdown()


def _execute(code: str) -> list[Message]:
    messages = list(py_mod.execute_python(code, [], None))
    assert messages, "execute_python must produce a tool result"
    content = "\n".join(message.content for message in messages)
    assert "Exception:" not in content, content
    assert "Kernel error:" not in content, content
    return messages


def _png(color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _image_code(data: bytes) -> str:
    """Construct an IPython Image entirely in memory, without a workspace file."""
    return (
        "import base64\n"
        "from IPython.display import Image, display\n"
        f"image = Image(data=base64.b64decode({base64.b64encode(data).decode()!r}), format='png')\n"
    )


def _assert_rasters(
    messages: list[Message], manager: LogManager, expected_count: int
) -> list[bytes]:
    files = [Path(str(file)) for message in messages for file in message.files]
    assert len(files) == expected_count, (
        "Kernel raster MIME output must be delivered as Message.files, not only "
        f"an Image repr: {messages!r}"
    )
    assert len(set(files)) == expected_count, (
        "Distinct outputs must not overwrite each other"
    )
    payloads = []
    for message in messages:
        descriptors = (message.metadata or {}).get("artifacts", [])
        for file in message.files:
            assert isinstance(file, Path), (
                "Kernel rasters must be local files, not URIs"
            )
            path = file
            assert path.is_file(), f"Unreadable raster attachment: {path}"
            assert path.resolve().is_relative_to(manager.logdir.parent)
            payloads.append(path.read_bytes())
            with Image.open(path) as image:
                assert image.format == "PNG"
                image.load()
            matching = [
                descriptor
                for descriptor in descriptors
                if (
                    Path(descriptor.get("path", ""))
                    if Path(descriptor.get("path", "")).is_absolute()
                    else manager.logdir / descriptor.get("path", "")
                ).resolve()
                == path.resolve()
            ]
            assert len(matching) == 1, (
                f"Missing or ambiguous ArtifactDescriptor for {path}"
            )
            descriptor = matching[0]
            assert descriptor["source_type"] == "attachment"
            assert descriptor["kind"] == "image"
            assert descriptor["mime_type"] == "image/png"
            assert descriptor["tool"] == "python"
    return payloads


def _assert_provider_roundtrip(messages: list[Message], manager: LogManager) -> None:
    """Exercise existing JSONL readers and real provider preparation, not an API."""
    from gptme.llm.llm_anthropic import _prepare_messages_for_api as prepare_anthropic
    from gptme.llm.llm_openai import _prepare_messages_for_api as prepare_openai

    init_tools(allowlist=["ipython"])
    tool = get_tool("ipython")
    assert tool is not None
    conversation = [
        Message("system", "You are helpful."),
        Message("user", "Render an image."),
        Message("assistant", '@ipython(raster1): {"code": "display(image)"}'),
        *[replace(message, call_id="raster1") for message in messages],
    ]
    logfile = manager.logdir / "raster-roundtrip.jsonl"
    Log(conversation).write_jsonl(logfile)
    reloaded = Log.read_jsonl(logfile).messages
    assert [message.metadata for message in reloaded] == [
        message.metadata for message in conversation
    ]
    expected = [
        Path(str(file)).read_bytes() for message in messages for file in message.files
    ]
    assert [
        Path(str(file)).read_bytes() for message in reloaded for file in message.files
    ] == expected

    converted_openai, _ = prepare_openai(reloaded, "openai/gpt-4o", [tool])
    openai_images = [
        part["image_url"]["url"]
        for message in cast(list[dict[str, Any]], converted_openai)
        for part in message.get("content", [])
        if isinstance(part, dict) and part["type"] == "image_url"
    ]
    assert openai_images == [
        "data:image/png;base64," + base64.b64encode(data).decode() for data in expected
    ]
    converted_anthropic, _, _ = prepare_anthropic(reloaded, [tool])
    anthropic_images = [
        part["source"]
        for message in cast(list[dict[str, Any]], converted_anthropic)
        for block in message["content"]
        if block["type"] == "tool_result"
        for part in block["content"]
        if part["type"] == "image"
    ]
    assert [source["media_type"] for source in anthropic_images] == ["image/png"] * len(
        expected
    )
    assert [base64.b64decode(source["data"]) for source in anthropic_images] == expected


@pytest.mark.parametrize("output", ["display(image)", "image"])
def test_kernel_ipython_png(raster_kernel: LogManager, output: str) -> None:
    data = _png("red")
    messages = _execute(_image_code(data) + 'print("raster stdout")\n' + output)
    assert "raster stdout" in "\n".join(message.content for message in messages)
    assert _assert_rasters(messages, raster_kernel, 1) == [data]
    _assert_provider_roundtrip(messages, raster_kernel)
    # The next ordinary cell reuses state, without reattaching old image output.
    followup = _execute(
        'print("still running")\nimage.data == base64.b64decode('
        + repr(base64.b64encode(data).decode())
        + ")"
    )
    content = "\n".join(message.content for message in followup)
    assert "still running" in content
    assert "True" in content
    assert not any(message.files for message in followup)


def test_kernel_multiple_pngs(raster_kernel: LogManager) -> None:
    first, second = _png("red"), _png("blue")
    messages = _execute(
        _image_code(first)
        + "display(image)\n"
        + _image_code(second)
        + "display(image)\nprint('both images')\n42"
    )
    content = "\n".join(message.content for message in messages)
    assert "both images" in content
    assert "Result:" in content and "42" in content
    assert _assert_rasters(messages, raster_kernel, 2) == [first, second]
    _assert_provider_roundtrip(messages, raster_kernel)


@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize("replacement", [False, True])
def test_kernel_clear_rasters(
    raster_kernel: LogManager, wait: bool, replacement: bool
) -> None:
    first, second = _png("red"), _png("blue")
    code = _image_code(first) + "display(image)\n"
    code += f"from IPython.display import clear_output\nclear_output(wait={wait!r})\n"
    if replacement:
        code += _image_code(second) + "display(image)\n"
    messages = _execute(code)
    expected = [second] if replacement else ([first] if wait else [])
    assert _assert_rasters(messages, raster_kernel, len(expected)) == expected


def test_kernel_deferred_clear_on_stream(raster_kernel: LogManager) -> None:
    messages = _execute(
        _image_code(_png("red"))
        + "display(image)\nfrom IPython.display import clear_output\n"
        + "clear_output(wait=True)\nprint('replacement stream')\n"
    )
    assert _assert_rasters(messages, raster_kernel, 0) == []
    assert "replacement stream" in "\n".join(message.content for message in messages)


@pytest.mark.parametrize("budget", ["count", "bytes"])
def test_kernel_clear_preserves_cell_raster_budget(
    raster_kernel: LogManager, monkeypatch: pytest.MonkeyPatch, budget: str
) -> None:
    from gptme.tools import python_kernel as kernel

    data = _png("red")
    if budget == "count":
        monkeypatch.setattr(kernel, "_MAX_RASTER_IMAGES", 1)
    else:
        monkeypatch.setattr(kernel, "_MAX_RASTER_TOTAL_BYTES", len(data))
    messages = _execute(
        _image_code(data)
        + "display(image)\nfrom IPython.display import clear_output\n"
        + "clear_output(wait=False)\ndisplay(image)\n"
    )
    assert _assert_rasters(messages, raster_kernel, 0) == []
    limit = "count" if budget == "count" else "byte"
    assert f"image {limit} limit" in "\n".join(message.content for message in messages)


def test_kernel_matplotlib_inline(raster_kernel: LogManager) -> None:
    pytest.importorskip("matplotlib", reason="requires Matplotlib for inline plotting")
    messages = _execute(
        "%matplotlib inline\n"
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(figsize=(2, 2))\n"
        "ax.plot([0, 1, 2], [2, 0, 1])\n"
        "print('inline plot')\n"
        "plt.show()"
    )
    assert "inline plot" in "\n".join(message.content for message in messages)
    _assert_rasters(messages, raster_kernel, 1)
    _assert_provider_roundtrip(messages, raster_kernel)
    followup = _execute("plt.get_fignums()")
    assert "[]" in "\n".join(message.content for message in followup)
    assert not any(message.files for message in followup)


def test_kernel_text_and_reuse_control(raster_kernel: LogManager) -> None:
    messages = _execute(
        'raster_control = 40\nprint("ordinary stdout")\nraster_control + 2'
    )
    content = "\n".join(message.content for message in messages)
    assert "ordinary stdout" in content
    assert "Result:" in content and "42" in content
    assert not any(message.files for message in messages)
    assert "43" in "\n".join(
        message.content for message in _execute("raster_control + 3")
    )


@pytest.mark.parametrize("with_raster", [False, True])
def test_kernel_message_helper_with_image_only_output(
    raster_kernel: LogManager, with_raster: bool
) -> None:
    helper_message = Message("system", "registered helper result")

    def raster_helper() -> Message:
        return helper_message

    py_mod.register_function(raster_helper)
    code = ""
    data = _png("green")
    if with_raster:
        # No text/plain fallback, stdout, stderr or execute_result. Raster output
        # alone must prevent the Message bridge's summary shortcut from dropping it.
        code = (
            "import base64\nfrom IPython.display import display\n"
            f"display({{'image/png': {base64.b64encode(data).decode()!r}}}, raw=True)\n"
        )
    # The bridge delivers the Message itself. Suppress the kernel-side proxy's
    # expression repr so this genuinely exercises the no-text summary shortcut.
    messages = _execute(code + "raster_helper();")
    assert any(message.content == helper_message.content for message in messages)
    if with_raster:
        assert _assert_rasters(messages, raster_kernel, 1) == [data]
    else:
        assert len(messages) == 1
        assert messages[0] is helper_message
        assert not messages[0].files
