# gptme-tauri

A desktop application for [gptme](https://github.com/gptme/gptme) built with [Tauri](https://tauri.app/).

This app packages the gptme web UI (bundled in the [gptme](https://github.com/gptme/gptme) repo) with a bundled `gptme-server` binary, providing a standalone desktop experience for gptme.

> **Note**: This directory lives inside the [gptme monorepo](https://github.com/gptme/gptme) under `tauri/`.
> Use the root `Makefile` targets (`make tauri-dev`, `make tauri-build`) to build from the repo root.

## Features

- 🖥️ Native desktop app with web UI
- 📦 Self-contained with bundled gptme-server
- 🚀 No need to install Python or manage dependencies
- 🔧 All gptme tools and capabilities available

## Prerequisites

- [Node.js](https://nodejs.org/) (for building)
- [Rust](https://rustup.rs/) (for Tauri)

## Development

From the **repo root**:

```bash
# Run in development mode (builds webui and starts Tauri dev server)
make tauri-dev
```

Or from the `tauri/` directory (after building webui once):

```bash
cd tauri
npm install
make dev
```

## Building

From the **repo root**:

```bash
# Build the sidecar (requires uv + pyinstaller). Rebuilds automatically when
# gptme Python sources are newer than the existing binary.
make tauri-build-sidecar

# Build the app
make tauri-build
```

The built application will be in `tauri/src-tauri/target/release/bundle/`.

## Project Structure

- `src-tauri/` - Tauri backend (Rust): app lifecycle, server management, IPC
- `scripts/build-sidecar.sh` - Builds the gptme-server binary for bundling
- `bins/` - Contains the bundled gptme-server binary (gitignored)
- The webui is at `../webui/` (the gptme monorepo's built-in webui)
