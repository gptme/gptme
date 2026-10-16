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
        agreement = int(agreement_raw) if type(agreement_raw) is int else 0
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
#: That schema uses Px labels ordered P1 (blocking) → P4 (nitpick).
_LOCAL_REVIEW_SEVERITY: dict[str, FindingSeverity] = {
    "p1": FindingSeverity.CRITICAL,
    "p2": FindingSeverity.ERROR,
    "p3": FindingSeverity.WARNING,
    "p4": FindingSeverity.NOTE,
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
        return {
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

        # Re-evaluate status after coercion errors — a malformed pr.owner,
        # pr.repo, pr.number or review_duration_s still counts as a deserialization problem.
        if deserialization_errors > 0 and review_status == ReviewStatus.COMPLETE:
            review_status = ReviewStatus.INCOMPLETE

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
                )
            )

        merge_safety = MergeSafety.UNKNOWN
        merge_raw = d.get("merge_safety")
        if merge_raw is not None:
            try:
                merge_safety = MergeSafety(merge_raw)
            except (TypeError, ValueError):
                errors += 1

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
        )

    @classmethod
    def _from_local_review(cls, d: dict) -> ReviewArtifact:
        """Ingest Bob's local-review artifact (``kind: "ai-review-local"``).

        Maps P1/P2/P3/P4 severity to the canonical ``FindingSeverity`` enum and
        extracts PR coordinates from the top-level ``repo`` field.  The ``class``
        field is preserved as ``category``; ``agreement`` (multi-pass consensus
        count) is carried through to ``ReviewFinding.agreement``.

        Fail-closed: any unrecognised severity, non-string ``file``, or
        non-integer ``line`` counts as a deserialization error and marks the
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
            agreement = int(agreement_raw) if type(agreement_raw) is int else 0
            findings.append(
                ReviewFinding(
                    body=body_raw,
                    file=file_raw if isinstance(file_raw, str) else "",
                    line=line_raw,
                    severity=severity,
                    status=FindingStatus.OPEN,
                    category=category,
                    agreement=agreement,
                )
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
