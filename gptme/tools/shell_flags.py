"""Per-binary permitted-flag tables for shell command auto-approval.

Security model: **permitted flags, not forbidden flags.**

The previous model denied a hand-picked set of four ``find``-specific flags
(``-exec``, ``-execdir``, ``-delete``, ``-ok``). That denylist was complete
only until someone found flag number five — and several exist, on binaries
other than ``find``:

- ``rg --pre <program>``            → ripgrep execs ``<program>`` per file
- ``rg --hostname-bin <program>``   → ripgrep execs ``<program>``
- ``rg --search-zip``               → ripgrep spawns decompressors
- ``sort --compress-program=<prog>``→ GNU sort execs the compressor
- ``sort -o <file>``                → arbitrary file overwrite
- ``find -fprintf/-fprint/-fls``    → arbitrary file write
- ``tree -o <file>``                → arbitrary file write

Rather than keep extending the denylist, each allowlisted binary declares the
flags it is *permitted* to be auto-approved with. Anything not in the set —
including flags added by a future release of the binary — makes
``is_allowlisted()`` return ``False``, which is **not a block**: the command
simply falls through to the normal confirmation prompt.

The permitted sets are deliberately generous: they were derived from the
``--help`` output of GNU coreutils 9.4, GNU findutils 4.9, GNU grep 3.11,
ripgrep 14.1 and tree 2.1, minus the exec/write-capable flags listed above.
Narrow sets would mean constant prompting, which is how safety features get
turned off.
"""

import shlex
from dataclasses import dataclass, field

__all__ = ["FlagSpec", "PERMITTED_FLAGS", "flags_permitted"]


@dataclass(frozen=True)
class FlagSpec:
    """Permitted command-line flags for a single binary.

    Names are stored *without* leading dashes.

    Attributes:
        short: Bundleable single-letter flags taking no value (``ls -la``).
        short_value: Single-letter flags requiring a value, either attached
            (``cut -d,``) or as the next token (``cut -d ,``).
        long: ``--long`` flags taking no value. Flags with an *optional*
            value (``ls --color[=WHEN]``) belong here: GNU getopt only
            accepts optional values in the ``--flag=value`` form, never as a
            separate token, so no token must be consumed.
        long_value: ``--long`` flags requiring a value.
        word: Single-dash multi-letter flags taking no value. Used by
            ``find``, whose expression primaries (``-type``, ``-print``) are
            not bundleable short flags.
        word_value: Single-dash multi-letter flags requiring a value.
        bundling: Whether single-letter flags may be bundled (``-la``).
            ``find`` sets this to False.
        numeric: Whether a bare numeric token like ``-5`` is a valid flag
            (``head -5``, ``tail -100``, ``grep -3``).
        max_operands: Maximum number of non-flag operands. Set for binaries
            where a trailing operand is an *output* file — ``uniq INPUT
            OUTPUT`` silently truncates OUTPUT.
        any_flag: Accept any flag-shaped token. Only for binaries that cannot
            spawn a process or write a file no matter what they are passed,
            and whose arguments are ordinary text (``echo``).
    """

    short: str = ""
    short_value: str = ""
    long: frozenset[str] = field(default_factory=frozenset)
    long_value: frozenset[str] = field(default_factory=frozenset)
    word: frozenset[str] = field(default_factory=frozenset)
    word_value: frozenset[str] = field(default_factory=frozenset)
    bundling: bool = True
    numeric: bool = False
    max_operands: int | None = None
    any_flag: bool = False


def _fs(names: str) -> frozenset[str]:
    """Build a frozenset of flag names from a whitespace-separated string."""
    return frozenset(names.split())


# ── find ─────────────────────────────────────────────────────────────
# find's expression primaries are single-dash words, not bundleable letters,
# so `bundling=False` and everything lives in word/word_value.
#
# Deliberately EXCLUDED (these are the vulnerability):
#   -exec -execdir -ok -okdir   → execute arbitrary commands
#   -delete                     → deletes files
#   -fprintf -fprint -fprint0 -fls → arbitrary file write (bypasses the >
#                                    redirection check entirely)
_FIND_WORD = _fs(
    """
    H L P O0 O1 O2 O3
    daystart follow nowarn warn
    depth d mount noleaf xdev ignore_readdir_race noignore_readdir_race
    empty false true nouser nogroup readable writable executable
    print print0 ls prune quit
    a and o or not help version
    """
)
# -newerXY compares timestamp X of a file against reference Y (find(1)).
_FIND_NEWER_XY = frozenset(
    f"newer{x}{y}" for x in "aBcm" for y in "aBcmt" if not (x == y == "t")
)
_FIND_WORD_VALUE = (
    _fs(
        """
    D regextype files0-from maxdepth mindepth
    amin anewer atime cmin cnewer context ctime fstype gid group
    ilname iname inum ipath iwholename iregex links lname mmin mtime name newer
    path perm regex samefile size type uid used user wholename xtype
    printf
    """
    )
    | _FIND_NEWER_XY
)

