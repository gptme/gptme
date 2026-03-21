"""Skill installer for gptme marketplace.

Supports installing skills from:
- Git URLs (cloned to user skills directory)
- Local paths (copied to user skills directory)
- Registry names (resolved from gptme-contrib index)
"""

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..dirs import get_config_dir

logger = logging.getLogger(__name__)

# Default registry: gptme-contrib GitHub repo
DEFAULT_REGISTRY_REPO = "https://github.com/gptme/gptme-contrib.git"
DEFAULT_REGISTRY_SKILLS_PATH = "skills"

# Where user-installed skills live
SKILLS_DIR_NAME = "skills"
MANIFEST_FILE = "installed-skills.yaml"


@dataclass
class InstalledSkill:
    """Record of an installed skill."""

    name: str
    version: str
    source: str  # git URL, local path, or "registry"
    install_path: str
    installed_at: str = ""


@dataclass
class SkillManifest:
    """Tracks all installed skills."""

    skills: dict[str, InstalledSkill] = field(default_factory=dict)

    def save(self, path: Path) -> None:
        """Save manifest to YAML file."""
        data = {
            "version": "1",
            "skills": {
                name: {
                    "name": skill.name,
                    "version": skill.version,
                    "source": skill.source,
                    "install_path": skill.install_path,
                    "installed_at": skill.installed_at,
                }
                for name, skill in self.skills.items()
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SkillManifest":
        """Load manifest from YAML file."""
        if not path.exists():
            return cls()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not data or "skills" not in data:
                return cls()
            manifest = cls()
            for name, skill_data in data["skills"].items():
                manifest.skills[name] = InstalledSkill(**skill_data)
            return manifest
        except Exception as e:
            logger.warning(f"Failed to load manifest: {e}")
            return cls()


def get_skills_dir() -> Path:
    """Get the user skills installation directory."""
    return get_config_dir() / SKILLS_DIR_NAME


def get_manifest_path() -> Path:
    """Get the path to the installed skills manifest."""
    return get_config_dir() / MANIFEST_FILE


def get_manifest() -> SkillManifest:
    """Load the current skill manifest."""
    return SkillManifest.load(get_manifest_path())


def _parse_skill_frontmatter(skill_md: Path) -> dict:
    """Parse SKILL.md frontmatter to extract metadata."""
    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}


def _sanitize_skill_name(name: str) -> str:
    """Sanitize a skill name to prevent path traversal attacks.

    Strips directory components (e.g. '../../evil') leaving only the
    final path component so names cannot escape the skills directory.
    Note: Path('..').name returns '..' unchanged, so we handle that explicitly.
    """
    safe = Path(name).name
    # Path(".").name → ".", Path("..").name → ".." — must reject these
    if safe in ("", ".", ".."):
        return "unnamed-skill"
    return safe


def _find_skill_md(directory: Path) -> Path | None:
    """Find SKILL.md in a directory (case-insensitive)."""
    if not directory.is_dir():
        return None
    for f in directory.iterdir():
        if f.name.lower() == "skill.md":
            return f
    return None


def _resolve_registry_skill(name: str) -> str | None:
    """Resolve a skill name to a git URL + path using the registry.

    Checks gptme-contrib for a matching skill directory.
    Returns the clone URL if found, None otherwise.
    """
    # Validate name is a reasonable skill identifier before constructing a URL.
    # This makes the None branch reachable (preventing a 60-second git clone
    # timeout on typos) and ensures the resolved URL is safe.
    safe = _sanitize_skill_name(name)
    if not safe or not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9\-_]*$", safe):
        return None
    # Skills in gptme-contrib follow the convention: skills/<name>/
    # In the future this could query a remote index.json.
    return f"{DEFAULT_REGISTRY_REPO}#skills/{safe}"


def _is_git_url(source: str) -> bool:
    """Check if source looks like a git URL."""
    return source.startswith(("https://", "git@", "ssh://", "git://"))


