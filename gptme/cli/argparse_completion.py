"""Static shell completion scripts for gptme's argparse entry points.

Click CLIs complete by re-running the program with a protocol environment
variable. Argparse CLIs have no such protocol, so running them on Tab would run
the command itself (capture tmux, convert files, read eval results). Instead,
the parser is introspected once at install time and its options, subcommands
and choices are written into a self-contained script that never invokes the
program.
"""

import argparse
import importlib
import re
import shlex
from dataclasses import dataclass, field
from pathlib import PurePath

# Console scripts built on argparse, mapped to a side-effect-free parser factory.
ARGPARSE_PARSERS = {
    "gptme-convert": "gptme.tools.convert:build_parser",
    "gptme-eval-trends": "gptme.eval.trends:build_parser",
    "gptme-wut": "gptme.cli.wut:build_parser",
}


@dataclass
class _Option:
    flags: list[str]
    help: str
    # None: flag, no value. []: free value. [...]: one of these choices.
    choices: list[str] | None
    files: bool


@dataclass
class _Scope:
    options: list[_Option] = field(default_factory=list)
    subcommands: dict[str, tuple[str, "_Scope"]] = field(default_factory=dict)
    positional_files: bool = False


def _help(action: argparse.Action) -> str:
    text = action.help or ""
    if text == argparse.SUPPRESS:
        return ""
    try:
        text = text % {**vars(action), "prog": ""}
    except (KeyError, TypeError, ValueError):
        pass
    return " ".join(text.split())


def _completes_files(action: argparse.Action) -> bool:
    """Suggest paths for path-typed values, or untyped ones named like paths."""
    if isinstance(action.type, type) and issubclass(action.type, PurePath):
        return True
    return action.type is None and bool(
        re.search(r"file|path|dir|input|output", action.dest)
    )


def _scope(parser: argparse.ArgumentParser) -> _Scope:
    scope = _Scope()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            helps = {a.dest: _help(a) for a in action._choices_actions}
            for name, sub in action.choices.items():
                scope.subcommands[name] = (helps.get(name, ""), _scope(sub))
        elif action.option_strings:
            if action.help == argparse.SUPPRESS:
                continue
            takes_value = action.nargs != 0
            choices = [str(c) for c in action.choices] if action.choices else []
            scope.options.append(
                _Option(
                    flags=list(action.option_strings),
                    help=_help(action),
                    choices=choices if takes_value else None,
                    files=takes_value and not choices and _completes_files(action),
                )
            )
        else:
            scope.positional_files = True
    return scope


def _identifier(prog: str) -> str:
    return re.sub(r"\W", "_", prog)


def _words(values: list[str]) -> str:
    return " ".join(shlex.quote(v) for v in values)


def _value_cases(scope: _Scope, files_action: str, choices_action: str) -> list[str]:
    """Case arms keyed on 'subcommand:previous-word' for options taking values."""
    arms = []
    scopes = [("", scope)] + [(n, s) for n, (_, s) in scope.subcommands.items()]
    for name, sc in scopes:
        for opt in sc.options:
            if opt.choices is None:
                continue
            pattern = "|".join(shlex.quote(f"{name}:{f}") for f in opt.flags)
            if opt.choices:
                arms.append(
                    f"{pattern}) {choices_action % _words(opt.choices)}; return;;"
                )
            elif opt.files:
                arms.append(f"{pattern}) {files_action}; return;;")
            else:
                arms.append(f"{pattern}) return;;")
    return arms


def _scope_cases(scope: _Scope, render) -> list[str]:
    scopes = [("", scope)] + [(n, s) for n, (_, s) in scope.subcommands.items()]
    return [f"{shlex.quote(name)}) {render(name, sc)};;" for name, sc in scopes]


def _bash(prog: str, scope: _Scope) -> str:
    func = f"_{_identifier(prog)}_completion"
    reply = 'COMPREPLY=($(compgen -W "%s" -- "$cur"))'
    files = 'COMPREPLY=($(compgen -f -- "$cur"))'

    def options(_name, sc):
        return reply % " ".join(f for o in sc.options for f in o.flags)

    def positionals(name, sc):
        if not name and sc.subcommands:
            return reply % " ".join(sc.subcommands)
        return files if sc.positional_files else ":"

    sub_pattern = "|".join(shlex.quote(n) for n in scope.subcommands)
    find_sub = (
        f"""    for ((i=1; i<COMP_CWORD; i++)); do
        case "${{COMP_WORDS[i]}}" in {sub_pattern}) sub="${{COMP_WORDS[i]}}"; break;; esac
    done
"""
        if scope.subcommands
        else ""
    )
    nl = "\n        "
    return f"""# bash completion for {prog}, generated from its argparse parser.
# Static: completing never runs {prog}.
{func}() {{
    local cur="${{COMP_WORDS[COMP_CWORD]}}" prev="${{COMP_WORDS[COMP_CWORD-1]}}" sub="" i
{find_sub}    case "$sub:$prev" in
        {nl.join(_value_cases(scope, files, reply)) or ":) ;;"}
    esac
    if [[ "$cur" == -* ]]; then
        case "$sub" in
        {nl.join(_scope_cases(scope, options))}
        esac
        return
    fi
    case "$sub" in
        {nl.join(_scope_cases(scope, positionals))}
    esac
}}
complete -o filenames -F {func} {prog}
"""


