"""CLI commands for skills and lessons management."""

import sys
from pathlib import Path

import click


@click.group()
def skills():
    """Browse and inspect skills and lessons."""


@skills.command("list")
@click.option("--all", "show_all", is_flag=True, help="Show both skills and lessons")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def skills_list(show_all: bool, json_output: bool):
    """List available skills (and optionally lessons)."""
    from ..lessons.index import LessonIndex

    index = LessonIndex()

    if not index.lessons:
        click.echo("No skills or lessons found.")
        return

    # Separate skills and lessons
    skills_items = [item for item in index.lessons if item.metadata.name]
    lessons_items = [item for item in index.lessons if not item.metadata.name]

    if json_output:
        import json

        result: dict = {
            "skills": [
                {
                    "name": s.metadata.name,
                    "description": s.metadata.description or s.description,
                    "path": str(s.path),
                    "category": s.category,
                }
                for s in sorted(skills_items, key=lambda s: s.metadata.name or "")
            ],
        }
        if show_all:
            result["lessons"] = [
                {
                    "title": lesson.title,
                    "category": lesson.category,
                    "keywords": lesson.metadata.keywords[:5],
                    "path": str(lesson.path),
                }
                for lesson in sorted(lessons_items, key=lambda x: x.title)
            ]
        click.echo(json.dumps(result, indent=2))
        return

    # Skills
    if skills_items:
        skills_sorted = sorted(skills_items, key=lambda s: s.metadata.name or "")
        click.echo(f"Skills ({len(skills_sorted)}):\n")
        for skill in skills_sorted:
            name = skill.metadata.name
            desc = skill.metadata.description or skill.description or ""
            if len(desc) > 60:
                desc = desc[:57] + "..."
            click.echo(f"  {name or '':30s} {desc}")
    else:
        click.echo("No skills found.")

    if not show_all:
        if lessons_items:
            click.echo(f"\n({len(lessons_items)} lessons available, use --all to show)")
        return

    # Lessons (grouped by category)
    if lessons_items:
        click.echo(f"\nLessons ({len(lessons_items)}):\n")
        by_category: dict[str, list] = {}
        for lesson in lessons_items:
            by_category.setdefault(lesson.category, []).append(lesson)

        for cat in sorted(by_category.keys()):
            click.echo(f"  [{cat}]")
            for lesson in sorted(by_category[cat], key=lambda x: x.title):
                click.echo(f"    {lesson.title}")
            click.echo()


@skills.command("show")
@click.argument("name")
def skills_show(name: str):
    """Show the full content of a skill or lesson."""
    from ..lessons.index import LessonIndex

    index = LessonIndex()

    if not index.lessons:
        click.echo("No skills or lessons found.")
        return

    name_lower = name.lower()

    # Search by skill name first, then lesson title/filename
    for item in index.lessons:
        if item.metadata.name and name_lower in item.metadata.name.lower():
            click.echo(f"# {item.metadata.name}")
            if item.metadata.description:
                click.echo(f"\n{item.metadata.description}")
            click.echo(f"\nPath: {item.path}\n")
            click.echo(item.body)
            return

    for item in index.lessons:
        if name_lower in item.title.lower() or name_lower in item.path.stem.lower():
            click.echo(f"# {item.title}")
            click.echo(f"\nPath: {item.path}\n")
            click.echo(item.body)
            return

    click.echo(f"Skill or lesson not found: {name}")
    sys.exit(1)


@skills.command("search")
@click.argument("query")
@click.option("-n", "--limit", default=10, help="Maximum number of results")
def skills_search(query: str, limit: int):
    """Search skills and lessons by keyword."""
    from ..lessons.index import LessonIndex

    index = LessonIndex()

    if not index.lessons:
        click.echo("No skills or lessons found.")
        return

    results = index.search(query)

    if not results:
        click.echo(f"No results for '{query}'")
        return

    results = results[:limit]
    click.echo(f"Results for '{query}' ({len(results)}):\n")

    for item in results:
        if item.metadata.name:
            label = f"[skill] {item.metadata.name}"
        else:
            label = f"[{item.category}] {item.title}"
        desc = item.metadata.description or item.description or ""
        if len(desc) > 50:
            desc = desc[:47] + "..."
        click.echo(f"  {label:40s} {desc}")


@skills.command("dirs")
def skills_dirs():
    """Show directories searched for skills and lessons."""
    from ..lessons.index import LessonIndex

    index = LessonIndex()

    click.echo("Skill/lesson directories:\n")
    for d in index.lesson_dirs:
        exists = d.exists()
        count = 0
        if exists:
            count = len(list(d.rglob("*.md"))) + len(list(d.rglob("*.mdc")))
        status = f"{count} files" if exists else "not found"
        icon = "+" if exists else "-"
        click.echo(f"  {icon} {d}  ({status})")


