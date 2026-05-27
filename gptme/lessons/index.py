"""Lesson index for discovery and search."""

import json
import logging
import os
from pathlib import Path

from .parser import Lesson, LessonMetadata, parse_lesson

logger = logging.getLogger(__name__)

# Module-level cache for parsed lessons
# Key: lesson file path, Value: (mtime, parsed_lesson)
_LESSON_CACHE: dict[Path, tuple[float, Lesson]] = {}


def _get_cached_lesson(path: Path) -> Lesson | None:
    """Get cached lesson if still valid.

    Args:
        path: Path to lesson file

    Returns:
        Cached lesson if valid, None otherwise
    """
    if path not in _LESSON_CACHE:
        return None

    cached_mtime, cached_lesson = _LESSON_CACHE[path]

    try:
        current_mtime = path.stat().st_mtime
        if current_mtime == cached_mtime:
            logger.debug(f"Cache hit: {path.name}")
            return cached_lesson
        logger.debug(f"Cache invalidated (mtime changed): {path.name}")
    except FileNotFoundError:
        # File was deleted, remove from cache
        logger.debug(f"Cache invalidated (file deleted): {path.name}")
        del _LESSON_CACHE[path]

    return None


def _cache_lesson(path: Path, lesson: Lesson) -> None:
    """Cache a parsed lesson.

    Args:
        path: Path to lesson file
        lesson: Parsed lesson
    """
    try:
        mtime = path.stat().st_mtime
        _LESSON_CACHE[path] = (mtime, lesson)
        logger.debug(f"Cached lesson: {path.name}")
    except FileNotFoundError:
        logger.warning(f"Cannot cache lesson, file not found: {path}")


def clear_cache() -> None:
    """Clear the lesson cache.

    Useful for testing or when lessons are known to have changed.
    """
    global _LESSON_CACHE
    cache_size = len(_LESSON_CACHE)
    _LESSON_CACHE.clear()
    logger.info(f"Cleared lesson cache ({cache_size} entries)")


def get_cache_stats() -> dict[str, int]:
    """Get cache statistics.

    Returns:
        Dictionary with cache statistics
    """
    return {
        "cached_lessons": len(_LESSON_CACHE),
    }


