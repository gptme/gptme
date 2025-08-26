"""
DSPy integration for gptme prompt optimization.

This module provides tools for automatically optimizing gptme's system prompts
using DSPy's prompt optimization techniques like MIPROv2 and BootstrapFewShot.
"""

from .prompt_optimizer import PromptOptimizer
from .metrics import create_task_success_metric, create_tool_usage_metric
from .signatures import GptmeTaskSignature, PromptEvaluationSignature
from .experiments import run_prompt_optimization_experiment

__all__ = [
    "PromptOptimizer",
    "create_task_success_metric",
    "create_tool_usage_metric",
    "GptmeTaskSignature",
    "PromptEvaluationSignature",
    "run_prompt_optimization_experiment",
]
