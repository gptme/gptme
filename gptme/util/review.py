"""Shared data types for the gptme review pipeline (gptme#3442).

The review pipeline is a two-stage autonomous loop:

    pr_review (gptme-contrib)     →   review-watch (gptme)
    ─────────────────────────         ─────────────────────
    AI reviews PR diff                AI acts as author
    Emits structured findings         Reads comments → fixes
    ReviewArtifact produced           ReviewArtifact consumed (optional)

``ReviewArtifact`` is the structured JSON handoff between the two stages.
When ``pr_review`` produces one, ``review-watch`` can consume it for
richer context (exact file/line, severity, confirmed/dropped status).

All fields are optional so the artifact degrades gracefully to the
plain-text comment path when not available.

What this module is for
-----------------------
This is the **canonical schema** for review findings and artifacts, and it
backs the model-facing primitive ``gptme-util review pr`` (diff in,
``ReviewArtifact`` out). Downstream reviewers are expected to emit *this*
schema rather than define their own.

What this module is NOT for
---------------------------
It deliberately contains **no forge plumbing** — no GitHub/Forgejo API calls,
no comment posting, no idempotency markers. ``review pr`` writes nothing; it
produces an artifact and exits. Publishing findings to a forge is out of scope
for gptme core and belongs in a downstream adapter.

Related implementations
-----------------------
Three PR-review implementations exist across three repos, and it is easy to
land in the wrong one:

- **this module + ``gptme/cli/cmd_review_pr.py``** (gptme core, public) — the
  schema and the diff→findings primitive. No forge writes, by design.
- **``gptme_runloops.pr_review``** (gptme-contrib) — forge-neutral local-diff
  entry point (``review --working-tree``): review uncommitted work with no
  forge calls at all.
- **Bob's ``scripts/github/ai-review.py``** (private brain repo) — the only
  one that posts findings to GitHub in production (marker + inline comments,
  consensus filtering, suppression ledger).

Which one do you want?

- Reviewing a PR diff, or building something that consumes findings: use
  ``gptme-util review pr`` and this module's schema.
- Reviewing uncommitted work in your own checkout, no forge involved: use
  ``review --working-tree`` from gptme-contrib.
- Publishing findings back to a forge as review comments: not in gptme core.
  Consume a ``ReviewArtifact`` from either of the above and post it from your
  own adapter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path


class FindingSeverity(str, Enum):
    """Severity levels for review findings, ordered low → high."""

    NOTE = "note"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class FindingStatus(str, Enum):
    """Lifecycle status of a finding during the review-watch fix loop."""

    OPEN = "open"
    """Finding has not been addressed yet."""
    CONFIRMED = "confirmed"
    """Reviewer confirmed the fix is correct."""
    DROPPED = "dropped"
    """Finding was intentionally not acted on (e.g. won't-fix, out-of-scope)."""
    IN_PROGRESS = "in_progress"
    """A fix session is currently working on this finding."""


class MergeSafety(str, Enum):
    """Merge-safety verdict carried by a forge-neutral review artifact.

    Mirrors the ``MergeSafety`` enum in gptme-contrib's ``pr_review`` schema so
    a forge-neutral artifact can be ingested without losing its merge verdict.
    """

    SAFE = "safe"
    UNSAFE = "unsafe"
    NEEDS_REVIEW = "needs_review"
    UNKNOWN = "unknown"


@dataclass
class ReviewFinding:
    """A single review finding produced by an AI reviewer.

    Corresponds to one inline PR comment or review note.  ``file`` and
    ``line`` mirror the GitHub PR review comment fields so the data can be
    round-tripped to/from the API without loss.
    """

    #: Short description of the finding (matches the reviewer comment body
    #: when the artifact is produced from existing review comments).
    body: str

    #: File path relative to repo root, or empty string for PR-level findings.
    file: str = ""

    #: Line number (1-based) in the file, or ``None`` for file-level comments.
    line: int | None = None

    #: Severity of the finding.
    severity: FindingSeverity = FindingSeverity.WARNING

    #: Current lifecycle status.
    status: FindingStatus = FindingStatus.OPEN

    #: GitHub review comment ID, if produced from an API comment.
    github_comment_id: int | None = None

    #: Optional reviewer login for attribution.
    reviewer: str = ""

    #: Category/class of the finding (e.g. "bug", "security", "style").
    #: Sourced from the ``class`` field in local-review artifacts or ``category``
    #: in forge-neutral artifacts.  Empty string when not provided.
    category: str = ""

    #: Number of independent review passes that agreed on this finding.
    #: 0 means unknown or not applicable (single-pass review).
    agreement: int = 0

    #: Unique fingerprint for this finding used for suppression and deduplication.
    #: Sourced from ``independence_key`` in local-review artifacts or ``id`` in
    #: forge-neutral artifacts.  Empty string when not provided.
    independence_key: str = ""

    #: Total number of review passes that evaluated this finding (including passes
    #: that disagreed).  0 means unknown or single-pass.  Combined with
    #: ``agreement`` to compute a consensus rate: ``agreement / passes``.
    passes: int = 0

    #: Quoted text from the diff that supports this finding.  Empty when the
    #: reviewer did not produce a quote.
    quote: str = ""

    #: Suggested fix text from the reviewer.  Empty when not provided.
    suggestion: str = ""

    #: Explanation of how the reviewer verified the finding (e.g. code search,
    #: schema check, test run).  Empty when not provided.
    verification: str = ""

    #: True when the reviewer classified this as a security-relevant finding.
    security: bool = False

    def to_dict(self) -> dict:
        d: dict = {
            "body": self.body,
            "file": self.file,
            "line": self.line,
            "severity": self.severity.value,
            "status": self.status.value,
            "github_comment_id": self.github_comment_id,
            "reviewer": self.reviewer,
        }
        if self.category:
            d["category"] = self.category
        if self.agreement:
            d["agreement"] = self.agreement
        if self.independence_key:
            d["independence_key"] = self.independence_key
        if self.passes:
            d["passes"] = self.passes
        if self.quote:
            d["quote"] = self.quote
        if self.suggestion:
            d["suggestion"] = self.suggestion
        if self.verification:
            d["verification"] = self.verification
        if self.security:
            d["security"] = self.security
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ReviewFinding:
        if not isinstance(d, dict):
            raise TypeError(
                f"ReviewFinding.from_dict expects a dict, got {type(d).__name__!r}"
            )
        file_raw = d.get("file", "")
        if not isinstance(file_raw, str):
            raise ValueError(
                f"ReviewFinding.file must be a string, got {type(file_raw).__name__!r}: {file_raw!r}"
            )
        line_raw = d.get("line")
        # JSON Schema integers include numbers with a zero fractional part.
        if isinstance(line_raw, float) and line_raw.is_integer():
            line_raw = int(line_raw)
        if line_raw is not None and type(line_raw) is not int:
            raise ValueError(
                f"ReviewFinding.line must be an int or None, got {type(line_raw).__name__!r}: {line_raw!r}"
            )
        try:
            severity = FindingSeverity(d.get("severity", FindingSeverity.WARNING.value))
        except (TypeError, ValueError):
            severity = FindingSeverity.WARNING
        try:
            status = FindingStatus(d.get("status", FindingStatus.OPEN.value))
        except (TypeError, ValueError):
            status = FindingStatus.OPEN
        body_raw = d.get("body", "")
        if not isinstance(body_raw, str):
            raise ValueError(
                f"ReviewFinding.body must be a string, got {type(body_raw).__name__!r}: {body_raw!r}"
            )
        category_raw = d.get("category", "")
        category = category_raw if isinstance(category_raw, str) else ""
        agreement_raw = d.get("agreement", 0)
        if isinstance(agreement_raw, float) and agreement_raw.is_integer():
            agreement_raw = int(agreement_raw)
        if type(agreement_raw) is not int:
            raise ValueError(
                f"ReviewFinding.agreement must be an int, got {type(agreement_raw).__name__!r}: {agreement_raw!r}"
            )
        agreement = agreement_raw
        independence_key_raw = d.get("independence_key", "")
        independence_key = (
            independence_key_raw if isinstance(independence_key_raw, str) else ""
        )
        passes_raw = d.get("passes", 0)
        if isinstance(passes_raw, float) and passes_raw.is_integer():
            passes_raw = int(passes_raw)
        if type(passes_raw) is not int:
            raise ValueError(
                f"ReviewFinding.passes must be an int, got {type(passes_raw).__name__!r}: {passes_raw!r}"
            )
        passes = passes_raw
        quote_raw = d.get("quote", "")
        quote = quote_raw if isinstance(quote_raw, str) else ""
        suggestion_raw = d.get("suggestion", "")
        suggestion = suggestion_raw if isinstance(suggestion_raw, str) else ""
        verification_raw = d.get("verification", "")
        verification = verification_raw if isinstance(verification_raw, str) else ""
        security = bool(d.get("security", False))
        return cls(
            body=body_raw,
            file=file_raw,
            line=line_raw,
            severity=severity,
            status=status,
            github_comment_id=d.get("github_comment_id"),
            reviewer=d.get("reviewer", ""),
            category=category,
            agreement=agreement,
            independence_key=independence_key,
            passes=passes,
            quote=quote,
            suggestion=suggestion,
            verification=verification,
            security=security,
        )

    @classmethod
    def from_github_comment(
        cls,
        comment: dict,
        *,
        severity: FindingSeverity = FindingSeverity.WARNING,
    ) -> ReviewFinding:
        """Construct a finding from a raw GitHub PR review comment dict."""
        return cls(
            body=comment.get("body", "").strip(),
            file=comment.get("path", ""),
            line=comment.get("original_line") or comment.get("line"),
            severity=severity,
            status=FindingStatus.OPEN,
            github_comment_id=comment.get("id"),
            reviewer=comment.get("user", {}).get("login", ""),
        )


class ReviewStatus(str, Enum):
    """Status of a review pass indicating whether it completed fully."""

    COMPLETE = "complete"
    """Review completed successfully and all findings were extracted cleanly."""
    INCOMPLETE = "incomplete"
    """Review session failed or timed out, but some findings were extracted from partial output."""


#: Severity translation for the forge-neutral (gptme-contrib) artifact schema.
#: That schema uses five levels ordered low → high; this module uses four. The
#: mapping is lossy by design and exists only for the migration reader below.
_FORGE_NEUTRAL_SEVERITY: dict[str, FindingSeverity] = {
    "critical": FindingSeverity.CRITICAL,
    "high": FindingSeverity.ERROR,
    "medium": FindingSeverity.WARNING,
    "low": FindingSeverity.NOTE,
    "info": FindingSeverity.NOTE,
}

#: Disposition translation for the forge-neutral artifact schema.
#:
#: The two vocabularies disagree on ``confirmed``: in the forge-neutral schema it
#: means the reviewer validated the finding as a *real defect*, whereas core's
#: ``FindingStatus.CONFIRMED`` means the reviewer confirmed the *fix is correct*.
#: Mapping one to the other would exclude live bugs from ``open_findings`` and
#: make review-watch report "nothing to fix". Core has no "validated, unfixed"
#: state, so both actionable dispositions collapse to ``OPEN``.
_FORGE_NEUTRAL_DISPOSITION: dict[str, FindingStatus] = {
    "confirmed": FindingStatus.OPEN,
    "needs_validation": FindingStatus.OPEN,
    "dropped": FindingStatus.DROPPED,
}


def _is_forge_neutral(d: dict) -> bool:
    """Detect gptme-contrib's forge-neutral ``pr_review`` artifact shape.

    That schema declares an immutable ``target`` object (hosted PR or local
    checkout) and a string ``schema_version`` (``"v1"``); this module's own
    artifacts use an integer ``schema_version`` and a ``pr`` object. The two
    shapes are unambiguous, so detection is safe.
    """
    return isinstance(d.get("target"), dict) and not isinstance(
        d.get("schema_version"), int
    )


def _is_local_review(d: dict) -> bool:
    """Detect Bob's local-review artifact shape (``kind: "ai-review-local"``).

    That schema has an integer ``schema_version`` (1) and a top-level ``kind``
    field set to ``"ai-review-local"``.  It does NOT have a nested ``pr`` object;
    PR coordinates live at the top level as ``repo: "owner/repo"``.
    """
    return d.get("kind") == "ai-review-local"


#: Severity translation for Bob's local-review artifact (``kind: "ai-review-local"``).
#: The producer (gptme-contrib ``gptme_review``) emits only ``P0``/``P1``/``P2``,
#: worst first, and maps core severities back with ``critical→P0``,
#: ``error→P1``, ``warning→P2``. This table is that inverse, so a finding keeps
#: its exact Px label across a round trip. In particular, a non-blocking P2 must
#: not come back as a blocking P1. Any other label fails closed.
_LOCAL_REVIEW_SEVERITY: dict[str, FindingSeverity] = {
    "p0": FindingSeverity.CRITICAL,
    "p1": FindingSeverity.ERROR,
    "p2": FindingSeverity.WARNING,
}


def _parse_line_range(raw: object) -> int | None:
    """Extract the first line number from a ``"42"`` or ``"42-49"`` range."""
    if not isinstance(raw, str):
        return None
    head = raw.split("-", 1)[0].strip()
    try:
        return int(head)
    except ValueError:
        return None


def _forge_neutral_body(finding: dict) -> str:
    """Compose a finding body from the forge-neutral title/description/evidence.

    The forge-neutral schema splits a finding across several mandatory prose
    fields; this module carries a single ``body``, so they are joined in
    review order. Returns an empty string when nothing usable is present,
    which the reader treats as a validation error.
    """
    parts = [
        value.strip()
        for key in ("title", "description", "evidence")
        if isinstance((value := finding.get(key)), str) and value.strip()
    ]
    return "\n\n".join(parts)


@dataclass
class ReviewArtifact:
    """Structured output of a review pass, consumed by review-watch.

    Produced by ``pr_review`` (gptme-contrib) and optionally consumed by
    ``review-watch`` to give the fix session exact coordinates (file, line,
    severity) instead of raw comment text.

    The artifact can be serialised to/from JSON for disk persistence and
    inter-process handoff.

    ``from_dict`` also accepts the richer *forge-neutral* artifact emitted by
    gptme-contrib's ``pr_review`` (a string ``schema_version``, an immutable
    ``target``, per-finding ``disposition``, and a ``merge_safety`` verdict).
    That shape is mapped onto this schema by :meth:`_from_forge_neutral` so
    ``review-watch --artifact`` can consume it directly; unmappable input is
    marked ``INCOMPLETE`` rather than silently treated as a clean review.

    Example JSON schema::

        {
          "schema_version": 2,
          "pr": {"owner": "gptme", "repo": "gptme", "number": 1234},
          "review_status": "complete",
          "findings": [
            {
              "body": "This variable name is unclear.",
              "file": "gptme/util/review.py",
              "line": 42,
              "severity": "warning",
              "status": "open",
              "github_comment_id": 99887766,
              "reviewer": "ErikBjare"
            }
          ]
        }
    """

    #: The PR this artifact was produced for.
    pr_owner: str
    pr_repo: str
    pr_number: int

    #: Findings from the review pass.
    findings: list[ReviewFinding] = field(default_factory=list)

    #: Whether the review completed fully or was interrupted.
    review_status: ReviewStatus = ReviewStatus.COMPLETE

    #: Merge-safety verdict from the reviewer, when one was produced.
    merge_safety: MergeSafety = MergeSafety.UNKNOWN

    #: Exit reason from the reviewer session ("done", "error", "timeout").
    session_exit_reason: str = ""

    #: Error message from the reviewer session, if any.
    session_error: str = ""

    #: Duration of the review session in seconds.
    review_duration_s: float = 0.0

    #: Number of findings entries that were skipped due to validation errors.
    validation_errors: int = 0

    #: Kind of review target.
    #: ``"hosted"`` = PR on a forge; ``"local"`` = working-tree review (no PR);
    #: ``"ai-review-local"`` = Bob's production local reviewer.
    #: Empty string when not provided by the producer.
    target_kind: str = ""

    #: Diff fingerprint: head commit SHA (for hosted reviews) or a
    #: ``"<base>/<merge_base>/<head>"`` triple (for local-review artifacts).
    #: Empty string when not provided.
    diff_fingerprint: str = ""

    #: Count of findings the reviewer produced but rejected before the final
    #: list (e.g., unverified, misquoted, out-of-scope).  Zero when not tracked.
    rejected_finding_count: int = 0

    #: Full rejected finding objects from the production writer.  Each element
    #: is a raw dict from the reviewer's output (same shape as a finding entry
    #: but with a ``reason`` field explaining why it was rejected).  Empty when
    #: not provided; ``rejected_finding_count`` remains authoritative for the
    #: count when this list is absent.
    rejected_findings: list[dict] = field(default_factory=list)

    #: Per-category drop counts from the reviewer's suppression ledger.
    #: Keys match the producer's vocabulary (e.g. ``"unverified_absence"``,
    #: ``"misquoted"``).  Empty dict when not tracked.
    dropped_counts: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Production evidence fields (from "ai-review-local" shape)
    # ------------------------------------------------------------------

    #: Overall reviewer score (0–10 integer).  ``None`` when not produced.
    score: int | None = None

    #: Paths passed to the reviewer.  ``None`` when not specified (full diff).
    paths: list[str] | None = None

    #: Whether the review included uncommitted working-tree changes.
    includes_uncommitted: bool = False

    #: Findings reported in the reviewer's structured summary but absent from
    #: the canonical ``findings`` list (e.g. suppressed before dedup).
    summary_only_findings: list[dict] = field(default_factory=list)

    #: Findings present only in the suppression ledger (carried forward from
    #: a prior pass, not newly surfaced).
    ledger_only_findings: list[dict] = field(default_factory=list)

    #: Multi-pass consensus metadata (requested/answered pass counts, min
    #: agreement threshold, etc.).  ``None`` for single-pass reviews.
    consensus: dict | None = None

    #: Context coverage report: which diff paths were fully preloaded vs
    #: truncated.  ``None`` when not tracked.
    context_coverage: dict | None = None

    # ------------------------------------------------------------------
    # Target identity fields (from "ai-review-local" shape)
    # ------------------------------------------------------------------

    #: Head commit SHA of the reviewed diff.  Empty when not provided.
    head: str = ""

    #: Base commit SHA (merge base's parent) of the reviewed diff.  Empty when not provided.
    base: str = ""

    #: Three-way merge base SHA.  Empty when not provided.
    merge_base: str = ""

    #: ISO-8601 timestamp when the review was completed.  Empty when not provided.
    reviewed_at: str = ""

    #: Model identifier used by the reviewer (e.g. ``"claude-opus-5-5"``).
    #: Empty when not provided.
    model: str = ""

    #: Inference engine/provider identifier (e.g. ``"anthropic"``, ``"openrouter"``).
    #: Empty when not provided.
    engine: str = ""

    #: Schema version for forward-compatibility.
    schema_version: Literal[2] = 2

    # ------------------------------------------------------------------
    # Derived views
    # ------------------------------------------------------------------

    @property
    def open_findings(self) -> list[ReviewFinding]:
        """Return only findings that have not yet been addressed."""
        return [f for f in self.findings if f.status == FindingStatus.OPEN]

    @property
    def confirmed_count(self) -> int:
        return sum(1 for f in self.findings if f.status == FindingStatus.CONFIRMED)

    @property
    def dropped_count(self) -> int:
        return sum(1 for f in self.findings if f.status == FindingStatus.DROPPED)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        d: dict = {
            "schema_version": self.schema_version,
            "pr": {
                "owner": self.pr_owner,
                "repo": self.pr_repo,
                "number": self.pr_number,
            },
            "review_status": self.review_status.value,
            "merge_safety": self.merge_safety.value,
            "session_exit_reason": self.session_exit_reason,
            "session_error": self.session_error,
            "review_duration_s": self.review_duration_s,
            "validation_errors": self.validation_errors,
            "findings": [f.to_dict() for f in self.findings],
        }
        if self.target_kind:
            d["target_kind"] = self.target_kind
        if self.diff_fingerprint:
            d["diff_fingerprint"] = self.diff_fingerprint
        if self.rejected_finding_count:
            d["rejected_finding_count"] = self.rejected_finding_count
        if self.rejected_findings:
            d["rejected_findings"] = self.rejected_findings
        if self.dropped_counts:
            d["dropped_counts"] = self.dropped_counts
        if self.score is not None:
            d["score"] = self.score
        if self.paths is not None:
            d["paths"] = self.paths
        if self.includes_uncommitted:
            d["includes_uncommitted"] = self.includes_uncommitted
        if self.summary_only_findings:
            d["summary_only_findings"] = self.summary_only_findings
        if self.ledger_only_findings:
            d["ledger_only_findings"] = self.ledger_only_findings
        if self.consensus is not None:
            d["consensus"] = self.consensus
        if self.context_coverage is not None:
            d["context_coverage"] = self.context_coverage
        if self.head:
            d["head"] = self.head
        if self.base:
            d["base"] = self.base
        if self.merge_base:
            d["merge_base"] = self.merge_base
        if self.reviewed_at:
            d["reviewed_at"] = self.reviewed_at
        if self.model:
            d["model"] = self.model
        if self.engine:
            d["engine"] = self.engine
        return d

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: Path) -> None:
        """Persist the artifact to a JSON file."""
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict) -> ReviewArtifact:
        if not isinstance(d, dict):
            raise TypeError(
                f"ReviewArtifact.from_dict expects a dict, got {type(d).__name__!r}"
            )
        if _is_forge_neutral(d):
            return cls._from_forge_neutral(d)
        if _is_local_review(d):
            return cls._from_local_review(d)
        pr = d.get("pr", {})
        if not isinstance(pr, dict):
            pr = {}
        review_status_raw = d.get("review_status", ReviewStatus.COMPLETE.value)
        try:
            review_status = ReviewStatus(review_status_raw)
        except (TypeError, ValueError):
            # Fail-safe: an unrecognised or container-typed status value is treated as
            # INCOMPLETE rather than COMPLETE so that the review-watch guard is not
            # silently bypassed when an artifact was produced by a newer tool version
            # with new status values, or when the field holds a malformed non-string value.
            review_status = ReviewStatus.INCOMPLETE

        # Deserialise findings one-by-one so a malformed entry does not crash the
        # whole load.  Each validation error downgrades the artifact to INCOMPLETE.
        deserialization_errors = 0
        try:
            stored_validation_errors = int(d.get("validation_errors", 0))
        except (TypeError, ValueError):
            stored_validation_errors = 0
            deserialization_errors += 1
        findings: list[ReviewFinding] = []
        findings_raw = d.get("findings", [])
        if not isinstance(findings_raw, list):
            # Non-list findings container (null, int, dict …): treat as a deserialization
            # error so the artifact is marked INCOMPLETE instead of crashing.
            findings_raw = []
            deserialization_errors += 1
        for f_raw in findings_raw:
            try:
                findings.append(ReviewFinding.from_dict(f_raw))
            except (ValueError, TypeError, KeyError, AttributeError):
                deserialization_errors += 1

        if deserialization_errors > 0 and review_status == ReviewStatus.COMPLETE:
            review_status = ReviewStatus.INCOMPLETE

        # Coerce numeric and string metadata fields — guard against container values
        # (list, dict …) that would otherwise be stored as-is and cause type errors
        # downstream when the artifact is used (e.g., string formatting, logging).
        pr_number_raw = pr.get("number", 0)
        if isinstance(pr_number_raw, bool):
            pr_number_val = 0
            deserialization_errors += 1
        else:
            try:
                pr_number_val = int(pr_number_raw)
            except (TypeError, ValueError):
                pr_number_val = 0
                deserialization_errors += 1

        try:
            review_duration_s = float(d.get("review_duration_s", 0.0))
        except (TypeError, ValueError):
            review_duration_s = 0.0
            deserialization_errors += 1

        # Coerce pr.owner and pr.repo to strings — if they're containers
        # (lists, dicts), set them to empty string and count as an error.
        pr_owner_val = pr.get("owner", "")
        if not isinstance(pr_owner_val, str):
            pr_owner_val = ""
            deserialization_errors += 1

        pr_repo_val = pr.get("repo", "")
        if not isinstance(pr_repo_val, str):
            pr_repo_val = ""
            deserialization_errors += 1

        merge_safety = MergeSafety.UNKNOWN
        merge_safety_raw = d.get("merge_safety")
        if merge_safety_raw is not None:
            try:
                merge_safety = MergeSafety(merge_safety_raw)
            except (TypeError, ValueError):
                # An unrecognised verdict is not gate-relevant on its own, but it
                # signals a producer/schema mismatch — count it like any other
                # coercion error so the artifact is not reported as COMPLETE.
                deserialization_errors += 1
                review_status = ReviewStatus.INCOMPLETE

        target_kind_raw = d.get("target_kind", "")
        target_kind = target_kind_raw if isinstance(target_kind_raw, str) else ""
        diff_fingerprint_raw = d.get("diff_fingerprint", "")
        diff_fingerprint = (
            diff_fingerprint_raw if isinstance(diff_fingerprint_raw, str) else ""
        )
        rejected_finding_count_raw = d.get("rejected_finding_count", 0)
        try:
            rejected_finding_count = int(rejected_finding_count_raw)
        except (TypeError, ValueError):
            rejected_finding_count = 0
            deserialization_errors += 1
        rejected_findings_raw = d.get("rejected_findings", [])
        rejected_findings = (
            [f for f in rejected_findings_raw if isinstance(f, dict)]
            if isinstance(rejected_findings_raw, list)
            else []
        )
        dropped_counts_raw = d.get("dropped_counts", {})
        dropped_counts = (
            dropped_counts_raw if isinstance(dropped_counts_raw, dict) else {}
        )

        # Production evidence fields (optional, pass-through)
        score_raw = d.get("score")
        score: int | None = None
        if (
            isinstance(score_raw, (int, float))
            and not (score_raw != score_raw)
            and abs(score_raw) != float("inf")
        ):
            score = int(score_raw)

        paths_raw = d.get("paths")
        paths: list[str] | None = None
        if isinstance(paths_raw, list):
            paths = [p for p in paths_raw if isinstance(p, str)]

        includes_uncommitted = bool(d.get("includes_uncommitted", False))

        summary_only_raw = d.get("summary_only_findings", [])
        summary_only_findings = (
            [f for f in summary_only_raw if isinstance(f, dict)]
            if isinstance(summary_only_raw, list)
            else []
        )

        ledger_only_raw = d.get("ledger_only_findings", [])
        ledger_only_findings = (
            [f for f in ledger_only_raw if isinstance(f, dict)]
            if isinstance(ledger_only_raw, list)
            else []
        )

        consensus_raw = d.get("consensus")
        consensus = consensus_raw if isinstance(consensus_raw, dict) else None

        context_coverage_raw = d.get("context_coverage")
        context_coverage = (
            context_coverage_raw if isinstance(context_coverage_raw, dict) else None
        )

        head_raw = d.get("head", "")
        head = head_raw if isinstance(head_raw, str) else ""
        base_raw = d.get("base", "")
        base = base_raw if isinstance(base_raw, str) else ""
        merge_base_raw = d.get("merge_base", "")
        merge_base = merge_base_raw if isinstance(merge_base_raw, str) else ""
        reviewed_at_raw = d.get("reviewed_at", "")
        reviewed_at = reviewed_at_raw if isinstance(reviewed_at_raw, str) else ""
        model_raw = d.get("model", "")
        model = model_raw if isinstance(model_raw, str) else ""
        engine_raw = d.get("engine", "")
        engine = engine_raw if isinstance(engine_raw, str) else ""

        # Re-evaluate status after all metadata coercion, including suppression counts.
        if deserialization_errors > 0 and review_status == ReviewStatus.COMPLETE:
            review_status = ReviewStatus.INCOMPLETE

        return cls(
            pr_owner=pr_owner_val,
            pr_repo=pr_repo_val,
            pr_number=pr_number_val,
            findings=findings,
            review_status=review_status,
            merge_safety=merge_safety,
            session_exit_reason=d.get("session_exit_reason", ""),
            session_error=d.get("session_error", ""),
            review_duration_s=review_duration_s,
            validation_errors=stored_validation_errors + deserialization_errors,
            target_kind=target_kind,
            diff_fingerprint=diff_fingerprint,
            rejected_finding_count=rejected_finding_count,
            rejected_findings=rejected_findings,
            dropped_counts=dropped_counts,
            score=score,
            paths=paths,
            includes_uncommitted=includes_uncommitted,
            summary_only_findings=summary_only_findings,
            ledger_only_findings=ledger_only_findings,
            consensus=consensus,
            context_coverage=context_coverage,
            head=head,
            base=base,
            merge_base=merge_base,
            reviewed_at=reviewed_at,
            model=model,
            engine=engine,
        )

    @classmethod
    def _from_forge_neutral(cls, d: dict) -> ReviewArtifact:
        """Ingest a gptme-contrib forge-neutral ``pr_review`` artifact.

        Maps the richer, forge-neutral schema (immutable ``target``,
        per-finding ``disposition``, ``merge_safety`` verdict) onto this
        module's canonical schema, so ``review-watch --artifact`` can consume a
        production reviewer artifact without a bespoke adapter.

        Fields this schema does not model (``confidence``, ``fix_hint``,
        per-run model/prompt/timestamps) are deliberately dropped: they are not
        gate-relevant to ``review-watch``. The mapping is **fail-closed** — any
        unmappable target, finding, or verdict marks the artifact
        ``INCOMPLETE`` rather than returning a partial, apparently-complete
        review (which review-watch would otherwise treat as clean).

        ``category`` is preserved as :attr:`ReviewFinding.category` so
        ``review-watch`` can group findings by class.

        A *local* target (a working-tree review with no PR) maps to empty PR
        coordinates; ``review-watch`` requires a hosted PR and will still need
        ``--repo``/``--pr`` supplied explicitly.
        """
        errors = 0

        target = d.get("target")
        target = target if isinstance(target, dict) else {}
        pr_owner = ""
        pr_repo = ""
        pr_number = 0
        if target.get("kind") == "local":
            repo_name = target.get("repo_name", "")
            if isinstance(repo_name, str) and "/" in repo_name:
                pr_owner, pr_repo = repo_name.split("/", 1)
            elif isinstance(repo_name, str):
                pr_repo = repo_name
        else:
            repo = target.get("repo", "")
            if isinstance(repo, str) and "/" in repo:
                pr_owner, pr_repo = repo.split("/", 1)
            else:
                errors += 1
            number = target.get("pr_number", 0)
            if type(number) is int:
                pr_number = number
            else:
                errors += 1

        findings: list[ReviewFinding] = []
        # ``findings`` is required by the forge-neutral schema: an absent field is
        # a malformed artifact, not a clean review with zero findings. An
        # explicitly empty list is still valid.
        findings_raw = d.get("findings")
        if not isinstance(findings_raw, list):
            findings_raw = []
            errors += 1
        for f_raw in findings_raw:
            if not isinstance(f_raw, dict):
                errors += 1
                continue
            body = _forge_neutral_body(f_raw)
            if not body:
                # No prose means the finding cannot be acted on; count it like
                # any other skipped finding rather than emitting an empty body.
                errors += 1
                continue
            file_raw = f_raw.get("file_path", "")
            category_raw = f_raw.get("category", "")
            category = category_raw if isinstance(category_raw, str) else ""
            finding_id = f_raw.get("id", "")
            independence_key = finding_id if isinstance(finding_id, str) else ""
            severity = _FORGE_NEUTRAL_SEVERITY.get(
                str(f_raw.get("severity", "")).lower()
            )
            status = _FORGE_NEUTRAL_DISPOSITION.get(
                str(f_raw.get("disposition", "")).lower()
            )
            if severity is None or status is None:
                # Fail-closed: an unrecognized severity or disposition would
                # otherwise be silently downgraded (WARNING/OPEN), hiding a
                # potentially critical finding from review-watch.
                errors += 1
                continue
            findings.append(
                ReviewFinding(
                    body=body,
                    file=file_raw if isinstance(file_raw, str) else "",
                    line=_parse_line_range(f_raw.get("line_range")),
                    severity=severity,
                    status=status,
                    category=category,
                    independence_key=independence_key,
                )
            )

        merge_safety = MergeSafety.UNKNOWN
        merge_raw = d.get("merge_safety")
        if merge_raw is not None:
            try:
                merge_safety = MergeSafety(merge_raw)
            except (TypeError, ValueError):
                errors += 1

        # Capture diff fingerprint from the forge-neutral target's head SHA or
        # pre-computed diff fingerprint if present.
        target_kind_raw = target.get("kind", "")
        forge_target_kind = target_kind_raw if isinstance(target_kind_raw, str) else ""
        head_sha = target.get("head_sha", "") or target.get("diff_fingerprint", "")
        diff_fingerprint = head_sha if isinstance(head_sha, str) else ""

        return cls(
            pr_owner=pr_owner,
            pr_repo=pr_repo,
            pr_number=pr_number,
            findings=findings,
            review_status=(
                ReviewStatus.INCOMPLETE if errors else ReviewStatus.COMPLETE
            ),
            merge_safety=merge_safety,
            validation_errors=errors,
            target_kind=forge_target_kind,
            diff_fingerprint=diff_fingerprint,
        )

    @classmethod
    def _from_local_review(cls, d: dict) -> ReviewArtifact:
        """Ingest Bob's local-review artifact (``kind: "ai-review-local"``).

        Maps P0/P1/P2 severity to the canonical ``FindingSeverity`` enum and
        extracts PR coordinates from the top-level ``repo`` field.  The ``class``
        field is preserved as ``category``; ``agreement`` (multi-pass consensus
        count) is carried through to ``ReviewFinding.agreement``.

        Fail-closed: any unrecognised severity, non-string ``file``, or
        non-integer ``line``/``agreement`` counts as a deserialization error and marks the
        artifact ``INCOMPLETE`` rather than silently downgrading a P1 to
        ``warning`` or dropping a location while still reporting ``COMPLETE``.
        """
        errors = 0

        # PR coordinates: top-level "repo": "owner/repo", no PR number in the artifact
        repo_raw = d.get("repo", "")
        if isinstance(repo_raw, str) and "/" in repo_raw:
            pr_owner, pr_repo = repo_raw.split("/", 1)
        else:
            pr_owner = ""
            pr_repo = repo_raw if isinstance(repo_raw, str) else ""
            errors += 1
        pr_number = 0  # pre-PR / working-tree reviews have no PR number

        findings: list[ReviewFinding] = []
        findings_raw = d.get("findings")
        if not isinstance(findings_raw, list):
            findings_raw = []
            errors += 1
        for f_raw in findings_raw:
            if not isinstance(f_raw, dict):
                errors += 1
                continue
            body_raw = f_raw.get("body", "")
            if not isinstance(body_raw, str) or not body_raw:
                errors += 1
                continue
            # Fail-closed: unknown Px label → deserialization error, not a fallback.
            severity = _LOCAL_REVIEW_SEVERITY.get(
                str(f_raw.get("severity", "")).lower()
            )
            if severity is None:
                errors += 1
                continue
            file_raw = f_raw.get("file", "")
            if not isinstance(file_raw, str):
                # Mirror the core reader's fail-closed path handling: a
                # non-string path would otherwise be silently dropped while
                # the artifact still claimed COMPLETE.
                errors += 1
                file_raw = ""
            line_raw = f_raw.get("line")
            if line_raw is not None and type(line_raw) is not int:
                errors += 1
                line_raw = None
            category_raw = f_raw.get("class", "")
            category = category_raw if isinstance(category_raw, str) else ""
            agreement_raw = f_raw.get("agreement", 0)
            if type(agreement_raw) is not int:
                errors += 1
                agreement_raw = 0
            agreement = agreement_raw
            independence_key_raw = f_raw.get("independence_key", "")
            independence_key = (
                independence_key_raw if isinstance(independence_key_raw, str) else ""
            )
            passes_raw = f_raw.get("passes", 0)
            if type(passes_raw) is not int:
                errors += 1
                passes_raw = 0
            passes = passes_raw
            quote_raw = f_raw.get("quote", "")
            quote = quote_raw if isinstance(quote_raw, str) else ""
            suggestion_raw = f_raw.get("suggestion", "")
            suggestion = suggestion_raw if isinstance(suggestion_raw, str) else ""
            verification_raw = f_raw.get("verification", "")
            verification = verification_raw if isinstance(verification_raw, str) else ""
            security = bool(f_raw.get("security", False))
            findings.append(
                ReviewFinding(
                    body=body_raw,
                    file=file_raw if isinstance(file_raw, str) else "",
                    line=line_raw,
                    severity=severity,
                    status=FindingStatus.OPEN,
                    category=category,
                    agreement=agreement,
                    independence_key=independence_key,
                    passes=passes,
                    quote=quote,
                    suggestion=suggestion,
                    verification=verification,
                    security=security,
                )
            )

        # Artifact-level identity fields
        base_sha = d.get("base", "")
        merge_base_sha = d.get("merge_base", "")
        head_sha = d.get("head", "")
        base = base_sha if isinstance(base_sha, str) else ""
        merge_base = merge_base_sha if isinstance(merge_base_sha, str) else ""
        head = head_sha if isinstance(head_sha, str) else ""
        if base and merge_base and head:
            diff_fingerprint = "/".join(filter(None, [base, merge_base, head]))
        else:
            diff_fingerprint = ""
        reviewed_at_raw = d.get("reviewed_at", "")
        reviewed_at = reviewed_at_raw if isinstance(reviewed_at_raw, str) else ""
        model_raw = d.get("model", "")
        model = model_raw if isinstance(model_raw, str) else ""
        engine_raw = d.get("engine", "")
        engine = engine_raw if isinstance(engine_raw, str) else ""

        rejected_raw = d.get("rejected_findings", [])
        rejected_findings = (
            [f for f in rejected_raw if isinstance(f, dict)]
            if isinstance(rejected_raw, list)
            else []
        )
        # Count everything the writer reported, not just the objects we could
        # keep: a malformed entry must not silently under-count rejections.
        rejected_finding_count = (
            len(rejected_raw) if isinstance(rejected_raw, list) else 0
        )

        dropped_raw = d.get("dropped", {})
        dropped_counts = dropped_raw if isinstance(dropped_raw, dict) else {}

        # Production evidence fields
        score_raw = d.get("score")
        score: int | None = None
        if (
            isinstance(score_raw, (int, float))
            and not (score_raw != score_raw)
            and abs(score_raw) != float("inf")
        ):
            score = int(score_raw)

        paths_raw = d.get("paths")
        paths: list[str] | None = None
        if isinstance(paths_raw, list):
            paths = [p for p in paths_raw if isinstance(p, str)]

        includes_uncommitted_raw = d.get("includes_uncommitted", False)
        includes_uncommitted = bool(includes_uncommitted_raw)

        summary_only_raw = d.get("summary_only_findings", [])
        summary_only_findings = (
            [f for f in summary_only_raw if isinstance(f, dict)]
            if isinstance(summary_only_raw, list)
            else []
        )

        ledger_only_raw = d.get("ledger_only_findings", [])
        ledger_only_findings = (
            [f for f in ledger_only_raw if isinstance(f, dict)]
            if isinstance(ledger_only_raw, list)
            else []
        )

        consensus_raw = d.get("consensus")
        consensus = consensus_raw if isinstance(consensus_raw, dict) else None

        context_coverage_raw = d.get("context_coverage")
        context_coverage = (
            context_coverage_raw if isinstance(context_coverage_raw, dict) else None
        )

        return cls(
            pr_owner=pr_owner,
            pr_repo=pr_repo,
            pr_number=pr_number,
            findings=findings,
            review_status=(
                ReviewStatus.INCOMPLETE if errors else ReviewStatus.COMPLETE
            ),
            validation_errors=errors,
            target_kind="ai-review-local",
            diff_fingerprint=diff_fingerprint,
            rejected_finding_count=rejected_finding_count,
            rejected_findings=rejected_findings,
            dropped_counts=dropped_counts,
            score=score,
            paths=paths,
            includes_uncommitted=includes_uncommitted,
            summary_only_findings=summary_only_findings,
            ledger_only_findings=ledger_only_findings,
            consensus=consensus,
            context_coverage=context_coverage,
            head=head,
            base=base,
            merge_base=merge_base,
            reviewed_at=reviewed_at,
            model=model,
            engine=engine,
        )

    @classmethod
    def from_json(cls, text: str) -> ReviewArtifact:
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: Path) -> ReviewArtifact:
        """Load an artifact from a JSON file."""
        return cls.from_json(path.read_text(encoding="utf-8"))

    @classmethod
    def from_github_comments(
        cls,
        *,
        owner: str,
        repo: str,
        pr_number: int,
        inline_comments: list[dict],
        conversation_comments: list[dict],
    ) -> ReviewArtifact:
        """Build an artifact from raw GitHub API comment dicts.

        Inline PR review comments are treated as ``WARNING`` severity by
        default.  Conversation-level comments (PR thread, not inline) use
        ``NOTE`` severity to reflect their lower specificity.
        """
        findings: list[ReviewFinding] = [
            ReviewFinding.from_github_comment(c, severity=FindingSeverity.WARNING)
            for c in inline_comments
        ]
        findings.extend(
            ReviewFinding.from_github_comment(c, severity=FindingSeverity.NOTE)
            for c in conversation_comments
        )
        return cls(
            pr_owner=owner,
            pr_repo=repo,
            pr_number=pr_number,
            findings=findings,
        )

    def validate_schema(self) -> list[str]:
        """Run the lightweight v2 checks on this artifact's serialized form.

        Returns a list of violation strings.  An empty list means only the
        lightweight checks passed, not full JSON Schema conformance.  For
        full conformance, validate ``to_dict()`` against
        ``REVIEW_ARTIFACT_SCHEMA_V2`` with a draft-07-compatible validator.
        Callers that need a hard error should raise
        ``ValueError("\\n".join(violations))`` on a non-empty list.
        """
        return validate_artifact_dict(self.to_dict())


