from .chat import chat
from .codeblock import Codeblock
from .logmanager import LogManager
from .message import Message
from .prompts import get_prompt

__all__ = ["Codeblock", "LogManager", "Message", "__version__", "chat", "get_prompt"]


def __getattr__(name: str):
    if name == "__version__":
        from .__version__ import __version__

        globals()["__version__"] = __version__
        return __version__
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