# ── ripgrep ──────────────────────────────────────────────────────────
# Deliberately EXCLUDED: --pre, --pre-glob (exec a preprocessor per file),
# --hostname-bin (exec), -z/--search-zip (spawns decompressors),
# --generate (writes completion/man output).
_RG_LONG = _fs(
    """
    auto-hybrid-regex binary block-buffered byte-offset case-sensitive column
    count count-matches crlf debug files files-with-matches files-without-match
    fixed-strings follow glob-case-insensitive heading help hidden ignore-case
    ignore-file-case-insensitive include-zero invert-match json line-buffered
    line-number line-regexp max-columns-preview mmap multiline multiline-dotall
    no-binary no-block-buffered no-column no-config no-crlf no-filename
    no-follow no-heading no-hidden no-ignore no-ignore-dot no-ignore-exclude
    no-ignore-files no-ignore-global no-ignore-messages no-ignore-parent
    no-ignore-vcs no-line-buffered no-line-number no-messages no-mmap
    no-multiline no-pcre2 no-pcre2-unicode no-require-git no-search-zip
    no-unicode null null-data one-file-system only-matching passthru pcre2
    pcre2-version pretty quiet smart-case sort-files stats stop-on-nonmatch
    text trace trim type-list unrestricted version vimgrep with-filename
    word-regexp
    """
)
_RG_LONG_VALUE = _fs(
    """
    after-context before-context color colors context context-separator
    dfa-size-limit encoding engine field-context-separator field-match-separator
    file glob iglob ignore-file hyperlink-format max-columns max-count max-depth
    max-filesize path-separator regex-size-limit regexp replace sort sortr
    threads type type-add type-clear type-not
    """
)

# ── the silver searcher (ag) ─────────────────────────────────────────
# Deliberately EXCLUDED: --pager (execs the pager program),
# -z/--search-zip (decompression), --print-long-lines is fine but
# anything spawning a process is not.
_AG_LONG = _fs(
    """
    ackmate affinity all-text all-types break color color-line-number
    color-match color-path column count filename filename-only files-with-matches
    files-without-matches follow group heading help hidden ignore-case invert-match
    literal no-numbers no-recurse nobreak nocolor nofilename nofollow nogroup
    noheading nonumbers nopager norecurse null numbers one-device only-matching
    parallel passthrough passthru print-all-files print-long-lines print0
    recurse search-binary search-files silent skip-vcs-ignores smart-case stats
    stats-only unrestricted version vimgrep word-regexp case-sensitive
    """
)
_AG_LONG_VALUE = _fs(
    """
    after before context depth file-search-regex ignore ignore-dir max-count
    path-to-ignore workers width
    """
)

# ── coreutils & friends ──────────────────────────────────────────────

