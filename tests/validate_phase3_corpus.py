"""
Validation corpus for Phase 3 extractive compression.

Measures token reduction and validates compression quality across
different conversation types.

Usage:
    pytest tests/validate_phase3_corpus.py -v -s
"""

from datetime import datetime

import pytest

from gptme.llm.models import get_default_model, get_model
from gptme.message import Message
from gptme.tools.autocompact import auto_compact_log
from gptme.util.tokens import len_tokens


def count_tokens(messages: list[Message], model: str) -> int:
    """Count total tokens in message list."""
    return sum(len_tokens(msg.content, model) for msg in messages)


def create_technical_doc_conversation() -> list[Message]:
    """Conversation with long technical documentation."""
    doc = (
        """
# System Architecture Documentation

## Overview
This system implements a distributed microservices architecture designed for scalability
and reliability. The architecture consists of multiple independent services that communicate
through well-defined APIs and message queues.

## Core Components

### API Gateway
The API gateway serves as the entry point for all client requests. It handles authentication,
rate limiting, request routing, and load balancing across backend services. The gateway
implements circuit breaker patterns to prevent cascading failures.

### Service Mesh
We use a service mesh to manage inter-service communication. This provides observability,
security, and traffic management capabilities without requiring changes to application code.
The mesh handles retries, timeouts, and automatic failover.

### Data Layer
Our data layer uses a combination of SQL and NoSQL databases. Relational data is stored in
PostgreSQL clusters with read replicas. Document and time-series data uses MongoDB and
InfluxDB respectively. We implement the CQRS pattern to separate read and write workloads.

## Deployment Strategy
Services are containerized using Docker and orchestrated with Kubernetes. We use GitOps
for deployment automation, with ArgoCD managing the deployment pipeline. Infrastructure
is defined as code using Terraform.

## Monitoring and Observability
The system uses Prometheus for metrics collection, Grafana for visualization, and
ELK stack for log aggregation. Distributed tracing is implemented with Jaeger to
track requests across services.
"""
        * 5
    )  # ~2000 tokens

    return [
        Message("system", "You are a helpful assistant.", datetime.now()),
        Message("user", "Explain the system architecture", datetime.now()),
        Message("assistant", doc, datetime.now()),
        Message("user", "How does the service mesh work?", datetime.now()),
    ]


def create_code_heavy_conversation() -> list[Message]:
    """Conversation with multiple code blocks."""
    code_msg = (
        """
Here's the implementation:

```python
def process_data(input_file: str, output_file: str) -> None:
    \"\"\"Process data from input file and write results to output file.\"\"\"
    with open(input_file, 'r') as f:
        data = json.load(f)

    results = []
    for item in data:
        processed = transform_item(item)
        if validate_item(processed):
            results.append(processed)

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
```

And here's the test:

```python
def test_process_data():
    \"\"\"Test data processing.\"\"\"
    # Setup
    input_data = {'items': [1, 2, 3]}
    with open('test_input.json', 'w') as f:
        json.dump(input_data, f)

    # Execute
    process_data('test_input.json', 'test_output.json')

    # Verify
    with open('test_output.json', 'r') as f:
        results = json.load(f)
    assert len(results['items']) == 3
```

You can extend this pattern for other operations.
"""
        * 10
    )  # ~1500 tokens

    return [
        Message("system", "You are a helpful assistant.", datetime.now()),
        Message("user", "Show me how to process data files", datetime.now()),
        Message("assistant", code_msg, datetime.now()),
        Message("user", "Can you add error handling?", datetime.now()),
    ]


def create_mixed_conversation() -> list[Message]:
    """Conversation with mix of text, code, and tool results."""
    return [
        Message("system", "You are a helpful assistant.", datetime.now()),
        Message("user", "Check the project status", datetime.now()),
        Message("assistant", "Let me check the repository status.", datetime.now()),
        Message(
            "system",
            "```shell\ngit status\n```\n\nOutput:\n" + "file.txt\n" * 100,
            datetime.now(),
        ),  # Tool result
        Message(
            "assistant",
            "The repository has many uncommitted changes. " * 50,
            datetime.now(),
        ),  # Long response
    ]


