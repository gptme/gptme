import shutil
from collections.abc import Generator

from ..message import Message
from ..util.gh import get_github_pr_content, parse_github_url
from . import ConfirmFunc, Parameter, ToolSpec, ToolUse


def has_gh_tool() -> bool:
    return shutil.which("gh") is not None


def execute_gh(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Execute GitHub operations."""
    if args and len(args) >= 2 and args[0] == "pr" and args[1] == "view":
        # Get PR URL from args or kwargs
        if len(args) > 2:
            url = args[2]
        elif kwargs:
            url = kwargs.get("url", "")
        else:
            yield Message("system", "Error: No PR URL provided")
            return

        # Fetch PR content
        content = get_github_pr_content(url)
        if content:
            yield Message("system", content)
        else:
            # Try to provide helpful error message
            github_info = parse_github_url(url)
            if not github_info:
                yield Message(
                    "system",
                    f"Error: Invalid GitHub URL: {url}\n\nExpected format: https://github.com/owner/repo/pull/number",
                )
            else:
                yield Message(
                    "system",
                    "Error: Failed to fetch PR content. Make sure 'gh' CLI is installed and authenticated.",
                )
    else:
        yield Message(
            "system", "Error: Unknown gh command. Available: gh pr view <url>"
        )


instructions = """Interact with GitHub via the GitHub CLI (gh).

For reading PRs with full context (review comments, code context, suggestions), use:
```gh pr view <pr_url>
```

For other operations, use the `shell` tool with the `gh` command."""


def examples(tool_format):
    return f"""
> User: read PR with full context including review comments
> Assistant:
{ToolUse("gh", ["pr", "view", "https://github.com/owner/repo/pull/123"], None).to_output(tool_format)}

> User: create a public repo from the current directory, and push. Note that --confirm and -y are deprecated, and no longer needed.
> Assistant:
{ToolUse("shell", [], '''
REPO=$(basename $(pwd))
gh repo create $REPO --public --source . --push
'''.strip()).to_output(tool_format)}

> User: show issues
> Assistant:
{ToolUse("shell", [], "gh issue list --repo $REPO").to_output(tool_format)}

> User: read issue with comments
> Assistant:
{ToolUse("shell", [], "gh issue view $ISSUE --repo $REPO --comments").to_output(tool_format)}

> User: show recent workflows
> Assistant:
{ToolUse("shell", [], "gh run list --repo $REPO --limit 5").to_output(tool_format)}

> User: show workflow
> Assistant:
{ToolUse("shell", [], "gh run view $RUN --repo $REPO --log").to_output(tool_format)}

> User: wait for workflow to finish
> Assistant:
{ToolUse("shell", [], "gh run watch $RUN --repo $REPO").to_output(tool_format)}
"""


tool: ToolSpec = ToolSpec(
    name="gh",
    available=has_gh_tool(),
    desc="Interact with GitHub",
    instructions=instructions,
    examples=examples,
    execute=execute_gh,
    block_types=["gh"],
    parameters=[
        Parameter(
            name="url",
            type="str",
            description="GitHub PR URL (e.g., https://github.com/owner/repo/pull/123)",
            required=True,
        ),
    ],
)
