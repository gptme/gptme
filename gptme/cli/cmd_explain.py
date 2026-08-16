"""Offline answers to conceptual questions about gptme.

``gptme explain branches`` answers from a bundled FAQ file — no model call, no
network. Users kept filing discussions asking what branches are or how the
context window works (gptme#136, gptme#138); those answers should be in the CLI.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import click
import yaml

FAQ_PATH = Path(__file__).parent.parent / "data" / "faq.yaml"


@dataclass
class FAQEntry:
    topic: str
    question: str
    answer: str
    aliases: list[str] = field(default_factory=list)
    see_also: list[str] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        """Topic id plus aliases, all matchable."""
        return [self.topic, *self.aliases]

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "question": self.question,
            "answer": self.answer.strip(),
            "aliases": self.aliases,
            "see_also": self.see_also,
        }


_STOP: frozenset[str] = frozenset(
    {
        # articles / conjunctions / prepositions
        "the",
        "and",
        "for",
        "not",
        "but",
        "all",
        "any",
        # question words
        "how",
        "what",
        "where",
        "when",
        "who",
        "why",
        "which",
        # common auxiliaries
        "are",
        "can",
        "does",
        "has",
        "had",
        "was",
        "will",
        "its",
        "you",
        # generic verbs that add no topic signal
        "get",
        "use",
        "set",
        "put",
        "find",
        "show",
        "make",
    }
)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _tokens(text: str) -> set[str]:
    tokens = {
        tok for tok in _normalize(text).split() if len(tok) > 2 and tok not in _STOP
    }
    # Match simple singular/plural variants without a language-heavy stemmer.
    variants = {tok[:-1] if tok.endswith("s") else f"{tok}s" for tok in tokens}
    return tokens | variants


@lru_cache(maxsize=1)
def load_faq(path: str | None = None) -> list[FAQEntry]:
    """Load FAQ entries from the bundled YAML file."""
    faq_path = Path(path) if path else FAQ_PATH
    try:
        text = faq_path.read_text()
    except OSError as e:
        raise click.ClickException(f"Could not read FAQ file {faq_path}: {e}") from e
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise click.ClickException(f"Could not parse FAQ file {faq_path}: {e}") from e
    try:
        return [
            FAQEntry(
                topic=entry["topic"],
                question=entry["question"],
                answer=entry["answer"],
                aliases=entry.get("aliases", []),
                see_also=entry.get("see_also", []),
            )
            for entry in data.get("topics", [])
        ]
    except (AttributeError, KeyError, TypeError) as e:
        raise click.ClickException(f"Invalid FAQ structure in {faq_path}: {e}") from e


def find_topic(query: str, entries: list[FAQEntry]) -> FAQEntry | None:
    """Best FAQ entry for a query, or None if nothing matches well enough.

    Exact topic/alias match wins; otherwise the entry sharing the most query
    words with its topic or aliases.
    """
    normalized = _normalize(query)
    if not normalized:
        return None

    for entry in entries:
        if any(_normalize(name) == normalized for name in entry.names):
            return entry

    query_tokens = _tokens(query)
    if not query_tokens:
        return None

    # Only match against topic names and aliases, NOT the question text.
    # The question is phrasing the same concept in a complete sentence, so it
    # shares generic words ("where", "stored", "what") with many entries and
    # inflates false-positive scores. Aliases are already designed for the
    # natural-language queries users type.
    best, best_score = None, 0
    for entry in entries:
        haystack = _tokens(" ".join(entry.names))
        score = len(query_tokens & haystack)
        if score > best_score:
            best, best_score = entry, score
    return best


def suggest_topics(query: str, entries: list[FAQEntry], limit: int = 3) -> list[str]:
    """Topic ids closest to an unmatched query."""
    names = {name: entry.topic for entry in entries for name in entry.names}
    matches = difflib.get_close_matches(
        _normalize(query), [_normalize(n) for n in names], n=limit, cutoff=0.5
    )
    normalized = {_normalize(name): topic for name, topic in names.items()}
    # dict.fromkeys preserves closeness order while dropping alias duplicates
    return list(dict.fromkeys(normalized[m] for m in matches))


def format_entry(entry: FAQEntry) -> str:
    lines = [entry.question, "=" * len(entry.question), "", entry.answer.strip()]
    if entry.see_also:
        lines += ["", f"See also: {', '.join(entry.see_also)}"]
    return "\n".join(lines)


@click.command("explain")
@click.argument("query", nargs=-1)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def explain(query: tuple[str, ...], as_json: bool) -> None:
    """Explain a gptme concept, offline.

    Run without arguments to list the available topics.
    """
    entries = load_faq()
    query_str = " ".join(query).strip()

    if not query_str:
        if as_json:
            click.echo(json.dumps([e.to_dict() for e in entries], indent=2))
            return
        if not entries:
            click.echo("No topics available.")
            return
        click.echo("Topics (use: gptme explain <topic>)\n")
        width = max(len(e.topic) for e in entries)
        for e in entries:
            click.echo(f"  {e.topic:<{width}}  {e.question}")
        return

    entry = find_topic(query_str, entries)
    if entry is None:
        suggestions = suggest_topics(query_str, entries) or [e.topic for e in entries]
        if as_json:
            click.echo(
                json.dumps(
                    {"query": query_str, "match": None, "suggestions": suggestions}
                )
            )
        else:
            click.echo(f"No topic matches {query_str!r}.", err=True)
            if suggestions:
                click.echo(f"Did you mean: {', '.join(suggestions)}?", err=True)
            else:
                click.echo("No topics available.", err=True)
        raise SystemExit(1)

    if as_json:
        click.echo(json.dumps(entry.to_dict(), indent=2))
    else:
        click.echo(format_entry(entry))
