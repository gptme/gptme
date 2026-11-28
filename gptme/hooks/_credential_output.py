"""Recognize obvious shell credential dumps without inspecting credential values.

This is a bounded policy, not a shell sandbox: dynamic command names, eval,
arbitrary programs and expansion obfuscation can bypass it. Parse command
positions so quoted documentation and ordinary heredoc data are not commands.
"""

import re
import shlex
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

_SHELLS = {"bash", "sh", "dash", "zsh", "ksh"}
# Literal variable names. printenv of named operands prints only those values,
# so it follows the same secret-name rule as echo "$NAME"; compgen -e (names
# only) is not a value dump.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_SECRET_NAME = re.compile(
    r"(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)(?:_|$)",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
_REASON = "Credential output blocked: shell tracing or variable-value dump."


def _words(node: "Node") -> list[str]:
    """Read literal argv after leading assignment nodes, preserving quoted args."""
    text = node.text or b""
    name = node.child_by_field_name("name")
    if name is not None:
        text = text[name.start_byte - node.start_byte :]
    try:
        return shlex.split(text.decode())
    except ValueError:
        return []


# Wrappers that run their operand as a command. Each maps option words that
# take a separate value; any other option word is a flag. Options that make the
# wrapper not run its operand (``command -v``, ``sudo -l``) stop the unwrap.
_WRAPPER_VALUE_OPTIONS: dict[str, set[str]] = {
    "command": set(),
    "builtin": set(),
    "exec": {"-a"},
    "sudo": {"-C", "-D", "-g", "-h", "-p", "-R", "-r", "-T", "-t", "-U", "-u"},
    "time": set(),
    "nohup": set(),
    "nice": {"-n"},
    "timeout": {"-s", "-k"},
    "xargs": {"-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s"},
}
_WRAPPER_NO_RUN_FLAGS: dict[str, set[str]] = {
    "command": {"v", "V"},
    "sudo": {"e", "l", "v", "k", "K", "V"},
}
# Long options taking a separate value word; `--opt=value` is always one word
# and any other long option is a flag.
_WRAPPER_LONG_VALUE = {
    "--user",
    "--group",
    "--adjustment",
    "--signal",
    "--kill-after",
    "--arg-file",
    "--delimiter",
    "--max-args",
    "--max-procs",
    "--max-chars",
    "--max-lines",
}


def _unwrap_wrapper(name: str, args: list[str]) -> list[str] | None:
    """Return the command a wrapper runs, or None if it cannot be determined."""
    value_options = _WRAPPER_VALUE_OPTIONS[name]
    no_run = _WRAPPER_NO_RUN_FLAGS.get(name, set())
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if not arg.startswith("-") or arg == "-":
            break
        if arg.startswith("--"):
            i += 2 if arg in _WRAPPER_LONG_VALUE else 1
            continue
        if name == "nice" and arg[1:].isdigit():
            i += 1  # legacy `nice -5 cmd`
            continue
        # Walk the cluster: the first value-taking letter ends it, taking the
        # rest of the word (`sudo -uroot`) or, if last, the next word.
        step = 1
        for pos, letter in enumerate(arg[1:], start=1):
            if letter in no_run:
                return None
            if f"-{letter}" in value_options:
                if pos == len(arg) - 1:
                    step = 2
                break
        i += step
    rest = args[i:]
    if name == "timeout":
        rest = rest[1:]  # the DURATION operand
    return rest or None


def _unwrap(words: list[str]) -> list[str]:
    """Unwrap command/builtin/exec/sudo/env and other runner prefixes."""
    while words:
        name, args = PurePosixPath(words[0]).name, words[1:]
        if name in _WRAPPER_VALUE_OPTIONS:
            inner = _unwrap_wrapper(name, args)
            if inner is None:
                return words
            words = inner
        elif name == "env":
            i = 0
            options = True
            while i < len(args):
                arg = args[i]
                if options and arg == "--":
                    options = False
                    i += 1
                elif options and arg in {"-u", "--unset", "-C", "--chdir"}:
                    i += 2
                elif options and (
                    arg in {"-i", "--ignore-environment", "-0", "--null"}
                    or arg.startswith(("--unset=", "--chdir="))
                    or (arg.startswith(("-u", "-C")) and len(arg) > 2)
                ):
                    i += 1
                elif _ASSIGNMENT.match(arg):
                    i += 1
                else:
                    break
            if not args[i:]:
                return ["env"]  # no utility: environment dump
            if args[i].startswith("-"):
                return words  # unsupported env option; do not guess operands
            words = args[i:]
        else:
            return words
    return words


