# gptme-tauri

Desktop app for [gptme](https://github.com/gptme/gptme), built with [Tauri 2](https://tauri.app/).

The Tauri app wraps the gptme webui (`webui/`) in a native window and manages a local
`gptme-server` process as a bundled sidecar. It supports deep-linking via the `gptme://` URL
scheme for authentication flows.

## Directory structure

```
tauri/
├── src-tauri/           # Rust backend
│   ├── src/lib.rs       # Server lifecycle, IPC commands, deep-link handling
│   ├── src/main.rs      # Entry point (desktop)
│   ├── Cargo.toml
│   ├── Cargo.lock
│   ├── build.rs
│   ├── tauri.conf.json  # References ../webui/ for frontend
│   └── capabilities/
│       └── default.json # Shell/sidecar permissions
├── scripts/
│   └── build-sidecar.sh # Build gptme-server binary for bundling
├── package.json         # Tauri CLI dependency only
└── README.md            # This file
```

The frontend lives in `webui/` (the existing gptme webui). The Tauri config references
it directly — no submodule or symlink needed.

## Development

### Prerequisites

- [Rust](https://rustup.rs/) (stable)
- [Node.js](https://nodejs.org/) (LTS)
- [Tauri prerequisites](https://tauri.app/start/prerequisites/) for your platform

### Run in development mode

```bash
# From repo root:
make tauri-dev

# Or directly:
cd tauri && npm install && npm run tauri dev
```

This will:
1. Start the webui dev server (`webui/` on port 5701)
2. Open the Tauri window pointing at the dev server
3. Hot-reload on webui changes

### Build

```bash
# From repo root:
make tauri-build

# Or directly:
cd tauri && npm install && npm run tauri build
```

### Lint

```bash
make tauri-lint
```

### Build sidecar binary

The app bundles a `gptme-server` binary. To build it from the local source:

```bash
make tauri-build-sidecar
```

Requires `pyinstaller`. The binary is placed in `tauri/bins/gptme-server-<triple>`.

## Architecture

### Server lifecycle

On launch, the app spawns `gptme-server` as a Tauri sidecar. The server runs on port 5700.
On window close, the sidecar is killed. IPC commands (`start_server`, `stop_server`,
`get_server_status`) let the webui manage the server lifecycle.

### Deep links

The `gptme://` URL scheme is registered for the app. Deep links are used for the OAuth
device flow — when gptme.ai redirects to `gptme://callback?code=...`, the app extracts
the code and injects it into the webview to complete the auth handshake.

### Frontend

The webui is built from `webui/` during `beforeBuildCommand`. In dev mode,
`beforeDevCommand` starts the Vite dev server. The Tauri window loads from
`http://localhost:5701` in dev, and from the built `dist/` in production.

## Migration from gptme-tauri

This directory replaces the standalone [gptme-tauri](https://github.com/gptme/gptme-tauri)
repo. The key difference is the frontend: instead of a git submodule pointing to `gptme/gptme`,
`tauri.conf.json` references `../webui/` directly — the webui is already in this repo.

See the [merge proposal](https://github.com/ErikBjare/bob/blob/master/knowledge/technical-designs/gptme-tauri-upstream-merge-proposal.md)
for rationale and migration steps.
