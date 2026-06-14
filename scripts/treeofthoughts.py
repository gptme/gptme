"""
Eval-guided tree search for gptme agents.

Implements the merge-reject loop pattern: the agent proposes changes, an eval
command measures progress, and if progress regressed the workspace is restored
to the last-good snapshot so the agent can try a different approach.

Cross-attempt history is injected into each iteration so the agent learns from
prior failures rather than repeating the same mistake.

Usage examples
--------------
  python scripts/treeofthoughts.py "fix the failing tests" --eval "pytest -q"
  python scripts/treeofthoughts.py "improve coverage" --eval "pytest --co -q | wc -l"
  python scripts/treeofthoughts.py "tighten types" --eval "make typecheck"
  python scripts/treeofthoughts.py "refactor X" --eval "make lint" --max-iters 5

Architecture
------------
Each iteration:
  1. Snapshot workspace state (before the agent touches anything).
  2. Run the agent for one prompt-to-completion turn.
  3. Run the eval command; extract a numeric score from its exit code (0=pass, 1=fail)
     or from the last numeric line of its stdout.
  4. If score improved (or eval passes for the first time): keep the change,
     append to success history.
  5. If score regressed: restore the workspace to the pre-turn snapshot,
     append failure + agent diagnosis to the attempt history, continue.
  6. Inject the full attempt history as a system message before the next turn
     so the agent sees why previous approaches failed.

See also
--------
  - /snapshot command (gptme/commands/snapshot.py)
  - /backtrack command (gptme/commands/backtrack.py)
  - workspace_snapshot module (gptme/workspace_snapshot.py)
  - Issue #495: Agents that tree search
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# We import gptme lazily inside main() so --help works without a full init.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Eval helpers
# ---------------------------------------------------------------------------


def run_eval(cmd: str, cwd: Path) -> tuple[bool, float, str]:
    """Run *cmd* in *cwd* and return (passed, score, output).

    Score extraction rules (first match wins):
    1. Parse the last non-empty line of stdout as a float.
    2. If that fails, treat exit-code 0 as 1.0 and non-zero as 0.0.

    Returns a tuple so callers get a full picture even when score is binary.
    """
    result = subprocess.run(
        cmd,
        check=False,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0

    # Try to parse a numeric score from the last stdout line.
    score: float | None = None
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if line:
            try:
                score = float(line)
                break
            except ValueError:
                pass

    if score is None:
        score = 1.0 if passed else 0.0

    return passed, score, output


# ---------------------------------------------------------------------------
# Attempt-history helpers
# ---------------------------------------------------------------------------


def _fmt_history(attempts: list[dict]) -> str:
    """Render attempt history as a compact human-readable summary."""
    if not attempts:
        return "(no prior attempts)"
    lines = ["Prior attempts (do NOT repeat these approaches):"]
    for i, a in enumerate(attempts, start=1):
        verdict = "✓ kept" if a.get("kept") else "✗ reverted"
        lines.append(
            f"  Attempt {i} [{verdict}] score {a['score_before']:.3f}→{a['score_after']:.3f}"
        )
        if a.get("diagnosis"):
            lines.append(f"    Diagnosis: {a['diagnosis']}")
        if a.get("files_changed"):
            lines.append(f"    Files changed: {', '.join(a['files_changed'])}")
    return "\n".join(lines)


def _changed_files(cwd: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        check=False,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return [f for f in result.stdout.splitlines() if f.strip()]


# ---------------------------------------------------------------------------
# Agent step wrapper
# ---------------------------------------------------------------------------


def _agent_step(
    messages: list,
    workspace: Path,
    model: str | None,
    stream: bool,
) -> list:
    """Run a single agent turn; return the new messages appended to *messages*."""
    from gptme.chat import step  # fmt: skip
    from gptme.logmanager import Log  # fmt: skip

    log = Log(messages)
    new_msgs: list = []
    for msg in step(log, stream=stream, workspace=workspace, model=model):
        new_msgs.append(msg)
        # Print to stdout so the user can follow along.
        role = msg.role
        content = msg.content or ""
        prefix = {"user": "👤", "assistant": "🤖", "system": "⚙️"}.get(role, "•")
        for line in content.splitlines():
            print(f"  {prefix} {line}")
    return messages + new_msgs


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def tree_search(
    task: str,
    eval_cmd: str,
    workspace: Path,
    model: str | None,
    max_iters: int,
    stream: bool,
    verbose: bool,
) -> bool:
    """Run the eval-guided tree search loop. Returns True if the eval passed."""
    from gptme.init import init  # fmt: skip
    from gptme.message import Message  # fmt: skip
    from gptme.prompts import get_prompt  # fmt: skip
    from gptme.tools import get_tools  # fmt: skip
    from gptme.workspace_snapshot import (  # fmt: skip
        Shadow,
        init_shadow,
        restore,
        snapshot,
    )

    # --- init gptme ---
    init(
        model=model,
        interactive=False,
        tool_allowlist=None,
        tool_format="markdown",
        no_confirm=True,
    )

    # --- init shadow snapshot repo ---
    shadow: Shadow = init_shadow(workspace)

    # --- baseline eval ---
    print(f"\n[tree-search] Running baseline eval: {eval_cmd!r}")
    passed, baseline_score, baseline_out = run_eval(eval_cmd, workspace)
    print(
        f"[tree-search] Baseline: {'PASS' if passed else 'FAIL'} (score={baseline_score:.3f})"
    )
    if verbose:
        print(textwrap.indent(baseline_out, "  "))

    current_score = baseline_score
    attempts: list[dict] = []

    # --- initial messages (list[Message] returned by get_prompt) ---
    tools = get_tools()
    initial_msgs = get_prompt(
        tools=tools,
        prompt="full",
        interactive=False,
        tool_format="markdown",
        workspace=workspace,
    )

    def _build_messages(attempt_num: int) -> list[Message]:
        """Build the message list for iteration *attempt_num*."""
        history_text = _fmt_history(attempts)
        system_context = Message(
            "system",
            f"You are running in eval-guided tree-search mode.\n"
            f"Eval command: {eval_cmd}\n"
            f"Current score: {current_score:.3f}\n\n"
            f"{history_text}\n\n"
            "After making changes, the eval will run automatically. "
            "If it regresses your changes will be reverted and you will be "
            "asked to try a different approach.",
        )
        user_msg = Message(
            "user", task if attempt_num == 1 else f"[attempt {attempt_num}] {task}"
        )
        return initial_msgs + [system_context, user_msg]

    for iteration in range(1, max_iters + 1):
        print(f"\n[tree-search] ── Iteration {iteration}/{max_iters} ──")

        # Snapshot workspace before agent acts.
        pre_sha = snapshot(shadow, label=f"pre-iter-{iteration}")
        if pre_sha is None:
            print(
                "[tree-search] WARNING: snapshot failed; continuing without rollback safety net"
            )

        files_before = set(_changed_files(workspace))

        # Build messages and run one agent turn.
        messages = _build_messages(iteration)
        messages = _agent_step(messages, workspace, model, stream)

        # Run eval.
        print(f"\n[tree-search] Running eval: {eval_cmd!r}")
        new_passed, new_score, eval_out = run_eval(eval_cmd, workspace)
        print(
            f"[tree-search] Result: {'PASS' if new_passed else 'FAIL'} "
            f"(score {current_score:.3f} → {new_score:.3f})"
        )
        if verbose or not new_passed:
            print(textwrap.indent(eval_out[-2000:], "  "))

        files_after = set(_changed_files(workspace))
        changed = sorted(files_after | (files_before ^ files_after))

        # Build diagnosis: ask the agent for a short post-mortem if it regressed.
        diagnosis = ""
        if new_score < current_score:
            # Quick LLM-guided diagnosis: ask the last assistant message what went wrong.
            assistant_msgs = [m for m in messages if m.role == "assistant"]
            if assistant_msgs:
                last_action = assistant_msgs[-1].content or ""
                diagnosis = (
                    f"Agent attempted: {last_action[:400].strip()!r} "
                    f"(truncated). Eval output: {eval_out[:400].strip()!r}"
                )
            else:
                diagnosis = f"Eval output: {eval_out[:400].strip()!r}"

        attempt_record = {
            "iteration": iteration,
            "score_before": current_score,
            "score_after": new_score,
            "passed": new_passed,
            "kept": new_score >= current_score,
            "files_changed": changed,
            "diagnosis": diagnosis,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        attempts.append(attempt_record)

        if new_score >= current_score:
            # Improvement or neutral: keep the change.
            current_score = new_score
            print(f"[tree-search] ✓ Keeping change (score {current_score:.3f}).")
            if new_passed:
                print("[tree-search] 🎉 Eval passed — task complete!")
                return True
        else:
            # Regression: restore workspace to pre-turn snapshot.
            print(
                f"[tree-search] ✗ Eval regressed "
                f"({new_score:.3f} < {current_score:.3f}) — restoring snapshot {pre_sha}."
            )
            if pre_sha is not None:
                ok = restore(shadow, pre_sha)
                if not ok:
                    print(
                        "[tree-search] WARNING: snapshot restore failed; workspace may be dirty"
                    )
            else:
                print(
                    "[tree-search] WARNING: no pre-turn snapshot; workspace NOT restored"
                )

    print(
        f"\n[tree-search] Reached max iterations ({max_iters}). Final score: {current_score:.3f}"
    )
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Eval-guided tree search for gptme agents.\n\n"
            "Runs the agent in a loop; if the eval command regresses after a "
            "turn the workspace is automatically restored and the agent tries "
            "a different approach."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples:
              %(prog)s "fix failing tests" --eval "pytest -q"
              %(prog)s "improve coverage" --eval "pytest --co -q | wc -l" --max-iters 5
              %(prog)s "tighten types" --eval "make typecheck" --model anthropic/claude-3-5-sonnet
            """
        ),
    )
    parser.add_argument("task", help="Task prompt for the agent")
    parser.add_argument(
        "--eval",
        dest="eval_cmd",
        required=True,
        metavar="CMD",
        help="Shell command to evaluate progress. Exit code 0 = pass; "
        "a numeric last-stdout-line is used as the score.",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        metavar="DIR",
        help="Workspace directory (default: current directory)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model to use (e.g. anthropic/claude-3-5-sonnet). Defaults to gptme default.",
    )
    parser.add_argument(
        "--max-iters",
        type=int,
        default=5,
        metavar="N",
        help="Maximum number of agent iterations (default: 5)",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming output",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print full eval output even on pass",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"Error: workspace {workspace} is not a directory", file=sys.stderr)
        sys.exit(1)

    success = tree_search(
        task=args.task,
        eval_cmd=args.eval_cmd,
        workspace=workspace,
        model=args.model,
        max_iters=args.max_iters,
        stream=not args.no_stream,
        verbose=args.verbose,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