def _is_xtrace_name(option: str) -> bool:
    """True for an option name enabling tracing in bash or zsh.

    zsh matches option names case-insensitively and ignores underscores
    (`X_TRACE`); bash rejects such spellings, so treating them as xtrace
    refuses nothing bash would run.
    """
    return option.replace("_", "").replace("-", "").lower() == "xtrace"


def _shell_options(args: list[str]) -> tuple[bool, str | None, bool]:
    """Return xtrace enabled, -c script, and whether stdin supplies shell code."""
    i = 0
    command_string = False
    stdin_mode = False
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if arg == "-":
            i += 1  # End options; a following operand is still a script path.
            break
        if not arg.startswith(("-", "+")):
            break
        if arg in {"--rcfile", "--init-file"}:
            i += 2
            continue
        if arg.startswith("--"):
            # zsh accepts long option names (`zsh --xtrace`).
            if _is_xtrace_name(arg[2:]):
                return True, None, False
            i += 1
            continue
        letters = arg[1:]
        # `-o NAME` may sit inside a cluster (`set -eo xtrace`); each `o`/`O`
        # takes the next word, in order.
        consumed = 0
        for letter in letters:
            if letter in "oO":
                consumed += 1
                value = args[i + consumed : i + consumed + 1]
                if (
                    letter == "o"
                    and arg.startswith("-")
                    and value
                    and _is_xtrace_name(value[0])
                ):
                    return True, None, False
        if arg.startswith("-"):
            if "x" in letters:
                return True, None, False
            command_string |= "c" in letters
        if "s" in letters:
            stdin_mode = arg.startswith("-")
        i += 1 + consumed
    script = args[i] if command_string and i < len(args) else None
    return False, script, not command_string and (stdin_mode or i == len(args))


def _command_reason(words: list[str], depth: int, shell: str) -> str | None:
    if not words:
        return None
    name, args = PurePosixPath(words[0]).name, words[1:]
    if name == "env":
        return None if args in [["--help"], ["--version"]] else _REASON
    if name == "printenv":
        if args in [["--help"], ["--version"]]:
            return None
        operands = args[:]
        while operands and operands[0] in {"-0", "--null"}:
            operands.pop(0)
        if operands and operands[0] == "--":
            operands.pop(0)
        return (
            None
            if operands
            and all(
                _IDENTIFIER.fullmatch(arg) and not _SECRET_NAME.search(arg)
                for arg in operands
            )
            else _REASON
        )
    if name == "set" and not args:
        return _REASON
    if name == "typeset" and depth == 0:
        # Top-level text (a tmux pane, a user's shell) may run under zsh,
        # where `typeset NAME` prints the value and `-F` lists floating-point
        # parameters. Inside an explicit `bash -c` the shell is known.
        shell = "zsh"
    if name in {"export", "declare", "typeset", "readonly"}:
        # `-p` prints values; bash also prints for `+p` (`declare +p NAME`
        # dumps `declare -x NAME="value"`), so both signs count.
        if any(arg.startswith(("-", "+")) and "p" in arg[1:] for arg in args):
            return _REASON
        if shell == "zsh" and name != "export":
            # Zsh prints existing named parameters without -p, and -m lists
            # pattern matches. Assignments do not print the previous value;
            # named -x only sets the export attribute (unlike -p or -m).
            flags = ""
            for arg in args:
                # A standalone sign ends option parsing. Later -x is an
                # operand, so it cannot enable the named-export exception.
                if arg in {"-", "+", "--"} or not arg.startswith(("-", "+")):
                    break
                if arg.startswith("-"):
                    flags += arg[1:]
                elif "m" in arg[1:]:
                    # `+m` forces the matching parameters to be printed even
                    # inside a function, like pattern mode with `-m`. The
                    # operands are patterns, so a secret name may be hidden
                    # behind a wildcard; mark pattern printing for the check
                    # below.
                    flags += "m"
                elif "p" in arg[1:]:
                    # Zsh's +p prints values too, including after -x.
                    return _REASON
            if any(
                not arg.startswith(("-", "+"))
                and not _ASSIGNMENT.match(arg)
                and ("m" in flags or (flags != "x" and _SECRET_NAME.search(arg)))
                for arg in args
            ):
                return _REASON
        # With no name operand these print variables, whatever the options:
        # `export -n`, `declare -g` and `declare +x` dump like `export -p`.
        # Only function listings are exempt. `-F` is one in bash alone; zsh
        # and ksh use it for floating-point variables, and `sh` may be either.
        # A lone `+` ends options in zsh; elsewhere it is an (invalid) name.
        if all(
            arg.startswith(("-", "+")) and (arg != "+" or shell == "zsh")
            for arg in args
        ):
            listing = {"f", "F"} if shell == "bash" else {"f"}
            if not any(arg.startswith("-") and listing & set(arg) for arg in args):
                return _REASON
    if name in {"setopt", "unsetopt"}:
        # zsh: `setopt xtrace` is `set -x`; `unsetopt noxtrace` is too.
        enable = "xtrace" if name == "setopt" else "noxtrace"
        for arg in args:
            normalized = arg.replace("_", "").replace("-", "").lower()
            if normalized == enable:
                return _REASON
    if name == "shopt":
        # `shopt -o -s xtrace` / `shopt -os xtrace` is `set -x`.
        flags = "".join(arg[1:] for arg in args if arg.startswith("-"))
        if "o" in flags and "s" in flags and "xtrace" in args:
            return _REASON
    if name == "set" or name in _SHELLS:
        xtrace, script, _ = _shell_options(args)
        if xtrace:
            return _REASON
        if script and name in _SHELLS:
            return credential_output_reason(script, depth + 1, shell=name)
    return None


