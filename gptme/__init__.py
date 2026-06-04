from __future__ import annotations

import warnings

# Suppress requests' overly-strict version-compatibility warning.
# Newer urllib3/chardet/charset_normalizer work fine with requests;
# the warning just pollutes every gptme command output.
warnings.filterwarnings(
    "ignore",
    message=r"urllib3 .* or chardet.*charset_normalizer .* doesn't match a supported version",
)

__all__ = ["Codeblock", "LogManager", "Message", "__version__", "chat", "get_prompt"]

_lazy: dict[str, tuple[str, str]] = {
    "chat": (".chat", "chat"),
    "Codeblock": (".codeblock", "Codeblock"),
    "LogManager": (".logmanager", "LogManager"),
    "Message": (".message", "Message"),
    "get_prompt": (".prompts", "get_prompt"),
}


def __getattr__(name: str):
    if name in _lazy:
        import importlib

        module_name, attr_name = _lazy[name]
        module = importlib.import_module(module_name, package=__package__)
        obj = getattr(module, attr_name)
        globals()[name] = obj  # cache for next access
        return obj
    if name == "__version__":
        from .__version__ import __version__

        globals()["__version__"] = __version__
        return __version__
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
