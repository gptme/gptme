"""Skills registry API endpoints."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, cast

import flask
from pydantic import BaseModel, Field

from ..lessons.index import LessonIndex
from .auth import require_auth
from .openapi_docs import ErrorResponse, api_doc_simple

logger = logging.getLogger(__name__)

skills_api = flask.Blueprint("skills_api", __name__)

ReputationBand = Literal["excellent", "good", "neutral", "low", "blocked"]


class SkillReputationOut(BaseModel):
    score: float | None = Field(
        None, description="Reputation score in [0, 1], or null when unscored"
    )
    band: ReputationBand = Field("neutral", description="Reputation display band")
    band_label: str = Field("Unproven", description="Human-readable reputation label")
    blocked: bool = Field(False, description="Whether safety signals block this skill")
    computed_at: str | None = Field(
        None, description="ISO timestamp for the reputation computation"
    )


class SkillOut(BaseModel):
    name: str = Field(..., description="Skill name")
    description: str = Field("", description="Short skill description")
    path: str = Field(..., description="Filesystem path to the skill's SKILL.md")
    category: str = Field("", description="Skill category or parent directory")
    install_count: int = Field(0, description="Registry install count")
    reputation: SkillReputationOut = Field(
        ..., description="Optional reputation summary from the skill reputation index"
    )


class SkillListResponse(BaseModel):
    skills: list[SkillOut] = Field(..., description="Discoverable skills")


def _default_reputation_index_path() -> Path:
    configured = os.environ.get("GPTME_SKILL_REPUTATION_INDEX")
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / "state" / "skill-reputation" / "scores" / "_index.json"


def _load_reputation_index(index_path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = index_path or _default_reputation_index_path()
    if not path.is_file():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read skill reputation index %s: %s", path, exc)
        return {}

    if not isinstance(raw, dict):
        return {}

    # Accept both Bob's scorer output (`{"skill-name": {...}}`) and a future
    # registry envelope (`{"skills": {"skill-name": {...}}}`).
    if isinstance(raw.get("skills"), dict):
        raw = raw["skills"]

    return {str(name): value for name, value in raw.items() if isinstance(value, dict)}


def _serialize_reputation(raw: dict[str, Any] | None) -> SkillReputationOut:
    if not raw:
        return SkillReputationOut(
            score=None,
            band="neutral",
            band_label="Unproven",
            blocked=False,
            computed_at=None,
        )

    score = raw.get("score")
    if not isinstance(score, (int, float)):
        score = None

    band = raw.get("band")
    valid_bands = {"excellent", "good", "neutral", "low", "blocked"}
    if band not in valid_bands:
        band = "neutral"
    band = cast(ReputationBand, band)

    default_labels = {
        "excellent": "Trusted",
        "good": "Recommended",
        "neutral": "Unproven",
        "low": "Caution",
        "blocked": "Blocked",
    }
    label = raw.get("band_label")
    if not isinstance(label, str) or not label.strip():
        label = default_labels[band]

    computed_at = raw.get("computed_at")
    if not isinstance(computed_at, str) or not computed_at.strip():
        computed_at = None

    return SkillReputationOut(
        score=float(score) if score is not None else None,
        band=band,
        band_label=label.strip(),
        blocked=bool(raw.get("blocked", band == "blocked")),
        computed_at=computed_at,
    )


def _serialize_skill(skill, reputation_index: dict[str, dict[str, Any]]) -> SkillOut:
    name = skill.metadata.name or skill.title
    description = skill.metadata.description or skill.description or ""
    return SkillOut(
        name=name,
        description=description,
        path=str(skill.path),
        category=skill.category,
        install_count=0,
        reputation=_serialize_reputation(reputation_index.get(name)),
    )


@skills_api.route("/api/v2/skills")
@require_auth
@api_doc_simple(
    responses={
        200: SkillListResponse,
        500: ErrorResponse,
    },
    tags=["skills"],
)
def list_skills():
    """List discoverable skills with optional reputation metadata."""
    try:
        index = LessonIndex()
        reputation_index = _load_reputation_index()
        skills = [
            _serialize_skill(skill, reputation_index).model_dump()
            for skill in index.lessons
            if skill.metadata.name
        ]
        skills.sort(key=lambda item: item["name"].lower())
        return flask.jsonify({"skills": skills})
    except Exception as exc:
        logger.exception("Error listing skills")
        return flask.jsonify({"error": str(exc)}), 500
