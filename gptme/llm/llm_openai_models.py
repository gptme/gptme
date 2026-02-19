from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import _ModelDictMeta  # fmt: skip

# Models marked deprecated are hidden from listings by default.
# They still work when explicitly requested via --model.

OPENAI_MODELS: dict[str, "_ModelDictMeta"] = {
    # GPT-5
    "gpt-5": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 1.25,  # $0.13 for cached inputs
        "price_output": 10,
        "supports_vision": True,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2024, 9, 30),
    },
    "gpt-5-mini": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 0.25,  # $0.025 for cached inputs
        "price_output": 2,
        "supports_vision": True,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2024, 5, 31),
    },
    "gpt-5-nano": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 0.05,  # $0.005 for cached inputs
        "price_output": 0.4,
        "supports_vision": True,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2024, 5, 31),
    },
    # GPT-4.1
    "gpt-4.1": {
        "context": 1_047_576,
        "max_output": 32_768,
        "price_input": 2,
        "price_output": 8,
        "supports_vision": True,
        "knowledge_cutoff": datetime(2025, 3, 1),
    },
    "gpt-4.1-mini": {
        "context": 1_047_576,
        "max_output": 32_768,
        "price_input": 0.4,
        "price_output": 1.6,
        "supports_vision": True,
        "knowledge_cutoff": datetime(2025, 3, 1),
    },
    "gpt-4.1-nano": {
        "context": 1_047_576,
        "max_output": 32_768,
        "price_input": 0.1,
        "price_output": 0.4,
        "supports_vision": True,
        "knowledge_cutoff": datetime(2025, 3, 1),
    },
    # GPT-4o
    "gpt-4o": {
        "context": 128_000,
        "price_input": 5,
        "price_output": 15,
        "supports_vision": True,
        # October 2023
        "knowledge_cutoff": datetime(2023, 10, 1),
    },
    "gpt-4o-2024-08-06": {
        "context": 128_000,
        "price_input": 2.5,
        "price_output": 10,
        "knowledge_cutoff": datetime(2023, 10, 1),
        "deprecated": True,
    },
    "gpt-4o-2024-05-13": {
        "context": 128_000,
        "price_input": 5,
        "price_output": 15,
        "knowledge_cutoff": datetime(2023, 10, 1),
        "deprecated": True,
    },
    # GPT-4o mini
    "gpt-4o-mini": {
        "context": 128_000,
        "price_input": 0.15,
        "price_output": 0.6,
        "supports_vision": True,
        "knowledge_cutoff": datetime(2023, 10, 1),
    },
    "gpt-4o-mini-2024-07-18": {
        "context": 128_000,
        "price_input": 0.15,
        "price_output": 0.6,
        "knowledge_cutoff": datetime(2023, 10, 1),
        "deprecated": True,
    },
    # OpenAI o4-mini
    "o4-mini": {
        "context": 200_000,
        "max_output": 100_000,
        "price_input": 1.1,
        "price_output": 4.4,
        "supports_vision": True,
        "supports_reasoning": True,
    },
    # OpenAI o3
    "o3": {
        "context": 200_000,
        "max_output": 100_000,
        "price_input": 2,
        "price_output": 8,
        "supports_vision": True,
        "supports_reasoning": True,
    },
    "o3-mini": {
        "context": 200_000,
        "max_output": 100_000,
        "price_input": 1.1,
        "price_output": 4.4,
        "supports_reasoning": True,
    },
    # OpenAI o1
    "o1": {
        "context": 200_000,
        "max_output": 100_000,
        "price_input": 15,
        "price_output": 60,
        "supports_reasoning": True,
    },
    "o1-preview": {
        "context": 128_000,
        "price_input": 15,
        "price_output": 60,
        "supports_reasoning": True,
        "deprecated": True,
    },
    "o1-preview-2024-09-12": {
        "context": 128_000,
        "price_input": 15,
        "price_output": 60,
        "supports_reasoning": True,
        "deprecated": True,
    },
    "o1-mini": {
        "context": 128_000,
        "price_input": 3,
        "price_output": 12,
        "supports_reasoning": True,
        "deprecated": True,
    },
    "o1-mini-2024-09-12": {
        "context": 128_000,
        "price_input": 3,
        "price_output": 12,
        "supports_reasoning": True,
        "deprecated": True,
    },
    # GPT-4 Turbo (deprecated: superseded by GPT-4o and GPT-4.1)
    "gpt-4-turbo": {
        "context": 128_000,
        "price_input": 10,
        "price_output": 30,
        "deprecated": True,
    },
    "gpt-4-turbo-2024-04-09": {
        "context": 128_000,
        "price_input": 10,
        "price_output": 30,
        "deprecated": True,
    },
    "gpt-4-0125-preview": {
        "context": 128_000,
        "price_input": 10,
        "price_output": 30,
        "deprecated": True,
    },
    "gpt-4-1106-preview": {
        "context": 128_000,
        "price_input": 10,
        "price_output": 30,
        "deprecated": True,
    },
    "gpt-4-vision-preview": {
        "context": 128_000,
        "price_input": 10,
        "price_output": 30,
        "deprecated": True,
    },
    # GPT-4 (deprecated: superseded by GPT-4 Turbo, GPT-4o)
    "gpt-4": {
        "context": 8192,
        "price_input": 30,
        "price_output": 60,
        "deprecated": True,
    },
    "gpt-4-32k": {
        "context": 32768,
        "price_input": 60,
        "price_output": 120,
        "deprecated": True,
    },
    # GPT-3.5 Turbo (deprecated: superseded by GPT-4o-mini)
    "gpt-3.5-turbo-0125": {
        "context": 16385,
        "price_input": 0.5,
        "price_output": 1.5,
        "deprecated": True,
    },
    "gpt-3.5-turbo": {
        "context": 16385,
        "price_input": 0.5,
        "price_output": 1.5,
        "deprecated": True,
    },
    "gpt-3.5-turbo-instruct": {
        "context": 4096,
        "price_input": 1.5,
        "price_output": 2,
        "deprecated": True,
    },
    "gpt-3.5-turbo-1106": {
        "context": 16385,
        "price_input": 1,
        "price_output": 2,
        "deprecated": True,
    },
    "gpt-3.5-turbo-0613": {
        "context": 4096,
        "price_input": 1.5,
        "price_output": 2,
        "deprecated": True,
    },
    "gpt-3.5-turbo-16k-0613": {
        "context": 16385,
        "price_input": 3,
        "price_output": 4,
        "deprecated": True,
    },
    "gpt-3.5-turbo-0301": {
        "context": 4096,
        "price_input": 1.5,
        "price_output": 2,
        "deprecated": True,
    },
    # Legacy completion models (deprecated)
    "davinci-002": {
        "context": 4096,
        "price_input": 2,
        "price_output": 2,
        "deprecated": True,
    },
    "babbage-002": {
        "context": 4096,
        "price_input": 0.4,
        "price_output": 0.4,
        "deprecated": True,
    },
}
