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


def _unwrap(words: list[str]) -> list[str]:
    """Unwrap simple command/exec/sudo/env prefixes, not arbitrary option syntax."""
    while words:
        name, args = PurePosixPath(words[0]).name, words[1:]
        if name in {"command", "exec", "sudo"}:
            if args and args[0] == "--":
                args = args[1:]
            if not args or args[0].startswith("-"):
                return words
            words = args
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
        if arg in {"--rcfile", "--init-file", "-O", "+O"}:
            i += 2
            continue
        if arg in {"-o", "+o"}:
            if arg == "-o" and args[i + 1 : i + 2] == ["xtrace"]:
                return True, None, False
            i += 2
            continue
        if arg.startswith("-") and not arg.startswith("--"):
            if "x" in arg[1:]:
                return True, None, False
            command_string |= "c" in arg[1:]
        if not arg.startswith("--") and "s" in arg[1:]:
            stdin_mode = arg.startswith("-")
        i += 1
    script = args[i] if command_string and i < len(args) else None
    return False, script, not command_string and (stdin_mode or i == len(args))


def _command_reason(words: list[str], depth: int) -> str | None:
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
    if name in {"export", "declare", "typeset"}:
        if not args or any(arg.startswith("-") and "p" in arg[1:] for arg in args):
            return _REASON
    if name == "set" or name in _SHELLS:
        xtrace, script, _ = _shell_options(args)
        if xtrace:
            return _REASON
        if script and name in _SHELLS:
            return credential_output_reason(script, depth + 1)
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


def credential_output_reason(command: str, depth: int = 0) -> str | None:
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
            if reason := _command_reason(words, depth):
                return reason
            if words and PurePosixPath(words[0]).name in {"echo", "printf"}:
                if _prints_secret_expansion(node):
                    return _REASON
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
                                    (child.text or b"").decode(), depth + 1
                                ):
                                    return reason
        pending.extend(node.named_children)
    return None
