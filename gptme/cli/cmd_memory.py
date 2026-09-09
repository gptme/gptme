"""gptme-util memory — cross-harness memory store CLI.

Entries are Claude Code compatible markdown files (``name``, ``description``,
``metadata.type`` frontmatter) read from an ordered list of roots and written
to one scope. Any harness can wrap these commands: Claude Code through hooks,
Codex through an AGENTS.md snippet, gptme through the ``memory`` tool.

See gptme/gptme#3734 for the design.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Literal

import click

# C0/C1 controls. ``keep_newlines`` still drops ESC/CSI/CR (CR overwrites the
# current terminal line) but preserves \t and \n so markdown stays readable.
_ALL_CONTROLS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_UNSAFE_CONTROLS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _clean(value: str, *, keep_newlines: bool = False) -> str:
    """Strip terminal controls from untrusted memory text."""
    pattern = _UNSAFE_CONTROLS_RE if keep_newlines else _ALL_CONTROLS_RE
    return pattern.sub("", value)


def _store():
    from ..memory import MemoryStore  # fmt: skip

    return MemoryStore.from_workspace()


@click.group("memory")
def memory():
    """Cross-harness memory store: CC-compatible entries, layered roots."""


@memory.command("roots")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def memory_roots(as_json: bool):
    """Show the resolved memory roots, nearest layer first."""
    store = _store()
    if as_json:
        click.echo(
            json.dumps(
                [
                    {"scope": r.scope, "path": str(r.path), "exists": r.exists}
                    for r in store.roots
                ],
                indent=2,
            )
        )
        return
    for r in store.roots:
        marker = "" if r.exists else "  (missing)"
        click.echo(f"{r.scope:8s} {r.path}{marker}")


@memory.command("list")
@click.option("--type", "type_", help="Only entries of this type.")
@click.option(
    "--scope", help="Only entries from this root (project, cc, agent, user, explicit)."
)
@click.option(
    "--status", help="Only entries with this status (living, superseded, historical)."
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def memory_list(
    type_: str | None, scope: str | None, status: str | None, as_json: bool
):
    """List entries across all roots (nearest root wins on name collision)."""
    store = _store()
    entries = store.entries(type=type_, status=status, scope=scope)
    if as_json:
        click.echo(json.dumps([e.to_dict() for e in entries], indent=2, default=str))
    else:
        for e in entries:
            click.echo(
                f"{_clean(e.type):10s} {_clean(e.name)}  — {_clean(e.description)}"
            )
    if store.errors:
        click.echo(
            f"({len(store.errors)} file(s) skipped: not memory entries)", err=True
        )


@memory.command("show")
@click.argument("name")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON (frontmatter fields + body).",
)
def memory_show(name: str, as_json: bool):
    """Print one entry by name."""
    entry = _store().get(name)
    if entry is None:
        click.echo(f"Error: no memory entry named {name!r}", err=True)
        sys.exit(1)
    if as_json:
        click.echo(
            json.dumps({**entry.to_dict(), "body": entry.body}, indent=2, default=str)
        )
    else:
        click.echo(_clean(entry.to_markdown(), keep_newlines=True), nl=False)


@memory.command("save")
@click.argument("name")
@click.argument("description")
@click.option(
    "--type",
    "type_",
    default="general",
    show_default=True,
    help="Entry type (user, feedback, project, reference, general, …).",
)
@click.option(
    "--scope", help="Root to write to (default: project when present, else cc)."
)
@click.option("--title", help="Index link text (defaults to the name).")
@click.option(
    "--body-file",
    type=click.Path(exists=True, dir_okay=False),
    help="Read the body from this file instead of stdin.",
)
@click.option("--json", "as_json", is_flag=True, help="Print the saved entry as JSON.")
def memory_save(
    name: str,
    description: str,
    type_: str,
    scope: str | None,
    title: str | None,
    body_file: str | None,
    as_json: bool,
):
    """Save an entry. The body is read from --body-file or from stdin when piped.

    Example:

    \b
        gptme-util memory save prefer-short-answers \\
          "User prefers short, direct answers." --type feedback < body.md
    """
    if body_file:
        with open(body_file, encoding="utf-8") as f:
            body = f.read()
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    else:
        body = ""
    store = _store()
    try:
        path = store.save(name, description, body, type=type_, scope=scope, title=title)
    except (KeyError, OSError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if as_json:
        entry = store.get(name)
        click.echo(
            json.dumps(
                entry.to_dict() if entry else {"path": str(path)}, indent=2, default=str
            )
        )
    else:
        click.echo(f"Saved memory to {path}")


def _read_recall_prompt(prompt: str | None) -> str:
    if prompt == "-":
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(payload, dict):
            value = payload.get("prompt")
            return value if isinstance(value, str) else ""
        return ""
    if prompt is not None:
        return prompt
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise click.UsageError("provide QUERY or --prompt - to read stdin")


@memory.command("recall")
@click.argument("query", required=False)
@click.option(
    "--prompt",
    help="Prompt text, or '-' to read a Claude Code hook payload/plain text from stdin.",
)
@click.option(
    "-k",
    "--limit",
    type=click.IntRange(min=1),
    default=3,
    show_default=True,
    help="Maximum number of entries to return.",
)
@click.option(
    "--backend",
    type=click.Choice(["auto", "tfidf", "overlap"]),
    default="auto",
    show_default=True,
    help="Retrieval backend; auto falls back to token overlap.",
)
@click.option(
    "--format",
    "format_",
    type=click.Choice(["text", "json", "hook-json"]),
    default="text",
    show_default=True,
    help="Output format. hook-json is a Claude Code UserPromptSubmit response.",
)
@click.option(
    "--body-chars",
    type=click.IntRange(min=1),
    default=1200,
    show_default=True,
    help="Maximum body characters to inject per text result.",
)
def memory_recall(
    query: str | None,
    prompt: str | None,
    limit: int,
    backend: Literal["auto", "tfidf", "overlap"],
    format_: str,
    body_chars: int,
):
    """Recall relevant entries from every layered memory root."""
    from ..memory import RecallBackendUnavailable, recall, render_recall

    if query is not None and prompt is not None:
        raise click.UsageError("use either QUERY or --prompt, not both")
    prompt_text = _read_recall_prompt(prompt if prompt is not None else query)
    try:
        result = recall(_store(), prompt_text, limit=limit, backend=backend)
    except (RecallBackendUnavailable, ValueError) as e:
        raise click.ClickException(str(e)) from e

    if format_ == "json":
        click.echo(json.dumps(result.to_dict(), indent=2, default=str))
        return

    rendered = _clean(render_recall(result, body_chars=body_chars), keep_newlines=True)
    if format_ == "hook-json":
        click.echo(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": rendered,
                    }
                }
            )
        )
        return
    if rendered:
        click.echo(rendered)


@memory.command("supersede")
@click.argument("old_name")
@click.argument("new_name")
@click.option("--scope", help="Root containing both entries (default: write root).")
def memory_supersede(old_name: str, new_name: str, scope: str | None):
    """Mark OLD_NAME superseded by NEW_NAME and link both entries."""
    try:
        old, new = _store().supersede(old_name, new_name, scope=scope)
    except (KeyError, OSError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"Superseded {_clean(old.name)} -> {_clean(new.name)}")


@memory.command("audit")
@click.option("--scope", help="Root to audit (default: write root).")
@click.option("--quiet", is_flag=True, help="Print nothing when the audit passes.")
def memory_audit(scope: str | None, quiet: bool):
    """Check strict YAML parsing and supersession links."""
    try:
        issues = _store().audit(scope=scope)
    except KeyError as e:
        raise click.ClickException(str(e)) from e
    for issue in issues:
        click.echo(f"{issue.code}: {issue.entry}: {issue.detail}")
    if issues:
        raise click.exceptions.Exit(1)
    if not quiet:
        click.echo("Memory audit passed")


@memory.command("index")
@click.option("--scope", help="Root whose index to generate (default: the write root).")
@click.option("--write", is_flag=True, help="Write MEMORY.md instead of printing it.")
@click.option(
    "--check",
    is_flag=True,
    help="Exit 1 when MEMORY.md differs from the regenerated index.",
)
@click.option(
    "--budget",
    type=click.IntRange(min=1),
    help="Cap the index at this many bytes.",
)
def memory_index(scope: str | None, write: bool, check: bool, budget: int | None):
    """Generate the always-on index (living entries grouped by type).

    Prints to stdout by default so a hand-curated MEMORY.md is never
    overwritten by accident; pass --write to replace it, --check to verify.
    """
    store = _store()
    try:
        if check:
            ok = store.check_index(scope, budget=budget)
            click.echo(f"{store.index_path(scope)}: {'up to date' if ok else 'DRIFT'}")
            sys.exit(0 if ok else 1)
        if write:
            path = store.write_index(scope, budget=budget)
            click.echo(f"Wrote {path}")
            return
        click.echo(
            _clean(
                store.render_index(store.index_entries(scope), budget=budget),
                keep_newlines=True,
            ),
            nl=False,
        )
    except (KeyError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