# ---------------------------------------------------------------------------
# Canonical JSON Schema (draft-07) for ReviewArtifact at schema_version=2.
#
# This dict is a machine-readable contract that producers and consumers can
# validate against.  It is intentionally kept in-module (rather than a
# separate .json file) so imports stay self-contained and the schema evolves
# in sync with the dataclasses above.
#
# Validation does NOT require the ``jsonschema`` package.  Use
# ``validate_artifact_dict()`` for a lightweight built-in check, or pass
# ``REVIEW_ARTIFACT_SCHEMA_V2`` to any draft-07-compatible JSON Schema
# validator for a full structural check.
# ---------------------------------------------------------------------------

REVIEW_ARTIFACT_SCHEMA_V2: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ReviewArtifact v2",
    "description": (
        "Structured review output produced by the gptme review pipeline "
        "(schema_version=2).  Consumed by review-watch and downstream adapters."
    ),
    "type": "object",
    "required": ["schema_version", "pr", "review_status", "findings"],
    "additionalProperties": True,
    "properties": {
        "schema_version": {"type": "integer", "const": 2},
        "pr": {
            "type": "object",
            "required": ["owner", "repo", "number"],
            "additionalProperties": False,
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                # Zero denotes local / pre-PR reviews.
                "number": {"type": "integer", "minimum": 0},
            },
        },
        "review_status": {
            "type": "string",
            "enum": [s.value for s in ReviewStatus],
        },
        "merge_safety": {
            "type": "string",
            "enum": [s.value for s in MergeSafety],
        },
        "session_exit_reason": {"type": "string"},
        "session_error": {"type": "string"},
        "review_duration_s": {"type": "number", "minimum": 0},
        "validation_errors": {"type": "integer", "minimum": 0},
        "target_kind": {"type": "string"},
        "diff_fingerprint": {"type": "string"},
        "rejected_finding_count": {"type": "integer", "minimum": 0},
        "rejected_findings": {"type": "array", "items": {"type": "object"}},
        "dropped_counts": {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        },
        "score": {"type": "integer", "minimum": 0, "maximum": 10},
        "paths": {
            "oneOf": [
                {"type": "array", "items": {"type": "string"}},
                {"type": "null"},
            ]
        },
        "includes_uncommitted": {"type": "boolean"},
        "summary_only_findings": {"type": "array", "items": {"type": "object"}},
        "ledger_only_findings": {"type": "array", "items": {"type": "object"}},
        "consensus": {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "requested": {"type": "integer", "minimum": 0},
                        "answered": {"type": "integer", "minimum": 0},
                        "min_agreement": {"type": "integer", "minimum": 0},
                        "rejected": {"type": "integer", "minimum": 0},
                    },
                },
                {"type": "null"},
            ]
        },
        "context_coverage": {"oneOf": [{"type": "object"}, {"type": "null"}]},
        "head": {"type": "string"},
        "base": {"type": "string"},
        "merge_base": {"type": "string"},
        "reviewed_at": {"type": "string"},
        "model": {"type": "string"},
        "engine": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["body", "severity", "status"],
                "properties": {
                    "body": {"type": "string"},
                    "file": {"type": "string"},
                    "line": {
                        "oneOf": [
                            {"type": "integer", "minimum": 1},
                            {"type": "null"},
                        ]
                    },
                    "severity": {
                        "type": "string",
                        "enum": [s.value for s in FindingSeverity],
                    },
                    "status": {
                        "type": "string",
                        "enum": [s.value for s in FindingStatus],
                    },
                    "github_comment_id": {
                        "oneOf": [{"type": "integer"}, {"type": "null"}]
                    },
                    "reviewer": {"type": "string"},
                    "category": {"type": "string"},
                    "agreement": {"type": "integer", "minimum": 0},
                    "independence_key": {"type": "string"},
                    "passes": {"type": "integer", "minimum": 0},
                    "quote": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "verification": {"type": "string"},
                    "security": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
    },
}