PERMITTED_FLAGS: dict[str, FlagSpec] = {
    "ls": FlagSpec(
        short="1ABCDFGHLNQRSUXZabcdfghiklmnopqrstuvx",
        short_value="ITw",
        long=_fs(
            """
            all almost-all author classify color context dereference
            dereference-command-line dereference-command-line-symlink-to-dir
            directory dired escape file-type full-time group-directories-first
            help hide-control-chars human-readable hyperlink ignore-backups
            inode kibibytes literal no-group numeric-uid-gid quote-name
            recursive reverse show-control-chars si size version zero
            """
        ),
        long_value=_fs(
            """
            block-size format hide ignore indicator-style quoting-style sort
            tabsize time time-style width
            """
        ),
    ),
    "stat": FlagSpec(
        short="Lft",
        short_value="c",
        long=_fs("dereference file-system terse help version"),
        long_value=_fs("cached format printf"),
    ),
    "cd": FlagSpec(short="LP@e"),
    "cat": FlagSpec(
        short="AbeEnstTuv",
        long=_fs(
            """
            help number number-nonblank show-all show-ends show-nonprinting
            show-tabs squeeze-blank version
            """
        ),
    ),
    "pwd": FlagSpec(short="LP", long=_fs("logical physical help version")),
    # echo has no flag that can spawn a process or write a file, and its
    # arguments are literal text — `echo "---"` and `echo "--- section ---"`
    # are extremely common separators that must not trigger a prompt.
    # Redirection (`echo x > f`) is still caught by _has_file_redirection().
    "echo": FlagSpec(short="neE", long=_fs("help version"), any_flag=True),
    "head": FlagSpec(
        short="qvz",
        short_value="cn",
        long=_fs("help quiet silent verbose version zero-terminated"),
        long_value=_fs("bytes lines"),
        numeric=True,  # head -5
    ),
    "find": FlagSpec(
        word=_FIND_WORD,
        word_value=_FIND_WORD_VALUE,
        bundling=False,
        long=_fs("help version"),
    ),
    "rg": FlagSpec(
        short=".0FHILNPSUVabchilnopqsuvwx",
        short_value="ABCEMTdefgjmrt",
        long=_RG_LONG,
        long_value=_RG_LONG_VALUE,
    ),
    "ag": FlagSpec(
        short="acDfHiLlnoQrsStuVvw0",
        short_value="ABCGgmp",
        long=_AG_LONG,
        long_value=_AG_LONG_VALUE,
    ),
    "tail": FlagSpec(
        short="Ffqvz",
        short_value="cns",
        long=_fs("follow help quiet retry silent verbose version zero-terminated"),
        long_value=_fs("bytes lines max-unchanged-stats pid sleep-interval"),
        numeric=True,  # tail -100
    ),
    "grep": FlagSpec(
        short="EFGHILPRTUVZabchilnoqrsvwxyz",
        short_value="ABCDdefm",
        long=_fs(
            """
            basic-regexp binary byte-offset color colour count
            dereference-recursive extended-regexp files-with-matches
            files-without-match fixed-strings help ignore-case initial-tab
            invert-match line-buffered line-number line-regexp no-filename
            no-group-separator no-ignore-case no-messages null null-data
            only-matching perl-regexp quiet recursive silent text version
            with-filename word-regexp
            """
        ),
        long_value=_fs(
            """
            after-context before-context binary-files context devices
            directories exclude exclude-dir exclude-from file group-separator
            include label max-count regexp
            """
        ),
        numeric=True,  # grep -3 (same as --context=3)
    ),
    "wc": FlagSpec(
        short="Lclmw",
        long=_fs("bytes chars help lines max-line-length version words"),
        long_value=_fs("files0-from total"),
    ),
    # EXCLUDED for sort: -o/--output (arbitrary file overwrite),
    # --compress-program (execs the compressor), -T/--temporary-directory,
    # --random-source, --files0-from.
    "sort": FlagSpec(
        short="CMRVbcdfghimnrsuz",
        short_value="Skt",
        long=_fs(
            """
            check debug dictionary-order general-numeric-sort help
            human-numeric-sort ignore-case ignore-leading-blanks
            ignore-nonprinting merge month-sort numeric-sort random-sort
            reverse stable unique version version-sort zero-terminated
            """
        ),
        long_value=_fs("batch-size buffer-size field-separator key parallel sort"),
    ),
    # `uniq INPUT OUTPUT` writes to OUTPUT — cap operands at one.
    "uniq": FlagSpec(
        short="Dcdiuz",
        short_value="fsw",
        long=_fs(
            """
            all-repeated count group help ignore-case repeated unique version
            zero-terminated
            """
        ),
        long_value=_fs("check-chars skip-chars skip-fields"),
        max_operands=1,
    ),
    "cut": FlagSpec(
        short="nsz",
        short_value="bcdf",
        long=_fs("complement help only-delimited version zero-terminated"),
        long_value=_fs("bytes characters delimiter fields output-delimiter"),
    ),
    # EXCLUDED for file: -C/--compile (writes a compiled .mgc magic file),
    # -S/--no-sandbox (disables libmagic's seccomp sandbox).
    "file": FlagSpec(
        short="0bcdhiklnNprsvzZL",
        short_value="eFfmP",
        long=_fs(
            """
            apple brief checking-printout debug dereference extension help
            keep-going list mime mime-encoding mime-type no-buffer
            no-dereference no-pad preserve-date print0 raw special-files
            uncompress uncompress-noreport version
            """
        ),
        long_value=_fs(
            "exclude exclude-quiet files-from magic-file parameter separator"
        ),
    ),
    "which": FlagSpec(
        short="ainsv",
        long=_fs(
            """
            all help read-alias read-functions show-dot show-tilde skip-alias
            skip-dot skip-functions skip-tilde tty-only version
            """
        ),
    ),
    "type": FlagSpec(short="afpPt"),
    # EXCLUDED for tree: -o (writes output to an arbitrary file).
    "tree": FlagSpec(
        short="ACDFJNQRSUXacdfghilnpqrstuvx",
        short_value="HILPT",
        long=_fs(
            """
            device dirsfirst du fflinks filesfirst fromfile fromtabfile
            gitignore help hintro houtro ignore-case info inodes matchdirs
            metafirst nolinks noreport prune si version
            """
        ),
        long_value=_fs("charset filelimit gitfile infofile sort timefmt"),
    ),
    "du": FlagSpec(
        short="0DHLPSabchklmsx",
        short_value="BXdt",
        long=_fs(
            """
            all apparent-size bytes count-links dereference dereference-args
            help human-readable inodes no-dereference null one-file-system
            separate-dirs si summarize time total version
            """
        ),
        long_value=_fs(
            "block-size exclude exclude-from files0-from max-depth threshold time-style"
        ),
    ),
    "df": FlagSpec(
        short="HPTahiklv",
        short_value="Btx",
        long=_fs(
            """
            all help human-readable inodes local no-sync output portability
            print-type si sync total version
            """
        ),
        long_value=_fs("block-size exclude-type type"),
    ),
}

