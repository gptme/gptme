# DSPy Prompt Optimization for gptme

This module provides automatic prompt optimization for gptme using the DSPy framework. It uses advanced techniques like MIPROv2 and BootstrapFewShot to systematically improve gptme's system prompts based on performance metrics.

## Overview

The DSPy integration allows you to:

- **Automatically optimize** gptme's system prompts using machine learning techniques
- **Evaluate prompts** across multiple tasks and metrics
- **Compare different** prompt variations systematically
- **Generate reports** on optimization results
- **Test specific aspects** like tool usage, reasoning, and instruction following

## Module Structure

```text
gptme/eval/dspy/
├── __init__.py           # Module exports and initialization
├── signatures.py         # DSPy signatures for optimization tasks
├── metrics.py            # Evaluation metrics for prompt performance
├── prompt_optimizer.py   # Core optimization logic using DSPy
├── experiments.py        # High-level experiment management
├── tasks.py              # Specialized evaluation tasks
├── cli.py                # Command-line interface
└── README.md             # Comprehensive documentation
```

## Key Features

### 1. Automatic Prompt Optimization
- **MIPROv2**: Advanced Bayesian optimization for system prompts
- **BootstrapFewShot**: Optimizes few-shot examples and instructions
- **Custom Metrics**: Task success, tool usage effectiveness, LLM judges
- **Multi-objective**: Balances different aspects of prompt performance

### 2. Comprehensive Evaluation Framework
- **Task Success Rate**: Measures completion of evaluation tasks
- **Tool Usage Analysis**: Evaluates appropriate tool selection and usage
- **LLM Judge Scoring**: Uses language models to assess response quality
- **Composite Metrics**: Combines multiple evaluation aspects

### 3. Specialized Tasks
- **Tool Usage Tasks**: Test appropriate tool selection patterns
- **Reasoning Tasks**: Evaluate problem-solving approaches
- **Instruction Following**: Test adherence to specific guidelines
- **Error Handling**: Assess recovery and correction abilities

### 4. User-Friendly Interface
```bash
# Quick optimization
python -m gptme.eval.dspy optimize --name "my_experiment"

# Compare prompt variations
python -m gptme.eval.dspy quick-test --prompt-files prompt1.txt prompt2.txt

# Show current system prompt
python -m gptme.eval.dspy show-prompt
```

## Installation

DSPy is an optional dependency added in pyproject.toml under `[tool.poetry.extras]`.

Install with:
```bash
pip install gptme[dspy]
```

Or for all features:
```bash
pip install gptme[all]
```

## Quick Start

### Basic Usage

1. **Show current system prompt:**
```bash
python -m gptme.eval.dspy show-prompt
```

2. **Run a quick test:**
```bash
python -m gptme.eval.dspy quick-test --num-examples 5
```

3. **Run full optimization:**
```bash
python -m gptme.eval.dspy optimize --name "my_experiment"
```

### Python API

```python
from gptme.eval.dspy import run_prompt_optimization_experiment

# Run optimization experiment
experiment = run_prompt_optimization_experiment(
    experiment_name="gptme_optimization_v1",
    model="anthropic/claude-haiku-4-5"
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
    model="anthropic/claude-haiku-4-5",
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
python -m gptme.eval.dspy optimize \
    --name "gptme_v1_optimization" \
    --model "anthropic/claude-haiku-4-5" \
    --max-demos 3 \
    --num-trials 15 \
    --optimizers miprov2 bootstrap \
    --output-dir ./results
```

### `quick-test` - Compare Prompts

Quickly compare different prompt variations:

```bash
python -m gptme.eval.dspy quick-test \
    --prompt-files prompt1.txt prompt2.txt \
    --num-examples 8 \
    --model "anthropic/claude-haiku-4-5"
```

### `show-prompt` - View Current Prompt

Display the current gptme system prompt:

```bash
python -m gptme.eval.dspy show-prompt --model "anthropic/claude-haiku-4-5"
```

### `list-tasks` - View Available Tasks

List evaluation tasks:

```bash
# Standard eval tasks
python -m gptme.eval.dspy list-tasks

# Prompt optimization specific tasks
python -m gptme.eval.dspy list-tasks --optimization-tasks
```

