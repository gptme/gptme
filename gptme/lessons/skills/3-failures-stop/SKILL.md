---
name: 3-failures-stop
description: Prevent infinite loops by stopping after 3 consecutive failures
---

# 3-Failures-Stop Pattern

Prevents infinite retry loops by implementing a formal failure recovery protocol: after 3 consecutive failures on the same operation, STOP, REVERT changes, and CONSULT for help.

## Overview

This skill implements the "3-failures → STOP, REVERT, CONSULT" pattern from oh-my-opencode. It provides a structured approach to failure recovery that prevents agents from wasting resources on impossible tasks while ensuring work isn't lost.

## The Pattern

### When to Apply

Apply this pattern when:
- Same operation fails repeatedly with similar errors
- Fix attempts aren't improving the situation
- You're going in circles (trying the same thing again)
- External factors may be preventing success

### The Protocol

```txt
Failure 1: Attempt fix
  → Analyze error message
  → Try reasonable correction
  → Continue to next attempt

Failure 2: Deeper investigation
  → Different approach from first fix
  → Check for environmental issues
  → Verify assumptions (paths, permissions, versions)
  → Continue to final attempt

Failure 3: STOP → REVERT → CONSULT
  → STOP: Don't attempt further fixes
  → REVERT: Undo changes that may have made things worse
  → CONSULT: Document the issue and ask for help
```

## Detection Signals

You need this pattern when you observe:
- Repeating the same command with minor variations
- Error messages cycling through similar patterns
- 3+ tool calls attempting the same logical operation
- Frustration building without progress
- "Let me try one more thing" mindset taking over

## Implementation

### Track Failure State

```txt
failure_count = 0
last_operation = None

for each operation:
  if operation == last_operation:
    if failed:
      failure_count += 1
      if failure_count >= 3:
        execute STOP-REVERT-CONSULT
    else:
      failure_count = 0  # Success resets counter
  else:
    failure_count = 1 if failed else 0
    last_operation = operation
```

### STOP Phase

When reaching 3 failures:

```txt
1. Acknowledge the pattern:
   "This approach has failed 3 times. Stopping to prevent further issues."

2. Don't attempt another variation of the same fix

3. Preserve current state for diagnosis
```

### REVERT Phase

Roll back changes that may have contributed to failures:

```shell
# Check what changed during failed attempts
git diff

# If changes exist that may have caused issues
git checkout -- <files>

# For staged changes
git reset HEAD <files>

# Keep a record of what was attempted
git stash  # Optional: preserve work for later analysis
```

### CONSULT Phase

Document and escalate:

```txt
## Issue Summary

### What I Was Trying
[Clear description of the goal]

### What Failed (3 attempts)
1. Attempt 1: [Action] → [Error]
2. Attempt 2: [Action] → [Error]
3. Attempt 3: [Action] → [Error]

### What I've Ruled Out
- [Verified X is correct]
- [Checked Y is available]

### What I Need
[Specific help request or question]
```

## Examples

### Example 1: Test Failure Loop

```txt
Attempt 1: pytest test_foo.py
  → Error: ModuleNotFoundError: No module named 'bar'
  → Fix: pip install bar
  → Result: Still fails

Attempt 2: pytest test_foo.py
  → Error: ModuleNotFoundError: No module named 'bar'
  → Fix: pip install bar --force-reinstall
  → Result: Still fails

Attempt 3: pytest test_foo.py
  → Error: ModuleNotFoundError: No module named 'bar'
  → STOP-REVERT-CONSULT:
    "Three attempts to fix import error failed.
     Issue may be: virtualenv not activated, path issue, or package name differs.
     Need: Verify correct package name and environment setup."
```

### Example 2: Git Push Failure

```txt
Attempt 1: git push origin feature
  → Error: Permission denied (publickey)
  → Fix: Check SSH key exists

Attempt 2: git push origin feature
  → Error: Permission denied (publickey)
  → Fix: Add SSH key to ssh-agent

Attempt 3: git push origin feature
  → Error: Permission denied (publickey)
  → STOP-REVERT-CONSULT:
    "SSH authentication failing persistently.
     Need: Help configuring SSH access to this remote."
```

### Example 3: Build Failure

```txt
Attempt 1: make build
  → Error: missing header file.h
  → Fix: apt install libfoo-dev

Attempt 2: make build
  → Error: missing header file.h
  → Fix: Try different package libfoo-headers

Attempt 3: make build
  → Error: missing header file.h
  → STOP-REVERT-CONSULT:
    "Cannot locate required header file.
     Checked: libfoo-dev, libfoo-headers packages.
     Need: Correct package name or manual header installation path."
```

## Anti-patterns

### Don't Do This

```txt
# Wrong: Continuing past 3 failures
Attempt 4, 5, 6... "Maybe this time..."

# Wrong: Minor variations of same fix
pip install bar
pip install Bar
pip install BAR

# Wrong: No reversion of broken changes
# (leaving half-applied fixes in codebase)

# Wrong: Vague consult message
"It's broken, please fix"
```

## Benefits

Following this pattern:
- **Prevents resource waste**: Stop before consuming excessive context/time
- **Preserves work state**: REVERT ensures no half-broken changes remain
- **Enables efficient help**: CONSULT provides clear problem statement
- **Reduces frustration**: Structured escalation beats endless retrying
- **Maintains code quality**: Don't leave failed fix attempts in codebase

## Integration

This skill works well with:
- **Git workflows**: REVERT uses standard git operations
- **Todo system**: Track "needs help" state for blocked items
- **Journal entries**: Document failure patterns for future learning
- **Lessons system**: Capture recurring failure types as lessons

## Related

- Origin: oh-my-opencode project philosophy
- Tool: git (for REVERT phase)
- Pattern: Escalation protocols