@skills.command("check")
@click.option(
    "--workspace",
    default=".",
    show_default=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Agent workspace to check (default: current directory).",
)
def skills_check(workspace: Path):
    """Validate lesson/skill health: frontmatter, keywords, sizing."""
    from ..agent.doctor import DoctorReport, check_lessons

    report = DoctorReport()
    check_lessons(workspace.resolve(), report)

    if not report.results:
        click.echo("No lesson directories found.")
        sys.exit(1)

    for result in report.results:
        click.echo(f"  {result.emoji} {result.name}: {result.message}")

    if report.errors:
        sys.exit(1)


@skills.command("install")
@click.argument("source")
@click.option("--name", "-n", help="Override skill name")
@click.option("--force", "-f", is_flag=True, help="Overwrite existing installation")
def skills_install(source: str, name: str | None, force: bool):
    """Install a skill from a source.

    SOURCE can be:

    \b
      - A skill name from the registry (e.g. 'code-review-helper')
      - A git URL (e.g. 'https://github.com/user/skills.git#path/to/skill')
      - A local path to a skill directory (e.g. './my-skill/')
    """
    from ..lessons.installer import install_skill

    click.echo(f"Installing skill from '{source}'...")
    success, message = install_skill(source, name=name, force=force)
    if success:
        click.echo(f"  {message}")
    else:
        click.echo(f"Error: {message}", err=True)
        sys.exit(1)


@skills.command("uninstall")
@click.argument("name")
def skills_uninstall(name: str):
    """Uninstall a skill by name."""
    from ..lessons.installer import uninstall_skill

    success, message = uninstall_skill(name)
    if success:
        click.echo(message)
    else:
        click.echo(f"Error: {message}", err=True)
        sys.exit(1)


@skills.command("validate")
@click.argument("path", type=click.Path(exists=True))
def skills_validate(path: str):
    """Validate a skill directory or SKILL.md file.

    Checks for required frontmatter fields and marketplace metadata.
    """
    from ..lessons.installer import validate_skill

    all_issues = validate_skill(Path(path))
    # Separate "recommended" warnings from real errors, matching publish_skill behavior
    real_errors = [e for e in all_issues if "recommended" not in e.lower()]
    warnings = [e for e in all_issues if "recommended" in e.lower()]

    if warnings:
        click.echo(f"Warnings ({len(warnings)}):")
        for w in warnings:
            click.echo(f"  - {w}")
    if real_errors:
        click.echo(f"Validation errors ({len(real_errors)}):")
        for error in real_errors:
            click.echo(f"  - {error}")
        sys.exit(1)
    elif warnings:
        click.echo("Skill is valid (with warnings).")
    else:
        click.echo("Skill is valid.")


@skills.command("installed")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def skills_installed(json_output: bool):
    """List installed skills from the user's skill directory."""
    from ..lessons.installer import list_installed

    installed = list_installed()

    if not installed:
        click.echo(
            "No skills installed. Use 'gptme-util skills install <name>' to install."
        )
        return

    if json_output:
        import json

        result = [
            {
                "name": s.name,
                "version": s.version,
                "source": s.source,
                "path": s.install_path,
                "installed_at": s.installed_at,
            }
            for s in installed
        ]
        click.echo(json.dumps(result, indent=2))
        return

    click.echo(f"Installed skills ({len(installed)}):\n")
    for skill in sorted(installed, key=lambda s: s.name):
        click.echo(f"  {skill.name:30s} v{skill.version:10s} ({skill.source})")


@skills.command("init")
@click.argument("path", type=click.Path())
@click.option("--name", "-n", help="Skill name (defaults to directory name)")
@click.option(
    "--description", "-d", default="A new gptme skill", help="Short description"
)
@click.option("--author", "-a", default="", help="Author name")
@click.option("--tags", "-t", default="", help="Comma-separated tags")
def skills_init(path: str, name: str | None, description: str, author: str, tags: str):
    """Create a new skill from a template.

    PATH is the directory to create the skill in.

    \b
    Example:
      gptme-util skills init ./my-skill --name my-skill -d "Does cool things"
    """
    from ..lessons.installer import init_skill

    target = Path(path).resolve()
    success, message = init_skill(
        target, name=name, description=description, author=author, tags=tags
    )
    if success:
        click.echo(f"  {message}")
        click.echo("\n  Next steps:")
        click.echo(f"    1. Edit {target}/SKILL.md with your instructions")
        click.echo(f"    2. Add supporting scripts/files to {target}/")
        click.echo(f"    3. Validate: gptme-util skills validate {target}")
        click.echo(f"    4. Publish: gptme-util skills publish {target}")
    else:
        click.echo(f"Error: {message}", err=True)
        sys.exit(1)


@skills.command("publish")
@click.argument("path", type=click.Path(exists=True))
def skills_publish(path: str):
    """Validate and package a skill for sharing.

    PATH is the skill directory (containing SKILL.md).

    Creates a .tar.gz archive and shows instructions for submitting
    to the gptme-contrib registry.
    """
    from ..lessons.installer import publish_skill

    target = Path(path).resolve()
    success, message, _archive_path = publish_skill(target)
    if success:
        click.echo(message)
    else:
        click.echo(f"Error: {message}", err=True)
        sys.exit(1)
