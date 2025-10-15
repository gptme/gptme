"""Lesson system for gptme.

Provides structured lessons with metadata that can be automatically included in context.
Similar to .cursorrules but with keyword-based triggering.

Example lesson format:
    ---
    match:
      keywords: [patch, file, editing]
    ---

    # Lesson Title

    Lesson content...
"""

from .auto_include import auto_include_lessons
from .cache import FileCache, MatchCache, get_file_cache, get_match_cache
from .commands import register_lesson_commands
from .index import LessonIndex
from .matcher import LessonMatcher, MatchContext, MatchResult
from .parser import Lesson, LessonMetadata, parse_lesson

# Register commands
register_lesson_commands()

__all__ = [
    # Core types
    "Lesson",
    "LessonMetadata",
    "MatchContext",
    "MatchResult",
    # Functions
    "parse_lesson",
    "auto_include_lessons",
    # Classes
    "LessonIndex",
    "LessonMatcher",
    # Caching
    "FileCache",
    "MatchCache",
    "get_file_cache",
    "get_match_cache",
]
