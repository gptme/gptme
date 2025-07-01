# Testing autocommit functionality - second test
from .__version__ import __version__
from .cli import chat, main
from .codeblock import Codeblock
from .logmanager import LogManager
from .message import Message
from .prompts import get_prompt

__all__ = ["main", "chat", "LogManager", "Message", "get_prompt", "Codeblock"]
__version__ = __version__
