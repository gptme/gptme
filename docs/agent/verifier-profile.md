# Verifier Profile

The `verifier` profile is a built-in gptme profile for subagents whose job is to
validate, test, or audit work produced by another subagent or the parent session.

## Motivation

Subagent delegation follows a natural pattern: one agent builds, another
reviews. Without an explicit `verifier` profile, callers had to encode
verification posture indirectly through prompt phrasing and parameter
bundles. The `verifier` profile makes the contract explicit.

## Behavior

| Attribute | Value |
|-----------|-------|
| System prompt | "You are in VERIFIER mode." — focuses on validation, testing, and correctness |
| Tool access | `read`, `shell`, `ipython`, `chats` — can read code, execute tests, analyze results, and query conversation history. No network access (no `browser`), no file modification (no `save`/`patch`) |
| Behavior rules | `read_only=True` — soft prompting only, not hard-enforced at the tool level for subprocess/ACP modes |

## Usage

```python
# Auto-detected from agent_id or alias
subagent("verifier", "Check that the refactored module passes all existing tests")

# Auto-detected from alias
subagent("verify", "Review the PR diff for correctness issues")

# Explicit profile parameter overrides auto-detection
subagent("my-reviewer", "Review this code", profile="verifier")
```

## Implementation

- Defined in `gptme/profiles.py` in the `BUILTIN_PROFILES` dict
- Tool restrictions: `["read", "shell", "ipython", "chats"]` — can analyze and test but not modify files or browse
- Behavior: `read_only=True` (soft prompt, not hard-enforced in subprocess mode)
- Alias `"verify"` mapped via `profile_aliases` dict in `gptme/tools/subagent/api.py`

## Precedence

Role resolution follows deterministic priority:
1. Explicit `profile=` parameter (highest priority)
2. Profile auto-detection from `agent_id` matching a profile name
3. Profile alias resolution (e.g., `"verify"` → `"verifier"`)
4. Profile auto-detection from `agent_id` matching a role alias
5. Existing base defaults (no profile applied)

## Differences from Related Profiles

| Profile | Tools | Network | Writes | Use case |
|---------|-------|---------|--------|----------|
| `explorer` | `read`, `chats` | No | No | Read-only codebase exploration |
| `researcher` | `browser`, `read`, `screenshot` | Yes | No | Web research |
| `verifier` | `read`, `shell`, `ipython`, `chats` | No | Soft (prompt only) | Test/validate code |
| `developer` | All | Yes | Yes | Full development |
