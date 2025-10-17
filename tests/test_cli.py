import os
import random
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory

import gptme.cli
import gptme.constants
import gptme.tools.browser
import pytest
from click.testing import CliRunner
from gptme.tools import ToolUse

project_root = Path(__file__).parent.parent
logo = project_root / "media" / "logo.png"


@pytest.fixture(scope="session", autouse=True)
def tmp_data_dir():
    tmpdir = TemporaryDirectory().name
    Path(tmpdir).mkdir(parents=True, exist_ok=True)

    # set the environment variable
    print(f"setting XDG_DATA_HOME to {tmpdir}")
    os.environ["XDG_DATA_HOME"] = tmpdir


@pytest.fixture(scope="session")
def runid():
    return random.randint(0, 100000)


runid_retries: dict[str, int] = {}


@pytest.fixture
def name(runid, request):
    attempt = runid_retries.get(request.node.nodeid, 0)
    runid_retries[request.node.nodeid] = attempt + 1
    return f"test-{runid}-{request.node.name}" + (
        f"-retry-{attempt}" if attempt else ""
    )


@pytest.fixture
def args(name: str) -> list[str]:
    return [
        "--name",
        name,
    ]


@pytest.fixture
def runner():
    runner = CliRunner()
    with runner.isolated_filesystem():
        yield runner


def test_help(runner: CliRunner):
    result = runner.invoke(gptme.cli.main, ["--help"])
    assert result.exit_code == 0


def test_version(runner: CliRunner):
    result = runner.invoke(gptme.cli.main, ["--version"])
    assert result.exit_code == 0
    assert "gptme" in result.output


def test_command_exit(args: list[str], runner: CliRunner):
    args.append("/exit")
    result = runner.invoke(gptme.cli.main, args)
    assert "/exit" in result.output
    assert "Missing dependency" not in result.output
    assert result.exit_code == 0


def test_command_help(args: list[str], runner: CliRunner):
    args.append("/help")
    result = runner.invoke(gptme.cli.main, args)
    assert "/help" in result.output
    assert result.exit_code == 0


def test_command_tokens(args: list[str], runner: CliRunner):
    args.append("/tokens")
    result = runner.invoke(gptme.cli.main, args)
    assert "/tokens" in result.output
    assert "Tokens:" in result.output
    assert result.exit_code == 0


def test_command_log(args: list[str], runner: CliRunner):
    args.append("/log")
    result = runner.invoke(gptme.cli.main, args)
    assert "/log" in result.output
    assert result.exit_code == 0


def test_command_tools(args: list[str], runner: CliRunner):
    args.append("/tools")
    result = runner.invoke(gptme.cli.main, args)
    assert "/tools" in result.output
    assert result.exit_code == 0


@pytest.mark.slow
@pytest.mark.requires_api
def test_command_summarize(args: list[str], runner: CliRunner, monkeypatch):
    # tests the /summarize command
    # Set timeout to 5 minutes to avoid Anthropic's streaming recommendation
    # (Anthropic requires streaming for timeouts >= 10 minutes)
    monkeypatch.setenv("LLM_API_TIMEOUT", "300")
    # Force reinitialization of the Anthropic client to pick up the new timeout
    from gptme.config import get_config
    from gptme.llm import init_anthropic

    config = get_config()
    init_anthropic(config)
    args.append("/summarize")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args)
    assert result.exit_code == 0