# Characters that make up bash's list/pipeline operators. A token composed
# entirely of these starts a new segment — and therefore a new binary whose
# flag table applies. Matching on the character set rather than an enumerated
# list covers `|`, `||`, `&`, `&&`, `|&`, `;`, `;;`, `;&` and `;;&` without
# relying on us having thought of every operator bash supports. `(` and `)`
# are deliberately excluded: escaped parens are how find groups expressions.
_OPERATOR_CHARS = frozenset("|&;")

# Redirection operators: the token that follows is a filename, not a flag.
_REDIRECTIONS = frozenset({"<", ">", ">>", "<<", "<<-", "<<<", ">|", "<>", "&>", ">&"})

_GLOB_METACHARS = "*?["


def _lex(cmd: str) -> list[str] | None:
    """Tokenise a command, keeping shell operators as separate tokens.

    Returns None when the command cannot be parsed (e.g. unbalanced quotes),
    in which case the caller should fail closed and require confirmation.
    """
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    # bash only starts a comment at the beginning of a word; shlex would
    # otherwise truncate tokens like `dir#1` and hide arguments from us.
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def _split_lines(cmd: str) -> list[str]:
    """Split a command block on newlines that actually separate commands.

    ``shlex`` treats newlines as ordinary whitespace, so a multi-command block
    would otherwise collapse into a single segment and later commands would be
    validated against the *first* binary's flag table. That is exploitable
    wherever two binaries give the same flag different meanings — ``find -o``
    is a harmless OR operator, ``sort -o`` overwrites a file.

    Newlines inside quotes are data, and a backslash-newline is a line
    continuation, so neither splits. Shell comments run to the newline, so
    quotes and continuations inside them cannot affect the following line.
    """
    parts: list[str] = []
    buf: list[str] = []
    in_single = in_double = in_comment = False
    i = 0
    while i < len(cmd):
        char = cmd[i]
        if char == "\n" and in_comment:
            parts.append("".join(buf))
            buf = []
            in_comment = False
            i += 1
            continue
        if in_comment:
            i += 1
            continue
        if char == "\\" and not in_single and i + 1 < len(cmd):
            if cmd[i + 1] == "\n":
                # Line continuation — drop both, the logical line continues.
                i += 2
                continue
            buf.append(char)
            buf.append(cmd[i + 1])
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
            and (not buf or buf[-1].isspace() or buf[-1] in ";&|")
        ):
            # Comments are inert data. Drop their contents so tokens that look
            # like flags cannot force an unnecessary confirmation prompt.
            in_comment = True
            i += 1
            continue

        if char == "\n" and not in_single and not in_double:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
        i += 1
    parts.append("".join(buf))
    return [part for part in parts if part.strip()]


def _is_operator(token: str) -> bool:
    """Whether a token is a bash list/pipeline operator."""
    return bool(token) and all(char in _OPERATOR_CHARS for char in token)


