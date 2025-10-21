"""Test GEPA integration with gptme evaluation."""

import importlib.util

import pytest

from gptme.eval.types import EvalSpec

if importlib.util.find_spec("dspy") is None:
    pytest.skip("DSPy not available", allow_module_level=True)

try:
    import dspy  # fmt: skip

    from gptme.eval.dspy.metrics import create_trajectory_feedback_metric  # fmt: skip
    from gptme.eval.dspy.prompt_optimizer import GptmeModule  # fmt: skip
except (ImportError, ModuleNotFoundError):
    pytest.skip("DSPy not available", allow_module_level=True)


def test_gepa_integration():
    """Test that GEPA integration works with actual gptme evaluation."""
    # Configure DSPy
    dspy.configure(lm=dspy.LM("anthropic/claude-haiku-4-5"))

    # Create components
    module = GptmeModule("You are a helpful AI assistant.")
    metric = create_trajectory_feedback_metric()

    # Create a proper EvalSpec for testing
    test_eval_spec: EvalSpec = {
        "name": "test-hello-world",
        "prompt": "Write hello.py that prints Hello World",
        "files": {},
        "run": "python hello.py",
        "expect": {
            "file_created": lambda ctx: "hello.py" in ctx.files,
            "correct_output": lambda ctx: "Hello World" in ctx.stdout,
        },
    }

    # Test with a simple task using the new signature
    prediction = module("Write hello.py that prints Hello World", "", test_eval_spec)

    # Verify evaluation ran
    assert hasattr(
        prediction, "eval_result"
    ), "Should have eval_result from gptme evaluation"

    # Test metric with the prediction
    example = dspy.Example(
        task_description="Write hello.py", context="", eval_spec=test_eval_spec
    ).with_inputs("task_description", "context")

    score = metric(example, prediction, None, None, None)
    actual_score = score.score if hasattr(score, "score") else score

    # Should get meaningful score (not 0.0)
    assert actual_score > 0, f"Expected score > 0, got {actual_score}"

    print(f"✅ GEPA integration test passed with score: {actual_score}")


if __name__ == "__main__":
    test_gepa_integration()
