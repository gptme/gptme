"""Skill installer for gptme marketplace.

Supports installing skills from:
- Git URLs (cloned to user skills directory)
- Local paths (copied to user skills directory)
- Registry names (resolved from gptme-contrib index)
"""

import logging
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


def _find_skill_md(directory: Path) -> Path | None:
    """Find SKILL.md in a directory (case-insensitive)."""
    for f in directory.iterdir():
        if f.name.lower() == "skill.md":
            return f
    return None


def _resolve_registry_skill(name: str) -> str | None:
    """Resolve a skill name to a git URL + path using the registry.

    Checks gptme-contrib for a matching skill directory.
    Returns the clone URL if found, None otherwise.
    """
    # For now, we use a convention: skills in gptme-contrib/skills/<name>/
    # In the future, this could query a remote index.json
    return f"{DEFAULT_REGISTRY_REPO}#skills/{name}"


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
        temp_name = name or (
            git_skill_path.split("/")[-1] if git_skill_path else "skill"
        )
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

        # Get name from SKILL.md if not provided
        if not name:
            fm = _parse_skill_frontmatter(skill_md)
            name = fm.get("name") or local_path.name

        dest = skills_dir / name

        if dest.exists() and not force:
            return False, f"Skill '{name}' already installed. Use --force to overwrite."

        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(local_path, dest)
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
    skill_name = name or fm.get("name", dest.name)

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

    # Check manifest first
    if name in manifest.skills:
        install_path = Path(manifest.skills[name].install_path)
        if install_path.exists():
            shutil.rmtree(install_path)
        del manifest.skills[name]
        manifest.save(get_manifest_path())
        return True, f"Uninstalled '{name}'"

    # Try direct directory match
    skill_dir = skills_dir / name
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