@pytest.mark.slow
def test_validation_corpus_technical_doc():
    """Validate Phase 3 on technical documentation."""
    model = get_default_model() or get_model("gpt-4")

    # Create conversation
    messages = create_technical_doc_conversation()
    original_tokens = count_tokens(messages, model.model)

    print("\n=== Technical Documentation Test ===")
    print(f"Original tokens: {original_tokens}")

    # Run auto_compact (includes Phase 3)
    compacted = list(auto_compact_log(messages, limit=1000))
    compacted_tokens = count_tokens(compacted, model.model)

    # Calculate reduction
    reduction_pct = ((original_tokens - compacted_tokens) / original_tokens) * 100

    print(f"Compacted tokens: {compacted_tokens}")
    print(f"Reduction: {reduction_pct:.1f}%")

    # Validate
    assert reduction_pct > 10, f"Expected >10% reduction, got {reduction_pct:.1f}%"
    assert compacted_tokens < original_tokens, "Compression should reduce tokens"

    # Verify important content preserved
    compacted_content = " ".join(msg.content for msg in compacted)
    assert (
        "System Architecture" in compacted_content
        or "architecture" in compacted_content.lower()
    )
    assert "API Gateway" in compacted_content or "gateway" in compacted_content.lower()


@pytest.mark.slow
def test_validation_corpus_code_heavy():
    """Validate Phase 3 preserves code blocks."""
    model = get_default_model() or get_model("gpt-4")

    # Create conversation
    messages = create_code_heavy_conversation()
    original_tokens = count_tokens(messages, model.model)

    print("\n=== Code Heavy Test ===")
    print(f"Original tokens: {original_tokens}")

    # Run auto_compact
    compacted = list(auto_compact_log(messages, limit=1000))
    compacted_tokens = count_tokens(compacted, model.model)

    # Calculate reduction
    reduction_pct = ((original_tokens - compacted_tokens) / original_tokens) * 100

    print(f"Compacted tokens: {compacted_tokens}")
    print(f"Reduction: {reduction_pct:.1f}%")

    # Verify code blocks preserved
    compacted_content = " ".join(msg.content for msg in compacted)
    assert "```python" in compacted_content, "Code blocks should be preserved"
    assert (
        "def process_data" in compacted_content
    ), "Function definitions should be preserved"


@pytest.mark.slow
def test_validation_corpus_mixed():
    """Validate Phase 3 on mixed content."""
    model = get_default_model() or get_model("gpt-4")

    # Create conversation
    messages = create_mixed_conversation()
    original_tokens = count_tokens(messages, model.model)

    print("\n=== Mixed Content Test ===")
    print(f"Original tokens: {original_tokens}")

    # Run auto_compact
    compacted = list(auto_compact_log(messages, limit=1000))
    compacted_tokens = count_tokens(compacted, model.model)

    # Calculate reduction
    reduction_pct = ((original_tokens - compacted_tokens) / original_tokens) * 100

    print(f"Compacted tokens: {compacted_tokens}")
    print(f"Reduction: {reduction_pct:.1f}%")

    # Validate reduction (Phase 2 handles tool results, Phase 3 handles long messages)
    assert (
        reduction_pct > 20
    ), f"Expected >20% reduction on mixed content, got {reduction_pct:.1f}%"


def test_validation_corpus_summary():
    """Run all corpus tests and report summary."""
    model = get_default_model() or get_model("gpt-4")

    results: list[dict[str, str | float]] = []

    # Test each conversation type
    for name, create_fn in [
        ("Technical Doc", create_technical_doc_conversation),
        ("Code Heavy", create_code_heavy_conversation),
        ("Mixed Content", create_mixed_conversation),
    ]:
        messages = create_fn()
        original = count_tokens(messages, model.model)
        compacted = list(auto_compact_log(messages, limit=1000))
        final = count_tokens(compacted, model.model)
        reduction = ((original - final) / original) * 100

        results.append(
            {
                "type": name,
                "original": original,
                "compacted": final,
                "reduction_pct": reduction,
            }
        )

    # Print summary
    print("\n" + "=" * 60)
    print("Phase 3 Validation Corpus Summary")
    print("=" * 60)
    for r in results:
        print(f"\n{r['type']}:")
        print(f"  Original:  {r['original']:,} tokens")
        print(f"  Compacted: {r['compacted']:,} tokens")
        print(f"  Reduction: {r['reduction_pct']:.1f}%")

    # Calculate average
    avg_reduction = sum(float(r["reduction_pct"]) for r in results) / len(results)
    print(f"\nAverage Reduction: {avg_reduction:.1f}%")
    print("=" * 60)

    # Validate overall performance
    assert (
        avg_reduction > 15
    ), f"Expected >15% average reduction, got {avg_reduction:.1f}%"
