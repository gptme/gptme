"""Automatic lesson inclusion based on context."""

import json
import logging
import os
import random
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .index import LessonIndex
from .matcher import LessonMatcher, MatchContext

if TYPE_CHECKING:
    from ..message import Message

logger = logging.getLogger(__name__)

# Optional hybrid matching support
try:
    from .hybrid_matcher import HybridConfig, HybridLessonMatcher

    HYBRID_AVAILABLE = True
except ImportError:
    HYBRID_AVAILABLE = False
    logger.info("Hybrid matching not available, using keyword-only matching")

# Default token budget for lesson injection (50K tokens).
# Configurable via GPTME_LESSONS_TOKEN_BUDGET env var.
_DEFAULT_TOKEN_BUDGET = 50000


def _get_token_budget() -> int:
    """Get the lesson token budget from environment or default."""
    try:
        budget = int(
            os.environ.get("GPTME_LESSONS_TOKEN_BUDGET", str(_DEFAULT_TOKEN_BUDGET))
        )
        if budget <= 0:
            logger.warning(
                "GPTME_LESSONS_TOKEN_BUDGET=%d is non-positive, using default %d",
                budget,
                _DEFAULT_TOKEN_BUDGET,
            )
            return _DEFAULT_TOKEN_BUDGET
        return budget
    except (ValueError, TypeError):
        return _DEFAULT_TOKEN_BUDGET


