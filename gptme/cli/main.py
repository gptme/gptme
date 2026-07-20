from __future__ import annotations

import atexit
import importlib
import importlib.metadata as _ilm
import logging
import os
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import click
from click.core import ParameterSource

import gptme

from ..constants import MULTIPROMPT_SEPARATOR
from ..dirs import get_logs_dir
from ..gears import parse_gear, resolve_gear

# NOTE: keep module-level imports of the wider gptme package out of this file.
# Importing gptme.cli.main should stay cheap: `gptme --help`, `--version`, and
# external-subcommand dispatch (`gptme foo` -> `gptme-foo`) all pay for it, and
# CI benchmarks it (.github/workflows/benchmark.yml). Heavy modules (chat, llm,
# tools, prompts, logmanager, ...) are imported inside the functions that need
# them instead.

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..logmanager import ConversationMeta
    from ..prompts import ContextMode
    from ..tools import ToolFormat

logger = logging.getLogger(__name__)

# Core scripts shipped with gptme itself — dynamically discovered from the
# installed package's console_scripts entry points so this never drifts from
# [project.scripts] in pyproject.toml.
try:
    _dist = _ilm.distribution("gptme")
    _CORE_GPTME_SCRIPTS: frozenset[str] = frozenset(
        ep.name for ep in _dist.entry_points if ep.group == "console_scripts"
    )
except _ilm.PackageNotFoundError:
    _CORE_GPTME_SCRIPTS = frozenset()


def _discover_gptme_plugins() -> list[str]:
    """Find gptme-* executables in PATH that are not part of core gptme.

    Returns a sorted list of binary names, e.g. ['gptme-sessions', 'gptme-tools'].
    """
    seen: set[str] = set()
    plugins: list[str] = []

    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        if not path_dir:
            continue
        try:
            for entry in Path(path_dir).iterdir():
                name = entry.name
                if (
                    name.startswith("gptme-")
                    and name not in _CORE_GPTME_SCRIPTS
                    and name not in seen
                    and entry.is_file()
                    and os.access(entry, os.X_OK)
                ):
                    seen.add(name)
                    plugins.append(name)
        except OSError:
            continue

    return sorted(plugins)


class _DynamicHelpCommand(click.Command):
    """click.Command subclass that renders the dynamic parts of --help lazily.

    Computing the command list, tool availability, and recommended models
    imports most of gptme, so it happens here — at --help render time — rather
    than at module import time. Placeholders like ``{commands_help}`` in the
    command/option help strings are substituted on first render. Also appends
    discovered gptme-* plugin subcommands.
    """

    _help_expanded = False

    def _expand_dynamic_help(self) -> None:
        if self._help_expanded:
            return
        self._help_expanded = True

        import textwrap

        from ..commands import _gen_help
        from ..llm.models import get_recommended_model
        from ..tools import get_available_tools
        from ..util import console

        # Tool discovery loads plugins, which log status lines (e.g.
        # "Using plugins ...") — suppress those while rendering help.
        prev_quiet = console.quiet
        console.quiet = True
        try:
            commands_help = "\n".join(_gen_help(incl_langtags=False))
            tools = get_available_tools(include_mcp=False)
        finally:
            console.quiet = prev_quiet
        available_tools = textwrap.fill(
            ", ".join(sorted(tool.name for tool in tools if tool.is_available)),
            width=76,
            initial_indent="  ",
            subsequent_indent="  ",
        )
        model_examples = (
            f"openai/{get_recommended_model('openai')}, "
            f"anthropic/{get_recommended_model('anthropic')}"
        )

        if self.help:
            self.help = self.help.replace("{commands_help}", commands_help).replace(
                "{available_tools}", available_tools
            )
        for param in self.params:
            if isinstance(param, click.Option) and param.help:
                param.help = param.help.replace("{model_examples}", model_examples)

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self._expand_dynamic_help()
        super().format_help(ctx, formatter)
        plugins = _discover_gptme_plugins()
        if plugins:
            with formatter.section("Installed external subcommands"):
                rows = [
                    (
                        f"gptme {name.removeprefix('gptme-')}",
                        f"delegates to {name}",
                    )
                    for name in plugins
                ]
                formatter.write_dl(rows)


