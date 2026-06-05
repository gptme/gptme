# gptme Chrome Extension

Chrome MV3 browser extension that brings gptme to the browser. The side panel
is a React app built with shared webui components. The background worker and
content script are TypeScript compiled with esbuild.

## Architecture

```
webui/extension/
├── manifest.json         # MV3 manifest
├── background.ts         # Service worker: GptmeClient + message bus
├── content/content.ts    # Selection capture content script
├── options/              # Options page (server URL, API key)
├── build.sh             # Build script
└── tsconfig.json        # Extension-specific TS config

webui/
├── panel.html            # Entry point for extension side panel
├── src/panel.tsx         # React entry point → ExtensionChat
├── src/components/ExtensionChat.tsx  # Chat UI component
└── vite.config.ts        # Multi-entry build (main + panel)
```

The extension reuses the webui's Vite build for the side panel (React, shared
components) while keeping the service worker and content script as standalone
TypeScript compiled with esbuild (no React needed in the background).

## Build

```bash
cd webui
npm install
cd extension
npm install
./build.sh
# Load webui/extension/dist/ in chrome://extensions
```

## Development

```bash
# Terminal 1: gptme server
uv run gptme-server

# Terminal 2: webui Vite dev (for panel dev)
cd webui
npm install
npm run dev
# The extension panel can be developed standalone at http://localhost:5701/panel.html
```

## Loading in Chrome

1. Run `./build.sh`
2. Open `chrome://extensions`
3. Enable Developer mode
4. Load unpacked → select `webui/extension/dist/`
5. Pin the extension and click to open the side panel
