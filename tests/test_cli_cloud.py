"""Tests for gptme-cloud, the runs API client. HTTP is stubbed at requests."""

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from gptme.cli.cmd_cloud import main

RUN = "11111111-2222-3333-4444-555555555555"
RUNS = "https://fleet.example/api/v1/operator/runs"


def _resp(status: int, body: object = None, content: bytes = b"") -> MagicMock:
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.ok = status < 400
    r.reason = "reason"
    r.content = content
    r.json.return_value = body
    return r


def _invoke(args: list[str], token: str | None = "gptme_pat_x"):
    env = {"GPTME_CLOUD_TOKEN": token} if token else {}
    return CliRunner().invoke(main, ["--url", "https://fleet.example", *args], env=env)


def test_run_sends_body_bearer_and_idempotency_key():
    with patch("requests.post") as post:
        post.return_value = _resp(202, {"id": RUN, "status": "queued"})
        res = _invoke(
            [
                "run",
                "fix it",
                "--repo",
                "https://github.com/o/r",
                "--ref",
                "main",
                "--ttl",
                "600",
            ]
        )
    assert res.exit_code == 0, res.output
    assert res.output.strip() == f"{RUN} queued"
    args, kwargs = post.call_args
    assert args[0] == RUNS
    assert kwargs["json"] == {
        "prompt": "fix it",
        "workspace": {"git": {"url": "https://github.com/o/r", "ref": "main"}},
        "limits": {"ttl_seconds": 600},
    }
    assert kwargs["headers"]["Authorization"] == "Bearer gptme_pat_x"
    assert kwargs["headers"]["Idempotency-Key"]


def test_run_respects_explicit_idempotency_key():
    with patch("requests.post") as post:
        post.return_value = _resp(200, {"id": RUN, "status": "running"})
        _invoke(["run", "p", "--idempotency-key", "abc"])
    assert post.call_args.kwargs["headers"]["Idempotency-Key"] == "abc"


def test_ref_without_repo_is_a_usage_error():
    res = _invoke(["run", "p", "--ref", "main"])
    assert res.exit_code == 2
    assert "--repo" in res.output


def test_missing_token_is_a_usage_error():
    res = _invoke(["status", RUN], token=None)
    assert res.exit_code == 2
    assert "GPTME_CLOUD_TOKEN" in res.output


@pytest.mark.parametrize(
    ("code", "fragment"),
    [
        ("token_expired", "mint a new"),
        ("token_revoked", "stop"),
        ("insufficient_scope", "scope"),
    ],
)
def test_auth_errors_are_distinguished(code, fragment):
    with patch("requests.get") as get:
        get.return_value = _resp(
            401 if code != "insufficient_scope" else 403, {"error": code}
        )
        res = _invoke(["status", RUN])
    assert res.exit_code == 1
    assert fragment in res.output


def test_run_id_is_not_interpolated_unless_uuid():
    with patch("requests.get") as get:
        res = _invoke(["status", "../instances"])
    assert res.exit_code == 2
    get.assert_not_called()


def test_wait_polls_until_terminal_and_exit_code_follows_status():
    with (
        patch("requests.post") as post,
        patch("requests.get") as get,
        patch("time.sleep"),
    ):
        post.return_value = _resp(202, {"id": RUN, "status": "queued"})
        get.side_effect = [
            _resp(200, {"id": RUN, "status": "running"}),
            _resp(200, {"id": RUN, "status": "failed"}),
        ]
        res = _invoke(["run", "p", "--wait"])
    assert get.call_count == 2
    assert res.exit_code == 1
    assert f"{RUN} failed" in res.output


def test_cancel_posts_to_cancel_route():
    with patch("requests.post") as post:
        post.return_value = _resp(200, {"id": RUN, "status": "cancelled"})
        res = _invoke(["cancel", RUN])
    assert res.exit_code == 0
    assert post.call_args.args[0] == f"{RUNS}/{RUN}/cancel"