def _prints_secret_expansion(node: "Node") -> bool:
    pending = list(node.children_by_field_name("argument"))
    while pending:
        child = pending.pop()
        # Substitutions execute their own commands; the main traversal checks
        # those separately. Assignments and redirects are not printed argv.
        if child.type == "command_substitution":
            continue
        if child.type == "variable_name" and _SECRET_NAME.search(
            (child.text or b"").decode()
        ):
            parent_text = (child.parent.text or b"") if child.parent else b""
            # Length/presence diagnostics do not print the value. Continue
            # visiting children so ${TOKEN:+$OTHER_TOKEN} still gets checked.
            suffix = parent_text[len(child.text or b"") + 2 :]
            if not parent_text.startswith(b"${#") and not suffix.startswith(
                (b":+", b"+")
            ):
                return True
        pending.extend(child.named_children)
    return False


def _herestring_reason(node: "Node", words: list[str], depth: int) -> str | None:
    """Check shell code fed to a shell through a here-string (`bash <<< 'env'`)."""
    if not words or PurePosixPath(words[0]).name not in _SHELLS:
        return None
    shell = PurePosixPath(words[0]).name
    for child in node.named_children:
        if child.type != "herestring_redirect":
            continue
        for value in child.named_children:
            text = (value.text or b"").decode()
            try:
                code = " ".join(shlex.split(text))
            except ValueError:
                code = text
            if reason := credential_output_reason(code, depth + 1, shell=shell):
                return reason
    return None


def credential_output_reason(
    command: str, depth: int = 0, shell: str = "bash"
) -> str | None:
    """Return a static refusal reason, never matched source or variable values.

    Covers literal shell options, env/printenv, shell state dumps, sensitive
    variable expansions passed to echo/printf, nested command substitutions,
    shell -c and shell-fed heredocs. Does not resolve sourced files or scripts.
    """
    if depth > 8:
        return None
    # Already a shell runtime dependency; load lazily to preserve CLI startup.
    from ..tools.shell import _parse_bash

    root = _parse_bash(command.encode())
    pending = [root]
    while pending:
        node = pending.pop()
        if node.type == "variable_assignment":
            name = node.child_by_field_name("name")
            if name and name.text == b"BASH_XTRACEFD":
                return _REASON
        if node.type in {"command", "declaration_command"}:
            raw_words = _words(node)
            # env assignments are words rather than variable_assignment nodes.
            if raw_words and PurePosixPath(raw_words[0]).name == "env":
                if any(arg.startswith("BASH_XTRACEFD=") for arg in raw_words[1:]):
                    return _REASON
            words = _unwrap(raw_words)
            if reason := _command_reason(words, depth, shell):
                return reason
            if words and PurePosixPath(words[0]).name in {"echo", "printf"}:
                if _prints_secret_expansion(node):
                    return _REASON
            if reason := _herestring_reason(node, words, depth):
                return reason
        if node.type == "redirected_statement":
            body = node.child_by_field_name("body")
            words = _unwrap(_words(body)) if body else []
            if words and PurePosixPath(words[0]).name in _SHELLS:
                _, _, stdin_code = _shell_options(words[1:])
                if stdin_code:
                    for redirect in node.named_children:
                        for child in redirect.named_children:
                            if child.type == "heredoc_body":
                                if reason := credential_output_reason(
                                    (child.text or b"").decode(),
                                    depth + 1,
                                    shell=PurePosixPath(words[0]).name,
                                ):
                                    return reason
        pending.extend(node.named_children)
    return None
