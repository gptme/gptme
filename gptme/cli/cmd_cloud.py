"""``gptme-cloud``: run a prompt in a gptme.ai ephemeral sandbox and fetch the result.

A thin client over the runs API (create, read, cancel, artifacts). It exists so
a person or an agent can hand a repo and a prompt to gptme.ai and get a patch
back without speaking gptme-server's session protocol::

    export GPTME_CLOUD_TOKEN=gptme_pat_...
    gptme-cloud run "fix the failing test" --repo https://github.com/org/repo --wait
    gptme-cloud fetch <run-id> --out ./result

Authentication is a personal access token (scopes ``runs:read``/``runs:write``),
read from ``--token`` or ``GPTME_CLOUD_TOKEN``. The token is only ever sent to the
gptme.ai control plane, never to a sandbox. The server returns a distinct 401
code for an expired token (mint a new one) and a revoked one (stop and tell the
owner); both are surfaced as such.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import click
import requests

DEFAULT_URL = "https://fleet.gptme.ai"
RUNS_PATH = "/api/v1/operator/runs"
TOKEN_ENV = "GPTME_CLOUD_TOKEN"
URL_ENV = "GPTME_CLOUD_URL"

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "expired"})

_RUN_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_AUTH_HINTS = {
    "token_expired": "token expired: mint a new personal access token",
    "token_revoked": "token revoked: stop, the owner revoked it",
    "insufficient_scope": "token lacks the required scope (runs:read / runs:write)",
}


class CloudError(click.ClickException):
    """A failed API call, with the server's error code when it sent one."""


def _headers(token: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **(extra or {})}


def _raise_for(resp: requests.Response) -> None:
    if resp.ok:
        return
    code = ""
    try:
        body = resp.json()
        code = str(body.get("error", "")) if isinstance(body, dict) else ""
    except ValueError:
        pass
    hint = _AUTH_HINTS.get(code)
    detail = hint or code or resp.reason or "request failed"
    raise CloudError(f"{resp.status_code}: {detail}")


def _json(resp: requests.Response) -> dict[str, Any]:
    _raise_for(resp)
    try:
        data = resp.json()
    except ValueError as e:
        raise CloudError(f"non-JSON response from server: {e}") from e
    if not isinstance(data, dict):
        raise CloudError("unexpected response shape from server")
    return data


def _check_run_id(run_id: str) -> str:
    # Interpolated into a URL path: refuse anything that is not a UUID.
    if not _RUN_ID_RE.match(run_id):
        raise click.BadParameter(f"not a run id: {run_id!r}")
    return run_id


def _emit(data: dict[str, Any], as_json: bool, line: str) -> None:
    click.echo(json.dumps(data, indent=2) if as_json else line)


