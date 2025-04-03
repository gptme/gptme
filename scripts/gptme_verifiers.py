#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "rich>=13.0.0",
#   "gptme @ git+https://github.com/ErikBjare/gptme.git",
#   "verifiers @ git+https://github.com/willccbb/verifiers.git",
# ]
# [tool.uv]
# exclude-newer = "2025-02-17T00:00:00Z"
# ///
#
# gptme_verifiers.py - RL environment for training LLMs with gptme tools
#
# Usage:
#   chmod +x gptme_verifiers.py
#   ./gptme_verifiers.py
import random
from dataclasses import dataclass
from typing import Any, TypedDict

from gptme.eval.suites.basic import tests as basic_tests
from gptme.eval.suites.init_projects import tests as project_tests
from gptme.eval.types import EvalSpec, ResultContext
from gptme.message import Message
from gptme.tools import execute_msg
from rich.console import Console
from rich.panel import Panel
from verifiers import MultiStepEnv  # type: ignore

console = Console()


class ToolResult(TypedDict, total=False):
    """Type for tool execution results"""

    output: str | None
    files: dict[str, str] | None
    error: str | None


class State(TypedDict):
    """Type for environment state"""

    files: dict[str, str | bytes]
    tool_history: list[str]
    output: str
    error: str | None


@dataclass
class ToolUseReward:
    """Rewards for tool usage quality"""

    successful_tool_use: float = 1.0
    failed_tool_format: float = -0.5
    failed_patch: float = -0.5
    unnecessary_tool_use: float = -0.2
    task_failure: float = -1.0  # Added missing attribute


@dataclass
class TaskReward:
    """Rewards for task completion"""

    task_success: float = 5.0
    partial_success: float = 1.0
    task_failure: float = 0.0


