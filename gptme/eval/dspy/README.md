# DSPy Prompt Optimization for gptme

This module provides automatic prompt optimization for gptme using the DSPy framework. It uses advanced techniques like MIPROv2 and BootstrapFewShot to systematically improve gptme's system prompts based on performance metrics.

## Overview

The DSPy integration allows you to:

- **Automatically optimize** gptme's system prompts using machine learning techniques
- **Evaluate prompts** across multiple tasks and metrics
- **Compare different** prompt variations systematically
- **Generate reports** on optimization results
- **Test specific aspects** like tool usage, reasoning, and instruction following

## Quick Start

### Installation

First, ensure DSPy is installed:

```bash
pip install dspy
```

### Basic Usage

1. **Show current system prompt:**
```bash
python -m gptme.eval.dspy.cli show-prompt
```

2. **Run a quick test:**
```bash
python -m gptme.eval.dspy.cli quick-test --num-examples 5
```

3. **Run full optimization:**
```bash
python -m gptme.eval.dspy.cli optimize --name "my_experiment"
```

### Python API

```python
from gptme.eval.dspy import run_prompt_optimization_experiment

# Run optimization experiment
experiment = run_prompt_optimization_experiment(
    experiment_name="gptme_optimization_v1",
    model="anthropic/claude-sonnet-4-20250514"
)

# Check results
print(experiment.generate_report())
```

## Components

### 1. Prompt Optimizer (`prompt_optimizer.py`)

The core optimization engine that uses DSPy's algorithms:

- **MIPROv2**: Advanced prompt optimization with Bayesian methods
- **BootstrapFewShot**: Optimizes few-shot examples and instructions
- **Custom metrics**: Task success, tool usage effectiveness, LLM judges

### 2. Evaluation Metrics (`metrics.py`)

Comprehensive metrics for evaluating prompt performance:

- **Task Success Rate**: How often tasks are completed correctly
- **Tool Usage Score**: Effectiveness of tool selection and usage
- **LLM Judge Score**: Quality assessment by language models
- **Composite Score**: Weighted combination of multiple metrics

### 3. Specialized Tasks (`tasks.py`)

Tasks designed specifically for prompt optimization:

- **Tool Usage Tasks**: Test appropriate tool selection
- **Reasoning Tasks**: Evaluate problem-solving approaches
- **Instruction Following**: Test adherence to guidelines
- **Error Handling**: Assess recovery and correction abilities

### 4. Experiments Framework (`experiments.py`)

High-level experiment management:

- **Baseline Evaluation**: Test current prompt performance
- **Optimization Runs**: Execute different optimization strategies
- **Comparison Analysis**: Compare all variants systematically
- **Report Generation**: Create comprehensive results reports

## Usage Examples

### Compare Prompt Variations

```python
from gptme.eval.dspy import quick_prompt_test

prompts = {
    "original": "You are gptme, a helpful assistant...",
    "enhanced": "You are gptme, an advanced AI assistant with tool access...",
    "concise": "gptme: AI assistant with terminal and code execution tools."
}

results = quick_prompt_test(prompts, num_examples=10)
```

### Custom Optimization

```python
from gptme.eval.dspy import PromptOptimizer

optimizer = PromptOptimizer(
    model="anthropic/claude-sonnet-4-20250514",
    optimizer_type="miprov2",
    max_demos=3,
    num_trials=15
)

base_prompt = get_current_gptme_prompt()
optimized_prompt, results = optimizer.optimize_prompt(
    base_prompt=base_prompt,
    train_size=20,
    val_size=10
)

print(f"Improvement: {results['average_score']:.3f}")
```

### Focus on Specific Areas

```python
from gptme.eval.dspy.tasks import get_tasks_by_focus_area

# Test only tool usage
tool_tasks = get_tasks_by_focus_area("tool_selection")

# Test only reasoning
reasoning_tasks = get_tasks_by_focus_area("reasoning")
```

## CLI Commands

### `optimize` - Full Optimization Experiment

Run a comprehensive optimization experiment:

```bash
python -m gptme.eval.dspy.cli optimize \
    --name "gptme_v1_optimization" \
    --model "anthropic/claude-sonnet-4-20250514" \
    --max-demos 3 \
    --num-trials 15 \
    --optimizers miprov2 bootstrap \
    --output-dir ./results
```

### `quick-test` - Compare Prompts

