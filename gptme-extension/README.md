# gptme Browser Extension

Ask gptme about anything on the web. Select text on any page, open the side panel, and get answers from your local gptme instance.

## Status

MVP Slice 1 — skeleton + side panel shell. Functional chat with streaming responses.

## Requirements

- Chrome 114+ (Side Panel API)
- A running [gptme server](https://gptme.org/docs/server.html): `gptme server`
- Node.js (for building from source)

## Quick Start

```bash
# Install deps and build
cd gptme-extension
npm install
npm run build

# Load in Chrome:
# 1. Open chrome://extensions
# 2. Enable "Developer mode" (top right)
# 3. Click "Load unpacked" → select the dist/ folder
```

## Usage

1. Click the gptme icon in the Chrome toolbar → side panel opens
2. Type a question and press Enter
3. Select text on any page first to add it as context

If the server is offline, you'll see a "Server offline" indicator. Start it with `gptme server`.

## Configuration

Click ⚙ in the side panel or visit the extension options page to set:
- **Server URL** — default `http://localhost:5700`
- **API Key** — only needed if you started gptme with `--auth`

## Architecture

See [../../knowledge/technical-designs/gptme-chrome-extension-mvp.md](../../knowledge/technical-designs/gptme-chrome-extension-mvp.md) for the full spec.

| File | Purpose |
|------|---------|
| `background.ts` | Service worker: `GptmeClient` + message bus |
| `sidepanel/panel.ts` | Chat UI, SSE token streaming |
| `content/content.ts` | Text selection capture |
| `options/options.ts` | Settings page |
| `build.sh` | esbuild compile script |

## Roadmap

- **Slice 2** — Conversation history (list + reload prior conversations)
- **Slice 3** — API key auth + tool call visualization
- **Slice 4** — gptme.ai remote server support
