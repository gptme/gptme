"""
Minimal project scaffold for gptme.

Creates a lightweight project directory with gptme configuration,
basic agent instructions, and the standard directory structure.

Usage:
    gptme init                # Scaffold current directory
    gptme init my-project     # Create my-project/ with scaffold
    gptme init --interactive  # Prompt for project details
"""

import datetime
import logging
import shutil
import subprocess
from pathlib import Path

import click

logger = logging.getLogger(__name__)

GPTME_TOML_TEMPLATE = """\
[agent]
name = "{name}"

[prompt]
files = [
  "README.md",
  "AGENTS.md",
  "gptme.toml"
]

[lessons]
dirs = ["lessons", "skills"]
"""

AGENTS_MD_TEMPLATE = """\
# Agent Instructions for {name}

This file defines how AI agents should work in this project.
It is auto-loaded by Claude Code and other agent runtimes.

## Project

{description}

## Core Rules

### 1. Absolute Paths

Use `git rev-parse --show-toplevel` for the repo root:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
```

### 2. Conventional Commits

- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation
- `refactor:` — code restructuring
- `test:` — tests
- `chore:` — maintenance

### 3. Stage Files Explicitly

Use `git add <files>`, never `git add .` or `git commit -a`.

### 4. Tasks

Tasks live in `tasks/` with YAML frontmatter.
CLI: `gptodo status|show|edit`

### 5. Journal

Append-only logs in `journal/YYYY-MM-DD/`.
Never modify historical entries.

## Getting Started

```bash
gptme "help me understand this project"
```
"""

TASKS_README_TEMPLATE = """\
# Tasks

Tasks are managed using Markdown files with YAML frontmatter.
CLI: `gptodo` (task management CLI)

## Quick Reference

```bash
gptodo status              # Show all tasks
gptodo show <task-id>      # Task details
gptodo edit <task-id> --set state active
```

## State Machine

backlog → todo → active → ready_for_review → done

Also: waiting (blocked), cancelled, someday

## Template

```yaml
---
state: backlog
created: {date}
priority: medium
---
# Task Title

## Problem
What needs to be done.

## Verification
How to confirm it's done.
```
"""

README_TEMPLATE = """\
# {name}

{description}

## Getting Started

```bash
# Install gptme
pipx install gptme

# Start working
gptme "help me understand this project"
```

## Structure

| Directory | Purpose |
|-----------|---------|
| `tasks/` | Task files with YAML frontmatter |
| `journal/` | Append-only session logs |
| `knowledge/` | Long-term documentation |
"""


def _prompt_name(target: Path, default: str | None = None) -> str:
    """Prompt for project name."""
    from rich.prompt import Prompt

    return Prompt.ask("Project name", default=default or target.name)


def _prompt_description(default: str = "") -> str:
    """Prompt for project description."""
    from rich.prompt import Prompt

    return Prompt.ask(
        "Short description",
        default=default or "A gptme-powered project",
    )


def _confirm(message: str, default: bool = True) -> bool:
    """Ask for confirmation."""
    # Use simple input to avoid rich dependency at runtime
    suffix = " [Y/n]" if default else " [y/N]"
    response = input(f"{message}{suffix} ").strip().lower()
    if not response:
        return default
    return response[0] == "y"


def _create_file(path: Path, content: str, *, target: Path) -> None:
    """Create a file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        logger.warning("Overwriting existing file: %s", path)
    path.write_text(content)
    click.echo(f"  Created {path.relative_to(target)}")


def _scaffold_project(
    target: Path,
    name: str,
    description: str,
    *,
    with_tasks: bool = True,
    with_journal: bool = True,
    with_knowledge: bool = True,
    with_ci: bool = False,
    with_makefile: bool = False,
    with_lessons: bool = False,
    with_skills: bool = False,
    template_repo: str | None = None,
) -> Path:
    """Create the project scaffold.

    Args:
        target: Target directory.
        name: Project name.
        description: Short description.
        with_tasks: Create tasks/ directory.
        with_journal: Create journal/ directory.
        with_knowledge: Create knowledge/ directory.
        with_ci: Create GitHub Actions workflow.
        with_makefile: Create Makefile.
        with_lessons: Create lessons/ directory.
        with_skills: Create skills/ directory.
        template_repo: Custom template repository (org/repo).

    Returns:
        Path to the created project.
    """
    target = target.resolve()

    if template_repo:
        return _scaffold_from_template(target, name, description, template_repo)

    # Create directories
    dirs = []
    if with_tasks:
        dirs.append("tasks")
    if with_journal:
        dirs.append("journal")
    if with_knowledge:
        dirs.append("knowledge")
    if with_lessons:
        dirs.append("lessons")
    if with_skills:
        dirs.append("skills")

    for subdir in dirs:
        (target / subdir).mkdir(parents=True, exist_ok=True)

    # Project files
    _create_file(
        target / "gptme.toml", GPTME_TOML_TEMPLATE.format(name=name), target=target
    )
    _create_file(
        target / "AGENTS.md",
        AGENTS_MD_TEMPLATE.format(name=name, description=description),
        target=target,
    )
    _create_file(
        target / "README.md",
        README_TEMPLATE.format(name=name, description=description),
        target=target,
    )
    if with_tasks:
        _create_file(
            target / "tasks" / "README.md",
            TASKS_README_TEMPLATE.format(
                date=datetime.datetime.now(datetime.timezone.utc).date().isoformat()
            ),
            target=target,
        )

    # Symlink CLAUDE.md -> AGENTS.md
    agents_path = target / "AGENTS.md"
    claude_path = target / "CLAUDE.md"
    if not claude_path.exists():
        try:
            claude_path.symlink_to("AGENTS.md")
            click.echo(f"  Created {claude_path.name} -> AGENTS.md")
        except OSError:
            # Symlinks may fail on some platforms; copy instead
            claude_path.write_text(agents_path.read_text())
            click.echo(f"  Created {claude_path.name} (copy)")

    # Optional: GitHub Actions CI
    if with_ci:
        ci_dir = target / ".github" / "workflows"
        ci_dir.mkdir(parents=True, exist_ok=True)
        ci_file = ci_dir / "test.yml"
        ci_content = """\