Quickly compare different prompt variations:

```bash
python -m gptme.eval.dspy.cli quick-test \
    --prompt-files prompt1.txt prompt2.txt \
    --num-examples 8 \
    --model "anthropic/claude-sonnet-4-20250514"
```

### `show-prompt` - View Current Prompt

Display the current gptme system prompt:

```bash
python -m gptme.eval.dspy.cli show-prompt --model "anthropic/claude-sonnet-4-20250514"
```

### `list-tasks` - View Available Tasks

List evaluation tasks:

```bash
# Standard eval tasks
python -m gptme.eval.dspy.cli list-tasks

# Prompt optimization specific tasks
python -m gptme.eval.dspy.cli list-tasks --optimization-tasks
```

### `analyze-coverage` - Task Coverage Analysis

Analyze what areas are covered by evaluation tasks:

```bash
python -m gptme.eval.dspy.cli analyze-coverage
```

## Optimization Strategies

### MIPROv2 (Recommended)

Advanced prompt optimization using:
- Bayesian optimization for instruction search
- Few-shot example bootstrapping
- Multi-objective optimization
- Automatic hyperparameter tuning

```python
optimizer_config = {
    "optimizer_type": "miprov2",
    "max_demos": 3,
    "num_trials": 10
}
```

### BootstrapFewShot

Focuses on generating effective few-shot examples:
- Bootstrap examples from training data
- Optimize example selection
- Validate against held-out data

```python
optimizer_config = {
    "optimizer_type": "bootstrap",
    "max_demos": 4,
    "num_trials": 8
}
```

## Evaluation Metrics

### Task Success Rate

Measures how often tasks are completed correctly:
- Checks expected outputs
- Validates file creation/modification
- Confirms command execution

### Tool Usage Effectiveness

Evaluates tool selection and usage:
- **Coverage**: Are required tools used?
- **Efficiency**: Are tools used efficiently?
- **Appropriateness**: Are the right tools chosen?

### LLM Judge Scoring

Uses language models to evaluate response quality:
- Overall effectiveness
- Clarity of explanations
- Following instructions
- Code quality

### Composite Scoring

Combines multiple metrics with configurable weights:
- Default: 40% task success, 30% tool usage, 30% LLM judge
- Customizable based on optimization goals

## Integration with gptme

The DSPy module integrates seamlessly with gptme's existing evaluation framework:

- **Reuses evaluation tasks** from `gptme/eval/suites/`
- **Compatible with all models** supported by gptme
- **Respects configuration** from gptme config files
- **Generates gptme-compatible** optimized prompts

## Best Practices

### 1. Start with Baseline

Always run baseline evaluation before optimization:

```bash
python -m gptme.eval.dspy.cli optimize --name "baseline_first"
```

### 2. Use Multiple Optimizers

Compare different optimization strategies:

```bash
--optimizers miprov2 bootstrap
```

### 3. Adequate Training Data

Use sufficient examples for reliable optimization:
- Minimum: 10-15 training examples
- Recommended: 20-30 training examples
- Validation: 5-10 examples

### 4. Focus Areas

Target specific improvement areas:

```python
# Focus on tool usage
tool_tasks = get_tasks_by_focus_area("tool_selection")

# Focus on reasoning
reasoning_tasks = get_tasks_by_focus_area("reasoning")
```

### 5. Iterative Improvement

Run multiple optimization rounds:
1. Initial optimization with broad tasks
2. Focused optimization on weak areas
3. Final validation on comprehensive test set

## Output and Results

Optimization experiments generate:

### Results Directory Structure
### Results Directory Structure
experiment_results/
├── experiment_name_results.json     # Complete results data
├── experiment_name_report.md        # Human-readable report
├── miprov2_prompt.txt              # Optimized prompt from MIPROv2
├── bootstrap_prompt.txt            # Optimized prompt from Bootstrap
└── baseline_evaluation.json        # Baseline performance data

### Report Contents

Optimization reports include:
- **Baseline Performance**: Current prompt metrics
- **Optimization Results**: Performance for each optimizer
- **Final Comparison**: Ranking of all prompt variants
- **Recommendations**: Best performing prompt and improvements
- **Detailed Analysis**: Per-task breakdowns and insights

### Example Report Output

