#!/usr/bin/env bash
"""
CLI Integration Example

This script demonstrates using gptme's task loop mode from the command line
for automated task execution in various scenarios.
"""

set -euo pipefail

WORKSPACE="/home/bob/gptme-bob"

echo "=== gptme Task Loop Mode CLI Examples ==="
echo

# Example 1: Execute single task
echo "1. Execute single task (default behavior)"
echo "   Command: gptme --task-loop --workspace $WORKSPACE"
echo "   Result: Selects and executes one task based on MIQ scoring"
echo

# Example 2: Execute multiple tasks
echo "2. Execute multiple tasks in sequence"
echo "   Command: gptme --task-loop --max-tasks 3 --workspace $WORKSPACE"
echo "   Result: Executes up to 3 tasks, stopping if all complete or fail"
echo

# Example 3: With custom timeout
echo "3. Execute with custom timeout"
echo "   Command: gptme --task-loop --timeout 600 --workspace $WORKSPACE"
echo "   Result: Each task gets 10 minutes (600s) to complete"
echo

# Example 4: Non-interactive mode (autonomous)
echo "4. Non-interactive autonomous execution"
echo "   Command: gptme --task-loop --non-interactive -y --workspace $WORKSPACE"
echo "   Result: Runs without user prompts, auto-confirms all actions"
echo

# Example 5: Integration with cron/systemd
echo "5. Scheduled execution (cron/systemd)"
echo "   Cron: 0 */2 * * * cd $WORKSPACE && gptme --task-loop -n -y"
echo "   Systemd: See .config/systemd/user/bob-task-loop.service"
echo "   Result: Automatic periodic task execution"
echo

# Example 6: With specific model
echo "6. Use specific model for execution"
echo "   Command: gptme --task-loop -m anthropic/claude-sonnet-4 --workspace $WORKSPACE"
echo "   Result: Uses specified model for task execution"
echo

# Example 7: Logging and monitoring
echo "7. Execute with detailed logging"
echo "   Command: gptme --task-loop -v --workspace $WORKSPACE 2>&1 | tee task-run.log"
echo "   Result: Verbose output saved to log file for review"
echo

# Example 8: Dry run (plan without executing)
echo "8. Preview next task without executing"
echo "   Command: gptme --task-loop --dry-run --workspace $WORKSPACE"
echo "   Result: Shows which task would be selected, doesn't execute"
echo

# Example 9: Resume from failure
echo "9. Resume after timeout or failure"
echo "   Command: gptme --task-loop --resume --workspace $WORKSPACE"
echo "   Result: Continues from last failed task, retries or skips"
echo

# Example 10: Integration with CI/CD
echo "10. CI/CD integration"
echo "    Command: gptme --task-loop --max-tasks 5 --timeout 300 --workspace ."
echo "    Result: Execute tasks as part of automated pipeline"
echo

echo "=== Complete CLI Reference ==="
echo
echo "Required flags:"
echo "  --task-loop          Enable task loop mode"
echo "  --workspace PATH     Path to workspace directory"
echo
echo "Optional flags:"
echo "  --max-tasks N        Maximum tasks to execute (default: 1)"
echo "  --timeout N          Timeout in seconds per task (default: 300)"
echo "  -m, --model MODEL    Model to use (default: configured default)"
echo "  -n, --non-interactive  No user prompts (autonomous mode)"
echo "  -y, --no-confirm     Auto-confirm all actions"
echo "  -v, --verbose        Show detailed output"
echo
echo "Task filtering (via workspace task metadata):"
echo "  Use task tags: @autonomous, @coding, @research"
echo "  Use priorities: high, medium, low"
echo "  Set state: new, active (others filtered out)"
echo
echo "See gptme/tasks/README.md for complete documentation"