class GPTMeRLEnv(MultiStepEnv):
    """RL environment for training LLMs with gptme tools using verifiers framework"""

    def __init__(
        self, eval_specs: list[EvalSpec] | None = None, tool_format: str = "markdown"
    ):
        """Initialize environment with eval specs"""
        super().__init__()  # Required by verifiers
        self.eval_specs = eval_specs or (basic_tests + project_tests)
        self.tool_rewards = ToolUseReward()
        self.task_rewards = TaskReward()
        self.current_spec: EvalSpec | None = None
        self.current_state: State | None = None

        # Store tool format
        self.tool_format = tool_format

        # Define limits
        self.max_history = 10
        self.max_args_length = 1024

    @property
    def available_tools(self) -> list[str]:
        """List of available tools"""
        return ["shell", "save", "patch", "read", "browser"]

    def reset(self, seed: int | None = None) -> tuple[dict, dict]:
        """Reset environment with a random eval spec"""
        super().reset(seed=seed)

        self.current_spec = random.choice(self.eval_specs)
        self.current_state = {
            "files": self.current_spec["files"].copy(),
            "tool_history": [],
            "output": "",
            "error": None,
        }

        console.print(
            Panel(
                f"Starting task: {self.current_spec['name']}\n{self.current_spec['prompt']}",
                title="New Episode",
            )
        )

        return self._get_observation(), {}

    def step(
        self, action: dict[str, Any]
    ) -> tuple[dict[str, Any], float, bool, dict[str, Any]]:
        """Execute one step in the environment"""
        if self.current_state is None or self.current_spec is None:
            raise RuntimeError("Environment not initialized, call reset() first")

        tool_name = self.available_tools[action["tool"]]
        tool_args = action["args"]

        # Execute tool
        tool_result = self._execute_tool(tool_name, tool_args)

        # Calculate tool use reward
        reward = self._calculate_tool_reward(tool_result)

        # Update state
        self.current_state["files"].update(tool_result.get("files", {}))
        self.current_state["output"] += tool_result.get("output", "")
        self.current_state["error"] = tool_result.get("error")
        self.current_state["tool_history"].append(f"{tool_name}: {tool_args}")

        # Check if task is complete
        done = self._is_done()
        if done:
            # Add task completion reward
            reward += self._calculate_task_reward()

            console.print(
                Panel(
                    f"Task complete!\nFinal reward: {reward}\nTool history:\n"
                    + "\n".join(self.current_state["tool_history"]),
                    title="Episode End",
                )
            )

        return self._get_observation(), reward, done, {}

    def _execute_tool(self, tool_name: str, args: str) -> dict[str, Any]:
        """Execute a tool and return the result"""
        try:
            # Create tool use message in markdown format
            tool_content = f"```{tool_name}\n{args}\n```"
            msg = Message("assistant", tool_content)

            # Execute tool using gptme's execute_msg
            results = list(execute_msg(msg, lambda _: True))  # Always confirm
            if not results:
                return {"error": "No result from tool execution"}

            result = results[0]  # Take first result
            return {
                "output": result.content,
                "files": {},  # Files are handled through the filesystem
                "error": None if result.role != "system" else result.content,
            }
        except Exception as e:
            return {"error": str(e)}

    def _calculate_tool_reward(self, tool_result: dict[str, Any]) -> float:
        """Calculate reward for tool usage with more sophisticated shaping"""
        # Base reward calculation
        reward = (
            self.tool_rewards.successful_tool_use
            if not tool_result.get("error")
            else self.tool_rewards.task_failure
        )

        # Error-specific penalties
        if tool_result.get("error"):
            if "invalid format" in str(tool_result["error"]):
                reward = self.tool_rewards.failed_tool_format
            elif "patch failed" in str(tool_result["error"]):
                reward = self.tool_rewards.failed_patch

        # Additional reward shaping
        if not tool_result.get("error"):
            if self.current_spec is None or self.current_state is None:
                return reward

            # Reward for using appropriate tools
            if self.current_spec.get("name", "").startswith("init-"):
                if "git" in str(tool_result.get("output", "")):
                    reward += 0.5

            # Reward for efficient tool sequences
            tool_history = self.current_state.get("tool_history", [])
            if len(tool_history) <= 3:
                reward += 0.3

            # Penalize redundant tool usage
            tool_counts: dict[str, int] = {}
            for tool in tool_history:
                tool_name = tool.split(":")[0]
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                if tool_counts[tool_name] > 2:
                    reward -= 0.2

        return reward

    def _calculate_task_reward(self) -> float:
        """Calculate reward for task completion"""
        if self.current_state is None or self.current_spec is None:
            raise RuntimeError("Environment not initialized, call reset() first")

        # Convert to dict for ResultContext
        files_dict: dict[str, str | bytes] = dict(self.current_state["files"])

        ctx = ResultContext(
            files=files_dict,
            stdout=self.current_state["output"],
            stderr="",
            exit_code=0,
        )

        # Use existing expect checks from eval spec
        results = [check(ctx) for check in self.current_spec["expect"].values()]

        if all(results):
            return self.task_rewards.task_success
        elif any(results):
            return self.task_rewards.partial_success
        return self.task_rewards.task_failure

    def _get_observation(self) -> dict[str, Any]:
        """Get current observation"""
        if self.current_state is None or self.current_spec is None:
            raise RuntimeError("Environment not initialized, call reset() first")

        return {
            "files": str(self.current_state["files"]),
            "tool_history": self.current_state["tool_history"],
            "task": self.current_spec["prompt"],
        }

    def _is_done(self) -> bool:
        """Check if current task is complete"""
        if self.current_state is None or self.current_spec is None:
            raise RuntimeError("Environment not initialized, call reset() first")

        if len(self.current_state["tool_history"]) >= 10:
            return True

        # Convert to dict for ResultContext
        files_dict: dict[str, str | bytes] = dict(self.current_state["files"])

        ctx = ResultContext(
            files=files_dict,
            stdout=self.current_state["output"],
            stderr="",
            exit_code=0,
        )
        return all(check(ctx) for check in self.current_spec["expect"].values())


def main() -> None:
    """Example usage of the environment"""
    env = GPTMeRLEnv()
    obs, info = env.reset()

    # Example episode with random actions
    done = False
    total_reward: float = 0.0
    while not done:
        action = {
            "tool": random.randint(0, len(env.available_tools) - 1),
            "args": "example args",
        }
        obs, reward, done, info = env.step(action)
        total_reward += reward

    console.print(f"Episode finished with total reward: {total_reward}")


if __name__ == "__main__":
    main()