def _clone_skill_from_git(url: str, skill_path: str | None, dest: Path) -> Path | None:
    """Clone a skill from a git repository.

    Args:
        url: Git repository URL
        skill_path: Optional path within the repo to the skill directory
        dest: Destination directory for the skill

    Returns:
        Path to the installed skill, or None on failure
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "repo"
        try:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--filter=blob:none",
                    "--sparse",
                    url,
                    str(tmp),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Git clone failed: {e.stderr}")
            return None
        except subprocess.TimeoutExpired:
            logger.error("Git clone timed out after 60 seconds")
            return None
        except FileNotFoundError:
            logger.error("git command not found")
            return None

        # If skill_path specified, enable sparse checkout for just that path
        if skill_path:
            try:
                subprocess.run(
                    ["git", "sparse-checkout", "set", skill_path],
                    cwd=tmp,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except subprocess.CalledProcessError as e:
                logger.warning(f"Sparse checkout failed, using full clone: {e.stderr}")
            except subprocess.TimeoutExpired:
                logger.warning("Sparse checkout timed out, using full clone")

            source_dir = tmp / skill_path
        else:
            source_dir = tmp

        if not source_dir.exists():
            logger.error(f"Skill path not found in repository: {skill_path}")
            return None

        # Find SKILL.md
        skill_md = _find_skill_md(source_dir)
        if not skill_md:
            logger.error(f"No SKILL.md found in {source_dir}")
            return None

        # Copy skill directory to destination
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source_dir, dest)
        return dest


def install_skill(
    source: str,
    name: str | None = None,
    force: bool = False,
) -> tuple[bool, str]:
    """Install a skill from a source.

    Args:
        source: Skill source — one of:
            - Skill name (resolved from registry)
            - Git URL (with optional #path suffix)
            - Local filesystem path
        name: Override skill name (auto-detected from SKILL.md if not provided)
        force: Overwrite existing installation

    Returns:
        Tuple of (success, message)
    """
    from datetime import datetime, timezone

    skills_dir = get_skills_dir()
    skills_dir.mkdir(parents=True, exist_ok=True)
    manifest = get_manifest()

    git_url: str | None = None
    git_skill_path: str | None = None
    local_path: Path | None = None

    # Determine source type
    if _is_git_url(source.split("#")[0]):
        # Git URL, possibly with #path suffix
        if "#" in source:
            git_url, git_skill_path = source.rsplit("#", 1)
        else:
            git_url = source
    elif Path(source).expanduser().exists():
        # Local path
        local_path = Path(source).expanduser().resolve()
    else:
        # Assume registry name
        resolved = _resolve_registry_skill(source)
        if resolved and "#" in resolved:
            git_url, git_skill_path = resolved.rsplit("#", 1)
        elif resolved:
            git_url = resolved
        else:
            return False, f"Skill '{source}' not found in registry"

    # Install from source
    if git_url:
        # Determine temporary name for installation
        raw_name = name or (
            git_skill_path.split("/")[-1] if git_skill_path else "skill"
        )
        temp_name = _sanitize_skill_name(raw_name)
        dest = skills_dir / temp_name

        if dest.exists() and not force:
            return (
                False,
                f"Skill '{temp_name}' already installed. Use --force to overwrite.",
            )

        result = _clone_skill_from_git(git_url, git_skill_path, dest)
        if not result:
            return False, f"Failed to install skill from {git_url}"

    elif local_path:
        # Verify it's a valid skill
        if local_path.is_file() and local_path.name.lower() == "skill.md":
            local_path = local_path.parent

        skill_md = _find_skill_md(local_path)
        if not skill_md:
            return False, f"No SKILL.md found in {local_path}"

        # Get name from SKILL.md if not provided; always sanitize to prevent path traversal
        if not name:
            fm = _parse_skill_frontmatter(skill_md)
            raw_name = fm.get("name") or local_path.name
            name = _sanitize_skill_name(raw_name)
        else:
            name = _sanitize_skill_name(name)

        dest = skills_dir / name

        if dest.exists() and not force:
            return False, f"Skill '{name}' already installed. Use --force to overwrite."

        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(local_path, dest, symlinks=True)
    else:
        return False, "Could not determine source type"

    # Parse installed skill metadata
    skill_md = _find_skill_md(dest)
    if not skill_md:
        # Clean up failed install
        if dest.exists():
            shutil.rmtree(dest)
        return False, "Installed files do not contain SKILL.md"

    fm = _parse_skill_frontmatter(skill_md)
    skill_name = _sanitize_skill_name(name or fm.get("name", dest.name))

    # Rename directory if skill name differs from temp name
    if dest.name != skill_name:
        new_dest = skills_dir / skill_name
        if new_dest.exists() and not force:
            shutil.rmtree(dest)
            return (
                False,
                f"Skill '{skill_name}' already installed. Use --force to overwrite.",
            )
        if new_dest.exists():
            shutil.rmtree(new_dest)
        dest.rename(new_dest)
        dest = new_dest

    # Get version from metadata
    metadata = fm.get("metadata", {})
    version = "unknown"
    if isinstance(metadata, dict):
        version = metadata.get("version", "unknown")
    if fm.get("version"):
        version = fm["version"]

    # Update manifest
    manifest.skills[skill_name] = InstalledSkill(
        name=skill_name,
        version=str(version),
        source=source,
        install_path=str(dest),
        installed_at=datetime.now(timezone.utc).isoformat(),
    )
    manifest.save(get_manifest_path())

    return True, f"Installed '{skill_name}' v{version} to {dest}"


def uninstall_skill(name: str) -> tuple[bool, str]:
    """Uninstall a skill by name.

    Args:
        name: Skill name to uninstall

    Returns:
        Tuple of (success, message)
    """
    manifest = get_manifest()
    skills_dir = get_skills_dir()
    safe_name = _sanitize_skill_name(name)

    # Check manifest first
    if safe_name in manifest.skills:
        install_path = Path(manifest.skills[safe_name].install_path).resolve()
        # Validate install_path is within skills_dir to prevent manifest-tampering attacks
        try:
            install_path.relative_to(skills_dir.resolve())
        except ValueError:
            return (
                False,
                f"Refusing to delete '{install_path}': not within skills directory",
            )
        if install_path.exists():
            shutil.rmtree(install_path)
        del manifest.skills[safe_name]
        manifest.save(get_manifest_path())
        return True, f"Uninstalled '{safe_name}'"

    # Try direct directory match
    skill_dir = skills_dir / safe_name
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
        return True, f"Removed '{name}' (not in manifest)"

    return False, f"Skill '{name}' not found"


def validate_skill(path: Path) -> list[str]:
    """Validate a skill directory.

    Args:
        path: Path to skill directory or SKILL.md file

    Returns:
        List of validation errors (empty if valid)
    """
    errors = []

    if path.is_file():
        if path.name.lower() != "skill.md":
            errors.append(f"Expected SKILL.md, got {path.name}")
            return errors
        path = path.parent

    skill_md = _find_skill_md(path)
    if not skill_md:
        errors.append("Missing SKILL.md file")
        return errors

    fm = _parse_skill_frontmatter(skill_md)
    if not fm:
        errors.append("SKILL.md has no YAML frontmatter")
        return errors

    # Required fields
    if not fm.get("name"):
        errors.append("Missing required field: name")
    elif not isinstance(fm["name"], str):
        errors.append("Field 'name' must be a string")

    if not fm.get("description"):
        errors.append("Missing required field: description")
    elif not isinstance(fm["description"], str):
        errors.append("Field 'description' must be a string")

    # Dependency declarations (optional — validated if present)
    depends = fm.get("depends", [])
    if isinstance(depends, str):
        depends = [depends]
    if not isinstance(depends, list):
        errors.append("Field 'depends' must be a list of strings")
    elif depends:
        for dep in depends:
            if not isinstance(dep, str) or not dep.strip():
                errors.append(f"Invalid dependency entry: {dep!r}")
            elif not re.match(r"^[a-zA-Z0-9][\w.\-]*$", dep.strip()):
                errors.append(
                    f"Dependency '{dep}' contains invalid characters. "
                    "Use skill names (alphanumeric, underscores, hyphens, dots)."
                )

    # Marketplace metadata (recommended but not required)
    metadata = fm.get("metadata", {})
    if isinstance(metadata, dict):
        recommended = ["author", "version", "tags"]
        errors.extend(
            f"Missing recommended marketplace field: metadata.{field_name}"
            for field_name in recommended
            if field_name not in metadata
        )

    return errors


def list_installed() -> list[InstalledSkill]:
    """List all installed skills from the manifest."""
    manifest = get_manifest()
    return list(manifest.skills.values())


def _extract_depends(fm: dict) -> list[str]:
    """Extract and normalise the ``depends`` field from parsed SKILL.md frontmatter.

    Handles ``str``, ``list``, ``None`` (YAML null), and other scalars gracefully.
    """
    raw = fm.get("depends", [])
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, list):
        # YAML null parses as None; other scalars (int, float) are also invalid
        raw = []
    return [d for d in raw if isinstance(d, str) and d.strip()]


def check_dependencies(
    skill_names: list[str] | None = None,
) -> list[dict[str, str]]:
    """Check if all skill dependencies are satisfied.

    Args:
        skill_names: Specific skills to check (None = check all installed)

    Returns:
        List of dicts with unsatisfied dependencies:
        [{"skill": "my-skill", "depends": "missing-dep"}, ...]

    Raises:
        KeyError: If any name in ``skill_names`` is not a known installed skill.
    """
    from .index import LessonIndex

    index = LessonIndex()
    manifest = get_manifest()

    # Build set of available skill names
    available: set[str] = set()
    for item in index.lessons:
        if item.metadata.name:
            available.add(item.metadata.name)
    for name in manifest.skills:
        available.add(name)

    missing: list[dict[str, str]] = []

    if skill_names is not None:
        unknown = [n for n in skill_names if n not in available]
        if unknown:
            raise KeyError(
                f"Unknown skill(s): {', '.join(sorted(unknown))}. "
                "Check installed skills with `gptme --list-skills`."
            )
        targets = skill_names
    else:
        # Include all known skills: indexed + manifest-only
        indexed_names = {
            item.metadata.name for item in index.lessons if item.metadata.name
        }
        targets = list(indexed_names) + [
            name for name in manifest.skills if name not in indexed_names
        ]

    for skill_name in targets:
        if not skill_name:
            continue
        # Find the skill in index or manifest
        deps: list[str] = []
        found_in_index = False
        for item in index.lessons:
            if item.metadata.name == skill_name:
                deps = item.metadata.depends
                found_in_index = True
                break
        if not found_in_index and skill_name in manifest.skills:
            # Check manifest entry's SKILL.md
            skill_path = Path(manifest.skills[skill_name].install_path)
            skill_md = _find_skill_md(skill_path)
            if skill_md:
                fm = _parse_skill_frontmatter(skill_md)
                deps = _extract_depends(fm)

        missing.extend(
            {"skill": skill_name, "depends": dep}
            for dep in deps
            if dep not in available
        )

    return missing


def dependency_graph() -> dict[str, list[str]]:
    """Build a dependency graph of all skills with dependencies.

    Returns:
        Dict mapping skill name to its dependency list.
        Only includes skills that have dependencies.

    Raises:
        ValueError: If circular dependencies are detected.
    """
    from .index import LessonIndex

    index = LessonIndex()
    manifest = get_manifest()
    graph: dict[str, list[str]] = {}

    indexed_names: set[str] = set()
    for item in index.lessons:
        if item.metadata.name and item.metadata.depends:
            graph[item.metadata.name] = list(item.metadata.depends)
        if item.metadata.name:
            indexed_names.add(item.metadata.name)

    # Include manifest-only skills (installed via git URL or local path, not in index)
    for name, skill_info in manifest.skills.items():
        if name not in indexed_names:
            skill_path = Path(skill_info.install_path)
            skill_md = _find_skill_md(skill_path)
            if skill_md:
                fm = _parse_skill_frontmatter(skill_md)
                deps = _extract_depends(fm)
                if deps:
                    graph[name] = deps

    # Detect circular dependencies (iterative DFS — no recursion limit risk)
    cycles: list[str] = []
    visited: set[str] = set()

    for start in graph:
        if start in visited:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: set[str] = set()
        while stack:
            node, idx = stack.pop()
            if idx == 0:
                if node in path:
                    # Back-edge — cycle detected
                    cycle = f"{stack[-1][0]} -> {node}" if stack else node
                    cycles.append(cycle)
                    logger.warning(f"Circular dependency detected: {cycle}")
                    continue
                if node in visited:
                    continue
                visited.add(node)
                path.add(node)
            deps = graph.get(node, [])
            if idx < len(deps):
                # Push current node back with incremented index, then push child
                stack.append((node, idx + 1))
                stack.append((deps[idx], 0))
            else:
                path.discard(node)

    if cycles:
        raise ValueError(f"Circular dependencies detected: {', '.join(cycles)}")

    return graph


# --- Skill template for init ---

SKILL_TEMPLATE = """\
---
name: {name}
description: {description}
# depends: [other-skill]  # Optional: list skill dependencies
metadata:
  author: {author}
  version: "0.1.0"
  tags: [{tags}]
  license: MIT
---

# {title}

## Instructions

Describe what this skill does and when to use it.

## Examples

```bash
# Example usage
```
"""


def init_skill(
    path: Path,
    name: str | None = None,
    description: str = "A new gptme skill",
    author: str = "",
    tags: str = "",
) -> tuple[bool, str]:
    """Scaffold a new skill directory with SKILL.md template.

    Args:
        path: Directory to create the skill in
        name: Skill name (defaults to directory name)
        description: Short description
        author: Author name
        tags: Comma-separated tags

    Returns:
        Tuple of (success, message)
    """
    skill_name = name or path.name

    if path.exists():
        skill_md = _find_skill_md(path)
        if skill_md:
            return False, f"Skill already exists at {path} (has SKILL.md)"

    path.mkdir(parents=True, exist_ok=True)

    # Format tags for YAML
    tag_list = ", ".join(f'"{t.strip()}"' for t in tags.split(",") if t.strip())

    # Create SKILL.md from template.
    # Escape braces in user-supplied values to prevent KeyError when
    # the template is rendered via str.format().  Input like "Parses JSON {key}"
    # would otherwise raise KeyError instead of writing the file.
    def _esc(s: str) -> str:
        return s.replace("{", "{{").replace("}", "}}")

    title = skill_name.replace("-", " ").replace("_", " ").title()
    content = SKILL_TEMPLATE.format(
        name=_esc(skill_name),
        description=_esc(description),
        author=_esc(author or "your-name"),
        tags=tag_list,
        title=_esc(title),
    )

    skill_md_path = path / "SKILL.md"
    skill_md_path.write_text(content, encoding="utf-8")

    return True, f"Created skill '{skill_name}' at {path}"


def publish_skill(path: Path) -> tuple[bool, str, Path | None]:
    """Validate and package a skill for sharing/submission.

    Creates a .tar.gz archive of the skill directory, ready for
    submission to the gptme-contrib registry or manual sharing.

    Args:
        path: Path to skill directory or SKILL.md file

    Returns:
        Tuple of (success, message, archive_path or None)
    """
    import tarfile

    if path.is_file():
        if path.name.lower() == "skill.md":
            path = path.parent
        else:
            return False, f"Expected a directory or SKILL.md, got {path.name}", None

    # Validate first
    errors = validate_skill(path)
    real_errors = [e for e in errors if "recommended" not in e.lower()]
    warnings = [e for e in errors if "recommended" in e.lower()]

    if real_errors:
        error_list = "\n".join(f"  - {e}" for e in real_errors)
        return False, f"Skill validation failed:\n{error_list}", None

    # Get skill metadata
    skill_md = _find_skill_md(path)
    if skill_md is None:
        return False, "SKILL.md disappeared unexpectedly after validation", None
    fm = _parse_skill_frontmatter(skill_md)
    # Sanitize skill_name and version from frontmatter to prevent archive path traversal.
    # A crafted SKILL.md with name: ../../../../tmp/evil could write outside the dir.
    skill_name = _sanitize_skill_name(fm.get("name", path.name))
    metadata = fm.get("metadata", {})
    version_raw = (
        metadata.get("version", "0.0.0") if isinstance(metadata, dict) else "0.0.0"
    )
    version = re.sub(r"[^\w.\-]", "_", str(version_raw))

    # Create archive
    archive_name = f"{skill_name}-{version}.tar.gz"
    archive_path = path.parent / archive_name

    with tarfile.open(archive_path, "w:gz") as tar:
        # Add all files in the skill directory under the skill name prefix
        for item in sorted(path.rglob("*")):
            # Skip symlinks (could expose files outside the skill directory)
            if item.is_symlink():
                continue
            if item.is_file():
                # Skip hidden files and __pycache__
                rel = item.relative_to(path)
                if any(p.startswith(".") or p == "__pycache__" for p in rel.parts):
                    continue
                arcname = f"{skill_name}/{rel}"
                tar.add(item, arcname=arcname)

    warning_text = ""
    if warnings:
        warning_text = "\n  Warnings:\n" + "\n".join(f"    - {w}" for w in warnings)

    message = (
        f"Packaged '{skill_name}' v{version} → {archive_path}{warning_text}\n\n"
        f"To share this skill:\n"
        f"  1. Others can install with: gptme-util skills install {path}\n"
        f"  2. To submit to the registry, open a PR to gptme-contrib:\n"
        f"     - Copy your skill directory to skills/{skill_name}/\n"
        f"     - PR: https://github.com/gptme/gptme-contrib/compare/master...your-branch"
    )

    return True, message, archive_path
