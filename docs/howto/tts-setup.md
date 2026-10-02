---
audience: power-user
---

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

gptme-tts is not published on PyPI. The server is a single script,
`tts_server.py`, in the gptme-contrib repo. It is a self-contained
[uv](https://docs.astral.sh/uv/) script with inline dependencies, so it needs
no separate install beyond `uv`.

1. Get the script:

   ```sh
   git clone https://github.com/gptme/gptme-contrib
   cd gptme-contrib/plugins/gptme-tts
   ```

2. Start the server: `./tts_server.py --backend kokoro` (listens on
   `http://127.0.0.1:8765`; `--list-voices` and `--voice` pick a voice).
3. In the webui, open **Settings → TTS engine** and select **gptme-tts server**.
4. Set the **gptme-tts server URL** to `http://localhost:8765`.

Kokoro models are downloaded automatically on first use. The server also
supports `kittentts` and `chatterbox` backends; see the
[gptme-tts README](https://github.com/gptme/gptme-contrib/blob/master/plugins/gptme-tts/README.md)
for all options.

```{note}
`tts_server.py` does not currently send CORS headers, so a webui served from a
different origin (another host or port) cannot read its responses and falls
back to the browser engine. Until that changes, put the server behind a reverse
proxy that adds `Access-Control-Allow-Origin`, or use it from the terminal
plugin below.
```

### Read replies aloud in the terminal

The same server also powers the `gptme-tts` plugin, which reads the agent's
replies aloud in the gptme CLI as they stream. Install it into the same Python
environment as gptme:

```sh
pip install "git+https://github.com/gptme/gptme-contrib#subdirectory=plugins/gptme-tts"
# or, for a pipx-installed gptme:
pipx inject gptme "git+https://github.com/gptme/gptme-contrib#subdirectory=plugins/gptme-tts"
```

The package registers itself through the `gptme.plugins` entry point, so no
`[plugins] paths` entry is needed. If your config sets a `[plugins] enabled`
allowlist, add `"gptme-tts"` to it. With the server running on
`localhost:8765` (the plugin always connects to that port), gptme enables the
`tts` tool at startup. It needs a working audio output device.

To use OpenRouter speech models instead of a local server, set
`GPTME_TTS_BACKEND=openrouter` along with `OPENROUTER_API_KEY`.

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
| Setup | API key | uv + local server | None |

The **Automatic** mode tries engines in order: gptme-server → gptme-tts server
(if a URL is set) → browser.
