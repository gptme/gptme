"""Project-configured shell hooks for lifecycle events."""

import logging
import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path

from ..config import ChatConfig, ScriptHookConfig
from ..llm.models import get_default_model
from ..logmanager import LogManager
from ..message import Message
from .registry import register_hook
from .types import HookType

logger = logging.getLogger(__name__)

_WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
_MAX_OUTPUT_LOG_BYTES = 4096
_PROCESS_REAP_TIMEOUT = 5
_SCRIPT_HOOK_EVENTS = {
    HookType.SESSION_START.value: HookType.SESSION_START,
    HookType.SESSION_END.value: HookType.SESSION_END,
}


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate a timed-out shell and its descendants."""
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                [
                    "taskkill",
                    "/F",
                    "/T",
                    "/PID",
                    str(process.pid),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError as exc:
            raise RuntimeError(
                f"failed to launch taskkill for process tree {process.pid}"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"taskkill failed for process tree {process.pid} "
                f"with exit code {result.returncode}"
            )
        return

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        process.kill()


def _run_script_hook(
    hook: ScriptHookConfig,
    workspace: Path,
    *,
    logdir: Path,
    model: str,
) -> None:
    env = {
        **os.environ,
        "GPTME_HOOK_EVENT": hook.event,
        "GPTME_LOGDIR": str(logdir),
        "GPTME_WORKSPACE": str(workspace),
        "GPTME_MODEL": model,
    }
    with tempfile.TemporaryFile(mode="w+t") as output:
        process = subprocess.Popen(
            hook.command,
            shell=True,
            cwd=workspace,
            env=env,
            stdout=output,
            stderr=output,
            text=True,
            start_new_session=sys.platform != "win32",
            creationflags=(
                _WINDOWS_CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            ),
        )
        try:
            process.wait(timeout=hook.timeout)
        except subprocess.TimeoutExpired:
            try:
                _terminate_process_tree(process)
            except Exception:
                logger.exception(
                    "Script hook %s timed out after %ds and its process tree "
                    "could not be terminated: %s",
                    hook.event,
                    hook.timeout,
                    hook.command,
                )
                # Tree termination is the only operation that can account for
                # descendants. If that OS primitive fails, still stop and reap
                # the tracked shell before surfacing the incomplete cleanup.
                try:
                    process.kill()
                except OSError:
                    logger.exception(
                        "Failed to terminate tracked script hook process %d",
                        process.pid,
                    )
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    logger.error(
                        "Tracked script hook process %d did not exit after kill",
                        process.pid,
                    )
                raise
            try:
                process.wait(timeout=_PROCESS_REAP_TIMEOUT)
            except subprocess.TimeoutExpired:
                logger.error(
                    "Script hook process %d did not exit within %ds after process-tree kill",
                    process.pid,
                    _PROCESS_REAP_TIMEOUT,
                )
            logger.warning(
                "Script hook %s timed out after %ds: %s",
                hook.event,
                hook.timeout,
                hook.command,
            )
            return
        except Exception as exc:
            _terminate_process_tree(process)
            process.wait()
            logger.warning("Script hook %s failed: %s", hook.event, exc)
            return

        if process.returncode != 0:
            output.seek(0)
            logger.warning(
                "Script hook %s failed (exit %d): %s",
                hook.event,
                process.returncode,
                output.read(_MAX_OUTPUT_LOG_BYTES).strip(),
            )


def _current_model(logdir: Path) -> str:
    """Resolve the effective model at trigger time, including `/model` changes."""
    chat_model = ChatConfig.from_logdir(logdir).model
    if chat_model:
        return chat_model
    default_model = get_default_model()
    return default_model.full if default_model else ""


def _register_script_hook(
    hook: ScriptHookConfig,
    workspace: Path,
    order: int,
) -> None:
    hook_type = _SCRIPT_HOOK_EVENTS[hook.event]
    hook_name = f"script.{order:06d}.{hook.event}"

    if hook_type is HookType.SESSION_START:

        def _on_session_start(
            logdir: Path,
            workspace: Path | None,
            initial_msgs: list[Message],
        ) -> Generator:
            del initial_msgs
            hook_workspace = workspace or Path.cwd()
            _run_script_hook(
                hook,
                hook_workspace,
                logdir=logdir,
                model=_current_model(logdir),
            )
            yield

        register_hook(
            hook_name,
            hook_type,
            _on_session_start,
            priority=hook.priority,
        )
        return

    def _on_session_end(manager: LogManager) -> Generator:
        _run_script_hook(
            hook,
            workspace,
            logdir=manager.logdir,
            model=_current_model(manager.logdir),
        )
        yield

    register_hook(
        hook_name,
        hook_type,
        _on_session_end,
        priority=hook.priority,
    )


def register_script_hooks(hooks: list[ScriptHookConfig], workspace: Path) -> None:
    """Register configured script hooks against the core hook registry."""
    for index, hook in enumerate(hooks):
        # Hook names sort descending, so reverse the index to preserve config order.
        _register_script_hook(hook, workspace, len(hooks) - index)
