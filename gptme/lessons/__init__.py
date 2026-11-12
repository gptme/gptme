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

# Submodules are imported lazily (PEP 562): `import gptme` imports
# gptme.lessons.selector, and eager imports here pulled in the command, LLM,
# tool and plugin stack for every process, including latency-sensitive hooks.
# Lesson commands are registered by gptme.commands with the other built-ins.
_lazy: dict[str, tuple[str, str]] = {
    "auto_include_lessons": (".auto_include", "auto_include_lessons"),
    "LessonIndex": (".index", "LessonIndex"),
    "clear_cache": (".index", "clear_cache"),
    "get_cache_stats": (".index", "get_cache_stats"),
    "LessonMatcher": (".matcher", "LessonMatcher"),
    "MatchContext": (".matcher", "MatchContext"),
    "MatchResult": (".matcher", "MatchResult"),
    "Lesson": (".parser", "Lesson"),
    "LessonMetadata": (".parser", "LessonMetadata"),
    "parse_lesson": (".parser", "parse_lesson"),
    "is_skill_command": (".skill_commands", "is_skill_command"),
    "register_skill_commands": (".skill_commands", "register_skill_commands"),
}


def __getattr__(name: str):
    if name in _lazy:
        import importlib

        module_name, attr_name = _lazy[name]
        obj = getattr(
            importlib.import_module(module_name, package=__package__), attr_name
        )
        globals()[name] = obj
        return obj
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Core types
    "Lesson",
    "LessonMetadata",
    "MatchContext",
    "MatchResult",
    # Functions
    "parse_lesson",
    "auto_include_lessons",
    "is_skill_command",
    "register_skill_commands",
    "clear_cache",
    "get_cache_stats",
    # Classes
    "LessonIndex",
    "LessonMatcher",
]
