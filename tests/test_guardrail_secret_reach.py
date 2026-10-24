"""Secret reach tests inspect inert commands and synthetic files only."""

from pathlib import Path

import pytest

from gptme.hooks.guardrails import _find_secret_path_in_cmd, guardrail_hook
from gptme.tools.base import ToolUse


@pytest.fixture
def secret_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "# synthetic fixture\nOPENROUTER_API_KEY=synthetic\n"
    )
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / ".env.local").write_text("TOKEN=synthetic\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "client.pem").write_text("synthetic certificate\n")
    return tmp_path


@pytest.mark.parametrize(
    "command",
    [
        "cat config/*",
        "cat config/*.p?m",
        "cat .[e]nv",
        "grep -rn OPENROUTER .",
        "grep -r -e OPENROUTER .",
        "grep --recursive --regexp=TOKEN nested",
        "grep -rF OPENROUTER .",
        "grep -ri openrouter .",
        "grep -r TOKEN nested; echo done",
        "echo before && grep -rn OPENROUTER .",
    ],
)
def test_secret_reach_is_detected(secret_tree: Path, command: str) -> None:
    assert _find_secret_path_in_cmd(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "grep -rn 'def foo' .",
        "grep -rF 'def foo' .",
        "grep -rn OPENROUTER config",
        "grep -rn OPENROUTER --include='*.py' .",
        "grep -r TOKEN --exclude-dir=nested .",
        "grep -rn OPENROUTER --include '*.py' .",
        "cat config/*.txt",
        "echo 'config/*'",
        "printf '%s' 'grep -rn OPENROUTER .'",
        "cat <<'EOF'\ngrep -rn OPENROUTER .\nEOF",
        "rg OPENROUTER .",
    ],
)
def test_ordinary_searches_stay_allowed(secret_tree: Path, command: str) -> None:
    assert _find_secret_path_in_cmd(command) is None


@pytest.mark.parametrize(
    ("mode", "blocked"), [("enforce", True), ("shadow", False), ("off", False)]
)
def test_hook_modes(
    secret_tree: Path, monkeypatch: pytest.MonkeyPatch, mode: str, blocked: bool
) -> None:
    monkeypatch.setenv("GPTME_GUARDRAILS", mode)
    result = guardrail_hook(
        ToolUse(tool="shell", args=[], content="grep -rn OPENROUTER .")
    )
    assert (result is not None) is blocked


@pytest.mark.parametrize(
    "command",
    [
        "grep -r TOKEN n*",
        "grep -r -A1 'synthetic fixture' .",
        "grep -r synthetic config",
        "grep -rE '(a+)+$' .",
    ],
)
def test_review_reach_cases(secret_tree: Path, command: str) -> None:
    assert _find_secret_path_in_cmd(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "grep -rw TOK nested",
        "grep -rx TOKEN nested",
        "grep -rq TOKEN nested",
        "grep -rl TOKEN nested",
        "grep -rc TOKEN nested",
        "grep -r TOKEN --include='*.py' n*",
        "cat config/'*'/*",
        "cat config/\\*/*",
    ],
)
def test_review_benign_cases(secret_tree: Path, command: str) -> None:
    (secret_tree / "config" / "keys").mkdir()
    (secret_tree / "config" / "keys" / "client.pem").write_text("synthetic\n")
    assert _find_secret_path_in_cmd(command) is None


