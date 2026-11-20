"""Composer effort capabilities. Unknown models inherit rather than guessing."""

from .types import ModelMeta


def reasoning_effort_levels(model: ModelMeta) -> list[str]:
    """Return only levels the selected model/transport can represent."""
    name = model.model.split("@", 1)[0].split(":", 1)[0]
    provider = model.provider
    if not model.supports_reasoning:
        return []
    if provider == "moonshot" and name == "kimi-k3":
        return ["low", "high", "max"]
    if provider == "anthropic":
        if (
            "opus-4-7" in name
            or "opus-4-8" in name
            or "opus-5-5" in name
            or "fable-5" in name
        ):
            return ["low", "medium", "high", "xhigh", "max"]
        return ["low", "medium", "high"]
    if provider == "openrouter":
        # OpenRouter translates these common levels to upstream budgets. Do not
        # advertise disable/minimal/max for arbitrary always-thinking models.
        if name == "minimax/minimax-m3.1-flash":
            return ["low", "medium", "high", "xhigh", "max"]
        return ["low", "medium", "high"]
    if provider in ("openai", "openai-subscription"):
        if "pro" in name:
            # Pro tiers have a different compute contract; inherit until their
            # per-model supported levels are explicitly catalogued.
            return []
        if name.startswith(("o1", "o3", "o4")):
            return ["low", "medium", "high"]
        if name.startswith(("gpt-5", "gpt-6")):
            levels = ["low", "medium", "high"]
            if name.startswith(
                (
                    "gpt-5.1",
                    "gpt-5.2",
                    "gpt-5.3",
                    "gpt-5.4",
                    "gpt-5.5",
                    "gpt-5.6",
                    "gpt-6",
                )
            ):
                if provider == "openai":
                    levels.insert(0, "none")
                if not name.startswith("gpt-5.1"):
                    levels.append("xhigh")
            elif provider == "openai":
                levels.insert(0, "minimal")
            return levels
    return []


def validate_reasoning_effort(model: ModelMeta, effort: str | None) -> None:
    """Reject incompatible explicit selections before dispatching a generation."""
    if effort and effort not in reasoning_effort_levels(model):
        raise ValueError(
            f"Reasoning effort {effort!r} is not supported for {model.full}; "
            "select Default to inherit or choose a supported level."
        )
