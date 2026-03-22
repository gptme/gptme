"""Model metadata, resolution, and listing.

Split from the original monolithic models.py into sub-modules:
- types: Provider types, ModelMeta, constants
- data: Static MODELS dict with per-provider model metadata
- resolution: Model lookup, alias resolution, default model management
- listing: Model listing, filtering, and display formatting
"""

# Re-export everything that was previously importable from gptme.llm.models
from .data import MODELS
from .listing import (
    _apply_model_filters,
    _get_models_for_provider,
    get_model_list,
    list_models,
)
from .resolution import (
    _default_model_var,
    _find_base_model_properties,
    _find_closest_model_properties,
    get_default_model,
    get_default_model_summary,
    get_model,
    get_recommended_model,
    get_summary_model,
    log_warn_once,
    set_default_model,
)
from .types import (
    MODEL_ALIASES,
    PROVIDERS,
    PROVIDERS_OPENAI,
    BuiltinProvider,
    CustomProvider,
    ModelMeta,
    Provider,
    _ModelDictMeta,
    is_custom_provider,
)

__all__ = [
    # Types
    "BuiltinProvider",
    "CustomProvider",
    "ModelMeta",
    "Provider",
    "_ModelDictMeta",
    # Constants
    "MODEL_ALIASES",
    "MODELS",
    "PROVIDERS",
    "PROVIDERS_OPENAI",
    # Internal (re-exported for test compatibility)
    "_default_model_var",
    "_find_base_model_properties",
    "_find_closest_model_properties",
    "_apply_model_filters",
    "_get_models_for_provider",
    # Functions
    "get_default_model",
    "get_default_model_summary",
    "get_model",
    "get_model_list",
    "get_recommended_model",
    "get_summary_model",
    "is_custom_provider",
    "list_models",
    "log_warn_once",
    "set_default_model",
]