def test_shared_directory_budget(
    secret_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from gptme.hooks import _secret_reach

    for index in range(30):
        (secret_tree / "config" / f"file{index}.txt").touch()
    monkeypatch.setattr(_secret_reach, "_LIMIT", 10)
    real_scandir = os.scandir
    visited = []

    class CountingScan:
        def __init__(self, path):
            self.entries = real_scandir(path)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.entries.close()

        def __iter__(self):
            return self

        def __next__(self):
            entry = next(self.entries)
            visited.append(entry.name)
            return entry

    monkeypatch.setattr(_secret_reach.os, "scandir", CountingScan)
    assert _find_secret_path_in_cmd("cat config/nomatch*") is None
    assert len(visited) <= 10


def test_backtracking_pattern_is_not_executed(
    secret_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gptme.hooks import _secret_reach

    (secret_tree / ".env").write_text("TOKEN=" + "a" * 64 + "!\n")

    def unexpected_compile(*args, **kwargs):
        raise AssertionError("Complex grep patterns must not reach Python re")

    monkeypatch.setattr(_secret_reach.re, "compile", unexpected_compile)
    assert _find_secret_path_in_cmd("grep -rE '(a+)+$' .") is not None


def test_glob_filters_apply_to_files(secret_tree: Path) -> None:
    # The old token scanner does not consider .[e]nv a literal secret path.
    assert (
        _find_secret_path_in_cmd("grep -r OPENROUTER --include='*.py' .[e]nv") is None
    )


def test_refusal_does_not_include_file_contents(
    secret_tree: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("GPTME_GUARDRAILS", "shadow")
    (secret_tree / ".env").write_text("TOKEN=unique-synthetic-value\n")
    assert (
        guardrail_hook(ToolUse(tool="shell", args=[], content="grep -r TOKEN ."))
        is None
    )
    assert "Secret path access" in caplog.text
    assert "unique-synthetic-value" not in caplog.text


def test_quoted_glob_does_not_expand(secret_tree: Path) -> None:
    assert _find_secret_path_in_cmd("cat 'config/*'") is None


def test_file_symlinks_in_globs(secret_tree: Path) -> None:
    # This policy does not resolve targets of innocuously named symlinks.
    (secret_tree / "config" / "link.txt").symlink_to(secret_tree / ".env")
    assert _find_secret_path_in_cmd("cat config/link.*") is None


@pytest.mark.parametrize(
    "command",
    [
        "grep -rF 'absent\nTOKEN' nested",
        "grep -r 'absent\nTOKEN' nested",
        "grep -r --exclude='[.]env.local' --include='[.]env.local' TOKEN nested",
    ],
)
def test_grep_pattern_and_filter_alternatives(secret_tree: Path, command: str) -> None:
    assert _find_secret_path_in_cmd(command) is not None


def test_excluded_traversal_root(secret_tree: Path) -> None:
    assert _find_secret_path_in_cmd("grep -r --exclude-dir=nested TOKEN nested") is None


@pytest.mark.parametrize(
    "command",
    [
        "grep -r '[[:upper:]]' .",
        "grep -r -1 'synthetic fixture' .",
        "grep -r1 'synthetic fixture' .",
        "cat config/*/* config/'*'/*",
        "cat config/'*'/* config/*/*",
    ],
)
def test_greptile_missed_reach_regressions(secret_tree: Path, command: str) -> None:
    (secret_tree / "config" / "keys").mkdir()
    (secret_tree / "config" / "keys" / "client.pem").write_text("synthetic\n")
    assert _find_secret_path_in_cmd(command) is not None


@pytest.mark.parametrize("option", ["-0", "-00", "-C0"])
def test_zero_context_does_not_include_comments(secret_tree: Path, option: str) -> None:
    assert _find_secret_path_in_cmd(f"grep -r {option} 'synthetic fixture' .") is None


def test_fixed_posix_class_is_literal(secret_tree: Path) -> None:
    assert _find_secret_path_in_cmd("grep -rF '[[:upper:]]' .") is None


@pytest.mark.parametrize("pattern", ["prefix$suffix", "prefix^suffix"])
def test_bre_internal_anchors_do_not_hide_values(
    secret_tree: Path, pattern: str
) -> None:
    (secret_tree / ".env").write_text(f"TOKEN={pattern}\n")
    assert _find_secret_path_in_cmd(f"grep -r '{pattern}' .") is not None


@pytest.mark.parametrize(
    "command",
    ["grep -r '^ABSENT$' .", "grep -rF 'prefix$suffix' ."],
)
def test_unmatched_anchor_and_fixed_patterns_stay_allowed(
    secret_tree: Path, command: str
) -> None:
    assert _find_secret_path_in_cmd(command) is None


@pytest.mark.parametrize("command", ["grep -r auth .", "cat .d*/*", "grep -r auth .d*"])
def test_relative_discovery_protects_home_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".docker").mkdir()
    (tmp_path / ".docker" / "config.json").write_text('{"auth": "synthetic"}\n')
    assert _find_secret_path_in_cmd(command) is not None


def test_relative_discovery_does_not_protect_nonhome_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".docker").mkdir()
    (tmp_path / ".docker" / "config.json").write_text('{"auth": "synthetic"}\n')
    assert _find_secret_path_in_cmd("grep -r auth .") is None
    assert _find_secret_path_in_cmd("cat .d*/*") is None


@pytest.mark.parametrize("option", ["-L", "-Li", "--follow"])
def test_ripgrep_follow_flag_still_detects_value_output(
    secret_tree: Path, option: str
) -> None:
    assert _find_secret_path_in_cmd(f"rg {option} synthetic config/*") is not None


@pytest.mark.parametrize(
    "command",
    [
        "grep -L synthetic config/*",
        "rg --files-without-match synthetic config/*",
        "rg -l synthetic config/*",
        "rg -q synthetic config/*",
        "rg -c synthetic config/*",
    ],
)
def test_filename_only_and_quiet_searches_stay_allowed(
    secret_tree: Path, command: str
) -> None:
    assert _find_secret_path_in_cmd(command) is None


@pytest.mark.parametrize(
    "option",
    ["-g client.pem", "-gclient.pem", "--glob client.pem", "--glob=client.pem"],
)
def test_ripgrep_glob_option_does_not_replace_search_pattern(
    secret_tree: Path, option: str
) -> None:
    assert _find_secret_path_in_cmd(f"rg {option} synthetic config/*") is not None


@pytest.mark.parametrize("option", ["-z", "--null-data"])
def test_null_delimited_grep_keeps_whole_record(secret_tree: Path, option: str) -> None:
    (secret_tree / ".env").write_text("HEADER\nTOKEN=synthetic\n")
    assert _find_secret_path_in_cmd(f"grep -r {option} HEADER .") is not None
    assert _find_secret_path_in_cmd(f"grep -r {option} ABSENT .") is None


@pytest.mark.parametrize("option", ["-zx", "-z -x", "--search-zip -x"])
def test_ripgrep_zip_search_keeps_newline_records(
    secret_tree: Path, option: str
) -> None:
    (secret_tree / "config" / "client.pem").write_text("HEADER\nTOKEN=synthetic\n")
    assert _find_secret_path_in_cmd(f"rg {option} TOKEN=synthetic config/*") is not None
    assert _find_secret_path_in_cmd(f"rg {option} ABSENT config/*") is None


@pytest.mark.parametrize("option", ["-z", "--search-zip"])
def test_ripgrep_zip_search_does_not_print_neighboring_env_lines(
    secret_tree: Path, option: str
) -> None:
    assert _find_secret_path_in_cmd(f"rg {option} 'synthetic fixture' .[e]nv") is None


def test_ripgrep_null_data_keeps_whole_record(secret_tree: Path) -> None:
    (secret_tree / "config" / "client.pem").write_text("HEADER\nTOKEN=synthetic\n")
    assert _find_secret_path_in_cmd("rg --null-data HEADER config/*") is not None
    assert _find_secret_path_in_cmd("rg --null-data ABSENT config/*") is None


def test_glob_preserves_quoted_directory_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / " config").mkdir()
    (tmp_path / " config" / "client.pem").write_text("synthetic certificate\n")
    assert _find_secret_path_in_cmd("cat ' config'/*") is not None


