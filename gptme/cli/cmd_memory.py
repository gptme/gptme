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

from ..memory.schema import STATUSES

# C0/C1 controls. ``keep_newlines`` still drops ESC/CSI/CR (CR overwrites the
# current terminal line) but preserves \t and \n so markdown stays readable.
_ALL_CONTROLS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_UNSAFE_CONTROLS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _clean(value: str, *, keep_newlines: bool = False) -> str:
    """Strip terminal controls from untrusted memory text."""
    pattern = _UNSAFE_CONTROLS_RE if keep_newlines else _ALL_CONTROLS_RE
    return pattern.sub("", value)


def _as_str_list(value: object) -> list[str]:
    """Normalize a legacy tags/keywords field to a list of strings.

    Hand-edited records may store a scalar string (``"tags": "git"``).
    Iterating that directly would yield individual characters, silently
    corrupting the migrated entry's keyword recall, so wrap it instead.
    """
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if isinstance(v, str)]
    return []


def _store():
    from ..memory import MemoryStore  # fmt: skip

    return MemoryStore.from_workspace()


def _validate_scope(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    if value is None:
        return None
    scopes = [root.scope for root in _store().roots]
    if value not in scopes:
        raise click.BadParameter(
            f"{value!r} is not one of {', '.join(repr(scope) for scope in scopes)}",
            ctx=ctx,
            param=param,
        )
    return value


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
    "--scope",
    callback=_validate_scope,
    help="Only entries from this root (project, cc, agent, user, explicit).",
)
@click.option(
    "--status",
    type=click.Choice(STATUSES),
    help="Only entries with this status (living, superseded, historical).",
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


@memory.command("search")
@click.argument("pattern")
@click.option("--type", "type_", help="Only entries of this type.")
@click.option(
    "--scope",
    callback=_validate_scope,
    help="Only entries from this root (project, cc, agent, user, explicit).",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def memory_search(pattern: str, type_: str | None, scope: str | None, as_json: bool):
    """Search entries by text pattern (name, description, or body).

    PATTERN is matched case-insensitively as a substring against each
    entry's name, description, and body.  Only living entries are searched;
    use ``list --status superseded`` for archived entries.

    Example:

    \\b
        gptme-util memory search "review"
        gptme-util memory search "deploy" --type project --json
    """
    store = _store()
    entries = store.entries(type=type_, scope=scope)
    # Living entries only (mirrors recall behaviour)
    entries = [e for e in entries if e.status not in ("superseded", "historical")]

    pat = re.compile(re.escape(pattern), re.IGNORECASE)
    matched = [
        e
        for e in entries
        if pat.search(e.name)
        or pat.search(e.description)
        or (e.body and pat.search(e.body))
    ]

    if as_json:
        click.echo(json.dumps([e.to_dict() for e in matched], indent=2, default=str))
    else:
        for e in matched:
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
    default=None,
    help="Entry type (preserve existing; new entries default to general).",
)
@click.option(
    "--scope", help="Root to write to (default: project when present, else cc)."
)
@click.option("--title", help="Index link text (defaults to the name).")
@click.option(
    "--metadata", help="JSON object merged into entry metadata (e.g. provenance)."
)
@click.option(
    "--keyword",
    "keywords",
    multiple=True,
    help="Trigger phrase (repeatable); replaces keywords when supplied, otherwise preserves them.",
)
@click.option(
    "--body-file",
    type=click.Path(exists=True, dir_okay=False),
    help="Read the body from this file instead of stdin.",
)
@click.option("--json", "as_json", is_flag=True, help="Print the saved entry as JSON.")
def memory_save(
    name: str,
    description: str,
    type_: str | None,
    scope: str | None,
    title: str | None,
    metadata: str | None,
    keywords: tuple[str, ...],
    body_file: str | None,
    as_json: bool,
):
    """Save an entry. The body is read from --body-file or from stdin when piped.

    Example:

    \b
        gptme-util memory save prefer-short-answers \\
          "User prefers short, direct answers." --type feedback < body.md
    """
    parsed_metadata = None
    if metadata is not None:
        try:
            parsed_metadata = json.loads(metadata)
        except ValueError as exc:
            raise click.BadParameter(
                "must be a JSON object", param_hint="--metadata"
            ) from exc
        if not isinstance(parsed_metadata, dict):
            raise click.BadParameter("must be a JSON object", param_hint="--metadata")
        if "type" in parsed_metadata:
            raise click.BadParameter(
                "type is reserved; use --type", param_hint="--metadata"
            )
    if body_file:
        with open(body_file, encoding="utf-8") as f:
            body = f.read()
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    else:
        body = ""
    from ..memory.schema import MemoryParseError  # fmt: skip

    store = _store()
    try:
        path = store.save(
            name,
            description,
            body,
            type=type_,
            scope=scope,
            title=title,
            metadata=parsed_metadata,
            keywords=list(keywords) if keywords else None,
        )
    except (KeyError, OSError, ValueError, MemoryParseError) as e:
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


def _string_values(root: object) -> list[str]:
    """Collect strings from nested dict/list trees without recursion.

    PreToolUse ``tool_input`` can be arbitrarily nested. A recursive walk
    raises ``RecursionError`` near Python's default limit (~1000); this
    iterative stack stays bounded by heap instead.
    """
    values: list[str] = []
    pending: list[object] = [root]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            pending.extend(reversed(value.values()))
        elif isinstance(value, list):
            pending.extend(reversed(value))
    return values


def _read_match_prompt(prompt: str | None, pre_tool: bool) -> tuple[str, str]:
    """Accept plain text or the relevant fields of a Claude Code hook payload."""
    event = "PreToolUse" if pre_tool else "UserPromptSubmit"
    if prompt != "-":
        return _read_recall_prompt(prompt), event
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw, event
    if not isinstance(payload, dict):
        return "", event
    event = payload.get("hook_event_name", event)
    if event == "PreToolUse":
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            return "", event

        # All string values under tool_input describe the pending action,
        # including nested keys that reuse envelope names (cwd, session_id,
        # transcript_path). Outer envelope fields never participate because
        # traversal starts at tool_input, not the payload root.
        return "\n".join(_string_values(tool_input)), event
    if event == "UserPromptSubmit":
        value = payload.get("prompt")
        return value if isinstance(value, str) else "", event
    return "", event


@memory.command("match")
@click.argument("query", required=False)
@click.option("--prompt", help="Text, or '-' for a hook payload/plain text on stdin.")
@click.option(
    "--pre-tool", is_flag=True, help="Use PreToolUse output for plain text input."
)
@click.option("-k", "--limit", type=click.IntRange(min=1), default=5, show_default=True)
@click.option(
    "--format",
    "format_",
    type=click.Choice(["text", "json", "hook-json"]),
    default="text",
    show_default=True,
)
@click.option(
    "--body-chars", type=click.IntRange(min=1), default=1200, show_default=True
)
def memory_match(
    query: str | None,
    prompt: str | None,
    pre_tool: bool,
    limit: int,
    format_: str,
    body_chars: int,
) -> None:
    """Inject living memories whose keywords match this turn's prompt or tool call.

    Use this instead of recall when a specific phrase should surface a rule
    before you act. Names and body similarity never trigger. The CLI stores no
    session dedup; wrappers apply their own injection budget. Hook input
    supports UserPromptSubmit and PreToolUse.
    """
    from ..memory.match import match_memories, render_matches

    if query is not None and prompt is not None:
        raise click.UsageError("use either QUERY or --prompt, not both")
    text, event = _read_match_prompt(prompt if prompt is not None else query, pre_tool)
    hits = match_memories(_store(), text, limit=limit)
    if format_ == "json":
        click.echo(
            json.dumps({"hits": [hit.to_dict() for hit in hits]}, indent=2, default=str)
        )
        return
    rendered = _clean(render_matches(hits, body_chars=body_chars), keep_newlines=True)
    if format_ == "hook-json":
        click.echo(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event,
                        "additionalContext": rendered,
                    }
                }
            )
        )
    elif rendered:
        click.echo(rendered)


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
    from ..memory.schema import MemoryParseError  # fmt: skip

    try:
        old, new = _store().supersede(old_name, new_name, scope=scope)
    except (KeyError, OSError, ValueError, MemoryParseError) as e:
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