class LessonIndex:
    """Index of available lessons with search capabilities."""

    def __init__(self, lesson_dirs: list[Path] | None = None):
        """Initialize lesson index.

        Args:
            lesson_dirs: Directories to search for lessons.
                         If None, uses default locations.
        """
        self.lesson_dirs = lesson_dirs or self._default_dirs()
        self.lessons: list[Lesson] = []
        self._index_lessons()

    @staticmethod
    def _default_dirs() -> list[Path]:
        """Get default lesson and skill directories.

        Searches for lessons/skills in:

        User-level (lessons):
        - ~/.config/gptme/lessons
        - ~/.agents/lessons (cross-platform standard)

        User-level (skills, Anthropic SKILL.md format):
        - ~/.config/gptme/skills
        - ~/.claude/skills (Claude CLI compatibility)
        - ~/.agents/skills (cross-platform standard)

        Workspace-level (lessons):
        - ./lessons
        - ./.gptme/lessons

        Workspace-level (skills):
        - ./skills
        - ./.gptme/skills

        Also:
        - Configured directories from gptme.toml
        - .cursor/ directory (Cursor rules compatibility)

        Note: Skills use the same parser as lessons but with
        'name' and 'description' frontmatter instead of 'keywords'.
        """
        from pathlib import Path

        from ..config import get_config

        dirs = []

        # === User-level lessons directories ===

        # gptme native config
        config_dir = Path.home() / ".config" / "gptme" / "lessons"
        if config_dir.exists():
            dirs.append(config_dir)

        # Cross-platform standard (~/.agents/lessons)
        agents_lessons_dir = Path.home() / ".agents" / "lessons"
        if agents_lessons_dir.exists():
            dirs.append(agents_lessons_dir)

        # === User-level skills directories ===

        # gptme native skills
        gptme_skills_dir = Path.home() / ".config" / "gptme" / "skills"
        if gptme_skills_dir.exists():
            dirs.append(gptme_skills_dir)

        # Claude CLI compatibility (~/.claude/skills)
        claude_skills_dir = Path.home() / ".claude" / "skills"
        if claude_skills_dir.exists():
            dirs.append(claude_skills_dir)

        # Cross-platform standard (~/.agents/skills)
        agents_skills_dir = Path.home() / ".agents" / "skills"
        if agents_skills_dir.exists():
            dirs.append(agents_skills_dir)

        # === Workspace-level lessons directories ===

        # Current workspace lessons
        workspace_dir = Path.cwd() / "lessons"
        if workspace_dir.exists():
            dirs.append(workspace_dir)

        # Project-local lessons (.gptme/lessons/)
        gptme_lessons_dir = Path.cwd() / ".gptme" / "lessons"
        if gptme_lessons_dir.exists():
            dirs.append(gptme_lessons_dir)

        # === Workspace-level skills directories ===

        # Current workspace skills
        workspace_skills_dir = Path.cwd() / "skills"
        if workspace_skills_dir.exists():
            dirs.append(workspace_skills_dir)

        # Project-local skills (.gptme/skills/)
        gptme_project_skills_dir = Path.cwd() / ".gptme" / "skills"
        if gptme_project_skills_dir.exists():
            dirs.append(gptme_project_skills_dir)

        # Cursor rules directory (.cursor/)
        cursor_dir = Path.cwd() / ".cursor"
        if cursor_dir.exists():
            dirs.append(cursor_dir)
            logger.debug("Using Cursor rules from .cursor/ directory")

        # Check for legacy .cursorrules file and provide guidance
        cursorrules_file = Path.cwd() / ".cursorrules"
        if cursorrules_file.exists():
            logger.debug(
                "Found .cursorrules file, consider migrating to .cursor/ directory with .mdc files"
            )

        # Extra directories from environment variable (colon-separated)
        # Useful for injecting external lesson sets (e.g., agent workspace lessons
        # during eval runs, or shared lesson libraries across projects)
        extra_dirs_env = os.environ.get("GPTME_LESSONS_EXTRA_DIRS", "")
        if extra_dirs_env:
            for dir_str in extra_dirs_env.split(":"):
                dir_str = dir_str.strip()
                if dir_str:
                    extra_dir = Path(dir_str).expanduser()
                    if extra_dir.exists():
                        dirs.append(extra_dir)
                        logger.debug(f"Added extra lesson dir from env: {extra_dir}")
                    else:
                        logger.warning(
                            f"GPTME_LESSONS_EXTRA_DIRS: directory not found: {extra_dir}"
                        )

        # Configured directories from config
        config = get_config()

        # User config lessons directories
        if config.user and config.user.lessons and config.user.lessons.dirs:
            for dir_str in config.user.lessons.dirs:
                lesson_dir = Path(dir_str).expanduser()
                if lesson_dir.exists():
                    dirs.append(lesson_dir)

        # Project config lessons directories
        if config.project and config.project.lessons.dirs:
            for dir_str in config.project.lessons.dirs:
                lesson_dir = Path(dir_str)
                # Make relative paths relative to config file location or cwd
                if not lesson_dir.is_absolute():
                    lesson_dir = Path.cwd() / lesson_dir
                if lesson_dir.exists():
                    dirs.append(lesson_dir)

        # Plugin lesson directories (auto-discovered from [plugins].paths)
        if config.project and config.project.plugins.paths:
            from ..plugins import discover_plugins

            plugin_paths = [
                Path(p).expanduser().resolve() for p in config.project.plugins.paths
            ]
            plugins = discover_plugins(plugin_paths)
            for plugin in plugins:
                # Check for lessons/ directory in plugin
                # For flat layout: lessons/ is directly in plugin.path
                # For src/ layout: plugin.path is src/module/, so lessons/ is at project root
                plugin_lessons_dir = plugin.path / "lessons"
                if plugin_lessons_dir.is_dir():
                    dirs.append(plugin_lessons_dir)
                    logger.debug(
                        f"Found plugin lessons: {plugin.name} -> {plugin_lessons_dir}"
                    )
                else:
                    # Check for src/ layout: walk up to find project root
                    # plugin.path might be /project/src/module/, lessons at /project/lessons/
                    project_root = plugin.path.parent.parent
                    if (project_root / "pyproject.toml").exists():
                        project_lessons_dir = project_root / "lessons"
                        if project_lessons_dir.is_dir():
                            dirs.append(project_lessons_dir)
                            logger.debug(
                                f"Found plugin lessons (src layout): {plugin.name} -> {project_lessons_dir}"
                            )

        return dirs

    def _index_lessons(self) -> None:
        """Discover and parse all lessons (with caching and deduplication).

        Deduplication is two-level:
        1. By resolved path (realpath) — catches symlinks to the same file
        2. By relative filename — catches same-named lessons across directories

        Directory order determines precedence (first directory wins):
        1. User config (~/.config/gptme/lessons)
        2. Workspace (./lessons)
        3. Configured dirs (from gptme.toml)
        """
        self.lessons = []
        cache_hits = 0
        cache_misses = 0
        skipped_duplicates = 0
        # Track seen lesson paths (resolved via realpath) for deduplication
        # This handles symlinks pointing to the same file
        seen_paths: set[str] = set()
        # Track seen relative paths for cross-directory deduplication
        # This handles same-named lessons in different configured directories
        # (e.g. lessons/social/foo.md and gptme-contrib/lessons/social/foo.md)
        seen_rel_paths: set[str] = set()

        for lesson_dir in self.lesson_dirs:
            if not lesson_dir.exists():
                logger.debug(f"Lesson directory not found: {lesson_dir}")
                continue

            hits, misses, skipped = self._index_directory(
                lesson_dir, seen_paths, seen_rel_paths
            )
            cache_hits += hits
            cache_misses += misses
            skipped_duplicates += skipped

        log_parts = [f"Indexed {len(self.lessons)} lessons"]
        log_parts.append(f"(cache: {cache_hits} hits, {cache_misses} misses)")
        if skipped_duplicates > 0:
            log_parts.append(f"(skipped {skipped_duplicates} duplicates)")
        logger.debug(" ".join(log_parts))

    def _index_directory(
        self,
        directory: Path,
        seen_paths: set[str],
        seen_rel_paths: set[str],
    ) -> tuple[int, int, int]:
        """Index all lessons in a directory (with caching and deduplication).

        Args:
            directory: Directory to scan for lessons
            seen_paths: Set of resolved lesson paths already indexed (for deduplication)
            seen_rel_paths: Set of relative paths already indexed (cross-dir dedup)

        Returns:
            Tuple of (cache_hits, cache_misses, skipped_duplicates)
        """
        cache_hits = 0
        cache_misses = 0
        skipped_duplicates = 0

        manifest_lessons, manifest_skill_paths = self._load_manifest_skill_stubs(
            directory
        )
        for lesson in manifest_lessons:
            if not self._claim_lesson_slot(
                lesson.path, directory, seen_paths, seen_rel_paths
            ):
                skipped_duplicates += 1
                continue

            if lesson.metadata.status != "active":
                logger.debug(
                    f"Skipping {lesson.metadata.status} lesson: {lesson.path.relative_to(directory)}"
                )
                continue

            self.lessons.append(lesson)

        # Find both .md and .mdc files
        lesson_files = list(directory.rglob("*.md")) + list(directory.rglob("*.mdc"))

        # Exclusion patterns for directories that shouldn't be indexed
        # Worktrees often contain symlinked/copied lesson directories
        excluded_patterns = ["worktree/", "/.git/"]

        for lesson_file in lesson_files:
            if lesson_file in manifest_skill_paths:
                continue

            # Skip special files
            if lesson_file.name.lower() in ("readme.md", "todo.md"):
                continue
            if "template" in lesson_file.name.lower():
                continue

            # Skip files in excluded directories (worktrees, .git, etc.)
            lesson_path_str = lesson_file.as_posix()
            if any(pattern in lesson_path_str for pattern in excluded_patterns):
                logger.debug(f"Skipping lesson in excluded directory: {lesson_file}")
                continue

            if not self._claim_lesson_slot(
                lesson_file, directory, seen_paths, seen_rel_paths
            ):
                skipped_duplicates += 1
                continue

            try:
                lesson, from_cache = self._load_lesson_file(lesson_file)
                if from_cache:
                    cache_hits += 1
                else:
                    cache_misses += 1

                # Filter based on status - only include active lessons
                if lesson.metadata.status != "active":
                    logger.debug(
                        f"Skipping {lesson.metadata.status} lesson: {lesson_file.relative_to(directory)}"
                    )
                    continue

                self.lessons.append(lesson)
            except Exception as e:
                logger.warning(f"Failed to parse lesson {lesson_file}: {e}")

        return cache_hits, cache_misses, skipped_duplicates

    @staticmethod
    def _load_lesson_file(path: Path) -> tuple[Lesson, bool]:
        """Load a lesson file, using the cache when possible."""
        lesson = _get_cached_lesson(path)
        if lesson is not None:
            return lesson, True

        lesson = parse_lesson(path)
        _cache_lesson(path, lesson)
        return lesson, False

    @staticmethod
    def _skill_path_from_manifest_entry(directory: Path, entry_path: str) -> Path:
        """Resolve a manifest entry path to its SKILL.md file."""
        skill_path = directory / Path(entry_path)
        if skill_path.name.lower() != "skill.md":
            skill_path = skill_path / "SKILL.md"
        return skill_path

    def _load_manifest_skill_stubs(
        self, directory: Path
    ) -> tuple[list[Lesson], set[Path]]:
        """Load lightweight skill stubs from ``index.json`` when present."""
        manifest_path = directory / "index.json"
        if not manifest_path.is_file():
            return [], set()

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to read skill manifest {manifest_path}: {e}")
            return [], set()

        if not isinstance(manifest, dict):
            logger.warning(f"Skill manifest {manifest_path} is not a JSON object")
            return [], set()

        skills = manifest.get("skills")
        if not isinstance(skills, list):
            logger.warning(
                f"Skill manifest {manifest_path} is missing a top-level 'skills' list"
            )
            return [], set()

        stubs: list[Lesson] = []
        manifest_paths: set[Path] = set()

        for entry in skills:
            if not isinstance(entry, dict):
                continue

            name = entry.get("name")
            entry_path = entry.get("path")
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(entry_path, str) or not entry_path.strip():
                continue

            skill_file = self._skill_path_from_manifest_entry(directory, entry_path)

            if not skill_file.is_file():
                logger.warning(
                    f"Skill manifest {manifest_path} references missing file: {skill_file}"
                )
                continue

            manifest_paths.add(skill_file)

            raw_keywords = entry.get("keywords", [])
            if isinstance(raw_keywords, str):
                raw_keywords = [raw_keywords]
            elif not isinstance(raw_keywords, list):
                raw_keywords = []
            keywords = [kw for kw in raw_keywords if isinstance(kw, str) and kw.strip()]

            raw_depends = entry.get("depends", [])
            if isinstance(raw_depends, str):
                raw_depends = [raw_depends]
            elif not isinstance(raw_depends, list):
                raw_depends = []
            depends = [dep for dep in raw_depends if isinstance(dep, str) and dep]

            description = entry.get("description", "")
            if not isinstance(description, str):
                description = ""

            status = entry.get("status", "active")
            if not isinstance(status, str):
                status = "active"

            stubs.append(
                Lesson(
                    path=skill_file,
                    metadata=self._manifest_metadata(
                        name=name.strip(),
                        description=description.strip(),
                        keywords=keywords,
                        depends=depends,
                        status=status,
                    ),
                    title=name.strip(),
                    description=description.strip(),
                    category=skill_file.parent.name,
                    body="",
                    is_stub=True,
                )
            )

        return stubs, manifest_paths

    @staticmethod
    def _manifest_metadata(
        *,
        name: str,
        description: str,
        keywords: list[str],
        depends: list[str],
        status: str,
    ):
        return LessonMetadata(
            name=name,
            description=description,
            depends=depends,
            keywords=keywords,
            status=status,
        )

    @staticmethod
    def _claim_lesson_slot(
        lesson_file: Path,
        directory: Path,
        seen_paths: set[str],
        seen_rel_paths: set[str],
    ) -> bool:
        """Reserve a lesson slot for deduplication, first directory wins."""
        try:
            relative_name = lesson_file.relative_to(directory).as_posix()
        except ValueError:
            logger.warning(
                f"Skipping out-of-tree lesson while indexing {directory}: {lesson_file}"
            )
            return False

        resolved_path = os.path.realpath(lesson_file)
        if resolved_path in seen_paths:
            logger.debug(
                f"Skipping duplicate lesson: {relative_name} "
                f"(resolves to already indexed file)"
            )
            return False

        if relative_name in seen_rel_paths:
            logger.debug(
                f"Skipping duplicate lesson: {relative_name} "
                f"(same relative path already indexed from earlier directory)"
            )
            return False

        seen_paths.add(resolved_path)
        seen_rel_paths.add(relative_name)
        return True

    def materialize_lesson(self, lesson: Lesson) -> Lesson:
        """Load the full lesson body/title for manifest-backed skill stubs."""
        if not lesson.is_stub:
            return lesson

        try:
            materialized, _ = self._load_lesson_file(lesson.path)
            return materialized
        except Exception as e:
            logger.warning(f"Failed to materialize stub {lesson.path}: {e}")
            return lesson

    def search(self, query: str) -> list[Lesson]:
        """Search lessons by keyword or content.

        Args:
            query: Search query (case-insensitive)

        Returns:
            List of matching lessons
        """
        query_lower = query.lower()
        results = []

        for lesson in self.lessons:
            # Check skill name (metadata.name)
            if lesson.metadata.name and query_lower in lesson.metadata.name.lower():
                results.append(lesson)
                continue

            # Check title
            if query_lower in lesson.title.lower():
                results.append(lesson)
                continue

            # Check skill description (metadata.description)
            if (
                lesson.metadata.description
                and query_lower in lesson.metadata.description.lower()
            ):
                results.append(lesson)
                continue

            # Check description
            if query_lower in lesson.description.lower():
                results.append(lesson)
                continue

            # Check keywords
            if any(query_lower in kw.lower() for kw in lesson.metadata.keywords):
                results.append(lesson)
                continue

        return [self.materialize_lesson(lesson) for lesson in results]

    def get_by_category(self, category: str) -> list[Lesson]:
        """Get all lessons in a category.

        Args:
            category: Category name (e.g., "tools", "patterns")

        Returns:
            List of lessons in category (stubs are materialized on return)
        """
        return [
            self.materialize_lesson(lesson)
            for lesson in self.lessons
            if lesson.category == category
        ]

    def refresh(self) -> None:
        """Refresh the index by re-parsing all lessons."""
        self._index_lessons()