def _estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Uses a simple character-based heuristic (~3 chars per token, conservative for
    code/markdown density). This is a rough estimate sufficient for budget
    enforcement — actual tokenization varies by model.
    """
    return max(1, len(text) // 3)


def _get_dropout_epsilon() -> float:
    """Get the randomized lesson-dropout probability from the environment.

    Controlled by ``LESSON_DROPOUT_EPSILON`` (float in [0, 1]). When > 0, each
    otherwise-matched lesson is independently withheld with this probability and
    the withheld set is logged for causal leave-one-out analysis. Default 0.0
    means no dropout (fully backwards compatible).
    """
    raw = os.environ.get("LESSON_DROPOUT_EPSILON")
    if not raw:
        return 0.0
    try:
        epsilon = float(raw)
    except (ValueError, TypeError):
        logger.warning("Invalid LESSON_DROPOUT_EPSILON=%r, ignoring", raw)
        return 0.0
    if epsilon <= 0.0:
        return 0.0
    if epsilon > 1.0:
        logger.warning("LESSON_DROPOUT_EPSILON=%s clamped to 1.0", epsilon)
        return 1.0
    return epsilon


def _get_dropout_session_id() -> str:
    """Resolve the session id used to correlate dropout logs with outcomes.

    Prefers ``GPTME_SESSION_ID`` / ``CC_SESSION_ID`` (the same id used in lesson
    trajectory logs) so causal analysis can join withheld lessons to session
    outcomes. Falls back to a random id when neither is set.
    """
    for key in ("GPTME_SESSION_ID", "CC_SESSION_ID"):
        value = os.environ.get(key)
        if value:
            return value
    return uuid.uuid4().hex


def _get_dropout_log_dir() -> Path:
    """Directory for randomized-dropout logs (``state/lesson-dropout`` default).

    Overridable via ``LESSON_DROPOUT_LOG_DIR``. The default is relative to the
    current working directory so analysis tooling that reads
    ``state/lesson-dropout/*.jsonl`` works without extra configuration.
    """
    return Path(os.environ.get("LESSON_DROPOUT_LOG_DIR", "state/lesson-dropout"))


# --- Lesson policy manifest (Stage 1 shadow logging) ---

_policy_manifest_cache: "dict[str, Any] | None" = None
_policy_manifest_cache_key: "tuple[str, Path, int | None, int | None, int | None] | None" = None


def _get_policy_manifest_path() -> Path:
    """Return path to lesson-policy manifest.

    Overridable via ``LESSON_POLICY_MANIFEST_PATH`` for testing.
    Defaults to ``state/lesson-policy/manifest.yaml`` relative to cwd.
    """
    return Path(
        os.environ.get(
            "LESSON_POLICY_MANIFEST_PATH", "state/lesson-policy/manifest.yaml"
        )
    )


def _load_policy_manifest() -> "dict[str, Any]":
    """Load and cache the lesson-policy manifest (YAML).

    Returns dict with keys: version, updated_at, validated_core, exempt, holdout_population.
    On load failure or missing file, returns a safe default (all lessons in holdout).
    Failures are swallowed — manifest loading must never break lesson injection.
    """
    global _policy_manifest_cache, _policy_manifest_cache_key

    manifest_path = _get_policy_manifest_path()
    configured_path = str(manifest_path)
    # A relative configured path is CWD-dependent only until it has loaded. Keep
    # using that load-time anchor across later CWD changes, while still allowing
    # a different configured path or an in-place file update to invalidate it.
    cached_abs_path = (
        _policy_manifest_cache_key[1]
        if _policy_manifest_cache_key is not None
        and _policy_manifest_cache_key[0] == configured_path
        else None
    )
    manifest_abs_path = cached_abs_path or manifest_path.resolve()
    try:
        stat = manifest_abs_path.stat()
        manifest_mtime_ns = stat.st_mtime_ns
        manifest_ctime_ns = stat.st_ctime_ns
        manifest_size = stat.st_size
    except OSError:
        manifest_mtime_ns = None
        manifest_ctime_ns = None
        manifest_size = None
    cache_key = (
        configured_path,
        manifest_abs_path,
        manifest_mtime_ns,
        manifest_ctime_ns,
        manifest_size,
    )
    if _policy_manifest_cache is not None and _policy_manifest_cache_key == cache_key:
        return _policy_manifest_cache
    _default: dict[str, Any] = {
        "version": 1,
        "updated_at": "",
        "validated_core": [],
        "exempt": [],
        "holdout_population": [],
    }
    # Sentinel used by _classify_lesson to distinguish "no manifest" (→ holdout)
    # from "manifest exists but lesson not listed" (→ unknown).
    _missing_default = {**_default, "_manifest_missing": True}

    if manifest_mtime_ns is None:
        _policy_manifest_cache = _missing_default
        _policy_manifest_cache_key = cache_key
        return _policy_manifest_cache

    try:
        import yaml

        with open(manifest_abs_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if raw is None:
            # Empty file (e.g. `yaml.safe_load` of a blank/whitespace-only
            # document) — treat like a missing manifest.
            manifest: dict[str, Any] = _missing_default
        elif not isinstance(raw, dict):
            logger.warning(
                "Lesson-policy manifest at %s has unexpected type (%s); using defaults",
                manifest_path,
                type(raw).__name__,
            )
            manifest = _missing_default
        else:
            # A valid-but-empty mapping (`{}`) means the manifest *exists* but
            # classifies nothing — distinct from a missing manifest, so lessons
            # should resolve to "unknown" rather than the missing-manifest
            # "holdout" default. Don't conflate the two via truthiness.
            manifest = raw
    except ImportError:
        logger.warning(
            "yaml not available; lesson-policy manifest at %s ignored", manifest_path
        )
        manifest = _missing_default
    except Exception as e:
        logger.warning("Failed to load lesson-policy manifest: %s", e)
        manifest = _missing_default

    # Resolve the manifest path once at load time and store it as a meta key.
    # _classify_lesson uses this to anchor relative `root:` values without
    # re-resolving against the process CWD — which may have changed since the
    # manifest was first cached (the Greptile "cached manifest loses its anchor"
    # finding).
    if not manifest.get("_manifest_missing"):
        manifest["_manifest_abs_path"] = manifest_abs_path

    _policy_manifest_cache = manifest
    _policy_manifest_cache_key = cache_key
    return _policy_manifest_cache


def _classify_lesson(lesson_path: str) -> tuple[str, int]:
    """Classify a lesson by its path against the policy manifest.

    Args:
        lesson_path: Filesystem path to the lesson file, e.g.
            ``/home/bob/lessons/patterns/foo.md`` or ``lessons/patterns/foo.md``.

    Returns:
        ``(policy_class, policy_version)`` where policy_class is one of:

        - ``"validated_core"``: high-ROI, recommended for all sessions
        - ``"exempt"``: safety/retention policy, exempt from dropout
        - ``"holdout"``: under evaluation (default)
        - ``"unknown"``: not in manifest (created after manifest timestamp)
    """
    manifest = _load_policy_manifest()
    try:
        policy_version = int(manifest.get("version", 1))
    except (TypeError, ValueError):
        policy_version = 1

    # Normalize: extract the category/name part for manifest key lookup.
    # Priority order:
    #   1. Manifest declares `root` → exact relative-path match. This guard takes
    #      precedence over the `lessons` heuristic so a path from an outside workspace
    #      that happens to contain a `lessons` component cannot inherit entries that
    #      were intended for this root.
    #   2. No root declared, path contains `lessons` dir component → key after that.
    #   3. No root, no `lessons` component → return unknown/holdout conservatively;
    #      suffix enumeration would accept unrelated custom-root lessons.
    path = Path(lesson_path)
    parts = path.parts
    # Validate `root` is a string before using it — a malformed YAML value
    # (int, list, dict) would raise TypeError in Path(), which the outer
    # _log_dropout handler would catch, suppressing the entire dropout record.
    manifest_root_raw = manifest.get("root") if isinstance(manifest, dict) else None
    manifest_root_str = (
        manifest_root_raw if isinstance(manifest_root_raw, str) else None
    )

    if manifest_root_str:
        # Declared root: use exact relative-path matching for ALL paths (including
        # those that happen to contain a "lessons" dir component).
        try:
            # Resolve to absolute so a relative `root:` value (e.g. `root: lessons`)
            # works correctly when the lesson path is absolute. Python's
            # Path.relative_to() raises ValueError if the base is relative but the
            # target is absolute, misclassifying every valid in-root lesson.
            #
            # Anchor against the manifest file's directory — NOT the process CWD.
            # When the hook runs from a workspace subdirectory, CWD-based resolve()
            # maps `root: lessons` to `<subdirectory>/lessons` while lesson paths are
            # rooted at the workspace root, misclassifying every in-root lesson as
            # `unknown`. Anchoring to the manifest file's parent is CWD-independent:
            # the manifest is always found at an absolute path, so its parent is stable.
            manifest_root = Path(manifest_root_str)
            if not manifest_root.is_absolute():
                # Use the path resolved at manifest-load time, not now — the
                # process CWD may have changed since the manifest was cached,
                # and re-resolving a relative manifest path against the new CWD
                # would anchor the relative `root:` to the wrong directory.
                manifest_file_abs = (
                    manifest.get("_manifest_abs_path")
                    or _get_policy_manifest_path().resolve()
                )
                manifest_root = (manifest_file_abs.parent / manifest_root_str).resolve()
            else:
                # Always resolve absolute roots too so that paths containing `..`
                # or symlink components compare correctly against lesson paths.
                manifest_root = manifest_root.resolve()
            # LessonIndex normally yields absolute paths. Accept relative paths too,
            # anchoring them to the declared lesson root instead of the process CWD.
            abs_path = path if path.is_absolute() else manifest_root / path
            try:
                rel = abs_path.relative_to(manifest_root)
            except ValueError:
                # `manifest_root` is always fully resolved above, but an absolute
                # lesson path is used as-is — so the two sides are normalized
                # asymmetrically. When any component of the lesson path is a
                # symlink or `..` (a symlinked workspace root, `/tmp` on macOS,
                # `$HOME` behind a symlink), the same file has two spellings and
                # relative_to() fails, mislabelling every in-root lesson
                # "unknown". Retry fully resolved.
                #
                # Ordering matters: the unresolved comparison is tried first so a
                # lesson file that is itself a symlink *out of* the root (the
                # index deliberately keeps such entries, deduping by realpath)
                # still classifies against the root it was discovered under.
                rel = abs_path.resolve().relative_to(manifest_root)
            candidate_keys = [str(rel).replace("\\", "/").removesuffix(".md")]
        except (ValueError, OSError):
            # Path is outside the declared root — it belongs to a different lesson
            # tree and must not inherit entries from this manifest.
            if manifest.get("_manifest_missing"):
                return "holdout", policy_version
            return "unknown", policy_version
    else:
        # A relative path is scoped by its LessonIndex caller, so preserve the
        # legacy ``lessons/`` heuristic. An absolute path is only safe when its
        # ``lessons`` directory is anchored to the manifest's workspace.
        if path.is_absolute():
            manifest_file_abs = manifest.get("_manifest_abs_path")
            if manifest_file_abs is None:
                if manifest.get("_manifest_missing"):
                    return "holdout", policy_version
                return "unknown", policy_version
            inferred_root = Path(manifest_file_abs).parent / "lessons"
            try:
                rel = path.relative_to(inferred_root)
            except ValueError:
                try:
                    rel = path.resolve().relative_to(inferred_root.resolve())
                except (ValueError, OSError):
                    return "unknown", policy_version
            candidate_keys = [str(rel).replace("\\", "/").removesuffix(".md")]
        else:
            try:
                lessons_idx = list(parts).index("lessons")
                candidate_keys = [
                    "/".join(parts[lessons_idx + 1 :]).removesuffix(".md")
                ]
            except ValueError:
                # No root and no "lessons" component. Suffix enumeration would
                # accept unrelated custom-root lessons, so classify conservatively.
                if manifest.get("_manifest_missing"):
                    return "holdout", policy_version
                return "unknown", policy_version

    # Build a lookup from manifest key → policy class so we can pick the
    # most specific (longest) matching suffix first, regardless of class order.
    # Without this, a shorter suffix in validated_core would shadow the intended
    # longer key in holdout_population (the Greptile-identified suffix-priority bug).
    key_to_class: dict[str, str] = {}
    for category, class_name in [
        ("validated_core", "validated_core"),
        ("exempt", "exempt"),
        ("holdout_population", "holdout"),
    ]:
        category_list = manifest.get(category)
        if not isinstance(category_list, list):
            if category_list is not None:
                logger.warning(
                    "Lesson-policy manifest category %s has unexpected type (%s); ignoring it",
                    category,
                    type(category_list).__name__,
                )
            continue
        for k in category_list:
            if isinstance(k, str):  # skip non-string (YAML mappings, nested lists)
                key_to_class.setdefault(k, class_name)

    # Iterate candidate keys longest-first: the most specific match wins.
    for key in sorted(candidate_keys, key=len, reverse=True):
        if key in key_to_class:
            return key_to_class[key], policy_version

    # No manifest on disk → default evaluation population is "holdout".
    # Manifest exists but lesson not listed → "unknown" (added after manifest stamp).
    if manifest.get("_manifest_missing"):
        return "holdout", policy_version
    return "unknown", policy_version


def _apply_lesson_dropout(matches: list) -> list:
    """Randomly withhold matched lessons for causal LOO measurement.

    For each match, flips a coin with probability ``LESSON_DROPOUT_EPSILON`` to
    withhold it. Withheld lessons are logged to
    ``<log dir>/<session-id>.jsonl`` and removed from the returned list so they
    are not injected. When epsilon is 0 (default), the input list is returned
    unchanged and nothing is logged. When epsilon is > 0, a log record is
    always written (even if no lessons were withheld), so analysis can
    distinguish treatment-group sessions from control.

    Args:
        matches: Match results (already truncated to the injection cap).

    Returns:
        The matches that survived the dropout roll (to be injected).
    """
    epsilon = _get_dropout_epsilon()
    if epsilon <= 0.0:
        return matches

    kept: list = []
    withheld: list[dict] = []
    for match in matches:
        if random.random() < epsilon:
            lesson = match.lesson
            withheld.append({"path": str(lesson.path), "title": lesson.title})
        else:
            kept.append(match)

    _log_dropout(epsilon, kept, withheld)

    return kept


def _log_dropout(epsilon: float, kept: list, withheld: list[dict]) -> None:
    """Append a randomized-dropout record for causal LOO analysis.

    Stage 1 shadow logging: includes ``policy_class`` and ``policy_version``
    for every lesson (both kept and withheld) so manifest classification can
    be verified without changing dropout behavior.

    Failures are logged and swallowed — dropout logging must never break lesson
    injection.
    """
    try:
        session_id = _get_dropout_session_id()
        log_dir = _get_dropout_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)

        # Enrich withheld entries with policy classification (Stage 1 shadow).
        enriched_withheld = []
        for entry in withheld:
            policy_class, policy_version = _classify_lesson(entry.get("path", ""))
            enriched_withheld.append(
                {
                    **entry,
                    "policy_class": policy_class,
                    "policy_version": policy_version,
                }
            )

        # Log kept (matched) lessons for treatment-assignment verification.
        enriched_matched = []
        for match in kept:
            lesson = match.lesson
            policy_class, policy_version = _classify_lesson(str(lesson.path))
            enriched_matched.append(
                {
                    "path": str(lesson.path),
                    "title": lesson.title,
                    "policy_class": policy_class,
                    "policy_version": policy_version,
                }
            )

        record = {
            "ts": time.time(),
            "session_id": session_id,
            "epsilon": epsilon,
            "withheld": enriched_withheld,
            "matched": enriched_matched,
        }
        with open(log_dir / f"{session_id}.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
        logger.debug(
            "Lesson dropout: withheld %d lesson(s) at epsilon=%s (session %s)",
            len(withheld),
            epsilon,
            session_id,
        )
    except Exception as e:
        logger.warning("Failed to log lesson dropout: %s", e)


def auto_include_lessons(
    messages: list["Message"],
    max_lessons: int = 5,
    enabled: bool = True,
    use_hybrid: bool = False,
    hybrid_config: "HybridConfig | None" = None,
    max_tokens: int | None = None,
) -> list["Message"]:
    """Automatically include relevant lessons in message context.

    Args:
        messages: List of messages
        max_lessons: Maximum number of lessons to include
        enabled: Whether auto-inclusion is enabled
        use_hybrid: Use hybrid matching (semantic + effectiveness)
        hybrid_config: Configuration for hybrid matching
        max_tokens: Token budget for lessons beyond the first (default from env GPTME_LESSONS_TOKEN_BUDGET).
            The highest-scored lesson is always force-included regardless of this limit.

    Returns:
        Updated message list with lessons included
    """
    if not enabled:
        return messages

    # Resolve token budget
    if max_tokens is None:
        max_tokens = _get_token_budget()

    # Get last user message
    user_msg = None
    for msg in reversed(messages):
        if msg.role == "user":
            user_msg = msg
            break

    if not user_msg:
        logger.debug("No user message found, skipping lesson inclusion")
        return messages

    # Build match context
    context = MatchContext(message=user_msg.content)

    # Find matching lessons
    try:
        index = LessonIndex()
        if not index.lessons:
            logger.debug("No lessons found in index")
            return messages

        # Choose matcher based on configuration
        matcher: LessonMatcher
        if use_hybrid and HYBRID_AVAILABLE:
            logger.debug("Using hybrid lesson matcher")
            matcher = HybridLessonMatcher(config=hybrid_config)
        else:
            if use_hybrid:
                logger.warning(
                    "Hybrid matching requested but not available, falling back to keyword-only"
                )
            logger.debug("Using keyword-only lesson matcher")
            matcher = LessonMatcher()

        matches = matcher.match(index.lessons, context)

        # Limit to top N (matcher may already limit, but ensure it)
        matches = matches[:max_lessons]

        # Optionally withhold a random subset for causal LOO measurement.
        # No-op unless LESSON_DROPOUT_EPSILON > 0.
        matches = _apply_lesson_dropout(matches)
        if not matches:
            logger.debug("No matching lessons found (or all withheld by dropout)")
            return messages

        for match in matches:
            if match.lesson.is_stub:
                match.lesson = index.materialize_lesson(match.lesson)

        # Format lessons for inclusion, respecting token budget
        lesson_content, dropped_count, subsequent_tokens = _format_with_budget(
            matches, max_tokens
        )

        # Log if we dropped lessons due to budget
        if dropped_count > 0:
            logger.warning(
                "Lesson token budget exceeded: dropped %d/%d matched lessons"
                " (%dK/%dK subsequent-lesson budget used)",
                dropped_count,
                len(matches),
                subsequent_tokens // 1000,
                max_tokens // 1000,
            )

        # Create system message with lessons
        from ..message import Message

        lesson_msg = Message(
            role="system",
            content=f"# Relevant Lessons\n\n{lesson_content}",
            hide=True,  # Don't show in UI by default
        )

        # Insert after initial system message
        # Assume first message is system prompt
        if messages and messages[0].role == "system":
            return [messages[0], lesson_msg] + messages[1:]
        return [lesson_msg] + messages

    except Exception as e:
        logger.warning(f"Failed to include lessons: {e}")
        return messages


def _format_with_budget(matches: list, max_tokens: int) -> tuple[str, int, int]:
    """Format matched lessons with token budget enforcement.

    The highest-scored lesson is always included regardless of size.
    Subsequent lessons are included only if their tokens fit within max_tokens
    counting only the non-first lessons — so an oversized first lesson does not
    consume the budget available to smaller subsequent ones.

    Args:
        matches: List of match results (already sorted by score, descending)
        max_tokens: Maximum token budget for non-first lessons

    Returns:
        Tuple of (formatted content, number of lessons dropped due to budget,
        tokens used by subsequent (non-first) lessons)
    """
    included: list[str] = []
    dropped = 0
    # Track tokens for budget enforcement separately from the first (forced) lesson.
    # This prevents an oversized first lesson from consuming the budget available
    # to smaller subsequent lessons.
    subsequent_tokens = 0

    for match in matches:
        lesson = match.lesson

        # Build individual lesson content (same format as _format_lessons)
        parts = []
        if included:
            parts.append("\n")
        parts.append(f"## {lesson.title}\n")
        parts.append(f"\n*Path: {lesson.path}*\n")
        parts.append(f"\n*Category: {lesson.category or 'general'}*\n")
        if match.matched_by:
            parts.append(f"\n*Matched by: {len(match.matched_by)} keyword(s)*\n")
        parts.append(f"\n{lesson.body}\n")

        lesson_str = "".join(parts)
        lesson_tokens = _estimate_tokens(lesson_str)

        if not included:
            # Always include the first (highest-scored) lesson regardless of size
            included.append(lesson_str)
        elif subsequent_tokens + lesson_tokens > max_tokens:
            dropped += 1
        else:
            included.append(lesson_str)
            subsequent_tokens += lesson_tokens

    return "".join(included), dropped, subsequent_tokens


def _format_lessons(matches: list) -> str:
    """Format matched lessons for inclusion.

    Note: This function is kept for backward compatibility.
    The token-budget-aware _format_with_budget is preferred.
    """
    parts = []

    for i, match in enumerate(matches, 1):
        lesson = match.lesson

        # Add separator between lessons
        if i > 1:
            parts.append("\n")

        # Add lesson header with metadata
        parts.append(f"## {lesson.title}\n")
        parts.append(f"\n*Path: {lesson.path}*\n")
        parts.append(f"\n*Category: {lesson.category or 'general'}*\n")

        # Add match info (count only — avoid injecting keyword text which
        # creates self-referential corpus matches in lesson effectiveness analysis)
        if match.matched_by:
            parts.append(f"\n*Matched by: {len(match.matched_by)} keyword(s)*\n")

        # Add lesson content
        parts.append(f"\n{lesson.body}\n")

    return "".join(parts)