@pytest.mark.parametrize(
    "option",
    [
        "--binary-files text",
        "--binary-files=text",
        "--directories recurse",
        "--devices read",
        "--label fixture",
        "--group-separator separator",
        "--exclude-from excludes.txt",
    ],
)
def test_grep_value_options_preserve_search_pattern(
    secret_tree: Path, option: str
) -> None:
    (secret_tree / "excludes.txt").write_text("*.py\n")
    assert _find_secret_path_in_cmd(f"grep -r {option} TOKEN .") is not None
    assert _find_secret_path_in_cmd(f"grep -r {option} ABSENT .") is None


@pytest.mark.parametrize("option", ["-d recurse", "-drecurse", "--directories=recurse"])
def test_grep_directory_option_enables_shallow_recursion(
    secret_tree: Path, option: str
) -> None:
    assert _find_secret_path_in_cmd(f"grep {option} TOKEN .") is not None


@pytest.mark.parametrize(
    ("options", "pattern", "detected"),
    [
        ("-S", "synthetic", True),
        ("--smart-case", "synthetic", True),
        ("-SF", "synthetic", True),
        ("-S", "Synthetic", False),
        ("-S", "ABSENT", False),
        ("-i -S", "Synthetic", False),
        ("-S -i", "Synthetic", True),
        ("-S -s", "synthetic", False),
        ("--smart-case --case-sensitive", "synthetic", False),
        ("-s -S", "synthetic", True),
        ("--ignore-case --smart-case", "Synthetic", False),
        ("--case-sensitive --smart-case", "synthetic", True),
        ("-S -e ABSENT -e", "synthetic", False),
        ("-S -e absent -e", "synthetic", True),
    ],
)
def test_ripgrep_case_modes(
    secret_tree: Path, options: str, pattern: str, detected: bool
) -> None:
    (secret_tree / "config" / "client.pem").write_text("SYNTHETIC\n")
    result = _find_secret_path_in_cmd(f"rg {options} {pattern} config/*")
    assert (result is not None) is detected


