from .main import main
from .run import execute
from .suites import suites, tests

__all__ = ["main", "execute", "suites", "tests"]

# Lazy imports for leaderboard (avoid import overhead for normal eval runs)
# Usage: from gptme.eval.leaderboard import generate_leaderboard