@memory.command("migrate-knowledge-jsonl")
@click.argument(
    "jsonl_path",
    required=False,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--scope", help="Root to write migrated entries to (default: write root)."
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be migrated without writing anything.",
)
@click.option(
    "--skip-existing/--no-skip-existing",
    default=True,
    show_default=True,
    help="Skip entries whose slugified name already exists in the store.",
)
def memory_migrate_knowledge_jsonl(
    jsonl_path: str | None,
    scope: str | None,
    dry_run: bool,
    skip_existing: bool,
):
    """Migrate a knowledge JSONL store to memory markdown entries.

    JSONL_PATH defaults to ``~/.local/share/gptme/knowledge/entries.jsonl``.

    Each knowledge entry becomes a ``general`` memory entry: the name is
    slugified from the primary text, the body records both fields under
    type-aware section headings (entry_type is preserved in provenance),
    and existing tags and keywords are preserved as memory keywords.

    After migration, ``gptme-util knowledge`` commands still work against the
    original JSONL; retire it once you are satisfied with the migrated entries.

    Example:

    \\b
        gptme-util memory migrate-knowledge-jsonl --dry-run
        gptme-util memory migrate-knowledge-jsonl --scope user
    """
    import json as _json
    from pathlib import Path as _Path
    from textwrap import shorten as _shorten

    from ..dirs import get_data_dir  # fmt: skip
    from ..knowledge import _ENTRY_TYPE_LABELS  # fmt: skip
    from ..memory.schema import MemoryParseError, slugify  # fmt: skip

    if jsonl_path is None:
        source = get_data_dir() / "knowledge" / "entries.jsonl"
    else:
        source = _Path(jsonl_path)

    if not source.exists():
        click.echo(f"No knowledge store at {source} — nothing to migrate.", err=True)
        sys.exit(0)

    raw_lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    entries = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("problem"), str):
            entries.append(obj)

    if not entries:
        click.echo(f"No valid entries found in {source}.")
        sys.exit(0)

    store = _store()
    migrated = skipped = errors = 0
    _seen_names: set[str] = set()

    for obj in entries:
        problem: str = obj["problem"]
        resolution: str = obj.get("resolution", "")
        raw_type = obj.get("entry_type")
        # Malformed/hand-edited records may carry a non-string entry_type
        # (e.g. a list); fall back to the default instead of aborting.
        entry_type: str = (
            raw_type if isinstance(raw_type, str) and raw_type else "problem_resolution"
        )
        tags = _as_str_list(obj.get("tags"))
        keywords = _as_str_list(obj.get("keywords"))
        original_id: str = obj.get("id", "")
        created_at: str = obj.get("created_at", "")

        # Combine tags and keywords, deduplicate
        all_keywords = list(dict.fromkeys(tags + keywords))

        # Build a slug from the problem text; append an ID suffix to avoid
        # collisions when two entries share similar problem text.
        slug_source = problem[:80]
        name = slugify(slug_source)
        if not name:
            name = f"knowledge-{original_id[:8]}" if original_id else "knowledge-entry"
        # Detect slug collision: if this name is already claimed by a *different*
        # in-progress entry (tracked below), append the entry's ID suffix.
        if name in _seen_names:
            suffix = (
                original_id[:8] if original_id else str(migrated + skipped + errors)
            )
            name = f"{name}-{suffix}"
        _seen_names.add(name)

        description = _shorten(problem, width=120, placeholder="…")

        # Type-aware section headings; typed entries keep their original
        # type in provenance so the semantic type stays recoverable.
        primary_label, secondary_label = _ENTRY_TYPE_LABELS.get(
            entry_type, ("Problem", "Resolution")
        )
        body = (
            f"## {primary_label}\n\n{problem}\n\n## {secondary_label}\n\n{resolution}\n"
        )

        provenance: dict = {"source": "knowledge-jsonl"}
        if entry_type != "problem_resolution":
            provenance["original_entry_type"] = entry_type
        if original_id:
            provenance["original_id"] = original_id
        if created_at:
            provenance["migrated_from_created_at"] = created_at

        if skip_existing and store.get(name) is not None:
            if dry_run:
                click.echo(f"  would skip (exists): {name!r}", err=True)
            else:
                click.echo(f"  skip (exists): {name!r}", err=True)
            skipped += 1
            continue

        if dry_run:
            click.echo(f"  would migrate: {name!r} ({len(all_keywords)} keyword(s))")
            migrated += 1
            continue

        try:
            store.save(
                name,
                description,
                body,
                type="general",
                scope=scope,
                keywords=all_keywords or None,
                metadata={"provenance": provenance},
            )
            click.echo(f"  migrated: {name!r}")
            migrated += 1
        except (KeyError, OSError, ValueError, MemoryParseError) as e:
            click.echo(f"  error ({name!r}): {e}", err=True)
            errors += 1

    action = "would migrate" if dry_run else "migrated"
    click.echo(
        f"\n{action} {migrated}, skipped {skipped}, errors {errors} (source: {source})"
    )
    if errors:
        sys.exit(1)


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
    from ..memory.schema import MemoryParseError  # fmt: skip

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
                store.render_root_index(scope, budget=budget),
                keep_newlines=True,
            ),
            nl=False,
        )
    except (KeyError, OSError, ValueError, MemoryParseError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@memory.command("export")
@click.option(
    "--view",
    type=click.Choice(["cc", "codex"]),
    default="cc",
    show_default=True,
    help=(
        "Output format: "
        "'cc' = MEMORY.md index (for CC always-on auto-load or context injection); "
        "'codex' = AGENTS.md memory-section snippet."
    ),
)
@click.option(
    "--scope", help="Root to export from (default: write root for cc, all for codex)."
)
@click.option(
    "--budget",
    type=click.IntRange(min=1),
    help="Byte cap for the export (same as 'index --budget'); applies to both views.",
)
def memory_export(view: Literal["cc", "codex"], scope: str | None, budget: int | None):
    """Export memory entries in a harness-specific format.

    cc:    MEMORY.md index — identical to ``index`` output; pipe into the file
           that Claude Code auto-loads, or inject into a CC hook payload.

    codex: AGENTS.md snippet — a ``## Memory`` section listing living entries
           with recall instructions, ready to paste into an AGENTS.md file for
           workspaces that need a memory bootstrap.

    Example:

    \\b
        gptme-util memory export --view cc > path/to/MEMORY.md
        gptme-util memory export --view codex >> AGENTS.md
    """
    from ..memory.schema import MemoryParseError  # fmt: skip

    store = _store()

    if view == "cc":
        try:
            text = store.render_root_index(scope, budget=budget)
            click.echo(_clean(text, keep_newlines=True), nl=False)
        except (KeyError, OSError, ValueError, MemoryParseError) as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    else:  # view == "codex"
        entries = store.entries(scope=scope)
        living = [e for e in entries if e.status not in ("superseded", "historical")]
        if not living:
            click.echo("(no living memory entries — nothing to export)", err=True)
            return

        header = [
            "## Memory\n",
            "\n",
            "Run recall at session start to surface relevant entries:\n",
            "\n",
            "```bash\n",
            'gptme-util memory recall "<one-line task description>" -k 5\n',
            "```\n",
            "\n",
            "Current entries:\n",
            "\n",
        ]
        footer = [
            "\n",
            "Save a memory with:\n",
            "\n",
            "```bash\n",
            "gptme-util memory save <slug> \"<description>\" --type <type> <<'EOF'\n",
            "<body>\n",
            "EOF\n",
            "```\n",
        ]

        def build(n_entries: int) -> str:
            lines = list(header)
            for entry in living[:n_entries]:
                type_tag = f"[{_clean(entry.type)}] " if entry.type else ""
                lines.append(
                    f"- **{_clean(entry.name)}** — {type_tag}{_clean(entry.description)}\n"
                )
            omitted = len(living) - n_entries
            if omitted:
                lines.append(
                    f"- … {omitted} more entries omitted (budget {budget} bytes)\n"
                )
            lines += footer
            return "".join(lines)

        if budget is None:
            text = build(len(living))
        else:
            n = len(living)
            text = build(n)
            while len(text.encode()) > budget and n > 0:
                n -= 1
                text = build(n)
            if len(text.encode()) > budget:
                click.echo(
                    f"Error: budget {budget} is too small for the codex export header",
                    err=True,
                )
                sys.exit(1)
        click.echo(text, nl=False)