@pytest.mark.parametrize(
    "option",
    ["--exclude-from excludes.txt", "--exclude-from=excludes.txt"],
)
def test_exclude_from_applies_listed_globs(secret_tree: Path, option: str) -> None:
    (secret_tree / "excludes.txt").write_text(".env\n")
    assert _find_secret_path_in_cmd(f"grep -r {option} OPENROUTER .") is None
    assert _find_secret_path_in_cmd(f"grep -r {option} TOKEN nested") is not None


@pytest.mark.parametrize(
    "separator", ["\v", "\f", "\r", "\x1c", "\x85", "\u2028", "\u2029"]
)
def test_exclude_from_preserves_non_newline_characters(
    secret_tree: Path, separator: str
) -> None:
    (secret_tree / "excludes.txt").write_bytes(f".env{separator}other\n".encode())
    assert (
        _find_secret_path_in_cmd("grep -r --exclude-from excludes.txt OPENROUTER .")
        is not None
    )


@pytest.mark.parametrize("suffix", [b"", b"\n", b"\r\n", b" \t\v\f\n"])
def test_exclude_from_final_record_and_trailing_whitespace(
    secret_tree: Path, suffix: bytes
) -> None:
    (secret_tree / "excludes.txt").write_bytes(b".env" + suffix)
    assert (
        _find_secret_path_in_cmd("grep -r --exclude-from excludes.txt OPENROUTER .")
        is None
    )


def test_exclude_from_does_not_apply_truncated_record(secret_tree: Path) -> None:
    (secret_tree / "excludes.txt").write_text("\n" * (65536 - 4) + ".env.other\n")
    assert (
        _find_secret_path_in_cmd("grep -r --exclude-from excludes.txt OPENROUTER .")
        is not None
    )


def test_exclude_from_blank_lines_are_ignored(secret_tree: Path) -> None:
    (secret_tree / "excludes.txt").write_text("\n.env\n\n")
    assert (
        _find_secret_path_in_cmd("grep -r --exclude-from excludes.txt OPENROUTER .")
        is None
    )


@pytest.mark.parametrize(
    "options",
    [
        "--directories=recurse --directories=skip",
        "--directories recurse --directories skip",
        "-d recurse -d skip",
        "--directories=recurse -dskip",
        "-r --directories=skip",
        "--directories=skip --directories=recurse --directories=read",
    ],
)
def test_directory_mode_last_option_wins(secret_tree: Path, options: str) -> None:
    assert _find_secret_path_in_cmd(f"grep {options} TOKEN .") is None


@pytest.mark.parametrize(
    "options",
    [
        "--directories=skip --directories=recurse",
        "-d skip -d recurse",
        "--directories=skip -r",
        "--directories=read --recursive",
    ],
)
def test_later_directory_recurse_restores_discovery(
    secret_tree: Path, options: str
) -> None:
    assert _find_secret_path_in_cmd(f"grep {options} TOKEN .") is not None


@pytest.mark.parametrize(
    "option",
    ["-f patterns.txt", "-fpatterns.txt", "--file patterns.txt", "--file=patterns.txt"],
)
def test_pattern_file_does_not_suppress_implicit_recursive_root(
    secret_tree: Path, option: str
) -> None:
    (secret_tree / "patterns.txt").write_text("OPENROUTER\n")
    assert _find_secret_path_in_cmd(f"grep -r {option}") is not None
    assert _find_secret_path_in_cmd(f"grep -r {option} config") is not None
    (secret_tree / "safe").mkdir()
    assert _find_secret_path_in_cmd(f"grep -r {option} safe") is None
    assert _find_secret_path_in_cmd(f"grep -rq {option}") is None
    assert _find_secret_path_in_cmd(f"grep {option}") is None


@pytest.mark.parametrize(
    "separator", ["\v", "\f", "\r", "\x1c", "\x85", "\u2028", "\u2029"]
)
def test_grep_value_records_split_only_at_lf(secret_tree: Path, separator: str) -> None:
    (secret_tree / ".env").write_bytes(f"TOKEN=synthetic{separator}MARKER\n".encode())
    assert _find_secret_path_in_cmd("grep -r MARKER .") is not None
    assert _find_secret_path_in_cmd("grep -r '^MARKER$' .") is None
    assert _find_secret_path_in_cmd("grep -r ABSENT .") is None


