"""Tests for the model selection attestation module (Phase 0)."""

import json
from contextvars import Context, copy_context
from unittest.mock import MagicMock

import pytest

from gptme.model_attestation import (
    ModelSelectionTrace,
    create_selection_trace,
    get_selection_trace,
    set_selection_trace,
)


def make_trace(
    requested="anthropic/claude-sonnet-4-6",
    resolved="anthropic/claude-sonnet-4-6",
    source_kind="cli",
    transport="anthropic",
    backend="anthropic",
) -> ModelSelectionTrace:
    return create_selection_trace(
        requested_model=requested,
        resolved_model=resolved,
        source_kind=source_kind,
        source_value=requested,
        transport_provider=transport,
        backend_provider=backend,
    )


def test_create_selection_trace_basic():
    trace = make_trace()
    assert trace.selection is not None
    assert trace.selection.requested_model == "anthropic/claude-sonnet-4-6"
    assert trace.selection.resolved_model == "anthropic/claude-sonnet-4-6"
    assert trace.selection.transport_provider == "anthropic"
    assert trace.selection.backend_provider == "anthropic"
    assert trace.identity is not None
    assert trace.identity.attestation_level == "selection_only"


def test_create_selection_trace_alias_resolution():
    trace = create_selection_trace(
        requested_model="gptme/claude-sonnet-4-6",
        resolved_model="anthropic/claude-sonnet-4-6",
        source_kind="cli",
        source_value="gptme/claude-sonnet-4-6",
        transport_provider="gptme",
        backend_provider="anthropic",
        alias_target="anthropic/claude-sonnet-4-6",
        resolution_notes=["gptme suffix match", "anthropic-first tie-break"],
    )
    assert trace.selection is not None
    assert trace.selection.transport_provider == "gptme"
    assert trace.selection.backend_provider == "anthropic"
    assert trace.selection.alias_target == "anthropic/claude-sonnet-4-6"
    assert len(trace.selection.resolution_notes) == 2


def test_schema_field():
    trace = make_trace()
    assert trace.schema == "gptme.model-attestation/v0"


def test_to_dict_roundtrip():
    trace = make_trace(
        requested="openrouter/anthropic/claude-sonnet-4-6",
        resolved="anthropic/claude-sonnet-4-6",
        source_kind="api_request",
        transport="openrouter",
        backend="anthropic",
    )
    d = trace.to_dict()
    assert d["schema"] == "gptme.model-attestation/v0"
    assert d["selection"]["requested_model"] == "openrouter/anthropic/claude-sonnet-4-6"
    assert d["selection"]["transport_provider"] == "openrouter"
    assert d["selection"]["backend_provider"] == "anthropic"
    assert d["identity"]["attestation_level"] == "selection_only"

    # Round-trip through from_dict
    trace2 = ModelSelectionTrace.from_dict(d)
    assert trace2.selection is not None
    assert trace2.identity is not None
    assert trace2.selection.requested_model == trace.selection.requested_model  # type: ignore[union-attr]
    assert trace2.selection.resolved_model == trace.selection.resolved_model  # type: ignore[union-attr]
    assert trace2.selection.transport_provider == trace.selection.transport_provider  # type: ignore[union-attr]
    assert trace2.selection.backend_provider == trace.selection.backend_provider  # type: ignore[union-attr]
    assert trace2.identity.attestation_level == trace.identity.attestation_level  # type: ignore[union-attr]


def test_to_json_roundtrip():
    trace = make_trace()
    json_str = trace.to_json()
    d = json.loads(json_str)
    assert d["schema"] == "gptme.model-attestation/v0"
    trace2 = ModelSelectionTrace.from_dict(d)
    assert trace2.selection is not None
    assert trace2.selection.requested_model == trace.selection.requested_model  # type: ignore[union-attr]


def test_from_dict_wrong_schema():
    d = {"schema": "wrong/v0", "selected_at": "2026-08-01T00:00:00Z"}
    with pytest.raises(ValueError, match="Unsupported schema"):
        ModelSelectionTrace.from_dict(d)