def test_fetch_writes_verified_artifacts_and_skips_withheld(tmp_path):
    good = b"diff --git a b\n"
    listing = {
        "dirty": False,
        "artifacts": [
            {
                "name": "patch",
                "status": "available",
                "url": "https://signed.example/patch",
                "sha256": hashlib.sha256(good).hexdigest(),
            },
            {"name": "uncommitted.diff", "status": "withheld", "reason": "secret"},
        ],
    }
    with patch("requests.get") as get:
        get.side_effect = [_resp(200, listing), _resp(200, content=good)]
        res = _invoke(["fetch", RUN, "--out", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "patch").read_bytes() == good
    assert not (tmp_path / "uncommitted.diff").exists()
    # The signed URL carries its own credential; the PAT must not go with it.
    assert "headers" not in get.call_args_list[1].kwargs


def test_fetch_rejects_checksum_mismatch_and_unsafe_names(tmp_path):
    listing = {
        "artifacts": [
            {
                "name": "patch",
                "status": "available",
                "url": "https://signed.example/p",
                "sha256": "0" * 64,
            },
            {
                "name": "../evil",
                "status": "available",
                "url": "https://signed.example/e",
                "sha256": "",
            },
            {"name": "final-message", "status": "expired"},
        ]
    }
    with patch("requests.get") as get:
        get.side_effect = [_resp(200, listing), _resp(200, content=b"tampered")]
        res = _invoke(["fetch", RUN, "--out", str(tmp_path)])
    assert res.exit_code == 1
    assert list(tmp_path.iterdir()) == []
    assert "checksum mismatch" in res.output
    assert get.call_count == 2  # the unsafe name was never downloaded


def test_json_flag_prints_the_response():
    with patch("requests.get") as get:
        get.return_value = _resp(200, {"id": RUN, "status": "running"})
        res = _invoke(["status", RUN, "--json"])
    assert json.loads(res.output) == {"id": RUN, "status": "running"}


def test_fetch_replaces_a_symlink_instead_of_writing_through_it(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    out = tmp_path / "out"
    out.mkdir()
    (out / "patch").symlink_to(victim)
    good = b"diff\n"
    listing = {
        "artifacts": [
            {
                "name": "patch",
                "status": "available",
                "url": "https://signed.example/p",
                "sha256": hashlib.sha256(good).hexdigest(),
            }
        ]
    }
    with patch("requests.get") as get:
        get.side_effect = [_resp(200, listing), _resp(200, content=good)]
        res = _invoke(["fetch", RUN, "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert victim.read_text() == "keep"
    assert not (out / "patch").is_symlink()
    assert (out / "patch").read_bytes() == good


@pytest.mark.parametrize("sha", [None, "", "abc", 12])
def test_fetch_refuses_artifacts_without_a_valid_checksum(tmp_path, sha):
    listing = {
        "artifacts": [
            {
                "name": "patch",
                "status": "available",
                "url": "https://signed.example/p",
                "sha256": sha,
            }
        ]
    }
    with patch("requests.get") as get:
        get.side_effect = [_resp(200, listing), _resp(200, content=b"x")]
        res = _invoke(["fetch", RUN, "--out", str(tmp_path)])
    assert res.exit_code == 1
    assert "no valid sha256" in res.output
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("poll", ["-1", "0", "nan", "inf"])
def test_bad_poll_is_rejected_before_a_run_is_created(poll):
    with patch("requests.post") as post:
        res = _invoke(["run", "p", "--wait", "--poll", poll])
    assert res.exit_code == 2
    post.assert_not_called()


@pytest.mark.parametrize(
    "bad",
    ["1" * 36, "-" * 36, "1111111122223333444455555555555555", RUN + "0"],
)
def test_run_id_must_be_a_real_uuid(bad):
    with patch("requests.get") as get:
        res = _invoke(["status", bad])
    assert res.exit_code != 0
    get.assert_not_called()


def test_transport_failure_is_a_clean_error_and_names_the_idempotency_key():
    with patch("requests.post", side_effect=requests.exceptions.Timeout("slow")):
        res = _invoke(["run", "fix it", "--idempotency-key", "key-1"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "Timeout" in res.output
    assert "--idempotency-key key-1" in res.output


def test_fetch_survives_an_unwritable_destination(tmp_path):
    out = tmp_path / "out"
    (out / "patch").mkdir(parents=True)  # a directory where the artifact goes
    body = b"diff"
    listing = {
        "artifacts": [
            {
                "name": "patch",
                "status": "available",
                "url": "https://signed/patch",
                "sha256": hashlib.sha256(body).hexdigest(),
            },
            {
                "name": "final.md",
                "status": "available",
                "url": "https://signed/final",
                "sha256": hashlib.sha256(body).hexdigest(),
            },
        ]
    }
    with patch("requests.get") as get:
        get.side_effect = [
            _resp(200, listing),
            _resp(200, content=body),
            _resp(200, content=body),
        ]
        res = _invoke(["fetch", RUN, "--out", str(out)])
    assert res.exit_code == 1
    assert "patch: write failed" in res.output
    assert (out / "final.md").read_bytes() == body