@pytest.mark.parametrize("separator", ["\r", "\r\n", "\v", "\u2028"])
def test_whole_line_glob_search_preserves_record_characters(
    secret_tree: Path, separator: str
) -> None:
    (secret_tree / "config" / "client.pem").write_bytes(
        f"HEADER{separator}MARKER\n".encode()
    )
    result = _find_secret_path_in_cmd("grep -x MARKER config/*")
    assert (result is not None) is (separator == "\r\n")


def test_ripgrep_crlf_whole_line_matches_protected_assignment(
    secret_tree: Path,
) -> None:
    (secret_tree / "config" / "client.pem").write_bytes(b"TOKEN=synthetic\r\n")
    assert _find_secret_path_in_cmd("rg --crlf -x TOKEN=synthetic config/*") is not None
    assert (
        _find_secret_path_in_cmd("rg --crlf --line-regexp TOKEN=synthetic config/*")
        is not None
    )
    assert _find_secret_path_in_cmd("rg -x TOKEN=synthetic config/*") is None
    assert _find_secret_path_in_cmd("rg -N -x TOKEN=synthetic config/*") is None
    assert _find_secret_path_in_cmd("grep -x TOKEN=synthetic config/*") is None
    assert (
        _find_secret_path_in_cmd("rg --crlf --no-crlf -x TOKEN=synthetic config/*")
        is None
    )
    assert (
        _find_secret_path_in_cmd("rg --no-crlf --crlf -x TOKEN=synthetic config/*")
        is not None
    )
    assert _find_secret_path_in_cmd("rg --crlf TOKEN=synthetic config/*") is not None
    assert _find_secret_path_in_cmd("rg --crlf -x ABSENT config/*") is None


def test_single_quoted_home_glob_does_not_expand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    literal = tmp_path / "$HOME"
    literal.mkdir()
    (literal / "client.pem").write_text("synthetic certificate\n")
    assert _find_secret_path_in_cmd("cat '$HOME'/*") is not None
    assert _find_secret_path_in_cmd(r"cat \$HOME/*") is not None
    (home / "id.pem").write_text("synthetic certificate\n")
    assert _find_secret_path_in_cmd("cat $HOME/*") is not None
    assert _find_secret_path_in_cmd('cat "$HOME"/*') is not None


@pytest.mark.parametrize(
    "option", ["--encoding utf-8", "--encoding=utf-8", "-E utf-8", "-Eutf-8"]
)
def test_ripgrep_encoding_preserves_pattern(secret_tree: Path, option: str) -> None:
    (secret_tree / "config" / "client.pem").write_text("TOKEN=synthetic\n")
    assert _find_secret_path_in_cmd(f"rg {option} TOKEN config/*") is not None
    assert _find_secret_path_in_cmd(f"rg {option} ABSENT config/*") is None


@pytest.mark.parametrize(
    "value", ["safe.txt config/*", "safe.txt\tconfig/*", " safe.txt\nconfig/* "]
)
def test_unquoted_expansion_splits_glob_fields(
    secret_tree: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("FILES", value)
    assert _find_secret_path_in_cmd("cat $FILES") is not None
    assert _find_secret_path_in_cmd("cat ${FILES}") is not None
    assert _find_secret_path_in_cmd('cat "$FILES"') is None
    assert _find_secret_path_in_cmd("cat '$FILES'") is None


@pytest.mark.parametrize(
    "value", ["nested missing*", "missing* nested", "nested missing"]
)
def test_unquoted_expansion_preserves_literal_search_roots(
    secret_tree: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DIRS", value)
    assert _find_secret_path_in_cmd("grep -r TOKEN $DIRS") is not None
    assert _find_secret_path_in_cmd("grep -r TOKEN ${DIRS}") is not None
    assert _find_secret_path_in_cmd('grep -r TOKEN "$DIRS"') is None
    assert _find_secret_path_in_cmd("grep -r ABSENT $DIRS") is None


def test_expansion_field_boundaries_preserve_prefix_suffix_and_quotes(
    secret_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FILES", "afe.txt config/")
    assert _find_secret_path_in_cmd("cat s${FILES}*") is not None
    monkeypatch.setenv("FILES", "safe.txt ")
    assert _find_secret_path_in_cmd("cat ${FILES}config/*") is not None
    monkeypatch.setenv("FILES", "safe.txt missing/*")
    assert _find_secret_path_in_cmd("cat $FILES") is None
    spaced = secret_tree / "with space"
    spaced.mkdir()
    (spaced / "client.pem").write_text("synthetic certificate\n")
    monkeypatch.setenv("DIR", "with space")
    assert _find_secret_path_in_cmd('cat "$DIR"/*') is not None
    assert _find_secret_path_in_cmd("cat $DIR/*") is None
