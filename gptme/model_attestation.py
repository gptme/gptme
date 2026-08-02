"""Model selection attestation and provenance tracking for gptme sessions.

This module provides durable, auditable records of:
- Which model a session requested
- How that request resolved through aliases and catalogs
- Which provider actually served it
- What evidence exists about the underlying checkpoint or backend revision
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


@dataclass
class ModelSelectionSource:
    """Describes where the model selection came from."""

    kind: Literal[
        "cli", "api_request", "chat_config", "models.default", "MODEL", "acp_runtime"
    ]
    """Source of the model selection."""

    value: str
    """The value selected from this source."""


@dataclass
class ModelSelectionResolution:
    """Describes how a model request was resolved."""

    source: ModelSelectionSource
    """Where the selection came from."""

    requested_model: str
    """The model string as requested by the user/API."""

    resolved_model: str
    """The final backend-facing model after aliasing and catalog resolution."""

    transport_provider: str
    """Provider handling the transport (e.g., 'gptme', 'openrouter', 'anthropic')."""

    backend_provider: str
    """Provider of the actual model weights (e.g., 'anthropic', 'openai')."""

    alias_target: str | None = None
    """If this was an alias, what it resolved to."""

    catalog_source: str | None = None
    """Where the catalog lookup came from (e.g., 'dynamic_fetch:gptme', 'local_config')."""

    resolution_notes: list[str] = field(default_factory=list)
    """Human-readable notes about resolution decisions."""


@dataclass
class ModelIdentityClaim:
    """Describes what we know about a model's identity and provenance."""

    attestation_level: Literal["selection_only", "provider_claim", "provider_signed"]
    """Honesty bit about what evidence supports this claim.

    - 'selection_only': we know what was requested and resolved to
    - 'provider_claim': provider exposed a revision/build identifier
    - 'provider_signed': provider exposed a cryptographically signed statement
    """

    provider_revision: str | None = None
    """Provider-specific revision, build ID, or date qualifier."""

    checkpoint_digest: str | None = None
    """Cryptographic digest of the actual model weights (not available for closed-weight models)."""

    registry_record: str | None = None
    """Reference to a model record in packages/model-capability-registry."""

    catalog_observed_at: datetime | None = None
    """Timestamp when this model was observed in a catalog."""

    signature: dict[str, Any] | None = None
    """Optional vendor-signed model identity envelope (Phase 3+).

    Contains:
    - algorithm: signing algorithm used
    - key_id: vendor's key identifier
    - payload_hash: hash of the signed payload
    - signed_at: ISO timestamp of the signature
    - value: the raw signed statement
    """