def test_session_trace_storage():
    trace = make_trace()
    set_selection_trace(trace)
    retrieved = get_selection_trace()
    assert retrieved is trace

    # Reset
    set_selection_trace(None)
    assert get_selection_trace() is None


def test_session_trace_storage_is_context_local():
    parent_trace = make_trace(requested="anthropic/parent")
    child_trace = make_trace(requested="anthropic/child")
    set_selection_trace(parent_trace)

    child_context = copy_context()
    child_context.run(set_selection_trace, child_trace)
    empty_context = Context()

    assert get_selection_trace() is parent_trace
    assert child_context.run(get_selection_trace) is child_trace
    assert empty_context.run(get_selection_trace) is None


def test_session_trace_distinguishes_providers():
    """Phase 0 acceptance: trace distinguishes transport vs backend provider."""
    trace = create_selection_trace(
        requested_model="gptme/claude-sonnet-4-6",
        resolved_model="anthropic/claude-sonnet-4-6",
        source_kind="cli",
        source_value="gptme/claude-sonnet-4-6",
        transport_provider="gptme",
        backend_provider="anthropic",
    )
    assert trace.selection is not None
    assert trace.identity is not None
    assert trace.selection.transport_provider != trace.selection.backend_provider
    assert trace.selection.requested_model != trace.selection.resolved_model
    assert trace.identity.attestation_level == "selection_only"


def test_source_kinds():
    for kind in (
        "cli",
        "api_request",
        "chat_config",
        "models.default",
        "MODEL",
        "acp_runtime",
    ):
        trace = make_trace(source_kind=kind)
        assert trace.selection is not None
        assert trace.selection.source.kind == kind


def _config() -> MagicMock:
    config = MagicMock()
    config.chat = MagicMock(model=None)
    config.user.models.default = None
    config.get_env.return_value = None
    return config


def test_record_selection_trace_resolves_gptme_backend():
    from gptme.init import _record_selection_trace
    from gptme.llm.models import ModelMeta

    _record_selection_trace(
        _config(),
        "gptme/claude-sonnet-4-6",
        "gptme/claude-sonnet-4-6",
        "gptme/anthropic/claude-sonnet-4-6",
        "gptme",
        ModelMeta(
            provider="gptme",
            model="anthropic/claude-sonnet-4-6",
            context=200_000,
        ),
    )

    trace = get_selection_trace()
    assert trace is not None and trace.selection is not None
    assert trace.selection.requested_model == "gptme/claude-sonnet-4-6"
    assert trace.selection.resolved_model == "gptme/anthropic/claude-sonnet-4-6"
    assert trace.selection.transport_provider == "gptme"
    assert trace.selection.backend_provider == "anthropic"
    assert trace.selection.resolution_notes == [
        "resolved backend model from provider catalog"
    ]


def test_record_selection_trace_resolves_openrouter_backend():
    from gptme.init import _record_selection_trace
    from gptme.llm.models import ModelMeta

    model = "openrouter/anthropic/claude-sonnet-4-6"
    _record_selection_trace(
        _config(),
        model,
        model,
        model,
        "openrouter",
        ModelMeta(
            provider="openrouter",
            model="anthropic/claude-sonnet-4-6",
            context=200_000,
        ),
    )

    trace = get_selection_trace()
    assert trace is not None and trace.selection is not None
    assert trace.selection.resolved_model == model
    assert trace.selection.transport_provider == "openrouter"
    assert trace.selection.backend_provider == "anthropic"


def test_record_selection_trace_resolves_openrouter_backed_gptme_backend():
    from gptme.init import _record_selection_trace
    from gptme.llm.models import ModelMeta

    requested = "gptme/gpt-5.4"
    resolved = "gptme/openrouter/openai/gpt-5.4"
    _record_selection_trace(
        _config(),
        requested,
        requested,
        resolved,
        "gptme",
        ModelMeta(
            provider="gptme",
            model="openrouter/openai/gpt-5.4",
            context=400_000,
        ),
    )

    trace = get_selection_trace()
    assert trace is not None and trace.selection is not None
    assert trace.selection.resolved_model == resolved
    assert trace.selection.transport_provider == "gptme"
    assert trace.selection.backend_provider == "openai"


