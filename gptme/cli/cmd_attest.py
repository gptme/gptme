"""CLI commands for gptme attestations."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..attestation import (
    AttestationError,
    create_file_attestation,
    create_text_attestation,
    verify_attestation,
    write_attestation,
)


@click.group()
def attest() -> None:
    """Generate and verify output attestations."""


@attest.command("sign")
@click.argument(
    "target",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--text",
    "inline_text",
    default=None,
    help="Literal text to attest instead of a file.",
)
@click.option(
    "--type",
    "output_type",
    type=click.Choice(["file", "text", "pr_body", "commit_message", "session"]),
    default=None,
    help="Output type metadata to store in the attestation.",
)
@click.option(
    "--workspace",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Workspace root to verify against. Defaults to the current git repo.",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Where to write the attestation JSON. Defaults to state/attestations/.",
)
@click.option("--url", default=None, help="Optional URL for the attested output.")
def sign(
    target: Path | None,
    inline_text: str | None,
    output_type: str | None,
    workspace: Path | None,
    out_path: Path | None,
    url: str | None,
) -> None:
    """Generate a JSON attestation for a file or text payload."""

    if target is not None and inline_text is not None:
        raise click.UsageError("Pass either TARGET or --text, not both.")
    if target is None and inline_text is None:
        raise click.UsageError("Pass a TARGET file or provide --text.")

    try:
        if target is not None:
            attestation, workspace_root = create_file_attestation(
                target,
                workspace=workspace,
                output_type=output_type or "file",
                url=url,
            )
        else:
            attestation, workspace_root = create_text_attestation(
                inline_text or "",
                workspace=workspace,
                output_type=output_type or "text",
                url=url,
            )
        written = write_attestation(
            attestation,
            workspace_root=workspace_root,
            destination=out_path,
        )
    except AttestationError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(str(written))


@attest.command("verify")
@click.argument(
    "attestation_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--workspace",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Workspace root to verify against. Defaults to the current git repo.",
)
@click.option(
    "--content-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Explicit file to hash when the attestation does not embed a path.",
)
@click.option(
    "--text",
    "inline_text",
    default=None,
    help="Literal text to hash instead of a file when verifying text attestations.",
)
@click.option("--json", "as_json", is_flag=True, help="Print the attestation as JSON.")
def verify(
    attestation_path: Path,
    workspace: Path | None,
    content_file: Path | None,
    inline_text: str | None,
    as_json: bool,
) -> None:
    """Verify an attestation id, workspace commit, and content hash."""

    if content_file is not None and inline_text is not None:
        raise click.UsageError("Pass either --content-file or --text, not both.")

    try:
        attestation = verify_attestation(
            attestation_path,
            workspace=workspace,
            content_path=content_file,
            text=inline_text,
        )
    except AttestationError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(attestation, indent=2, sort_keys=True))
        return

    click.echo(f"verified {attestation['id']}")


def main() -> None:
    """Entry point for the standalone gptme-attest script."""
    attest()


if __name__ == "__main__":
    main()
