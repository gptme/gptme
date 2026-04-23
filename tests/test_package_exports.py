import importlib

import gptme
import gptme.context


def test_gptme_package_root_lazy_exports() -> None:
    module = importlib.reload(gptme)

    assert "chat" not in module.__dict__
    assert "Codeblock" not in module.__dict__
    assert "LogManager" not in module.__dict__
    assert "Message" not in module.__dict__
    assert "get_prompt" not in module.__dict__

    assert module.chat.__module__ == "gptme.chat"
    assert module.Codeblock.__module__ == "gptme.codeblock"
    assert module.LogManager.__module__ == "gptme.logmanager.manager"
    assert module.Message.__module__ == "gptme.message"
    assert module.get_prompt.__module__ == "gptme.prompts"

    assert module.__dict__["chat"] is module.chat
    assert module.__dict__["Codeblock"] is module.Codeblock
    assert module.__dict__["LogManager"] is module.LogManager
    assert module.__dict__["Message"] is module.Message
    assert module.__dict__["get_prompt"] is module.get_prompt


def test_gptme_context_lazy_strip_reasoning_export() -> None:
    module = importlib.reload(gptme.context)

    assert "strip_reasoning" not in module.__dict__

    assert module.strip_reasoning.__module__ == "gptme.context.compress"
    assert module.__dict__["strip_reasoning"] is module.strip_reasoning