def test_record_runtime_selection_resolves_server_model(monkeypatch):
    from gptme.llm.models import ModelMeta
    from gptme.model_attestation import record_runtime_selection

    monkeypatch.setattr(
        "gptme.llm.models.get_model",
        lambda _model: ModelMeta(
            provider="gptme",
            model="anthropic/claude-sonnet-4-6",
            context=200_000,
        ),
    )

    trace = record_runtime_selection("gptme/claude-sonnet-4-6", "api_request")
    assert trace.selection is not None
    assert trace.selection.source.kind == "api_request"
    assert trace.selection.resolved_model == "gptme/anthropic/claude-sonnet-4-6"
    assert trace.selection.backend_provider == "anthropic"
    assert get_selection_trace() is trace


def test_record_runtime_selection_preserves_alias_resolution(monkeypatch):
    from gptme.llm.models import MODEL_ALIASES, ModelMeta
    from gptme.model_attestation import record_runtime_selection

    monkeypatch.setitem(
        MODEL_ALIASES,
        "openai-subscription",
        {"gpt-5.6": "gpt-5.6-sol"},
    )
    monkeypatch.setattr(
        "gptme.llm.models.get_model",
        lambda _model: ModelMeta(
            provider="openai-subscription",
            model="gpt-5.6:high",
            context=400_000,
        ),
    )

    model = "openai-subscription/gpt-5.6:high"
    trace = record_runtime_selection(model, "api_request")

    assert trace.selection is not None
    assert trace.selection.requested_model == model
    assert trace.selection.alias_target == "openai-subscription/gpt-5.6-sol"
    assert trace.selection.resolved_model == "openai-subscription/gpt-5.6-sol"
    assert trace.selection.backend_provider == "openai-subscription"
    assert trace.selection.resolution_notes == [
        "resolved model alias for metadata lookup"
    ]


def test_record_selection_trace_preserves_alias_resolution(monkeypatch):
    from gptme.init import MODEL_ALIASES, _record_selection_trace
    from gptme.llm.models import ModelMeta

    monkeypatch.setitem(
        MODEL_ALIASES,
        "anthropic",
        {"claude-haiku-4-5": "claude-haiku-4-5-20251001"},
    )
    _record_selection_trace(
        _config(),
        "anthropic/claude-haiku-4-5",
        "anthropic/claude-haiku-4-5",
        "anthropic/claude-haiku-4-5",
        "anthropic",
        ModelMeta(provider="anthropic", model="claude-haiku-4-5", context=200_000),
    )

    trace = get_selection_trace()
    assert trace is not None and trace.selection is not None
    assert trace.selection.alias_target == "anthropic/claude-haiku-4-5-20251001"
    assert trace.selection.resolved_model == "anthropic/claude-haiku-4-5-20251001"
    assert trace.selection.resolution_notes == [
        "resolved model alias for metadata lookup"
    ]


def test_record_selection_trace_preserves_reasoning_suffixed_alias(monkeypatch):
    from gptme.init import MODEL_ALIASES, _record_selection_trace
    from gptme.llm.models import ModelMeta

    monkeypatch.setitem(
        MODEL_ALIASES,
        "openai-subscription",
        {"gpt-5.6": "gpt-5.6-sol"},
    )
    model = "openai-subscription/gpt-5.6:high"
    _record_selection_trace(
        _config(),
        model,
        model,
        model,
        "openai-subscription",
        ModelMeta(
            provider="openai-subscription",
            model="gpt-5.6:high",
            context=400_000,
        ),
    )

    trace = get_selection_trace()
    assert trace is not None and trace.selection is not None
    assert trace.selection.requested_model == model
    assert trace.selection.alias_target == "openai-subscription/gpt-5.6-sol"
    assert trace.selection.resolved_model == "openai-subscription/gpt-5.6-sol"
    assert trace.selection.resolution_notes == [
        "resolved model alias for metadata lookup"
    ]