def _validate_model_param(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    """Reject empty --model early so the CLI exits before heavy setup work."""
    if value is None:
        return value

    model = value.strip()
    if not model:
        raise click.BadParameter("Model name cannot be empty.", ctx=ctx, param=param)
    if "/" in model and any(part == "" for part in model.split("/")):
        raise click.BadParameter(
            "Model path components cannot be empty. Use 'provider/model' or a bare provider name.",
            ctx=ctx,
            param=param,
        )
    return value


script_path = Path(os.path.realpath(__file__))
_STDIN_PIPE_GRACE_PERIOD = 1.0

# Sub-timeout used by the inner read loop in `_read_stdin` to disambiguate
# "data ready" from "pipe fd is readable but write end is open and idle".
# Tuned to 100ms — generous enough to bridge small producer gaps without
# being so long that an idle pipe stalls the prompt for a noticeable beat.
_STDIN_PIPE_INTER_CHUNK_TIMEOUT = 0.1


class CommaSeparatedChoice(click.ParamType):
    """Click type that validates comma-separated values against a set of choices."""

    name = "TEXT"

    def __init__(
        self,
        choices: list[str] | Callable[[], list[str]],
        allow_prefix: str | None = None,
        allow_prefixes: list[str] | None = None,
        extra_choices_for_prefix: dict[str, list[str] | Callable[[], list[str]]]
        | None = None,
        lenient_prefixes: list[str] | None = None,
        metavar: str | None = None,
    ):
        # Choices may be a zero-arg callable, resolved on first use, so that
        # expensive sources (tool discovery) don't run at CLI definition time.
        self._choices_src = choices
        # Support both single prefix and multiple prefixes
        if allow_prefixes:
            self.allow_prefixes = allow_prefixes
        elif allow_prefix:
            self.allow_prefixes = [allow_prefix]
        else:
            self.allow_prefixes = []
        self._extra_choices_src = extra_choices_for_prefix or {}
        # Prefixes for which unknown names are accepted at parse time. Plugin
        # tools aren't known when the CLI is built (plugins load later), so a
        # prefixed name like "+tts" must pass; it's resolved against the loaded
        # toolset later, which warns if it's genuinely missing.
        self.lenient_prefixes = set(lenient_prefixes or [])
        self._metavar = metavar

    @property
    def choices(self) -> list[str]:
        if callable(self._choices_src):
            self._choices_src = list(self._choices_src())
        return self._choices_src

    @property
    def _choice_set(self) -> set[str]:
        return set(self.choices)

    def _extra_choices(self, prefix: str) -> set[str]:
        src = self._extra_choices_src.get(prefix)
        if src is None:
            return set()
        if callable(src):
            src = list(src())
            self._extra_choices_src[prefix] = src
        return set(src)

    def convert(self, value, param, ctx):
        # Click keeps the leading "=" for short options passed as `-x=value`.
        # Normalize that form so documented examples like `-t=-browser` work.
        value = value.removeprefix("=")
        parts = [v.strip() for v in value.split(",") if v.strip()]
        if not parts:
            self.fail("value cannot be empty.", param, ctx)
        for part in parts:
            check = part
            matched_prefix = None
            for prefix in self.allow_prefixes:
                if check.startswith(prefix):
                    check = check[len(prefix) :]
                    matched_prefix = prefix
                    break
            # Allow file paths (e.g. path/to/tool.py) to pass through
            if check.endswith(".py") or "/" in check or "\\" in check:
                continue
            # Defer validation for lenient prefixes (e.g. "+tts" plugin tools)
            if matched_prefix in self.lenient_prefixes:
                continue
            extra_choices = (
                self._extra_choices(matched_prefix)
                if matched_prefix is not None
                else set()
            )
            if check not in self._choice_set and check not in extra_choices:
                self.fail(
                    f"invalid choice: {part}. (choose from {', '.join(self.choices)})",
                    param,
                    ctx,
                )
        return value

    def get_metavar(
        self, param: click.Parameter, ctx: click.Context | None = None
    ) -> str | None:
        if self._metavar:
            return self._metavar
        return "[" + "|".join(self.choices) + "]"


class WorkspacePath(click.ParamType):
    """Click type for workspace paths: a directory path or '@log'."""

    name = "DIRECTORY"

    def convert(self, value, param, ctx):
        if value == "@log":
            return value
        path = Path(value).expanduser()
        if not path.exists():
            self.fail(f"directory '{value}' does not exist.", param, ctx)
        if not path.is_dir():
            self.fail(f"'{value}' is not a directory.", param, ctx)
        return str(path.resolve())


class ConversationName(click.ParamType):
    """Click type for conversation names stored under the logs directory."""

    name = "TEXT"

    def convert(self, value, param, ctx):
        if value == "random":
            return value
        # Empty/whitespace-only names silently default to "random" instead of
        # crashing with a validation error. This guards against Click version
        # and shell edge cases where --name "" bypasses the ParamType's
        # convert method and passes an empty string straight to main().
        # Non-empty values still go through conversation_name_error() below.
        if not value or not value.strip():
            return "random"
        from ..logmanager import conversation_name_error

        if error := conversation_name_error(value):
            self.fail(error, param, ctx)
        return value


def _looks_like_tool_file_path(value: str) -> bool:
    return (
        value.endswith(".py")
        or value.startswith(("/", "./", "../", "~"))
        or (len(value) > 2 and value[1] == ":" and value[2] in "/\\")
    )


def _validate_custom_tool_paths(tool_allowlist: str | None) -> None:
    """Fail fast on missing custom tool files before config/logging init."""
    if not tool_allowlist:
        return

    for raw_item in tool_allowlist.split(","):
        item = raw_item.strip().removeprefix("+").removeprefix("-")
        if not item or not _looks_like_tool_file_path(item):
            continue

        path = Path(item).expanduser()
        if path.suffix != ".py":
            raise click.UsageError(f"Tool file must be a .py file: {item}")
        if not path.exists():
            raise click.UsageError(f"Tool file does not exist: {item}")
        if not path.is_file():
            raise click.UsageError(f"Tool path is not a file: {item}")


def _extract_missing_explicit_local_path(prompt: str) -> str | None:
    """Return an explicit local-path prompt that is missing on disk.

    Only catches unambiguous local path forms so ordinary text prompts and
    repo/host-style strings like ``github.com/org/repo`` keep working.
    """
    from ..util.content import is_message_command

    stripped = prompt.strip()
    if not stripped or any(ch.isspace() for ch in stripped):
        return None
    if is_message_command(stripped):
        return None

    candidate = stripped.removeprefix("@")
    explicit_local = candidate.startswith(("/", "~/", "./", "../")) or (
        len(candidate) >= 3
        and candidate[1] == ":"
        and candidate[2] in ("/", "\\")
        and candidate[0].isalpha()
    )
    if not explicit_local:
        return None

    try:
        if Path(candidate).expanduser().exists():
            return None
    except OSError:
        return None
    return candidate


def _find_missing_explicit_local_path(prompts: list[str]) -> str | None:
    """Return the first missing explicit local-path prompt in raw CLI argv order.

    This catches mixed positional argv like ``gptme missing.py "fix it"`` before
    prompt arguments are merged into a single message, where the path would
    otherwise be masked by surrounding text.
    """
    for prompt in prompts:
        if missing := _extract_missing_explicit_local_path(prompt):
            return missing
    return None


def _known_tool_names() -> list[str]:
    """Names of all known built-in tools (available or not).

    Imports the tool subsystem, so only call this lazily (option validation,
    --help rendering) — never at module import time.
    """
    from ..tools import get_available_tools

    return sorted(tool.name for tool in get_available_tools(include_mcp=False))


docstring = f"""
gptme is a chat-CLI for LLMs, empowering them with tools to run shell commands, execute code, read and manipulate files, and more.

If PROMPTS are provided, a new conversation will be started with it.
PROMPTS can be chained with the '{MULTIPROMPT_SEPARATOR}' separator.

\b
Examples:
  gptme "hello"                              Start a conversation
  gptme "fix TODOs" main.py                  Include file or URL in context
  gptme "review" github.com/org/repo/pull/1  Include a GitHub PR in context
  gptme --tools none "what is 2+2"           No tools, just chat
  gptme -t patch,save "fix typo" main.py     Only specific tools (comma-separated)
  gptme -t +subagent "plan a refactor"       Default tools + subagent
  gptme -t=-browser "summarize code"         Default tools minus browser
  gptme --context files "do task"             Skip context_cmd, keep project files

\b
Available tools:
{{available_tools}}

\b
The interface provides /commands during a conversation:
{{commands_help}}

\b
Subcommand shortcuts:
  gptme search QUERY      Search conversation logs (alias for gptme-util chats search)
  gptme chats [args]      Any gptme-util subcommand works directly (chats, tools, skills, ...)
  gptme <cmd> [args]      gptme-util subcommand or gptme-<cmd> binary, in that order
  (installed gptme-* binaries in PATH are listed at the bottom of this help)

\b
Utilities:
  gptme tools list        List all tools and their availability
  gptme tools info TOOL   Show detailed tool instructions/examples
  gptme skills list       List discoverable skills in the current workspace
  gptme skills show NAME  Show a skill or lesson by name
  gptme chats list        List past conversations
  gptme chats search Q    Search conversations for query (full options)
  gptme chats send ID MSG Queue a prompt for a running chat from another terminal
  gptme chats rename      Rename a conversation
  gptme models list       List available models
  gptme snapshot list     List workspace snapshots outside a session
  gptme context index     Index project files for RAG
  gptme llm generate      Direct LLM generation without chat

Run 'gptme-util --help' for all utility commands."""


@click.command(
    help=docstring,
    context_settings={"auto_envvar_prefix": "GPTME"},
    cls=_DynamicHelpCommand,
)
@click.pass_context
@click.argument(
    "prompts",
    default=None,
    required=False,
    nargs=-1,
)
@click.option(
    "--name",
    default="random",
    type=ConversationName(),
    help="Conversation ID (used to resume). Defaults to a random name.",
)
@click.option(
    "-m",
    "--model",
    default=None,
    callback=_validate_model_param,
    help="Model to use, e.g. {model_examples}. If only provider given then a default is used.",
)
@click.option(
    "-w",
    "--workspace",
    "workspace",
    default=None,
    type=WorkspacePath(),
    help="Path to workspace directory, or '@log' to use the log directory.",
)
@click.option(
    "--agent-path",
    "agent_path",
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Path to agent workspace directory.",
)
@click.option(
    "-r",
    "--resume",
    is_flag=True,
    help="Load most recent conversation.",
)
@click.option(
    "-y",
    "--no-confirm",
    is_flag=True,
    help="Skip all confirmation prompts.",
)
@click.option(
    "--gear",
    type=click.IntRange(0, 4),
    default=None,
    help="Autonomy preset: 0=observe, 1=review, 2=plan, 3=execute, 4=integrate. Explicit --tools/--agent-profile/--no-confirm override preset parts.",
)
@click.option(
    "-n",
    "--non-interactive",
    "non_interactive",
    is_flag=True,
    help="Non-interactive mode. Implies --no-confirm.",
)
@click.option(
    "--output-format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format for non-interactive mode. 'json' emits one JSON object per line on stdout.",
)
@click.option(
    "--system",
    "prompt_system",
    default=None,
    help="System prompt [full|short|<custom>]. Defaults to 'full', or the value of `system` in gptme.toml [prompt] if set.",
)
@click.option(
    "-t",
    "--tools",
    "tool_allowlist",
    default=None,
    multiple=True,
    type=CommaSeparatedChoice(
        # Accept all *known* tools, not just currently-available ones, so a tool
        # that exists but is temporarily unavailable (e.g. 'tts' when its server
        # isn't running) is reported as unavailable at load time rather than as a
        # misleading "invalid choice" here. Resolved lazily: tool discovery
        # imports most of gptme and must not run at module import time.
        lambda: _known_tool_names() + ["none"],
        allow_prefixes=["+", "-"],
        extra_choices_for_prefix={"-": _known_tool_names},
        # Only '+' is lenient: plugin tools (added via '+tool') aren't known at
        # parse time. '-tool' exclusions stay strict against known tools so typos
        # like '-shel' are caught early instead of being silently ignored.
        lenient_prefixes=["+"],
        metavar="TOOL",
    ),
    help="Tools to allow. Comma-separated or repeated. Use '+tool' to add to defaults (e.g., '-t +subagent'). Use '-tool' to exclude from defaults (e.g., '-t=-browser'). Use 'none' to disable all tools. Supports .py file paths for custom tools (e.g., '-t path/to/tool.py'). See 'Available tools' above for the list.",
)
@click.option(
    "--agent-profile",
    "agent_profile",
    default=None,
    help="Agent profile to use. Profiles provide system prompts, tool access hints, and behavior rules. Use 'gptme-util profile list' to see available profiles.",
)
@click.option(
    "--tool-format",
    "tool_format",
    default=None,
    type=click.Choice(["markdown", "xml", "tool"]),
    help="Tool format to use.",
)
@click.option(
    "--prune-tool-output/--no-prune-tool-output",
    "prune_tool_output",
    default=None,
    help="Use a summary model to keep only the relevant lines from large shell/read tool outputs.",
)
@click.option(
    "--stream/--no-stream",
    "stream",
    default=True,
    help="Stream responses",
)
@click.option(
    "--show-hidden",
    is_flag=True,
    help="Show hidden system messages.",
)
@click.option(
    "--show-prompt-stats",
    is_flag=True,
    help="Show startup system-prompt token stats for the current configuration and exit.",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show verbose output.",
)
@click.option(
    "--multi-tool/--no-multi-tool",
    "multi_tool",
    default=None,
    hidden=True,
    help="Allow multiple tool calls per LLM response (disables break-on-tooluse). Enables efficient API usage with sequential execution.",
)
@click.option(
    "--version",
    is_flag=True,
    help="Show version and configuration information",
)
@click.option(
    "--version-json",
    "version_json",
    is_flag=True,
    hidden=True,
    help="Show version info as JSON (for scripting)",
)
@click.option(
    "--profile",
    is_flag=True,
    help="Enable profiling and save results to gptme-profile-{timestamp}.prof",
)
@click.option(
    "--context",
    "context_include",
    multiple=True,
    type=CommaSeparatedChoice(["all", "files", "cmd"], metavar="[all|files|cmd]"),
    callback=lambda ctx, param, value: value or None,
    help="Context to include (default: all). Comma-separated or repeated. Tools and agent config (--agent-path) are always included.",
)
@click.option(
    "--context-include",
    "context_include",
    multiple=True,
    type=CommaSeparatedChoice(["all", "files", "cmd"], metavar="[all|files|cmd]"),
    hidden=True,
)
@click.option(
    "--no-workspace",
    "no_workspace",
    is_flag=True,
    help="Skip all workspace context (prompt files and context_cmd). Tools and agent config are still included.",
)
@click.option(
    "--architect",
    "architect_enabled",
    is_flag=True,
    help="Enable architect/editor split mode: plan with strong model, execute with cheap model.",
)
@click.option(
    "--architect-model",
    "architect_model",
    default=None,
    help="Model to use for the architect (planning) turn. E.g. openai/o3, anthropic/claude-opus-4-7.",
)
@click.option(
    "--editor-model",
    "editor_model",
    default=None,
    help="Model to use for the editor (execution) turn. E.g. anthropic/claude-sonnet-4-5, openai/gpt-5-mini.",
)
@click.option(
    "--auto-accept-architect",
    "auto_accept_architect",
    is_flag=True,
    help="Skip user confirmation between architect and editor turns.",
)
@click.option(
    "--output-schema",
    "output_schema",
    default=None,
    hidden=True,
    help="Schema for structured output in format 'module:ClassName'. The class should be a Pydantic BaseModel.",
)
@click.option(
    "--injection-hygiene",
    "injection_hygiene",
    type=click.Choice(["off", "warn", "block"]),
    default=None,
    envvar="GPTME_INJECTION_HYGIENE",
    help="Prompt injection hygiene for tool outputs: off (disabled), warn (flag suspicious content), block (redact HIGH-severity patterns). Overrides GPTME_INJECTION_HYGIENE env var.",
)
@click.option(
    "--manifest-dir",
    "manifest_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    envvar="GPTME_MANIFEST_DIR",
    help="Write a JSON record before and after each tool call to this directory. "
    "Records can be committed alongside session artifacts for tool-call-level attribution.",
)
def main(
    ctx: click.Context,
    prompts: list[str],
    prompt_system: str | None,
    name: str,
    model: str | None,
    tool_allowlist: tuple[str, ...],
    gear: int | None,
    agent_profile: str | None,
    tool_format: ToolFormat | None,
    prune_tool_output: bool | None,
    stream: bool,
    verbose: bool,
    no_confirm: bool,
    non_interactive: bool,
    output_format: str,
    show_hidden: bool,
    show_prompt_stats: bool,
    version: bool,
    version_json: bool,
    resume: bool,
    workspace: str | None,
    agent_path: str | None,
    profile: bool,
    multi_tool: bool | None,
    architect_enabled: bool,
    architect_model: str | None,
    editor_model: str | None,
    auto_accept_architect: bool,
    context_include: tuple[str, ...],
    no_workspace: bool,
    output_schema: str | None,
    injection_hygiene: str | None,
    manifest_dir: Path | None,
):
    """Main entrypoint for the CLI."""

    # Dispatch: `gptme search QUERY` → chats search (discoverability alias)
    if prompts and prompts[0] == "search" and not version and not version_json:
        from ..tools.chats import search_chats  # fmt: skip

        query = " ".join(prompts[1:]).strip()
        if not query:
            raise click.UsageError(
                "Usage: gptme search <query>\n\nFor all options, use: gptme-util chats search --help"
            )
        search_chats(
            query, max_results=20, context_lines=1, max_matches=1
        )  # show more results than the default 5
        return

    # gptme-util subcommand mirroring: `gptme chats [...]` → `gptme-util chats [...]`
    # Any top-level gptme-util subcommand can be invoked without typing 'gptme-util'.
    if prompts and not version and not version_json:
        from .util import UTIL_SUBCOMMANDS  # cheap: just a sorted list constant

        if prompts[0] in UTIL_SUBCOMMANDS:
            if util_exec := shutil.which("gptme-util"):
                sys.exit(subprocess.call([util_exec, *prompts]))
            else:
                print(
                    f"Error: '{prompts[0]}' is a gptme-util subcommand but gptme-util is not installed.\n"
                    "Install it with: pip install gptme[util]",
                    file=sys.stderr,
                )
                sys.exit(1)

    # Plugin dispatch: `gptme CMD [args...]` → `gptme-CMD [args...]` if installed
    # Enables extensibility: `gptme sessions` works if gptme-sessions is in PATH.
    if prompts and not version and not version_json:
        plugin = f"gptme-{prompts[0]}"
        if plugin_path := shutil.which(plugin):
            sys.exit(subprocess.call([plugin_path, *prompts[1:]]))

    # Register manifest hooks early so they are in the registry before any tool call.
    if manifest_dir is not None:
        from ..hooks.manifest import register_manifest_hooks  # fmt: skip

        register_manifest_hooks(manifest_dir)

    # Defense-in-depth: handle empty/whitespace names in case Click bypasses convert()
    # (observed to occur in some Click versions when --name "" is passed)
    if not name or not name.strip():
        name = "random"

    if no_workspace and context_include:
        raise click.UsageError(
            "--no-workspace and --context are mutually exclusive: "
            "--no-workspace strips all workspace context, so --context values would be silently ignored."
        )

    # Apply gear defaults before explicit profile/tools/no-confirm flags.
    selected_gear = parse_gear(gear)
    if selected_gear is not None:
        gear_resolution = resolve_gear(selected_gear)
        if agent_profile is None and gear_resolution.profile_name:
            agent_profile = gear_resolution.profile_name
        if (
            ctx.get_parameter_source("tool_allowlist") == ParameterSource.DEFAULT
            and gear_resolution.tool_allowlist is not None
        ):
            tool_allowlist = gear_resolution.tool_allowlist
        if (
            ctx.get_parameter_source("no_confirm") == ParameterSource.DEFAULT
            and gear_resolution.no_confirm
        ):
            no_confirm = True
        logger.info(
            "Using gear %s (%s): %s",
            gear_resolution.gear,
            gear_resolution.name,
            gear_resolution.description,
        )

    # Apply agent profile if specified
    selected_profile = None
    if agent_profile:
        from ..profiles import get_profile

        selected_profile = get_profile(agent_profile)
        if not selected_profile:
            raise click.BadParameter(
                f"unknown profile '{agent_profile}'. "
                "Use 'gptme-util profile list' to see available profiles.",
                ctx=ctx,
                param_hint="'--agent-profile'",
            )

        logger.info(f"Using agent profile: {selected_profile.name}")

        # Apply profile tools if no explicit tools specified
        if (
            ctx.get_parameter_source("tool_allowlist") == ParameterSource.DEFAULT
            and selected_profile.tools is not None
        ):
            tool_allowlist = tuple(selected_profile.tools)

    # Handle multi-tool flag - controls break_on_tooluse
    if multi_tool is not None:
        # Only set GPTME_BREAK_ON_TOOLUSE - multi-tool mode allows multiple tool calls
        # per LLM response but executes them sequentially (no thread-safety issues)
        os.environ["GPTME_BREAK_ON_TOOLUSE"] = "0" if multi_tool else "1"

    # Propagate --injection-hygiene to the env var read by the hook at call time.
    # envvar= on the option means Click already reads GPTME_INJECTION_HYGIENE if set,
    # so this only fires when the flag was explicitly passed on the command line.
    if injection_hygiene is not None:
        os.environ["GPTME_INJECTION_HYGIENE"] = injection_hygiene

    # Convert tool_allowlist from tuple to string or None
    # Use get_parameter_source to distinguish between default (None) and explicit empty list

    tool_allowlist_str: str | None
    if (
        ctx.get_parameter_source("tool_allowlist") == ParameterSource.DEFAULT
        and not selected_profile
    ):
        # Not provided by user, use None to indicate "use defaults"
        tool_allowlist_str = None
    elif tool_allowlist and any(
        t.strip().lower() == "none" for spec in tool_allowlist for t in spec.split(",")
    ):
        # --tools none: disable all tools
        all_specs = [
            t.strip() for spec in tool_allowlist for t in spec.split(",") if t.strip()
        ]
        non_none = [t for t in all_specs if t.lower() != "none"]
        if non_none:
            raise click.UsageError(
                f"Cannot combine 'none' with other tools: {', '.join(non_none)}"
            )
        tool_allowlist_str = ""
    elif tool_allowlist:
        # User provided tools - flatten any comma-separated values and join
        tools_list: list[str] = []
        for tool_spec in tool_allowlist:
            # Each tool_spec might be comma-separated
            tools_list.extend(t.strip() for t in tool_spec.split(",") if t.strip())

        # Check if any tool starts with '+' (additive syntax)
        additive_mode = any(t.startswith("+") for t in tools_list)
        # Check if any tool starts with '-' (exclusion syntax)
        exclusion_mode = any(t.startswith("-") for t in tools_list)

        if additive_mode and exclusion_mode:
            raise click.UsageError(
                "Cannot mix '+tool' (additive) and '-tool' (exclusion) syntax. "
                "Use one or the other."
            )

        if additive_mode:
            # Strip '+' prefix from all tools
            additional_tools = [t.removeprefix("+") for t in tools_list]
            # Filter out empty strings (e.g., from '+' alone)
            additional_tools = [t for t in additional_tools if t]

            if additional_tools:
                # Prefix with '+' to signal additive mode to config layer
                tool_allowlist_str = "+" + ",".join(additional_tools)
            else:
                # Just '+' means use defaults
                tool_allowlist_str = None
        elif exclusion_mode:
            # Guard: bare tool names mixed with '-' exclusion tools is ambiguous
            bare_tools = [t for t in tools_list if not t.startswith("-")]
            if bare_tools:
                raise click.UsageError(
                    f"Cannot mix bare tool names ({', '.join(bare_tools)}) with '-tool' exclusion syntax. "
                    "Prefix all tools with '-' to exclude them."
                )
            # Strip '-' prefix from all tools
            excluded_tools = [t.removeprefix("-") for t in tools_list]
            # Filter out empty strings
            excluded_tools = [t for t in excluded_tools if t]

            if excluded_tools:
                # Prefix with '-' to signal exclusion mode to config layer
                tool_allowlist_str = "-" + ",".join(excluded_tools)
            else:
                tool_allowlist_str = None
        else:
            # Normal mode - replace defaults with specified tools
            tool_allowlist_str = ",".join(tools_list) if tools_list else None
    else:
        # User explicitly provided empty list (e.g., no -t flags with multiple=True)
        tool_allowlist_str = None

    _validate_custom_tool_paths(tool_allowlist_str)

    if profile:
        import cProfile
        import pstats

        print("Profiling enabled...")
        pr = cProfile.Profile()
        pr.enable()

        profile_dir = Path("profiles")
        profile_dir.mkdir(exist_ok=True)
        profile_path = (
            profile_dir
            / f"gptme-{datetime.now(tz=timezone.utc).strftime('%Y%m%d-%H%M%S')}.prof"
        )

        def save_profile():
            pr.disable()
            pr.dump_stats(profile_path)
            print(f"\nProfile saved to {profile_path}")
            print(f"View with: snakeviz {profile_path}")

            # Print top 20 functions
            stats = pstats.Stats(pr)
            stats.sort_stats("cumulative")
            print("\nTop 20 functions by cumulative time:")
            stats.print_stats(20)

        atexit.register(save_profile)

    interactive = not non_interactive
    auto_switched_noninteractive = False
    if version or version_json:
        from ..info import format_version_info

        print(format_version_info(verbose=verbose, output_json=version_json))

        # hint about utilities (non-JSON only)
        if not version_json:
            print()
            print("Utilities: gptme-util (run 'gptme-util --help' for more)")
        exit(0)

    if "PYTEST_CURRENT_TEST" in os.environ:
        interactive = False

    # Everything below is an actual chat session: import the heavy parts of
    # gptme now, after the cheap early-exit paths (--help/--version/dispatch).
    from ..chat import chat
    from ..config import ensure_workspace_dir, get_config, setup_config_from_cli
    from ..init import init_logging
    from ..llm import get_provider_from_model
    from ..llm import reply as llm_reply
    from ..message import Message
    from ..profiles import get_profile
    from ..prompts import (
        PromptSectionStat,
        format_prompt_stats,
        get_prompt,
        get_prompt_stats,
    )
    from ..telemetry import init_telemetry, shutdown_telemetry
    from ..tools import init_tools
    from ..util.context import md_codeblock
    from ..util.interrupt import handle_keyboard_interrupt, set_interruptible
    from ..util.prompt import add_history
    from ..util.tokens import len_tokens

    # init logging
    # Route log output through stdout (via shared Rich Console) when interactive
    # so that logging and streaming assistant output are serialized through the
    # same Console, preventing stderr/stdout interleave mid-stream.
    # In non-interactive/pipe modes, keep traditional stderr routing.
    init_logging(verbose, stderr=not interactive)

    if not interactive:
        no_confirm = True

    if no_confirm:
        logger.info("Skipping all confirmation prompts.")

    # if stdin is not a tty, we might be getting piped input, which we should include in the prompt
    was_piped = False
    piped_input = None
    if not sys.stdin.isatty():
        # fetch prompt from stdin
        piped_input = _read_stdin()
        if piped_input:
            was_piped = True

            # Attempt to switch to interactive mode
            # https://github.com/prompt-toolkit/python-prompt-toolkit/issues/502#issuecomment-466591259
            sys.stdin = sys.stdout
        else:
            # If stdin is not a tty and we have prompts provided as arguments,
            # automatically switch to non-interactive mode to avoid termios errors
            if prompts:
                logger.info(
                    "stdin is not a TTY and prompts provided, switching to non-interactive mode"
                )
                interactive = False
                no_confirm = True
                auto_switched_noninteractive = True

    # add prompts to prompt-toolkit history
    for prompt in prompts:
        if prompt and len(prompt) > 1000:
            # skip adding long prompts to history (slows down startup, unlikely to be useful)
            continue
        add_history(prompt)

    # join prompts, grouped by `-` if present, since that's the separator for "chained"/multiple-round prompts
    sep = "\n\n" + MULTIPROMPT_SEPARATOR

    if missing_path := _find_missing_explicit_local_path(prompts):
        raise click.UsageError(
            "Prompt looks like an explicit local path, but it does not exist: "
            f"{missing_path}"
        )

    # Validate and resolve --output-schema early (format: "module:ClassName"),
    # before creating a logdir or running setup. An explicit but malformed or
    # unloadable schema is a usage error, not something to silently ignore — the
    # user explicitly asked for structured output.
    output_schema_type: type | None = None
    if output_schema:
        if ":" not in output_schema:
            raise click.UsageError(
                f"Invalid --output-schema format: '{output_schema}'. "
                "Expected 'module:ClassName' (e.g. 'mymodule:MyModel')."
            )
        module_name, class_name = output_schema.rsplit(":", 1)
        try:
            module = importlib.import_module(module_name)
            output_schema_type = getattr(module, class_name)
        except (ImportError, AttributeError, ValueError) as e:
            raise click.UsageError(
                f"Could not load --output-schema '{output_schema}': {e}. "
                "Verify the module is installed and the class name is correct."
            ) from e

    prompts = [
        stripped for p in "\n\n".join(prompts).split(sep) if (stripped := p.strip())
    ]
    # File paths in multiprompts are expanded at runtime by include_paths() in
    # _run_chat_loop (gptme/chat.py:194), not at parse time. Each prompt from the
    # queue goes through include_paths when popped, ensuring fresh content.
    prompt_msgs = [Message("user", p) for p in prompts]

    def inject_stdin(prompt_msgs, piped_input: str | None) -> list[Message]:
        # if piped input, append it to first prompt, or create a new prompt if none exists
        if not piped_input:
            return prompt_msgs
        stdin_msg = Message("user", md_codeblock("stdin", piped_input))
        if not prompt_msgs:
            prompt_msgs.append(stdin_msg)
        else:
            prompt_msgs[0] = prompt_msgs[0].replace(
                content=f"{prompt_msgs[0].content}\n\n{stdin_msg.content}"
            )
        return prompt_msgs

    if show_prompt_stats:
        stats_root = Path(tempfile.mkdtemp(prefix="gptme-prompt-stats-"))
        try:
            stats_logdir = stats_root / "log"
            if workspace == "@log":
                stats_workspace_path = stats_logdir / "workspace"
                stats_workspace_path.mkdir(parents=True, exist_ok=True)
            else:
                stats_workspace_path = Path(workspace) if workspace else Path.cwd()

            try:
                config = setup_config_from_cli(
                    workspace=stats_workspace_path,
                    logdir=stats_logdir,
                    model=model,
                    tool_allowlist=tool_allowlist_str,
                    tool_format=tool_format,
                    prune_tool_output=prune_tool_output,
                    gear=selected_gear,
                    no_confirm=no_confirm or None,
                    stream=stream,
                    interactive=interactive,
                    agent_path=Path(agent_path) if agent_path else None,
                )
            except ValueError as e:
                raise click.UsageError(str(e)) from e
            assert config.chat and config.chat.tool_format
            if selected_profile is None and config.chat.gear is not None:
                gear_profile_name = resolve_gear(config.chat.gear).profile_name
                selected_profile = (
                    get_profile(gear_profile_name) if gear_profile_name else None
                )

            # Resolve prompt type using project config if --system was not set
            effective_prompt_system = prompt_system
            if effective_prompt_system is None:
                effective_prompt_system = (
                    config.project.system if config.project else None
                ) or "full"

            logger.debug(f"Using tools: {config.chat.tools}")
            try:
                tools = init_tools(config.chat.tools)
            except ValueError as e:
                raise click.UsageError(str(e)) from e

            stats_context_mode: ContextMode | None = (
                "selective" if (context_include or no_workspace) else None
            )
            stats_context_include: list[str] | None = (
                []
                if no_workspace
                else (
                    [item for val in context_include for item in val.split(",")]
                    if context_include
                    else None
                )
            )
            stats = get_prompt_stats(
                tools=tools,
                prompt=effective_prompt_system,
                interactive=config.chat.interactive,
                tool_format=config.chat.tool_format,
                model=config.chat.model,
                workspace=stats_workspace_path,
                agent_path=config.chat.agent,
                context_mode=stats_context_mode,
                context_include=stats_context_include,
                initial_prompt=prompt_msgs[0].content if prompt_msgs else None,
            )
            extra_sections: list[PromptSectionStat] = []
            if selected_profile and selected_profile.system_prompt:
                profile_msg = Message(
                    "system",
                    f"# Agent Profile: {selected_profile.name}\n\n{selected_profile.system_prompt}",
                )
                extra_sections.append(
                    PromptSectionStat(
                        name="agent_profile",
                        messages=1,
                        chars=len(profile_msg.content),
                        tokens=len_tokens(profile_msg, config.chat.model or "gpt-4"),
                    )
                )
            header = (
                "System prompt stats"
                f" (prompt={effective_prompt_system}, tool_format={config.chat.tool_format}, "
                f"tools={len(tools)}, interactive={config.chat.interactive})"
            )
            click.echo(
                format_prompt_stats(stats, header=header, extra_sections=extra_sections)
            )
            return
        finally:
            shutil.rmtree(stats_root, ignore_errors=True)

    logdir_preexisting = True

    if resume:
        if workspace == "@log":
            resume_workspace_filter: Path | None = None
        elif workspace is None:
            resume_workspace_filter = Path.cwd()
        else:
            resume_workspace_filter = Path(workspace)
        try:
            logdir = get_logdir_resume(name, workspace=resume_workspace_filter)
        except ValueError as e:
            raise click.UsageError(str(e)) from e
        prompt_msgs = inject_stdin(prompt_msgs, piped_input)
    # don't run pick in tests/non-interactive mode, or if the user specifies a name
    elif (
        interactive
        and name == "random"
        and not prompt_msgs
        and not was_piped
        and sys.stdin.isatty()
    ):
        logdir = pick_log()
    else:
        logdir_preexisting = name != "random" and (get_logs_dir() / name).exists()
        logdir = get_logdir(name)
        prompt_msgs = inject_stdin(prompt_msgs, piped_input)

    show_resume_hint_on_exit = False

    # Register atexit handler to show conversation ID on exit
    def goodbye_handler():
        if show_resume_hint_on_exit and _should_print_resume_hint(
            logdir, output_format
        ):
            print(f"\nGoodbye! (resume with: {_format_resume_hint(logdir.name)})")

    atexit.register(goodbye_handler)

    for prompt_msg in prompt_msgs:
        missing_path = _extract_missing_explicit_local_path(prompt_msg.content)
        if missing_path:
            _cleanup_aborted_new_logdir(logdir, preexisting=logdir_preexisting)
            raise click.UsageError(
                "Prompt looks like an explicit local path, but it does not exist: "
                f"{missing_path}"
            )

    if workspace == "@log":
        workspace_path = logdir / "workspace"
        assert workspace_path  # mypy not smart enough to see its not None
        ensure_workspace_dir(workspace_path)
    else:
        workspace_path = Path(workspace) if workspace else Path.cwd()

    # Setup complete configuration from CLI arguments and workspace
    try:
        config = setup_config_from_cli(
            workspace=workspace_path,
            logdir=logdir,
            model=model,
            tool_allowlist=tool_allowlist_str,
            tool_format=tool_format,
            prune_tool_output=prune_tool_output,
            gear=selected_gear,
            no_confirm=no_confirm or None,
            stream=stream,
            interactive=interactive,
            agent_path=Path(agent_path) if agent_path else None,
        )
    except ValueError as e:
        raise click.UsageError(str(e)) from e
    assert config.chat and config.chat.tool_format
    if selected_profile is None and config.chat.gear is not None:
        gear_profile_name = resolve_gear(config.chat.gear).profile_name
        selected_profile = get_profile(gear_profile_name) if gear_profile_name else None

    # Resolve effective system prompt type: CLI flag > gptme.toml [prompt] system > "full"
    if prompt_system is None:
        prompt_system = (config.project.system if config.project else None) or "full"

    # early init tools to generate system prompt
    # We pass the tool_allowlist CLI argument. If it's not provided, init_tools
    # will load it from the environment variable TOOL_ALLOWLIST or the chat config.
    logger.debug(f"Using tools: {config.chat.tools}")
    try:
        tools = init_tools(config.chat.tools)
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    # init telemetry with agent name and interactive mode
    agent_config = config.chat.agent_config
    agent_name = agent_config.name if agent_config else None
    init_telemetry(
        service_name="gptme-cli",
        agent_name=agent_name,
        interactive=interactive,
    )

    # Check if we're opening an existing conversation (via --resume, --name, or pick)
    # If so, skip generating initial messages (including expensive context_cmd)
    # as they're already in the loaded log
    log_file = logdir / "conversation.jsonl"
    is_existing_conversation = log_file.exists() and log_file.stat().st_size > 0

    # Validate --output-format json and --non-interactive requirements early,
    # before the expensive get_prompt() call (which can take 10+ seconds).
    # This avoids CI timeouts when the CLI will just exit with usage error.
    if output_format == "json" and not (
        non_interactive or auto_switched_noninteractive
    ):
        _cleanup_aborted_new_logdir(logdir, preexisting=logdir_preexisting)
        logger.error("--output-format json is only allowed with --non-interactive.")
        sys.exit(1)

    if not interactive and not prompt_msgs and not is_existing_conversation:
        _cleanup_aborted_new_logdir(logdir, preexisting=logdir_preexisting)
        logger.error(
            "Non-interactive mode requires a prompt. Provide a prompt as an argument, "
            "use --resume to continue an existing conversation, or pipe input via stdin.\n\n"
            "Examples:\n"
            "  gptme --non-interactive 'hello world'\n"
            "  gptme --non-interactive --resume\n"
            "  echo 'hello' | gptme --non-interactive"
        )
        sys.exit(1)

    # Validate model early to fail fast before the expensive get_prompt() call.
    # Only check models with a provider/ prefix; bare provider names (e.g. "anthropic")
    # and model aliases (e.g. "gpt-4o") are left for init_model() to resolve.
    if config.chat.model and "/" in config.chat.model:
        try:
            get_provider_from_model(config.chat.model)
        except ValueError as e:
            _cleanup_aborted_new_logdir(logdir, preexisting=logdir_preexisting)
            raise click.UsageError(f"--model: {e}") from e

    if is_existing_conversation:
        logger.debug("Existing conversation found, skipping initial prompt generation")
        initial_msgs = []
    else:
        # Infer context mode: --context-include / --no-workspace both imply selective mode
        effective_context_mode: ContextMode | None = (
            "selective" if (context_include or no_workspace) else None
        )
        effective_context_include: list[str] | None = (
            []
            if no_workspace
            else (
                [item for val in context_include for item in val.split(",")]
                if context_include
                else None
            )
        )

        # get initial system prompt
        initial_msgs = get_prompt(
            tools=tools,
            prompt=prompt_system,
            interactive=config.chat.interactive,
            tool_format=config.chat.tool_format,
            model=config.chat.model,
            workspace=workspace_path,
            agent_path=config.chat.agent,
            context_mode=effective_context_mode,
            context_include=effective_context_include,
            initial_prompt=prompt_msgs[0].content if prompt_msgs else None,
        )

    # Append profile system prompt if using a profile
    if selected_profile and selected_profile.system_prompt:
        profile_msg = Message(
            "system",
            f"# Agent Profile: {selected_profile.name}\n\n{selected_profile.system_prompt}",
        )
        initial_msgs.append(profile_msg)

    # register a handler for Ctrl-C
    set_interruptible()  # prepare, user should be able to Ctrl+C until user prompt ready
    signal.signal(signal.SIGINT, handle_keyboard_interrupt)

    # Architect/editor split: if enabled via CLI flag OR via TOML config
    _toml_architect_enabled = bool(
        config.project and config.project.architect and config.project.architect.enabled
    )
    if (
        (architect_enabled or _toml_architect_enabled)
        and prompt_msgs
        and not is_existing_conversation
    ):
        # Determine architect model: CLI flag > config > default model
        _arch_model = architect_model or (
            config.project
            and config.project.architect
            and config.project.architect.architect_model
        )
        # Determine editor model: CLI flag > config > current model
        _editor_model = editor_model or (
            config.project
            and config.project.architect
            and config.project.architect.editor_model
        )
        _auto_accept = auto_accept_architect or (
            config.project
            and config.project.architect
            and config.project.architect.auto_accept
        )

        # Use the architect model for the planning turn, or fall back
        _arch_model = _arch_model or config.chat.model
        assert _arch_model, "Architect mode requires a model to be configured"

        # Validate architect/editor model names up front so a malformed value
        # (e.g. missing provider prefix) surfaces as a clean usage error rather
        # than a raw traceback from llm_reply mid-planning. Mirrors the main
        # --model path, which validates inside setup_config_from_cli above.
        for _flag, _value in (
            ("--architect-model", _arch_model),
            ("--editor-model", _editor_model),
        ):
            if _value:
                try:
                    get_provider_from_model(_value)
                except ValueError as e:
                    raise click.UsageError(f"{_flag}: {e}") from e

        # Construct architect messages from first user prompt
        from ..prompts.architect import (
            make_architect_messages,
            make_editor_injection,
        )

        # Build architect messages: stripped context (no tool docs).
        # Do NOT include initial_msgs — the full tool-laden system prompt contradicts
        # the design intent of a stripped planning context where the model sees
        # only ARCHITECT_SYSTEM_PROMPT + the user's request.
        first_prompt = prompt_msgs[0]
        architect_msgs = make_architect_messages(first_prompt.content)

        logger.info(
            "Architect mode: planning with %s, will edit with %s",
            _arch_model,
            _editor_model or _arch_model,
        )

        # Run architect turn
        architect_response = llm_reply(
            architect_msgs,
            model=_arch_model,
            stream=False,
            tools=None,  # architect has no tools (planning only)
            workspace=workspace_path,
        )

        plan_text = architect_response.content.strip()
        logger.info("Architect plan generated (%d chars)", len(plan_text))

        # Confirmation gate: show plan and ask before handing off to editor
        if not _auto_accept and not no_confirm:
            from ..util import console

            console.print("\n[bold]Architect plan:[/bold]")
            console.print(plan_text)
            console.print()
            answer = input("Proceed with editor turn? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                logger.info("Architect turn cancelled by user.")
                return

        if len(prompt_msgs) > 1:
            logger.warning(
                "Architect mode: %d extra prompt message(s) beyond the first will be dropped. "
                "Only the first user message is used for planning.",
                len(prompt_msgs) - 1,
            )

        # Inject plan as system message + editor prompt, replace original prompt
        editor_injection = make_editor_injection(plan_text)
        config.chat.model = _editor_model or _arch_model
        prompt_msgs = [
            Message(
                first_prompt.role,
                f"The architect's plan is in the system message above. "
                f"Implement it now.\n\nOriginal request: {first_prompt.content}",
            )
        ]
        initial_msgs = list(initial_msgs) + [editor_injection]

    try:
        chat(
            prompt_msgs,
            initial_msgs,
            logdir,
            config.chat.workspace,
            config.chat.model,
            config.chat.stream,
            config.chat.no_confirm
            if config.chat.no_confirm is not None
            else no_confirm,
            config.chat.interactive,
            show_hidden,
            config.chat.tools,
            config.chat.tool_format,
            output_schema_type,
            output_format,
        )
        show_resume_hint_on_exit = True
    except click.ClickException:
        raise  # let Click handle proper exit code (2 for UsageError)
    except (RuntimeError, Exception) as e:
        logger.error("Fatal error occurred")
        if verbose:
            logger.exception(e)
        else:
            logger.error(e)
            # Print last call site in gptme code for context
            tb = traceback.extract_tb(sys.exc_info()[2])

            # Get actual gptme package directory

            gptme_dir = Path(gptme.__file__).parent.resolve()

            # Filter for frames actually in gptme source code
            gptme_frames = [
                frame for frame in tb if Path(frame.filename).is_relative_to(gptme_dir)
            ]

            if gptme_frames:
                last_frame = gptme_frames[-1]
                logger.error(
                    f"  at {last_frame.filename}:{last_frame.lineno} in {last_frame.name}"
                )
        sys.exit(1)
    finally:
        shutdown_telemetry()
        if get_config().get_env_bool("GPTME_EXIT_STATS"):
            try:
                from ..util.cost import print_exit_stats

                print_exit_stats()
            except Exception:
                pass


def pick_log(limit=20) -> Path:  # pragma: no cover
    # let user select between starting a new conversation and loading a previous one
    # using the library
    from ..logmanager import get_user_conversations
    from ..util import epoch_to_age

    try:
        pick = importlib.import_module("pick").pick
    except (ImportError, AttributeError):
        pick = None

    title = "New conversation or load previous? "
    NEW_CONV = "New conversation"
    LOAD_MORE = "Load more"
    gen_convs = get_user_conversations()
    convs: list[ConversationMeta] = []

    # load conversations
    convs.extend(islice(gen_convs, limit))

    try:
        terminal_width = os.get_terminal_size().columns
    except OSError:
        terminal_width = 80  # Default fallback for Windows/non-TTY

    prev_convs: list[str] = []
    for conv in convs:
        name = conv.name
        metadata = f"{epoch_to_age(conv.modified)}  {conv.messages:4d} msgs"
        spacing = terminal_width - len(name) - len(metadata) - 6
        prev_convs.append(" ".join([name, spacing * " ", metadata]))

    options = (
        [
            NEW_CONV,
        ]
        + prev_convs
        + [LOAD_MORE]
    )

    index: int
    if pick is None:
        # Fallback when pick library is unavailable (e.g. Windows)
        from ..util import console

        console.print(f"[bold]{title}[/bold]")
        for i, option in enumerate(options):
            console.print(f"  {i}: {option}")
        index = int(input("Select option number: "))
    else:
        _, index = pick(options, title)
    if index == 0:
        return get_logdir("random")
    if index == len(options) - 1:
        return pick_log(limit + 100)
    return get_logdir(convs[index - 1].id)


def get_logdir(logdir: Path | str | Literal["random"]) -> Path:
    from ..logmanager import conversation_name_error
    from ..util.auto_naming import generate_conversation_id

    logs_dir = get_logs_dir()
    if logdir == "random":
        logdir = logs_dir / generate_conversation_id(name="random", logs_dir=logs_dir)
    elif isinstance(logdir, str):
        error = conversation_name_error(logdir)
        if error:
            raise ValueError(error)
        logdir = logs_dir / logdir

    logdir.mkdir(parents=True, exist_ok=True)
    return logdir


def get_logdir_resume(name: str = "random", workspace: Path | None = None) -> Path:
    from ..logmanager import get_user_conversations

    if name != "random":
        logdir = get_logs_dir() / name
        if (logdir / "conversation.jsonl").exists():
            return logdir
        raise ValueError(f"No conversation named '{name}' to resume")

    conversations = get_user_conversations(detail=False)
    if workspace is not None:
        workspace = workspace.resolve()
        conversations = (
            conv
            for conv in conversations
            if Path(conv.workspace).resolve() == workspace
        )

    if conv := next(conversations, None):
        return Path(conv.path).parent

    if workspace is not None:
        raise ValueError(
            f"No previous conversations to resume for workspace '{workspace}'"
        )
    raise ValueError("No previous conversations to resume")


def _should_print_resume_hint(logdir: Path, output_format: str) -> bool:
    if output_format == "json":
        return False

    log_file = logdir / "conversation.jsonl"
    try:
        return log_file.stat().st_size > 0
    except OSError:
        return False


def _format_resume_hint(name: str) -> str:
    return f"gptme --name {shlex.quote(name)}"


def _cleanup_aborted_new_logdir(logdir: Path, *, preexisting: bool) -> None:
    """Remove logdirs created for a conversation that never actually started."""
    if preexisting:
        return

    log_file = logdir / "conversation.jsonl"
    try:
        if log_file.exists() and log_file.stat().st_size > 0:
            return
    except OSError:
        return

    try:
        shutil.rmtree(logdir)
    except OSError:
        pass


def _read_stdin() -> str:
    # In automation, stdin is often an open pipe with no bytes pending yet.
    # Wait briefly for readability so we don't block forever on read-until-EOF,
    # while still giving moderately slow pipeline producers time to write.
    try:
        readable, _, _ = select.select(
            [sys.stdin.fileno()], [], [], _STDIN_PIPE_GRACE_PERIOD
        )
    except (AttributeError, OSError, ValueError):
        readable = [True]

    if not readable:
        return ""

    # stdin is readable (data available or pipe open but idle).
    # Use os.read + select with sub-timeouts rather than sys.stdin.read() which
    # blocks until EOF on a pipe — "readable" from select on a pipe fd can
    # fire even when the write end is open but idle (e.g. under uv run).
    try:
        fd = sys.stdin.fileno()
    except (OSError, ValueError, AttributeError):
        # No real fd (e.g. StringIO in tests or piped stdin where fileno fails).
        # Use blocking read — safe because non-pipe fds return immediately.
        return sys.stdin.read()

    all_data = ""
    deadline = time.monotonic() + _STDIN_PIPE_GRACE_PERIOD

    try:
        while time.monotonic() < deadline:
            r, _, _ = select.select([fd], [], [], _STDIN_PIPE_INTER_CHUNK_TIMEOUT)
            if not r:
                # No data arrived within the sub-timeout — pipe is open but idle.
                # Don't block on read; return what we have.
                break
            chunk = os.read(fd, 4096)
            if not chunk:
                break  # EOF
            all_data += chunk.decode("utf-8", errors="replace")
    except (OSError, ValueError):
        pass

    return all_data