def _split_segments(tokens: list[str]) -> list[list[str]]:
    """Split a flat token list into pipeline/list segments."""
    segments: list[list[str]] = [[]]
    for token in tokens:
        if _is_operator(token):
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _check_short_bundle(body: str, spec: FlagSpec) -> tuple[bool, bool]:
    """Validate a bundle of single-letter flags (the part after the ``-``).

    Returns ``(permitted, consumes_next_token)``.
    """
    i = 0
    while i < len(body):
        char = body[i]
        if char in spec.short:
            i += 1
            continue
        if char in spec.short_value:
            # Value is either attached (``-d,``) or the next token (``-d ,``).
            attached = body[i + 1 :]
            return True, not attached
        return False, False
    return True, False


def _check_flag_token(token: str, spec: FlagSpec) -> tuple[bool, bool]:
    """Validate a single ``-``-prefixed token.

    Returns ``(permitted, consumes_next_token)``.
    """
    if spec.any_flag:
        return True, False

    # A run of dashes (``---``, ``----``) is never a flag — no binary defines
    # one — but it is a very common text separator.
    if set(token) == {"-"}:
        return True, False

    # ``--flag`` / ``--flag=value``
    if token.startswith("--"):
        name, sep, _value = token[2:].partition("=")
        if name in spec.long:
            # Optional-value flags (``--color=auto``) are permitted either way.
            return True, False
        if name in spec.long_value:
            return True, not sep
        return False, False

    body = token[1:]

    # Legacy numeric shorthand: ``head -5``, ``tail -100``, ``grep -3``.
    if spec.numeric and body.isdigit():
        return True, False

    # Single-dash words (find primaries). Checked before letter bundling so
    # that ``-name`` is not decomposed into ``-n -a -m -e``.
    if body in spec.word:
        return True, False
    if body in spec.word_value:
        return True, True

    if spec.bundling:
        return _check_short_bundle(body, spec)

    # No bundling (find): a single-letter option is still valid if declared.
    if len(body) == 1:
        if body in spec.short:
            return True, False
        if body in spec.short_value:
            return True, True
    return False, False


def _check_segment(segment: list[str]) -> bool:
    """Validate every flag in one pipeline segment against its binary."""
    binary = segment[0]
    spec = PERMITTED_FLAGS.get(binary)
    if spec is None:
        # Unknown binary: fail closed if it is given anything flag-shaped.
        # (The caller has already verified the binary is in allowlist_commands.)
        return not any(token.startswith("-") and token != "-" for token in segment[1:])

    operands: list[str] = []
    end_of_options = False
    index = 1
    while index < len(segment):
        token = segment[index]
        index += 1

        if token in _REDIRECTIONS:
            # Skip the redirection target — it is a filename, not an operand.
            index += 1
            continue

        if end_of_options or token == "-" or not token.startswith("-"):
            operands.append(token)
            continue

        if token == "--":
            end_of_options = True
            continue

        permitted, consumes_next = _check_flag_token(token, spec)
        if not permitted:
            return False
        if consumes_next:
            if index >= len(segment):
                return False
            value = segment[index]
            # Values may legitimately start with a dash (grep patterns and
            # negative find durations), but a *forbidden long option* here
            # could otherwise be swallowed by a value-taking flag (for
            # example ``rg -T --pre``). Reject all unknown long options; short
            # values remain supported because arbitrary patterns such as
            # ``grep -e -dashy-pattern`` are common and cannot name long flags.
            if value.startswith("--") and value != "--":
                name = value[2:].partition("=")[0]
                if name not in spec.long and name not in spec.long_value:
                    return False
            index += 1

    if spec.max_operands is not None:
        if len(operands) > spec.max_operands:
            return False
        # Globs expand after validation, so one glob operand can become
        # several — and for ``uniq`` the last one would be overwritten.
        if any(char in operand for operand in operands for char in _GLOB_METACHARS):
            return False

    return True


def flags_permitted(cmd: str) -> bool:
    """Check that every flag in ``cmd`` is permitted for its binary.

    Returns False when any token that looks like a flag is not in its
    binary's permitted set, or when the command cannot be parsed. A False
    result does not forbid the command — it makes it require confirmation.
    """
    for line in _split_lines(cmd):
        tokens = _lex(line)
        if tokens is None:
            return False
        if not all(_check_segment(segment) for segment in _split_segments(tokens)):
            return False
    return True
