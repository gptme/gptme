# Browser Tool

gptme includes a browser tool that lets the assistant load pages, read their
content, take screenshots, and interact with web pages.

## Backends

### Playwright (recommended)

Full browser automation with screenshots, ARIA snapshots, clicking, form
filling, and scrolling.

**Installation:**

```bash
pipx install 'gptme[browser]'
PW_VERSION=$(pipx runpip gptme show playwright | grep Version | cut -d' ' -f2)
pipx run playwright==$PW_VERSION install chromium-headless-shell
```

### Lynx

Text-only fallback for basic page reading and web search.  No screenshot
support.

```bash
# Ubuntu / Debian
sudo apt install lynx
# macOS
brew install lynx
```

---

## Engine Configuration (`GPTME_BROWSER_ENGINE`)

The Playwright backend accepts three forms for `GPTME_BROWSER_ENGINE`:

| Value | Meaning |
|-------|---------|
| `chromium` (default) | Playwright's bundled Chromium headless shell |
| `firefox` | Playwright's bundled Firefox |
| `/path/to/binary` | Custom executable at a filesystem path |
| `executable-name` | Executable resolved via `$PATH` (`shutil.which`) |

Custom executables (path or name) are always launched with the **Firefox**
engine so they receive the same Playwright browser-context options.

### Use Firefox instead of Chromium

Useful for pages that detect and block headless Chromium.

```bash
PW_VERSION=$(pipx runpip gptme show playwright | grep Version | cut -d' ' -f2)
pipx run playwright==$PW_VERSION install firefox
export GPTME_BROWSER_ENGINE=firefox
gptme "read https://example.com"
```

### Use a fingerprint-patched Firefox (anti-detection)

Some pages fingerprint headless browsers even when Firefox is used.
A patched build such as [Camoufox](https://camoufox.com/) or
[invisible-playwright](https://github.com/QIN2DIM/undetected-playwright)
is a drop-in replacement that passes most fingerprint checks.

**By absolute path:**

```bash
export GPTME_BROWSER_ENGINE=/usr/local/bin/camoufox-runner
gptme "read https://bot-detection-test.vercel.app"
```

**By executable name on `$PATH`:**

```bash
# Assuming camoufox is on your PATH
export GPTME_BROWSER_ENGINE=camoufox
gptme "screenshot https://example.com"
```

gptme detects that the value is not a named engine, resolves it via
`shutil.which`, and passes it to Playwright's `executable_path=` kwarg when
launching Firefox.

#### Example: Camoufox setup

```bash
# Install Camoufox (fingerprint-patched Firefox)
pip install camoufox
python -m camoufox fetch           # downloads the patched Firefox binary

# Point gptme at it
export GPTME_BROWSER_ENGINE=$(python -m camoufox path)
gptme "read https://example.com"
```

---

## CDP Mode (`GPTME_BROWSER_CDP_URL`)

Connect to an already-running Chromium-based browser over the Chrome DevTools
Protocol instead of launching a new one.  Useful when you want to reuse an
existing authenticated browser session.

```bash
# Start Chrome/Chromium with remote debugging
chromium --remote-debugging-port=9222

# Connect gptme to it
export GPTME_BROWSER_CDP_URL=http://127.0.0.1:9222
gptme "read https://example.com"
```

> **Note:** CDP only works with Chromium-based browsers.  `GPTME_BROWSER_ENGINE`
> is ignored in CDP mode.

---

## Session Persistence (`GPTME_BROWSER_STORAGE_STATE`)

Save login cookies and local storage so authenticated sessions persist across
restarts.

```bash
# Log in once and save state
gptme "open https://x.com/login and log in with user@example.com / hunter2, then save_browser_state ~/.config/gptme/twitter.json"

# Reuse in the next session
export GPTME_BROWSER_STORAGE_STATE=~/.config/gptme/twitter.json
gptme "tweet 'hello from gptme'"
```

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `GPTME_BROWSER_ENGINE` | `chromium` | Engine or executable: `chromium`, `firefox`, a path, or a name on `$PATH` |
| `GPTME_BROWSER_CDP_URL` | *(unset)* | WebSocket URL of an existing Chrome DevTools Protocol server |
| `GPTME_BROWSER_STORAGE_STATE` | *(unset)* | Path to a Playwright storage-state JSON file for persistent sessions |