### `analyze-coverage` - Task Coverage Analysis

Analyze what areas are covered by evaluation tasks:

```bash
python -m gptme.eval.dspy analyze-coverage
```

## Technical Approach

### DSPy Integration

1. **Signature Definitions**: Formal input/output specifications for optimization
2. **Metric Functions**: Evaluation functions that return 0-1 scores
3. **Dataset Conversion**: Transform gptme eval specs to DSPy format
4. **Optimization Loop**: Use DSPy algorithms to improve prompts iteratively

### Evaluation Metrics

- **Task Success**: Binary success on evaluation tasks
- **Tool Effectiveness**: Appropriate tool selection and usage
- **Response Quality**: LLM-judged quality assessments
- **Composite Scoring**: Weighted combination of multiple metrics

### Experiment Management

- **Baseline Evaluation**: Test current prompt performance
- **Multi-optimizer Comparison**: Test different optimization strategies
- **Results Analysis**: Statistical comparison and reporting
- **Artifact Storage**: Save optimized prompts and detailed results

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

### Seamless Integration

- Uses existing evaluation framework from `gptme/eval/suites/`
- Compatible with all gptme-supported models
- Respects gptme configuration and preferences
- Generates prompts compatible with gptme's prompt system

### Optional Dependency

- DSPy is optional - doesn't affect core gptme functionality
- Clean import handling with graceful fallbacks
- Only loaded when explicitly used

## Testing

Comprehensive test suite covering:
- **Unit Tests**: Individual component functionality
- **Integration Tests**: Cross-component interactions
- **CLI Tests**: Command-line interface behavior
- **Mock Tests**: Expensive operations avoided in CI

Run tests:
```bash
python -m pytest tests/test_dspy*.py -v
```

## Best Practices

### 1. Start with Baseline

Always run baseline evaluation before optimization:

```bash
python -m gptme.eval.dspy optimize --name "baseline_first"
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
```text
experiment_results/
├── experiment_name_results.json     # Complete results data
├── experiment_name_report.md        # Human-readable report
├── miprov2_prompt.txt              # Optimized prompt from MIPROv2
├── bootstrap_prompt.txt            # Optimized prompt from Bootstrap
└── baseline_evaluation.json        # Baseline performance data
```

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
**Model:** anthropic/claude-haiku-4-5
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

## Benefits

### For gptme Development
- **Data-Driven**: Objective measurement of prompt quality
- **Automated**: Reduces manual prompt engineering effort
- **Systematic**: Comprehensive evaluation across multiple dimensions
- **Reproducible**: Consistent methodology and metrics

### For Users
- **Better Performance**: Optimized prompts work more effectively
- **Customization**: Optimize for specific use cases or domains
- **Transparency**: Clear metrics and evaluation criteria
- **Accessibility**: Easy-to-use CLI and Python API

## Future Enhancements

- **Model-Specific Optimization**: Tailor prompts for different LLM providers
- **Domain Adaptation**: Optimize for specific programming languages or tasks
- **Continuous Learning**: Incorporate user feedback and interaction data
- **A/B Testing**: Built-in experimentation framework
- **Prompt Templates**: Generate reusable prompt components

## Known Issues and Findings

### Tool Usage Metric Bug
**Issue**: Tool usage score always shows `0.000` even for successful tasks that use tools correctly.

**Expected**: If tasks complete successfully (`task: 1.000`) and use required tools, `tool` score should be `1.0`.

**Current behavior**: Tool usage score is `0.000` for all tasks.

**Needs investigation**: The tool usage metric in `metrics.py` likely has a bug in detecting tool usage from evaluation results.

### Meta-Optimization Behavior
**Finding**: MIPROv2 can improve performance scores (60% → 65%) even when final prompt text remains identical. This indicates optimization of the improvement *process* rather than just the final output.

**Recommendation**: For significant prompt text changes, test with more complex tasks that require sophisticated prompting.

## Research Applications

This implementation enables research into:
- Prompt engineering best practices
- Task-specific optimization strategies
- Cross-model prompt transferability
- Automated prompt evolution
- Performance prediction and modeling

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
python -m pytest tests/test_dspy*.py -v
```

## License

This module is part of gptme and follows the same license terms.
