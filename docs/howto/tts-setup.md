# Set Up Text-to-Speech

gptme's webui can speak assistant messages aloud. Three engines are available,
from highest quality to simplest setup:

## Option 1 — OpenRouter (cloud, highest quality)

Requires an `OPENROUTER_API_KEY`. The gptme server proxies synthesis via
OpenRouter's speech API.

1. Get an API key at <https://openrouter.ai>.
2. Set it on the server: `export OPENROUTER_API_KEY=sk-or-...` before starting `gptme-server`.
3. In the webui, open **Settings → TTS engine** and select **Automatic** or
   **gptme-server (provider-backed)**.

## Option 2 — gptme-tts server (local, no API key)

[gptme-tts](https://github.com/gptme/gptme-tts) is a standalone TTS server
that runs locally using Kokoro. No cloud, no API key, ~80 MB model download.

1. Install: `pip install gptme-tts`
2. Start the server: `gptme-tts` (defaults to `http://localhost:5001`)
3. In the webui, open **Settings → TTS engine** and select **gptme-tts server**.
4. Set the **gptme-tts server URL** to `http://localhost:5001`.

Kokoro models are downloaded automatically on first use.

## Option 3 — Browser (always available)

The browser's built-in Web Speech API requires no setup but quality varies by
OS and browser. Select **Browser (Web Speech API)** in the TTS engine dropdown
to use this always, or it is used as an automatic fallback when neither of the
above is configured.

## Choosing an engine

| | OpenRouter | gptme-tts | Browser |
|---|---|---|---|
| Quality | High | Good | Variable |
| Latency | ~500 ms | ~1–5 s (CPU) | Instant |
| Privacy | Cloud | Local | Local |
| Setup | API key | pip install | None |

The **Automatic** mode tries engines in order: gptme-server → gptme-tts server
(if a URL is set) → browser.
