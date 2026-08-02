"""
K-Best Guess validation framework.

Reduces agent hallucination at decision points by generating K candidate
outputs, scoring each, and returning the winner. Instead of committing to
a single model output, this explores the solution space and keeps the best.

Usage::

    from gptme.k_best import k_best_guess

    @k_best_guess(k=3, check=lambda result: run_tests(result))
    def generate_code(prompt: str) -> str:
        # This function is called K times; the highest-scoring result wins
        return call_llm(prompt)

    best_code = generate_code("implement a binary search")

The ``check`` callable receives the return value and must return a numeric
score; higher is better. If ``check`` is omitted, the first non-exception
result is returned. Candidates are run in parallel by default.

Exceptions from individual candidates and validation checks are caught and
logged; if **all** K candidates fail generation or validation, the last
exception is re-raised.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import wraps
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

CheckFn = Callable[[T], float | int]


@dataclass
class Candidate:
    """A single evaluated candidate from a K-best run."""

    result: object
    score: float
    index: int
    error: BaseException | None = None


@dataclass
class KBestResult:
    """Full result from a K-best run, including all candidates."""

    winner: object
    winner_score: float
    winner_index: int
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def k(self) -> int:
        return len(self.candidates)


def k_best_guess(
    k: int = 3,
    check: CheckFn | None = None,
    *,
    parallel: bool = True,
    max_workers: int | None = None,
    return_metadata: bool = False,
) -> Callable:
    """Decorator factory: run the wrapped function K times and return the best result.

    Args:
        k: Number of candidates to generate. Must be >= 1.
        check: Callable ``(result) -> float`` scoring each candidate.
               Higher scores win. If omitted, returns the first successful result.
        parallel: Run candidates in parallel threads (default True).
        max_workers: Thread pool size. Defaults to ``k``.
        return_metadata: If True, return a :class:`KBestResult` with all
                         candidates instead of just the winning value.

    Returns:
        A decorator that wraps any function to explore K candidates.

    Raises:
        ValueError: If ``k < 1``.
        Exception: Re-raises the last generation or validation exception if all
                   K candidates fail.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k!r}")

    def decorator(fn: Callable[..., T]) -> Callable[..., T | KBestResult]:
        @wraps(fn)
        def wrapper(*args, **kwargs) -> T | KBestResult:
            candidates: list[Candidate] = []

            if parallel and k > 1:
                workers = max_workers or k
                futures: dict[Future, int] = {}
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    for i in range(k):
                        f = pool.submit(fn, *args, **kwargs)
                        futures[f] = i

                    for future in as_completed(futures):
                        idx = futures[future]
                        exc = future.exception()
                        if exc is not None:
                            logger.debug(
                                "k_best_guess candidate %d raised: %s", idx, exc
                            )
                            candidates.append(
                                Candidate(
                                    result=None,
                                    score=float("-inf"),
                                    index=idx,
                                    error=exc,
                                )
                            )
                        else:
                            result = future.result()
                            score, error = _score(result, check, idx)
                            candidates.append(
                                Candidate(
                                    result=result,
                                    score=score,
                                    index=idx,
                                    error=error,
                                )
                            )
            else:
                for i in range(k):
                    try:
                        result = fn(*args, **kwargs)
                        score, error = _score(result, check, i)
                        candidates.append(
                            Candidate(result=result, score=score, index=i, error=error)
                        )
                    except Exception as e:
                        logger.debug("k_best_guess candidate %d raised: %s", i, e)
                        candidates.append(
                            Candidate(
                                result=None, score=float("-inf"), index=i, error=e
                            )
                        )

            successful = [c for c in candidates if c.error is None]
            if not successful:
                last_err = max(candidates, key=lambda c: c.index).error
                raise last_err  # type: ignore[misc]

            winner = max(successful, key=lambda c: c.score)
            logger.debug(
                "k_best_guess winner: candidate %d (score=%.4f) out of %d",
                winner.index,
                winner.score,
                len(successful),
            )

            if return_metadata:
                return KBestResult(
                    winner=winner.result,
                    winner_score=winner.score,
                    winner_index=winner.index,
                    candidates=candidates,
                )
            return winner.result  # type: ignore[return-value]

        return wrapper

    return decorator


def _score(
    result: object, check: CheckFn | None, index: int
) -> tuple[float, BaseException | None]:
    """Return the numeric score and any validation error for a candidate."""
    if check is None:
        # First successful result gets score 0; parallel order is non-deterministic
        # so we do not award bonus points for first-arrival here.
        return 0.0, None
    try:
        raw = check(result)
        score = float(raw)
        if not math.isfinite(score):
            raise ValueError(f"check returned a non-finite score: {score!r}")
        return score, None
    except Exception as e:
        logger.debug(
            "k_best_guess check function raised for candidate %d: %s", index, e
        )
        return float("-inf"), e