@dataclass
class ModelSelectionTrace:
    """Complete durable record of model selection for a session.

    This record travels with a session and can be embedded into output
    attestations, session metadata, and conversation summaries.
    """

    schema: str = "gptme.model-attestation/v0"
    """Schema version for forward compatibility."""

    selected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """When this selection was made."""

    selection: ModelSelectionResolution | None = None
    """How the model was resolved."""

    identity: ModelIdentityClaim | None = None
    """What we know about the model's identity."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        return asdict(self, dict_factory=self._dict_factory)

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())

    @staticmethod
    def _dict_factory(
        fields: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        """Custom dict factory to handle datetime serialization."""
        result: dict[str, Any] = {}
        for key, value in fields:
            if isinstance(value, datetime):
                result[key] = value.isoformat()
            elif isinstance(value, dict):
                result[key] = value
            elif hasattr(value, "__dataclass_fields__"):
                # Recursively handle nested dataclasses
                result[key] = ModelSelectionTrace._dict_factory(
                    [
                        (f.name, getattr(value, f.name))
                        for f in value.__dataclass_fields__.values()
                    ]
                )
            else:
                result[key] = value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelSelectionTrace:
        """Reconstruct from a dictionary."""
        if data.get("schema") != cls.schema:
            raise ValueError(f"Unsupported schema: {data.get('schema')}")

        # Parse selected_at timestamp
        selected_at_raw = data.get("selected_at")
        selected_at: datetime = datetime.now(timezone.utc)
        if isinstance(selected_at_raw, str):
            selected_at = datetime.fromisoformat(selected_at_raw.replace("Z", "+00:00"))

        # Reconstruct nested objects
        selection_data = data.get("selection")
        selection: ModelSelectionResolution | None = None
        if selection_data:
            source_data = selection_data.get("source", {})
            source = ModelSelectionSource(**source_data)
            selection = ModelSelectionResolution(
                source=source,
                requested_model=selection_data.get("requested_model"),
                resolved_model=selection_data.get("resolved_model"),
                transport_provider=selection_data.get("transport_provider"),
                backend_provider=selection_data.get("backend_provider"),
                alias_target=selection_data.get("alias_target"),
                catalog_source=selection_data.get("catalog_source"),
                resolution_notes=selection_data.get("resolution_notes", []),
            )

        identity_data = data.get("identity")
        identity: ModelIdentityClaim | None = None
        if identity_data:
            # Parse catalog_observed_at if present
            catalog_observed_at_raw = identity_data.get("catalog_observed_at")
            catalog_observed_at: datetime | None = None
            if isinstance(catalog_observed_at_raw, str):
                catalog_observed_at = datetime.fromisoformat(
                    catalog_observed_at_raw.replace("Z", "+00:00")
                )

            identity = ModelIdentityClaim(
                attestation_level=identity_data.get(
                    "attestation_level", "selection_only"
                ),
                provider_revision=identity_data.get("provider_revision"),
                checkpoint_digest=identity_data.get("checkpoint_digest"),
                registry_record=identity_data.get("registry_record"),
                catalog_observed_at=catalog_observed_at,
                signature=identity_data.get("signature"),
            )

        return cls(
            schema=data.get("schema", cls.schema),
            selected_at=selected_at,
            selection=selection,
            identity=identity,
        )


def create_selection_trace(
    requested_model: str,
    resolved_model: str,
    source_kind: Literal[
        "cli", "api_request", "chat_config", "models.default", "MODEL", "acp_runtime"
    ],
    source_value: str,
    transport_provider: str,
    backend_provider: str,
    alias_target: str | None = None,
    catalog_source: str | None = None,
    resolution_notes: list[str] | None = None,
    provider_revision: str | None = None,
    registry_record: str | None = None,
) -> ModelSelectionTrace:
    """Convenience constructor for creating a selection trace.

    Args:
        requested_model: The model string as requested
        resolved_model: The final backend-facing model
        source_kind: Where the selection came from
        source_value: The value selected from the source
        transport_provider: Provider handling transport
        backend_provider: Provider of the model weights
        alias_target: If this was an alias, what it resolved to
        catalog_source: Where the catalog lookup came from
        resolution_notes: Human-readable notes about resolution
        provider_revision: Provider-specific revision identifier
        registry_record: Reference to model-capability-registry record

    Returns:
        A ModelSelectionTrace with selection_only attestation level.
    """
    return ModelSelectionTrace(
        selection=ModelSelectionResolution(
            source=ModelSelectionSource(kind=source_kind, value=source_value),
            requested_model=requested_model,
            resolved_model=resolved_model,
            transport_provider=transport_provider,
            backend_provider=backend_provider,
            alias_target=alias_target,
            catalog_source=catalog_source,
            resolution_notes=resolution_notes or [],
        ),
        identity=ModelIdentityClaim(
            attestation_level="selection_only",
            provider_revision=provider_revision,
            registry_record=registry_record,
        ),
    )


# Context-local storage mirrors the default model's ownership. This keeps
# concurrent ACP/server sessions from seeing each other's provenance.
_current_trace_var: ContextVar[ModelSelectionTrace | None] = ContextVar(
    "model_selection_trace", default=None
)


def get_selection_trace() -> ModelSelectionTrace | None:
    """Return the model selection trace captured in the current context."""
    return _current_trace_var.get()


def set_selection_trace(trace: ModelSelectionTrace | None) -> None:
    """Store the model selection trace in the current context."""
    _current_trace_var.set(trace)


def record_runtime_selection(
    model: str, source_kind: Literal["api_request", "acp_runtime"]
) -> ModelSelectionTrace:
    """Resolve and record a model selected outside CLI initialization.

    This is intentionally explicit rather than part of ``set_default_model``:
    internal temporary model switches are not user/session selections.
    """
    from .llm.models import MODEL_ALIASES, get_model

    model_meta = get_model(model)
    transport_provider, model_name = model.split("/", 1)
    resolved_model = model
    if transport_provider == "gptme" and "/" in model_meta.model:
        resolved_model = f"gptme/{model_meta.model}"

    alias_name = model_name
    if transport_provider == "openai-subscription" and ":" in alias_name:
        alias_name = alias_name.rsplit(":", 1)[0]
    alias_model = MODEL_ALIASES.get(transport_provider, {}).get(alias_name)
    alias_target = (
        f"{transport_provider}/{alias_model}" if alias_model is not None else None
    )
    if alias_target is not None:
        resolved_model = alias_target

    parts = resolved_model.split("/")
    backend_provider = transport_provider
    if transport_provider == "gptme" and len(parts) >= 3:
        backend_provider = parts[2] if parts[1] == "openrouter" else parts[1]
    elif transport_provider == "openrouter" and len(parts) >= 3:
        backend_provider = parts[1]

    trace = create_selection_trace(
        requested_model=model,
        resolved_model=resolved_model,
        source_kind=source_kind,
        source_value=model,
        transport_provider=transport_provider,
        backend_provider=backend_provider,
        alias_target=alias_target,
        resolution_notes=(
            ["resolved model alias for metadata lookup"]
            if alias_target is not None
            else ["resolved backend model from provider catalog"]
            if resolved_model != model
            else []
        ),
    )
    set_selection_trace(trace)
    return trace
