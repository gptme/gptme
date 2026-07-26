"""Project-configured shell hooks for lifecycle events."""

import logging
import os
import subprocess
from collections.abc import Generator
from pathlib import Path

from ..config import ChatConfig, ScriptHookConfig
from ..llm.models import get_default_model
from ..logmanager import LogManager
from ..message import Message
from .registry import register_hook
from .types import HookType

logger = logging.getLogger(__name__)

_SCRIPT_HOOK_EVENTS = {
    HookType.SESSION_START.value: HookType.SESSION_START,
    HookType.SESSION_END.value: HookType.SESSION_END,
}


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
    try:
        result = subprocess.run(
            hook.command,
            shell=True,
            check=False,
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=hook.timeout,
        )
        if result.returncode != 0:
            logger.warning(
                "Script hook %s failed (exit %d): %s",
                hook.event,
                result.returncode,
                result.stderr.strip(),
            )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Script hook %s timed out after %ds: %s",
            hook.event,
            hook.timeout,
            hook.command,
        )
    except Exception as exc:
        logger.warning("Script hook %s failed: %s", hook.event, exc)


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
    index: int,
) -> None:
    hook_type = _SCRIPT_HOOK_EVENTS[hook.event]

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
            return
            yield

        register_hook(f"script.{index}.{hook.event}", hook_type, _on_session_start)
        return

    def _on_session_end(manager: LogManager) -> Generator:
        _run_script_hook(
            hook,
            workspace,
            logdir=manager.logdir,
            model=_current_model(manager.logdir),
        )
        return
        yield

    register_hook(f"script.{index}.{hook.event}", hook_type, _on_session_end)


def register_script_hooks(hooks: list[ScriptHookConfig], workspace: Path) -> None:
    """Register project script hooks against the core hook registry."""
    for index, hook in enumerate(hooks):
        _register_script_hook(hook, workspace, index)
