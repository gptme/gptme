from gptme.llm import get_model_from_api_key, list_available_providers
from gptme.llm.llm_openai import extra_headers


def test_get_model_from_api_key_detects_aimlapi():
    sample_key = "3aaa9c515e894402a47c136ee3dd8f5a"
    api_key, provider, env_var = get_model_from_api_key(sample_key)
    assert api_key == sample_key
    assert provider == "aimlapi"
    assert env_var == "AIML_API_KEY"


def test_list_available_providers_includes_aimlapi(monkeypatch):
    monkeypatch.setenv("AIML_API_KEY", "test-key")
    providers = list_available_providers()
    assert ("aimlapi", "AIML_API_KEY") in providers
    # ensure no side-effect persists
    monkeypatch.delenv("AIML_API_KEY", raising=False)


def test_extra_headers_includes_defaults():
    expected = {
        "HTTP-Referer": "https://github.com/gptme/gptme",
        "X-Title": "gptme",
    }
    assert extra_headers("aimlapi") == expected
    assert extra_headers("openrouter") == expected
