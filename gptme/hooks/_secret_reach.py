"""Bounded glob and shallow recursive-grep reach; not a shell sandbox.

No command evaluation, cwd tracking, deep walk or directory-symlink traversal.
Complex grep patterns are conservative rather than evaluated with unbounded
backtracking. File contents never enter refusal messages.
"""

import fnmatch
import glob
import os
import re
import shlex
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ._credential_output import _unwrap, _words

_LIMIT = 256
_READERS = {
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "sed",
    "awk",
    "grep",
    "egrep",
    "fgrep",
    "rg",
}


@dataclass
class _Budget:
    remaining: int = field(default_factory=lambda: _LIMIT)

    def entries(self, directory: str) -> Iterator[os.DirEntry[str]]:
        try:
            with os.scandir(directory) as entries:
                while self.remaining > 0:
                    entry = next(entries, None)
                    if entry is None:
                        break
                    self.remaining -= 1
                    yield entry
        except OSError:
            return


@dataclass
class _Grep:
    flags: str = ""
    patterns: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    filters: list[tuple[bool, str]] = field(default_factory=list)
    exclude_dirs: list[str] = field(default_factory=list)
    pattern_file: bool = False
    pattern_files: list[str] = field(default_factory=list)
    crlf: bool = False


def _parse_grep(args: list[str], *, ripgrep: bool = False) -> _Grep:
    result = _Grep()
    operands: list[str] = []
    args_iter = iter(args)
    long_flags = {
        "--recursive": "r",
        "--dereference-recursive": "R",
        "--ignore-case": "i",
        "--fixed-strings": "F",
        "--invert-match": "v",
        "--word-regexp": "w",
        "--line-regexp": "x",
        "--quiet": "q",
        "--silent": "q",
        "--files-with-matches": "l",
        "--files-without-match": "l",
        "--count": "c",
        "--null-data": "z",
    }
    value_flags = {
        "--regexp": "e",
        "--file": "f",
        "--include": "include",
        "--exclude": "exclude",
        "--exclude-dir": "exclude_dir",
        "--max-count": "m",
        "--context": "C",
        "--after-context": "A",
        "--before-context": "B",
    }

    if ripgrep:
        # Consume selection operands without treating them as search patterns.
        # Selection is conservative: rg globs have different semantics to grep.
        value_flags["--glob"] = "g"
        value_flags["--iglob"] = "g"
        value_flags["--encoding"] = "encoding"
        long_flags.update({"--smart-case": "S", "--case-sensitive": "s"})
    else:
        value_flags.update(
            {
                "--binary-files": "binary_files",
                "--directories": "d",
                "--devices": "D",
                "--label": "label",
                "--group-separator": "group_separator",
                "--exclude-from": "exclude_from",
            }
        )

    def add_flag(flag: str) -> None:
        # Ripgrep's last case-mode option wins (GNU grep -s is unrelated).
        if ripgrep and flag in {"i", "s", "S"}:
            result.flags = result.flags.translate(str.maketrans("", "", "isS"))
        # GNU grep's last directory mode wins: -r/--directories=recurse vs skip/read.
        if flag in {"r", "R"}:
            result.flags = result.flags.translate(str.maketrans("", "", "rR"))
        result.flags += flag

    def value_option(flag: str, value: str) -> None:
        if flag == "e":
            result.patterns.append(value)
        elif flag == "f":
            result.pattern_file = True
            result.pattern_files.append(value)
        elif flag in {"include", "exclude"}:
            result.filters.append((flag == "include", value))
        elif flag == "exclude_dir":
            result.exclude_dirs.append(value)
        elif flag == "exclude_from":
            try:
                fd = os.open(value, os.O_RDONLY | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as stream:
                    data = stream.read(65537)
            except OSError:
                return
            if len(data) > 65536:
                # A partial record must not become an exclusion of its own.
                data = data[:65536].rpartition(b"\n")[0]
            # GNU grep splits only at LF and strips trailing ASCII whitespace.
            for record in data.split(b"\n"):
                record = record.rstrip(b" \t\r\v\f")
                if record:
                    result.filters.append((False, os.fsdecode(record)))
        elif flag == "d":
            if value == "recurse":
                add_flag("r")
            elif value in {"skip", "read"}:
                result.flags = result.flags.translate(str.maketrans("", "", "rR"))
        elif flag in {"A", "B", "C"} and value.strip("0"):
            result.flags += "C"

    for arg in args_iter:
        if arg == "--":
            operands.extend(args_iter)
            break
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            # --crlf has no short option; -N is ripgrep --no-line-number.
            if ripgrep and name == "--crlf":
                result.crlf = True
                continue
            if ripgrep and name == "--no-crlf":
                result.crlf = False
                continue
            if name in value_flags:
                value_option(value_flags[name], value if eq else next(args_iter, ""))
            else:
                add_flag(long_flags.get(name, ""))
            continue
        if arg.startswith("-") and len(arg) > 1:
            for index, flag in enumerate(arg[1:], 1):
                # rg -L follows symlinks and -z searches compressed files;
                # neither has GNU grep's filename-only/NUL-record meaning.
                if flag not in "ABC" and not (ripgrep and flag in "LzE"):
                    add_flag(flag)
                if flag in "123456789":
                    result.flags += "C"
                if flag in "efmABCdD" or (ripgrep and flag in "gE"):
                    value_option(flag, arg[index + 1 :] or next(args_iter, ""))
                    break
            continue
        operands.append(arg)
    if not result.patterns and not result.pattern_file and operands:
        result.patterns.append(operands.pop(0))
    result.files.extend(operands)
    return result


def _dollar_expand(raw: str, index: int) -> tuple[str, int]:
    """Expand $VAR/${VAR} like os.path.expandvars: unknown names stay literal."""
    if index + 1 < len(raw) and raw[index + 1] == "{":
        end = raw.find("}", index + 2)
        if end < 0:
            return "$", index + 1
        name = raw[index + 2 : end]
        value = os.environ.get(name)
        return (value if value is not None else raw[index : end + 1], end + 1)
    start = index + 1
    if start < len(raw) and (raw[start].isalpha() or raw[start] == "_"):
        end = start + 1
        while end < len(raw) and (raw[end].isalnum() or raw[end] == "_"):
            end += 1
        name = raw[start:end]
        value = os.environ.get(name)
        return (value if value is not None else raw[index:end], end)
    return "$", index + 1


def _tilde_expand(raw: str, index: int) -> tuple[str, int]:
    end = index + 1
    while end < len(raw) and (raw[end].isalnum() or raw[end] in "_-"):
        end += 1
    return os.path.expanduser(raw[index:end]), end


def _glob_patterns(raw: str) -> list[str]:
    """Preserve quoting and split unquoted expansions on default IFS whitespace."""
    quote = ""
    escaped = False
    active = False
    pattern: list[str] = []
    fields: list[str] = []

    def finish_field() -> None:
        nonlocal active
        if active:
            fields.append("".join(pattern))
        pattern.clear()
        active = False

    index = 0
    while index < len(raw):
        char = raw[index]
        if escaped:
            pattern.append(glob.escape(char))
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote:
            if char == quote:
                quote = ""
                index += 1
                continue
            if quote == '"' and char == "$":
                replacement, index = _dollar_expand(raw, index)
                pattern.append(glob.escape(replacement))
                continue
            pattern.append(glob.escape(char))
            index += 1
            continue
        if char in "\"'":
            quote = char
            index += 1
            continue
        if char == "$":
            replacement, index = _dollar_expand(raw, index)
            # Only expansion results undergo field splitting, not literal or
            # quoted whitespace. Prefix/suffix text joins the first/last field.
            for part in re.split(r"([ \t\n]+)", replacement):
                if part and part[0] in " \t\n":
                    finish_field()
                else:
                    active |= any(marker in part for marker in "*?[")
                    pattern.append(part)
            continue
        if char == "~" and not pattern:
            replacement, index = _tilde_expand(raw, index)
            pattern.append(replacement)
            continue
        active |= char in "*?["
        pattern.append(char)
        index += 1
    finish_field()
    return fields


def _expand(pattern: str, budget: _Budget) -> Iterator[str]:
    """Expand pathname components with a shared directory-entry budget."""
    paths = [os.sep if os.path.isabs(pattern) else "."]
    for component in Path(pattern).parts:
        if component == os.sep:
            continue
        next_paths = []
        for parent in paths:
            if glob.has_magic(component):
                for entry in budget.entries(parent):
                    if entry.name.startswith(".") and not component.startswith("."):
                        continue
                    if fnmatch.fnmatchcase(entry.name, component):
                        next_paths.append(entry.path)
            else:
                candidate = os.path.join(parent, component)
                if os.path.lexists(candidate):
                    next_paths.append(candidate)
        paths = next_paths
    yield from paths


def _shallow_files(directory: str, budget: _Budget) -> Iterator[str]:
    # Prioritize the root before spending the shared budget on child entries.
    children = list(budget.entries(directory))
    for entry in children:
        if entry.is_file(follow_symlinks=False):
            yield entry.path
    for entry in children:
        if entry.is_dir(follow_symlinks=False):
            for child in budget.entries(entry.path):
                if child.is_file(follow_symlinks=False):
                    yield child.path


def _matches_values(grep: _Grep, path: str) -> bool:
    if set(grep.flags) & set("qlLc"):
        return False
    if grep.pattern_file or "v" in grep.flags or not grep.patterns:
        return True
    try:
        # O_NONBLOCK avoids blocking if a file is replaced with a FIFO.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, errors="replace", newline="") as stream:
            text = stream.read(65536)
    except OSError:
        return False
    lines = text.split("\0") if "z" in grep.flags else text.split("\n")
    if grep.crlf and "z" not in grep.flags:
        lines = [line.removesuffix("\r") for line in lines]
    if Path(path).name.startswith(".env") and not set(grep.flags) & {"C", "z"}:
        lines = [
            line for line in lines if "=" in line and not line.lstrip().startswith("#")
        ]
    # Smart case is selected for the entire pattern set, not each alternative.
    ignore_case = "i" in grep.flags or (
        "S" in grep.flags
        and not any(char.isupper() for pattern in grep.patterns for char in pattern)
    )
    for pattern in (part for operand in grep.patterns for part in operand.split("\n")):
        if "F" in grep.flags:
            source = re.escape(pattern)
        else:
            # Only non-repeating regex syntax is evaluated. This intentionally
            # over-approximates complex BRE/ERE/PCRE rather than risk ReDoS.
            # BRE treats internal ^/$ as literals, unlike Python re. Keep
            # edge anchors precise; conservatively handle ambiguous positions.
            if (
                any(char in pattern for char in "*+?{(|\\")
                or any(marker in pattern for marker in ("[:", "[.", "[="))
                or "^" in pattern[1:]
                or "$" in pattern[:-1]
            ):
                return True
            source = pattern
        if "w" in grep.flags:
            source = rf"(?<!\w)(?:{source})(?!\w)"
        if "x" in grep.flags:
            source = rf"^(?:{source})$"
        try:
            regex = re.compile(source, re.IGNORECASE if ignore_case else 0)
        except re.error:
            return True
        if any(regex.search(line) for line in lines):
            return True
    return False


def _selected(grep: _Grep, path: str, root: str) -> bool:
    basename = Path(path).name
    # GNU grep uses the last matching rule; an initial include changes the
    # default to exclusion, while an initial exclude leaves it inclusion.
    selected = not grep.filters or not grep.filters[0][0]
    for include, pattern in grep.filters:
        if fnmatch.fnmatchcase(basename, pattern):
            selected = include
    if not selected:
        return False
    dirs = Path(os.path.relpath(path, root)).parts[:-1]
    return not any(fnmatch.fnmatchcase(d, p) for d in dirs for p in grep.exclude_dirs)


def find_secret_reach(
    command: str, is_secret: Callable[[str], bool], normalize: Callable[[str], str]
) -> str | None:
    """Find a literal reader's glob or shallow recursive-grep secret operand."""
    from ..tools.shell import _parse_bash

    budget = _Budget()
    pending = [_parse_bash(command.encode())]
    while pending:
        node = pending.pop()
        pending.extend(node.named_children)
        if node.type != "command":
            continue
        words = _unwrap(_words(node))
        if not words or Path(words[0]).name not in _READERS:
            continue
        name = Path(words[0]).name
        grep = (
            _parse_grep(words[1:], ripgrep=name == "rg")
            if name in {"grep", "egrep", "fgrep", "rg"}
            else None
        )
        if grep and name == "fgrep":
            grep.flags += "F"
        files = (
            grep.files
            if grep
            else [word for word in words[1:] if not word.startswith("-")]
        )
        recursive = bool(grep and name != "rg" and set(grep.flags) & {"r", "R"})
        if recursive and not files:
            files = ["."]
        if grep:
            # Pattern files are read too, but do not replace an implicit search root.
            files = [*files, *grep.pattern_files]
        patterns: dict[str, list[str]] = {}
        for child in node.children_by_field_name("argument"):
            raw = (child.text or b"").decode()
            expanded_patterns = _glob_patterns(raw)
            if expanded_patterns:
                try:
                    patterns.setdefault(shlex.split(raw)[0], []).extend(
                        expanded_patterns
                    )
                except (ValueError, IndexError):
                    pass
        for operand in files:
            expanded = (
                (
                    path
                    for pattern in patterns[operand]
                    for path in _expand(pattern, budget)
                )
                if operand in patterns
                else [normalize(operand)]
            )
            for path in expanded:
                if (
                    operand in patterns
                    and is_secret(os.path.abspath(path))
                    and os.path.isfile(path)
                ):
                    if grep is None or (
                        _selected(grep, path, os.path.dirname(path))
                        and _matches_values(grep, path)
                    ):
                        return path
                if recursive and grep:
                    if any(
                        fnmatch.fnmatchcase(Path(path).name, p)
                        for p in grep.exclude_dirs
                    ):
                        continue
                    for reached in _shallow_files(path, budget):
                        if (
                            is_secret(os.path.abspath(reached))
                            and grep
                            and _selected(grep, reached, path)
                            and _matches_values(grep, reached)
                        ):
                            return reached
    return None