```markdown
# Prompt Optimization Report: gptme_v1_optimization
**Model:** anthropic/claude-sonnet-4-20250514
**Timestamp:** 2024-08-26T15:30:00

## Baseline Performance
- Average Score: 0.672
- Task Success Rate: 0.700
- Tool Usage Score: 0.650

## Optimization Results
### miprov2
- Average Score: 0.745
- Optimizer Config: {'max_demos': 3, 'num_trials': 10}

### bootstrap
- Average Score: 0.721
- Optimizer Config: {'max_demos': 3, 'num_trials': 8}

## Final Comparison
| Prompt | Average Score | Examples |
|--------|---------------|----------|
| miprov2 | 0.745 | 10 |
| bootstrap | 0.721 | 10 |
| baseline | 0.672 | 10 |

## Recommendations
**Best performing prompt:** miprov2 (score: 0.745)
**Improvement over baseline:** +0.073
```

## Troubleshooting

### Common Issues

1. **DSPy Import Error**
   ```bash
   pip install dspy
   ```

2. **Model Configuration Issues**
   - Ensure API keys are set correctly
   - Check model name format (e.g., "anthropic/claude-sonnet-4-20250514")

3. **Evaluation Task Failures**
   - Verify task dependencies are installed
   - Check file permissions in workspace

4. **Memory Issues with Large Optimizations**
   - Reduce `--num-trials` and `--max-demos`
   - Use fewer evaluation examples

### Debug Mode

Enable verbose logging:
```bash
python -m gptme.eval.dspy.cli optimize --verbose --name "debug_run"
```

## Advanced Configuration

### Custom Metrics

Define custom evaluation metrics:

```python
def custom_metric(gold, pred, trace=None):
    # Custom evaluation logic
    return score_between_0_and_1

optimizer = PromptOptimizer(model="anthropic/claude-sonnet-4-20250514")
metric = create_composite_metric(
    task_weight=0.5,
    tool_weight=0.3,
    judge_weight=0.2
)
```

### Custom Tasks

Add domain-specific evaluation tasks:

```python
custom_tasks = [
    {
        "name": "custom-task",
        "files": {"input.txt": "test data"},
        "run": "python process.py",
        "prompt": "Process the input file and generate output",
        "tools": ["save", "shell"],
        "expect": {
            "creates_output": lambda ctx: "output.txt" in ctx.files
        },
        "focus_areas": ["custom_domain"]
    }
]
```

### Integration with CI/CD

The gptme repository includes a GitHub Actions workflow for automated prompt optimization. The workflow runs weekly and can also be triggered manually with custom parameters.

**Workflow file:** [`.github/workflows/optimize-prompts.yml`](../../.github/workflows/optimize-prompts.yml)

**Features:**
- **Scheduled runs**: Automatic weekly optimization on Sundays at 2 AM UTC
- **Manual dispatch**: Run optimization on-demand with custom parameters
- **Multiple optimizers**: Supports MIPROv2, BootstrapFewShot, and others
- **Configurable parameters**: Model selection, number of trials, demonstrations
- **Result artifacts**: Automatically uploads optimization results and reports
- **Failure notifications**: Creates GitHub issues when optimization fails
- **Environment support**: Configured for multiple LLM providers (OpenAI, Anthropic, OpenRouter)

**Manual trigger example:**
```bash
# Via GitHub CLI
gh workflow run optimize-prompts.yml \
  -f model="anthropic/claude-sonnet-4-20250514" \
  -f optimizers="miprov2,bootstrap" \
  -f num_trials="15" \
  -f max_demos="4"
```

The workflow generates comprehensive reports and stores them as GitHub Actions artifacts, making it easy to track optimization progress over time.

## Contributing

To contribute to the DSPy integration:

1. **Add new evaluation tasks** in `tasks.py`
2. **Implement new metrics** in `metrics.py`
3. **Create new optimizers** or improve existing ones
4. **Add tests** for new functionality
5. **Update documentation** for new features

### Running Tests

```bash
# Run DSPy-specific tests
python -m pytest gptme/eval/dspy/tests/ -v

# Run with optimization tasks
python -m pytest gptme/eval/dspy/tests/ -v --run-optimization
```

## Related Work

- **DSPy Framework**: https://github.com/stanfordnlp/dspy
- **Prompt Engineering Guide**: https://www.promptingguide.ai/
- **gptme Documentation**: https://gptme.org/docs/

## License

This module is part of gptme and follows the same license terms.
