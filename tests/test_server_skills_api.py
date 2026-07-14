"""Tests for the skills API endpoint."""

import json

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient

from gptme.lessons.index import clear_cache


def _write_skill(root, name: str, description: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n",
        encoding="utf-8",
    )


def test_skills_endpoint_lists_skills_with_reputation(
    client: FlaskClient, tmp_path, monkeypatch
) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "code-review-helper", "Review code changes")
    _write_skill(skills_dir, "deploy-checklist", "Check deployments")

    reputation_index = tmp_path / "_index.json"
    reputation_index.write_text(
        json.dumps(
            {
                "code-review-helper": {
                    "score": 0.82,
                    "band": "excellent",
                    "band_label": "Trusted",
                    "blocked": False,
                    "computed_at": "2026-07-13T21:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GPTME_LESSONS_EXTRA_DIRS", str(skills_dir))
    monkeypatch.setenv("GPTME_SKILL_REPUTATION_INDEX", str(reputation_index))
    clear_cache()

    response = client.get("/api/v2/skills")

    assert response.status_code == 200
    data = response.get_json()
    by_name = {skill["name"]: skill for skill in data["skills"]}

    assert by_name["code-review-helper"]["description"] == "Review code changes"
    assert by_name["code-review-helper"]["install_count"] == 0
    assert by_name["code-review-helper"]["reputation"] == {
        "score": 0.82,
        "band": "excellent",
        "band_label": "Trusted",
        "blocked": False,
        "computed_at": "2026-07-13T21:00:00+00:00",
    }
    assert by_name["deploy-checklist"]["reputation"]["score"] is None
    assert by_name["deploy-checklist"]["reputation"]["band"] == "neutral"
    assert by_name["deploy-checklist"]["reputation"]["band_label"] == "Unproven"