def _zsh(prog: str, scope: _Scope) -> str:
    func = f"_{_identifier(prog)}_completion"

    def options(_name, sc):
        return "compadd -- " + _words([f for o in sc.options for f in o.flags])

    def positionals(name, sc):
        if not name and sc.subcommands:
            return "compadd -- " + _words(list(sc.subcommands))
        return "_files" if sc.positional_files else ":"

    sub_pattern = "|".join(shlex.quote(n) for n in scope.subcommands)
    find_sub = (
        f"""    for ((i=2; i<CURRENT; i++)); do
        case "${{words[i]}}" in {sub_pattern}) sub="${{words[i]}}"; break;; esac
    done
"""
        if scope.subcommands
        else ""
    )
    nl = "\n        "
    return f"""#compdef {prog}
# zsh completion for {prog}, generated from its argparse parser.
# Static: completing never runs {prog}.
{func}() {{
    local cur="${{words[CURRENT]}}" prev="${{words[CURRENT-1]}}" sub="" i
{find_sub}    case "$sub:$prev" in
        {nl.join(_value_cases(scope, "_files", "compadd -- %s")) or ":) ;;"}
    esac
    if [[ "$cur" == -* ]]; then
        case "$sub" in
        {nl.join(_scope_cases(scope, options))}
        esac
        return
    fi
    case "$sub" in
        {nl.join(_scope_cases(scope, positionals))}
    esac
}}

if [[ $zsh_eval_context[-1] == loadautofunc ]]; then
    {func} "$@"
else
    compdef {func} {prog}
fi
"""


def _fish_quote(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _fish(prog: str, scope: _Scope) -> str:
    lines = [
        f"# fish completion for {prog}, generated from its argparse parser.",
        f"# Static: completing never runs {prog}.",
        f"complete -c {prog} -e",
    ]
    if not scope.positional_files:
        lines.append(f"complete -c {prog} -f")
    subs = list(scope.subcommands)
    for name, (help_, _) in scope.subcommands.items():
        lines.append(
            f"complete -c {prog} -n __fish_use_subcommand -a {_fish_quote(name)}"
            + (f" -d {_fish_quote(help_)}" if help_ else "")
        )
    scopes = [("", scope)] + [(n, s) for n, (_, s) in scope.subcommands.items()]
    for name, sc in scopes:
        if name:
            cond = f" -n {_fish_quote('__fish_seen_subcommand_from ' + name)}"
        else:
            cond = " -n __fish_use_subcommand" if subs else ""
        for opt in sc.options:
            parts = [f"complete -c {prog}{cond}"]
            for flag in opt.flags:
                if flag.startswith("--"):
                    parts.append(f"-l {_fish_quote(flag[2:])}")
                elif len(flag) == 2:
                    parts.append(f"-s {_fish_quote(flag[1])}")
                else:
                    parts.append(f"-o {_fish_quote(flag[1:])}")
            if opt.choices:
                parts.append(f"-x -a {_fish_quote(' '.join(opt.choices))}")
            elif opt.choices is not None:
                parts.append("-r -F" if opt.files else "-x")
            if opt.help:
                parts.append(f"-d {_fish_quote(opt.help)}")
            lines.append(" ".join(parts))
    return "\n".join(lines) + "\n"


_GENERATORS = {"bash": _bash, "zsh": _zsh, "fish": _fish}


def generate_argparse_completion(
    shell: str, prog: str, parser: argparse.ArgumentParser
) -> str | None:
    """Return a static completion script for ``parser``, or None if unsupported."""
    generator = _GENERATORS.get(shell)
    return generator(prog, _scope(parser)) if generator else None


def load_parser(prog: str) -> argparse.ArgumentParser | None:
    """Build a registered CLI's parser, or None if its optional deps are missing."""
    module_name, _, attr = ARGPARSE_PARSERS[prog].partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    return getattr(module, attr)()