def validate_artifact_dict(d: dict) -> list[str]:
    """Run lightweight checks on a ReviewArtifact v2 dictionary.

    Returns a list of human-readable violation strings.  An empty list
    means only the lightweight checks passed, not full JSON Schema conformance.

    This lightweight validator covers the constraints that matter for safe
    inter-process handoff.  It does not require the ``jsonschema`` package.
    For a full structural check, pass ``REVIEW_ARTIFACT_SCHEMA_V2`` to any
    draft-07-compatible JSON Schema validator.
    """
    if not isinstance(d, dict):
        return [f"artifact must be a dict, got {type(d).__name__!r}"]

    violations: list[str] = []

    sv = d.get("schema_version")
    if sv != 2:
        violations.append(f"schema_version must be 2, got {sv!r}")

    pr = d.get("pr")
    if not isinstance(pr, dict):
        violations.append(f"'pr' must be a dict, got {type(pr).__name__!r}")
    else:
        for key in ("owner", "repo"):
            val = pr.get(key)
            if not isinstance(val, str):
                violations.append(
                    f"pr.{key} must be a string, got {type(val).__name__!r}"
                )
        num = pr.get("number")
        if not isinstance(num, int) or isinstance(num, bool):
            violations.append(
                f"pr.number must be an int, got {type(num).__name__!r}: {num!r}"
            )
        elif num < 0:
            violations.append(f"pr.number must be ≥0, got {num}")

    rs = d.get("review_status")
    valid_statuses = {s.value for s in ReviewStatus}
    if not isinstance(rs, str) or rs not in valid_statuses:
        violations.append(
            f"review_status must be one of {sorted(valid_statuses)!r}, got {rs!r}"
        )

    # Optional (mirrors the schema, which declares merge_safety with an enum but
    # does not require it; a missing value means UNKNOWN).
    ms = d.get("merge_safety")
    if ms is not None:
        valid_merge_safety = {s.value for s in MergeSafety}
        if not isinstance(ms, str) or ms not in valid_merge_safety:
            violations.append(
                f"merge_safety must be one of {sorted(valid_merge_safety)!r}, "
                f"got {ms!r}"
            )

    findings = d.get("findings")
    if not isinstance(findings, list):
        violations.append(f"'findings' must be a list, got {type(findings).__name__!r}")
    else:
        valid_severities = {s.value for s in FindingSeverity}
        valid_finding_statuses = {s.value for s in FindingStatus}
        for i, f_raw in enumerate(findings):
            if not isinstance(f_raw, dict):
                violations.append(
                    f"findings[{i}] must be a dict, got {type(f_raw).__name__!r}"
                )
                continue
            body = f_raw.get("body")
            if not isinstance(body, str):
                violations.append(
                    f"findings[{i}].body must be a string, got {type(body).__name__!r}"
                )
            sev = f_raw.get("severity")
            if not isinstance(sev, str) or sev not in valid_severities:
                violations.append(
                    f"findings[{i}].severity must be one of "
                    f"{sorted(valid_severities)!r}, got {sev!r}"
                )
            status = f_raw.get("status")
            if not isinstance(status, str) or status not in valid_finding_statuses:
                violations.append(
                    f"findings[{i}].status must be one of "
                    f"{sorted(valid_finding_statuses)!r}, got {status!r}"
                )

    return violations