name: Test

on:
  push:
    branches: [master, main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.12"
    - name: Check gptme works
      run: |
        pip install gptme
        gptme --version
"""
        _create_file(ci_file, ci_content, target=target)

    # Optional: Makefile
    if with_makefile:
        makefile_content = """\
.PHONY: test typecheck format

test:
\tgptme --version

format:
\truff format .

typecheck:
\tmypy .
"""
        _create_file(target / "Makefile", makefile_content, target=target)

    return target


def _scaffold_from_template(
    target: Path, name: str, description: str, template_repo: str
) -> Path:
    """Create project from a custom template repository."""
    template_url = f"https://github.com/{template_repo}.git"

    # Check if gh is available
    has_gh = shutil.which("gh") is not None

    if has_gh:
        click.echo(f"  Cloning template from {template_repo}...")
        result = subprocess.run(
            ["gh", "repo", "clone", template_repo, str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        click.echo(f"  Cloning template from {template_url}...")
        result = subprocess.run(
            ["git", "clone", template_url, str(target)],
            capture_output=True,
            text=True,
            check=False,
        )

    if result.returncode != 0:
        raise click.ClickException(f"Failed to clone template: {result.stderr.strip()}")

    # Remove .git history to start fresh
    git_dir = target / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir)

    click.echo(f"  Created from template {template_repo}")
    return target


@click.command(
    name="init",
    help="Create a new gptme project scaffold.",
    context_settings={"auto_envvar_prefix": "GPTME"},
)
@click.argument(
    "path",
    type=click.Path(path_type=Path),
    required=False,
    default=None,
)
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    help="Prompt for project details interactively.",
)
@click.option(
    "--template",
    "-t",
    "template_repo",
    type=str,
    default=None,
    help="Custom template repository (org/repo).",
)
@click.option(
    "--name",
    "-n",
    type=str,
    default=None,
    help="Project name (default: directory basename).",
)
@click.option(
    "--description",
    "-d",
    type=str,
    default="",
    help="Short project description.",
)
@click.option(
    "--ci",
    is_flag=True,
    help="Include GitHub Actions CI workflow.",
)
@click.option(
    "--makefile",
    is_flag=True,
    help="Include a Makefile.",
)
@click.option(
    "--lessons",
    is_flag=True,
    help="Include lessons/ directory.",
)
@click.option(
    "--skills",
    is_flag=True,
    help="Include skills/ directory.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing files.",
)
def main(
    path: Path | None,
    interactive: bool,
    template_repo: str | None,
    name: str | None,
    description: str,
    ci: bool,
    makefile: bool,
    lessons: bool,
    skills: bool,
    force: bool,
):
    """Create a new gptme project scaffold.

    Creates a minimal project structure with gptme configuration,
    agent instructions, and standard directories.

    Examples:

        \b
        gptme init my-project          # Create in subdirectory
        gptme init                     # Scaffold current directory
        gptme init --interactive       # Interactive prompts
        gptme init --template gptme/gptme-agent-template my-agent
    """
    # Resolve target directory
    if path is not None:
        target = path.expanduser().resolve()
    else:
        target = Path.cwd().resolve()

    # Interactive mode
    if interactive:
        name = _prompt_name(target=target, default=name)
        description = _prompt_description(description)
        ci = _confirm("Include GitHub Actions CI workflow?", default=ci)
        makefile = _confirm("Include Makefile?", default=makefile)
        lessons = _confirm("Include lessons/ directory?", default=lessons)
        skills = _confirm("Include skills/ directory?", default=skills)
    else:
        name = name or target.name
        description = description or f"{name} — a gptme-powered project"

    # Check if target exists
    if target.exists() and any(target.iterdir()):
        if not force:
            raise click.ClickException(
                f"Target directory {target} already exists and is not empty. "
                "Use --force to overwrite existing files."
            )
        click.echo(f"Overwriting files in {target}...")
    else:
        target.mkdir(parents=True, exist_ok=True)
        click.echo(f"Creating project in {target}...")

    if template_repo and "/" not in template_repo:
        # Allow bare repo name -> gptme/org convention
        template_repo = f"gptme/{template_repo}"

    try:
        _scaffold_project(
            target,
            name=name,
            description=description,
            with_ci=ci,
            with_makefile=makefile,
            with_lessons=lessons,
            with_skills=skills,
            template_repo=template_repo,
        )
    except Exception as e:
        raise click.ClickException(str(e)) from e

    click.echo()
    click.echo(f"✅ Initialized {name} in {target}")
    click.echo()
    click.echo("Next steps:")
    click.echo(f"  cd {target}")
    click.echo("  gptme 'help me understand this project'")
