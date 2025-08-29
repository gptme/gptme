"""Test GEPA integration with gptme evaluation."""

import dspy
from gptme.eval.dspy.prompt_optimizer import GptmeModule
from gptme.eval.dspy.metrics import create_trajectory_feedback_metric


def test_gepa_integration():
    """Test that GEPA integration works with actual gptme evaluation."""
    # Configure DSPy
    dspy.configure(lm=dspy.LM("anthropic/claude-3-5-haiku-20241022"))

    # Create components
    module = GptmeModule("You are a helpful AI assistant.")
    metric = create_trajectory_feedback_metric()

    # Test with a simple task
    prediction = module("Write hello.py that prints Hello World", "")

    # Verify evaluation ran
    assert hasattr(
        prediction, "eval_result"
    ), "Should have eval_result from gptme evaluation"

    # Test metric with the prediction
    example = dspy.Example(task_description="Write hello.py", context="").with_inputs(
        "task_description", "context"
    )

    score = metric(example, prediction, None, None, None)
    actual_score = score.score if hasattr(score, "score") else score

    # Should get meaningful score (not 0.0)
    assert actual_score > 0, f"Expected score > 0, got {actual_score}"

    print(f"✅ GEPA integration test passed with score: {actual_score}")


if __name__ == "__main__":
    test_gepa_integration()