def test_command_fork(args: list[str], runner: CliRunner, name: str):
    # tests the /fork command
    name += "-fork"
    args.append(f"/fork {name}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args)
    assert result.exit_code == 0


def test_command_rename(args: list[str], runner: CliRunner, name: str):
    # tests the /rename command
    name += "-rename"
    args.append(f"/rename {name}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args)
    assert result.exit_code == 0


@pytest.mark.requires_api
def test_command_rename_auto(args: list[str], runner: CliRunner, name: str):
    # test with "auto" name
    args.append("/rename auto")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args)
    assert result.exit_code == 0, (result.output, result.exception)


@pytest.mark.slow
def test_fileblock(args: list[str], runner: CliRunner):
    args_orig = args.copy()

    # tests saving with a ```filename.txt block
    tooluse = ToolUse("save", ["hello.py"], "print('hello')")
    args.append(f"/impersonate {tooluse.to_output()}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args)
    assert result.exit_code == 0

    # read the file
    with open("hello.py") as f:
        content = f.read()
    assert content == "print('hello')\n"

    # test append
    args = args_orig.copy()
    tooluse = ToolUse("append", ["hello.py"], "print('world')")
    args.append(f"/impersonate {tooluse.to_output()}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args)
    assert result.exit_code == 0

    # read the file
    with open("hello.py") as f:
        content = f.read()
    assert content == "print('hello')\nprint('world')\n"

    # test write file to directory that doesn't exist
    tooluse = ToolUse("save", ["hello/hello.py"], 'print("hello")')
    args = args_orig.copy()
    args.append(f"/impersonate {tooluse.to_output()}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args)
    assert result.exit_code == 0

    # test patch on file in directory
    patch = '<<<<<<< ORIGINAL\nprint("hello")\n=======\nprint("hello world")\n>>>>>>> UPDATED'
    tooluse = ToolUse("patch", ["hello/hello.py"], patch)
    args = args_orig.copy()
    args.append(f"/impersonate {tooluse.to_output()}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args)
    assert result.exit_code == 0

    # read the file
    with open("hello/hello.py") as f:
        content = f.read()
    assert content == 'print("hello world")\n'


def test_shell(args: list[str], runner: CliRunner):
    args.append("/shell echo 'yes'")
    result = runner.invoke(gptme.cli.main, args)
    output = result.output.split("System")[-1]
    # check for two 'yes' in output (both command and stdout)
    assert output.count("yes") == 2, result.output
    assert result.exit_code == 0


# Flaky in CI
@pytest.mark.xfail(strict=False)
def test_shell_file(args: list[str], runner: CliRunner):
    # test running the shell tool with a filename
    # make sure we don't accidentally expand the filename and include it in the shell command
    # create new file with contents
    tmp_path = tempfile.mktemp()
    with open(tmp_path, "w") as f:
        f.write("yes")
    args.append(f"/shell cat {tmp_path}")
    result = runner.invoke(gptme.cli.main, args)
    output_pre, output_post = result.output.split("System", 1)
    # check for no 'yes' in parsed input (only direct command output)
    assert output_pre.count("yes") == 1, "no yes before System message: " + output_pre
    # check for one 'yes' in system response (only message stdout)
    assert output_post.count("yes") == 1, output_post
    assert result.exit_code == 0


def test_python(args: list[str], runner: CliRunner):
    args.append("/py print('yes')")
    result = runner.invoke(gptme.cli.main, args)
    assert "yes\n" in result.output
    assert result.exit_code == 0


def test_python_error(args: list[str], runner: CliRunner):
    args.append("/py raise Exception('yes')")
    result = runner.invoke(gptme.cli.main, args)
    assert "Exception: yes" in result.output
    assert result.exit_code == 0


_block_sh = """function test() {
    echo "start"  # start

    echo "after empty line"
}
"""
_block_py = """def test():
    print("start")  # start

    print("after empty line")
"""
blocks = {"ipython": _block_py, "sh": _block_sh}


@pytest.mark.slow
@pytest.mark.parametrize("lang", blocks.keys())
def test_block(args: list[str], lang: str, runner: CliRunner):
    # tests that shell codeblocks are formatted correctly such that whitespace and newlines are preserved
    code = blocks[lang]
    code = f"""```{lang}
{code.strip()}
```"""
    assert "'" not in code

    args.append(f"/impersonate {code}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args)
    output = result.output
    print(f"output: {output}\nEND")
    # check everything after the second '# start'
    # (get not the user impersonation command, but the assistant message and everything after)
    output = output.split("# start", 2)[-1]
    printcmd = "print" if lang == "ipython" else "echo"
    assert f"\n\n    {printcmd}" in output
    assert result.exit_code == 0


@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.skipif(
    os.environ.get("MODEL") == "openai/gpt-4o-mini", reason="unreliable for gpt-4o-mini"
)
def test_generate_primes(args: list[str], runner: CliRunner):
    args.append("compute the first 10 prime numbers using ipython")
    result = runner.invoke(gptme.cli.main, args)
    # check that the 9th and 10th prime is present
    assert "23" in result.output
    assert "29" in result.output
    assert result.exit_code == 0


def test_stdin(args: list[str], runner: CliRunner):
    args.append("/exit")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args, input="hello")
    assert "```stdin\nhello\n```" in result.output
    assert result.exit_code == 0


# Flaky, seems to not always get "User:" outputted?
@pytest.mark.xfail(strict=False)
@pytest.mark.slow
@pytest.mark.requires_api
def test_chain(args: list[str], runner: CliRunner):
    """tests that the "-" argument works to chain commands, executing after the agent has exhausted the previous command"""
    # first command needs to be something requiring two tools, so we can check both are ran before the next chained command
    args.append(
        "we are testing, follow instructions carefully without extra steps. write a test.txt file with the save tool"
    )
    args.append("-")
    args.append("patch it to contain emojis")
    args.append("-")
    args.append("read the contents")
    args.extend(["--tool-format", "markdown"])
    args.extend(["--tools", "save,patch,shell"])
    result = runner.invoke(gptme.cli.main, args)
    print(result.output)
    # check that outputs came in expected order
    user1_loc = result.output.index("User:")
    user2_loc = result.output.index("User:", user1_loc + 1)
    user3_loc = result.output.index("User:", user2_loc + 1)
    save_loc = result.output.index("```save")
    patch_loc = result.output.index("```patch")
    print_loc = result.output.rindex("cat test.txt")
    print(
        f"{user1_loc=} {save_loc=} {user2_loc=} {patch_loc=} {user3_loc=} {print_loc=}"
    )
    assert user1_loc < save_loc
    assert save_loc < user2_loc
    assert user2_loc < patch_loc
    assert patch_loc < user3_loc
    assert user3_loc < print_loc
    assert result.exit_code == 0


# TODO: move elsewhere
@pytest.mark.slow
@pytest.mark.requires_api
def test_tmux(args: list[str], runner: CliRunner):
    """
    $ gptme '/impersonate lets find out the current load
    ```tmux
    new_session top
    ```'
    """
    args.append(
        "/impersonate lets find out the current load\n```tmux\nnew_session top\n```"
    )
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args)
    assert "%CPU" in result.output
    assert result.exit_code == 0


# TODO: move elsewhere
@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.flaky(retries=2, delay=5)
@pytest.mark.skipif(
    os.environ.get("MODEL") in ["openai/gpt-4o-mini", "anthropic/claude-haiku-4-5"],
    reason="unreliable/slow for gpt-4o-mini and claude-haiku-4-5",
)
def test_subagent(args: list[str], runner: CliRunner):
    # f14: 377
    # f15: 610
    # f16: 987
    args.extend(["--tools", "ipython,subagent"])
    args.extend(
        [
            "We are in a test. Use the subagent tool to compute `fib(15)`, where `fib(1) = 1` and `fib(2) = 1`.",
            "-",
            "Answer with the value.",
        ]
    )
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args)
    print(result.output)

    # apparently this is not obviously 610
    accepteds = ["377", "610"]
    assert any(
        [accepted in result.output for accepted in accepteds]
    ), f"Accepteds '{accepteds}' not in output: {result.output}"
    assert any(
        [accepted in "```".join(result.output.split("```")) for accepted in accepteds]
    ), "more complex case, not sure if needed"


@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.skipif(
    gptme.tools.browser.browser is None,
    reason="no browser tool available",
)
def test_url(args: list[str], runner: CliRunner):
    args.append("Who is the CEO of https://superuserlabs.org?")
    result = runner.invoke(gptme.cli.main, args)
    assert "Erik Bjäreholt" in result.output
    assert result.exit_code == 0


@pytest.mark.slow
@pytest.mark.requires_api
def test_vision(args: list[str], runner: CliRunner):
    args.append(f"can you see the image at {logo}? answer with yes or no")
    result = runner.invoke(gptme.cli.main, args)
    if result.exception:
        raise result.exception
    assert result.exit_code == 0
    assert "yes" in result.output


@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.parametrize(
    "tool_format, expected, not_expected",
    [
        ("markdown", ["```shell\nls"], ["<tool-use>\n<shell>\nls"]),
        ("xml", ["<tool-use>\n<shell>\nls"], ["```shell\nls"]),
        (
            "tool",
            ["@shell:"],
            ["```shell\nls", "<tool-use>\n<shell>\nls"],
        ),
    ],
)
def test_tool_format_option(
    args: list[str], runner: CliRunner, tool_format, expected, not_expected
):
    args.append("--show-hidden")
    args.append("--tool-format")
    args.append(tool_format)
    args.append("we are testing, just ls the current dir and then do nothing else")

    result = runner.invoke(gptme.cli.main, args)
    if result.exception:
        raise result.exception
    assert result.exit_code == 0

    for expect in expected:
        assert expect in result.output

    for not_expect in not_expected:
        assert not_expect not in result.output


@pytest.mark.slow
def test_nested_gptme_calls(args: list[str], runner: CliRunner):
    """Test that gptme can call itself recursively, even without --non-interactive flag."""
    # Run a nested gptme instance that echoes a message
    args.append('/shell gptme "/shell echo we are testing nested gptme"')

    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(gptme.cli.main, args)

    # Check that the nested echo output is present
    assert "we are testing nested gptme" in result.output
    # Check that both gptme instances exited successfully
    assert result.exit_code == 0
