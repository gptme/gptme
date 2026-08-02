"""Tests for the model selection attestation module (Phase 0)."""

import json

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
    set_selection_trace(None)  # type: ignore[arg-type]
    assert get_selection_trace() is None


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