class _Client:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/") + RUNS_PATH
        self.token = token

    def create(self, body: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        resp = requests.post(
            self.base,
            json=body,
            headers=_headers(self.token, {"Idempotency-Key": idempotency_key}),
            timeout=30,
        )
        return _json(resp)

    def get(self, run_id: str) -> dict[str, Any]:
        return _json(
            requests.get(
                f"{self.base}/{_check_run_id(run_id)}",
                headers=_headers(self.token),
                timeout=30,
            )
        )

    def cancel(self, run_id: str) -> dict[str, Any]:
        return _json(
            requests.post(
                f"{self.base}/{_check_run_id(run_id)}/cancel",
                headers=_headers(self.token),
                timeout=30,
            )
        )

    def artifacts(self, run_id: str) -> dict[str, Any]:
        return _json(
            requests.get(
                f"{self.base}/{_check_run_id(run_id)}/artifacts",
                headers=_headers(self.token),
                timeout=30,
            )
        )


def _client(ctx: click.Context) -> _Client:
    token = ctx.obj["token"]
    if not token:
        raise click.UsageError(f"no token: pass --token or set {TOKEN_ENV}")
    return _Client(ctx.obj["url"], token)


@click.group()
@click.option("--token", envvar=TOKEN_ENV, default=None, help="gptme.ai PAT.")
@click.option(
    "--url",
    envvar=URL_ENV,
    default=DEFAULT_URL,
    show_default=True,
    help="Control-plane base URL.",
)
@click.pass_context
def main(ctx: click.Context, token: str | None, url: str) -> None:
    """Run prompts in gptme.ai ephemeral sandboxes."""
    ctx.obj = {"token": token, "url": url}


@main.command("run")
@click.argument("prompt")
@click.option("--repo", help="GitHub repo to clone, https://github.com/<owner>/<repo>.")
@click.option("--ref", help="Git ref to check out (default: the repo's default).")
@click.option("--model", help="Model id (default: the platform default).")
@click.option("--ttl", "ttl_seconds", type=int, help="Hard wall-clock limit, seconds.")
@click.option("--budget", "credit_budget", type=int, help="Credit budget for the run.")
@click.option("--max-turns", type=int, help="Maximum assistant turns.")
@click.option(
    "--idempotency-key",
    default=None,
    help="Retry-safe key; the same key returns the same run. Default: random.",
)
@click.option("--wait", is_flag=True, help="Poll until the run finishes.")
@click.option("--poll", "poll_seconds", default=5.0, show_default=True, type=float)
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_context
def run_cmd(
    ctx: click.Context,
    prompt: str,
    repo: str | None,
    ref: str | None,
    model: str | None,
    ttl_seconds: int | None,
    credit_budget: int | None,
    max_turns: int | None,
    idempotency_key: str | None,
    wait: bool,
    poll_seconds: float,
    as_json: bool,
) -> None:
    """Start a run. With --wait, block until it reaches a terminal status."""
    if ref and not repo:
        raise click.UsageError("--ref needs --repo")
    body: dict[str, Any] = {"prompt": prompt}
    if model:
        body["model"] = model
    if repo:
        git: dict[str, str] = {"url": repo}
        if ref:
            git["ref"] = ref
        body["workspace"] = {"git": git}
    limits = {
        k: v
        for k, v in (
            ("ttl_seconds", ttl_seconds),
            ("credit_budget", credit_budget),
            ("max_turns", max_turns),
        )
        if v is not None
    }
    if limits:
        body["limits"] = limits

    client = _client(ctx)
    run = client.create(body, idempotency_key or str(uuid.uuid4()))
    if not wait:
        _emit(run, as_json, f"{run['id']} {run['status']}")
        return
    click.echo(f"{run['id']} {run['status']}", err=True)
    while run["status"] not in TERMINAL_STATUSES:
        time.sleep(poll_seconds)
        run = client.get(run["id"])
    _emit(run, as_json, f"{run['id']} {run['status']}")
    if run["status"] != "succeeded":
        sys.exit(1)


@main.command("status")
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_context
def status_cmd(ctx: click.Context, run_id: str, as_json: bool) -> None:
    """Show a run's status."""
    run = _client(ctx).get(run_id)
    _emit(run, as_json, f"{run['id']} {run['status']}")


@main.command("cancel")
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_context
def cancel_cmd(ctx: click.Context, run_id: str, as_json: bool) -> None:
    """Cancel a run."""
    run = _client(ctx).cancel(run_id)
    _emit(run, as_json, f"{run['id']} {run['status']}")


@main.command("fetch")
@click.argument("run_id")
@click.option(
    "--out",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Directory to write artifacts into.",
)
@click.pass_context
def fetch_cmd(ctx: click.Context, run_id: str, out: Path) -> None:
    """Download a run's artifacts (patch, final message) and verify checksums.

    Withheld artifacts (for example a diff that held a secret) are listed with
    their reason and never written. The command exits non-zero if a requested
    artifact is expired, unavailable or fails its checksum.
    """
    data = _client(ctx).artifacts(run_id)
    out.mkdir(parents=True, exist_ok=True)
    problems = 0
    for art in data.get("artifacts", []):
        name = art.get("name", "")
        status = art.get("status")
        if not _SAFE_NAME_RE.match(str(name)):
            click.echo(f"skip: unsafe artifact name {name!r}", err=True)
            problems += 1
            continue
        if status != "available":
            reason = f" ({art['reason']})" if art.get("reason") else ""
            click.echo(f"{name}: {status}{reason}", err=True)
            if status != "withheld":
                problems += 1
            continue
        # The signed URL carries its own credential: do not add the PAT.
        resp = requests.get(art["url"], timeout=120)
        if not resp.ok:
            click.echo(f"{name}: download failed ({resp.status_code})", err=True)
            problems += 1
            continue
        digest = hashlib.sha256(resp.content).hexdigest()
        if art.get("sha256") and digest != art["sha256"]:
            click.echo(f"{name}: checksum mismatch, not written", err=True)
            problems += 1
            continue
        (out / name).write_bytes(resp.content)
        click.echo(f"{out / name} ({len(resp.content)} bytes)")
    if data.get("dirty"):
        click.echo("note: the agent left uncommitted changes", err=True)
    if problems:
        sys.exit(1)
