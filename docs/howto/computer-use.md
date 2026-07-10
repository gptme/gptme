# How to Automate GUIs with Computer Use

Use gptme to control desktop applications, automate web forms, and capture screenshots
with the computer and browser tools.

## Prerequisites

Install the required system tools before using computer control:

```bash
# Check what's missing and see fix suggestions
gptme-doctor

# Linux/X11
sudo apt install xdotool scrot
# or: sudo pacman -S xdotool scrot

# macOS
brew install cliclick
# Then grant your terminal Screen Recording + Accessibility permissions in System Settings (macOS Ventura+) or System Preferences (older macOS)
```

For web automation (structured ARIA snapshots), install Playwright:

```bash
# Install gptme with browser support (provides the 'playwright' CLI)
pip install "gptme[browser]"
# Install Playwright system browsers (required for snapshot_url, open_page, etc.)
playwright install chromium
```

Optional: for screen recording (``start_recording`` / ``record_screen``), install ffmpeg:

```bash
# Linux
sudo apt install ffmpeg
# or: sudo pacman -S ffmpeg

# macOS
brew install ffmpeg
```

Optional: for accessibility-first control of native Linux apps (no screenshot needed),
install the AT-SPI2 stack:

```bash
sudo apt install python3-pyatspi
# or: sudo pacman -S python-pyatspi
```

For headless Linux environments, start an Xvfb display first:

```bash
Xvfb :1 -screen 0 1024x768x24 &
export DISPLAY=:1
fluxbox &   # or any window manager
```

## Validate your setup

After installing prerequisites, confirm everything is working:

```bash
# Check computer-use specific prerequisites (X11 tools, Playwright, ffmpeg, AT-SPI2...)
gptme-util computer doctor

# Run all three milestone demos (tweet, factorio, doom) against local HTML fixtures
gptme-util computer demo --all
```

`gptme-util computer demo --all` drives the real Playwright browser tool against
local HTML fixtures — the same `open_page`, `fill_element`, and `click_element`
calls that work against live sites. All three milestones should pass in under
3 seconds. If they do, browser interaction and selector resolution are working.

For X11/native-desktop diagnostics (requires a running display):

```bash
gptme-util computer latency            # screenshot capture latency (target: < 200 ms)
gptme-util computer latency --terminal # terminal startup latency (target: < 500 ms)
```

## Start a computer-use session

The `computer-use` profile sets the right tool access and backend selection policy:

```bash
gptme --agent-profile computer-use 'take a screenshot and describe what you see'
```

Or enable the computer tool for a one-off task:

```bash
gptme --tools +computer 'open Firefox, navigate to github.com, and take a screenshot'
```

## Web automation (structured-first)

For web targets, prefer structured ARIA snapshots over raw screenshots — they're faster,
cheaper, and more reliable when the page has a DOM you can address:

```bash
gptme --agent-profile computer-use 'go to https://news.ycombinator.com, find the top story, and summarize it'
```

gptme will automatically:
1. Use `snapshot_url()` to read the page's ARIA/accessibility tree
2. Use `open_page()` + `click_element()` / `fill_element()` when it needs to interact
3. Fall back to screenshots only for canvas, layout verification, or image-heavy content

Use `observe_web(url)` as a single-call shortcut — it calls `snapshot_url()` internally
and optionally appends a screenshot in one call:

```bash
gptme --agent-profile computer-use 'use observe_web() to read https://news.ycombinator.com and summarize the top story'
```

Fill a form without screenshots:

```bash
gptme --agent-profile computer-use \
  'go to the login form at http://localhost:3000/login, fill username "alice" and password "hunter2", click submit'
```

## Authenticated sessions (save and reuse login state)

For sites that require a login — Twitter/X, GitHub, your app — log in once and
save the session so future runs start already authenticated.

**Step 1: Log in and save the session**

```bash
gptme --agent-profile computer-use \
  'open https://x.com/login, fill username and password, click Log in, then call save_browser_state("~/.config/gptme/twitter-session.json")'
```

Or interactively from IPython inside a gptme session:

```python
open_page("https://x.com/login")
fill_element("#username", "yourhandle")
click_element("text=Next")
fill_element("[name='password']", "yourpassword")
click_element("text=Log in")
save_browser_state("~/.config/gptme/twitter-session.json")
```

**Step 2: Load the session in future runs**

```bash
export GPTME_BROWSER_STORAGE_STATE=~/.config/gptme/twitter-session.json
gptme --agent-profile computer-use 'go to https://x.com/compose/tweet, type "Hello from gptme!", and click Post'
```

Every `open_page()`, `snapshot_url()`, and `read_url()` call will now start with
the saved cookies and localStorage, so the site sees you as already logged in.

> **Security note**: The session file contains your browser cookies in plain
> text. Store it with restricted permissions (`chmod 600`) and keep it out of
> version control.

## Desktop / native app control

For native apps or anything not reachable via a URL, the `computer` tool takes over:

```bash
gptme --tools +computer 'open the calculator app, compute 137 * 42, and tell me the result'
```

### Accessibility-first (Linux AT-SPI2 / macOS)

On Linux (with `python3-pyatspi` installed) and macOS, prefer accessibility-first
interaction over pixel coordinates — it's robust against window position and size changes:

```bash
# Inspect the native accessibility tree of all open apps (no screenshot needed)
gptme --tools +computer 'use computer("accessibility_tree") to list the interactive elements in the open dialog'

# Click an element by role and name — no coordinates needed
gptme --tools +computer 'click the Save button using click_accessible_element'
```

In IPython inside a gptme session:

```python
# Linux: role names like "push button", "entry", "check box"
computer('accessibility_tree')
computer('click_accessible_element', text='push button:Save')

# macOS: role names use AX prefix
computer('accessibility_tree')
computer('click_accessible_element', text='AXButton:Save')
```

Fall back to coordinate-based `left_click` only for apps without accessibility support
(games, canvas UIs, some Electron apps).

The observe-act-verify loop:

```bash
gptme --tools +computer \
  'take a screenshot to see the current state' \
  - 'click the "New File" button at the top-left' \
  - 'type "hello world" then press Control+S' \
  - 'take a screenshot and confirm the file was saved'
```

## Screenshot and visual verification

Use `observe_desktop()` as a single-call shortcut for taking a desktop screenshot with
explicit observation intent — equivalent to `computer('screenshot')` but signals the
"look" phase of a look-act-look loop:

```python
# From IPython inside a gptme session
msg = observe_desktop()   # returns a screenshot message, or None if capture failed
```

Take a screenshot and analyse what's on screen:

```bash
gptme --tools +computer,+vision 'screenshot the screen and describe any UI errors you see'
```

Verify a web page renders correctly:

```bash
gptme --agent-profile computer-use \
  'open http://localhost:5173, take a screenshot, and check that the login button is visible'
```

## Efficient UI loops

Use `act_and_observe(action, ...)` to perform an action and automatically get a screenshot
once the screen settles — one call instead of separate action + wait + screenshot:

```python
# From IPython inside a gptme session
msgs = act_and_observe("left_click", coordinate=(760, 540))   # click then observe
msgs = act_and_observe("window_focus", text="Terminal")        # focus then observe
msgs = act_and_observe("type", text="ls -la\n")               # type then observe
```

Use `wait_for_change` after triggering actions when you need a longer timeout or more
control than `act_and_observe`'s 3-second default:

```bash
gptme --tools +computer \
  'click the submit button, then wait for the screen to change and describe the result'
```

Use `window_focus` when opening new windows so input goes to the right app:

```bash
gptme --tools +computer \
  'open a new terminal window, wait for it to appear, then run "ls -la"'
```

## Context-efficient multi-step tasks

For long, multi-step automations — filling multi-page forms, running GUI workflows, or
anything that would generate dozens of screenshots — use `computer_task()` to delegate
the whole task to a subagent. All intermediate screenshots stay inside the subagent's
context; only a text summary comes back to the parent:

```python
# From IPython inside a gptme session with the computer tool enabled
result = computer_task(
    "Open Firefox, go to https://x.com/compose/tweet, "
    "type 'Hello from gptme!', and click Tweet.",
    timeout=120,
)
print(result["status"], result["result"])
```

This is the "context-efficient tool-use loop until goal is achieved" pattern: rather
than having the parent accumulate dozens of screenshots and intermediate steps, the
subagent runs the full loop internally and reports back a brief summary.

If the task fails or you need the full step-by-step transcript:

```python
from gptme.tools.subagent import subagent_read_log
print(subagent_read_log(result["agent_id"]))
```

## Record a session (create demo videos)

Use `gptme-util computer record` to capture a screen recording as an MP4, then
`video-frames` to extract key frames for review or as visual context in gptme:

```bash
# Record 30 seconds to a file (blocks until done, then prints the path)
gptme-util computer record /tmp/tweet-demo.mp4 --duration 30

# Record at higher fps for smoother game-like recordings
gptme-util computer record game.mp4 --fps 24 --duration 10

# Extract frames for LLM review
gptme-util computer video-frames /tmp/tweet-demo.mp4 --fps 1 --limit 10
```

From IPython inside a gptme session, use `start_recording()` for async control:

```python
# Start recording, interact, then stop
rec = start_recording("demo.mp4")
computer_task("open Firefox, go to https://example.com, and describe the page")
path = rec.stop()     # saves the MP4
print(f"Saved to {path}")

# Or as a context manager:
with start_recording("demo.mp4") as rec:
    computer_task("open Firefox, navigate to https://x.com/compose/tweet, "
                  "type 'Hello from gptme!', and click Tweet.", timeout=120)
print(rec.output_path)

# Fixed-duration, synchronous:
path = record_screen("demo.mp4", duration=30)
```

Recordings use H.264 with fast-start so they play directly in any browser.
Combine with `video-frames` to extract a small set of key frames for debugging
or including as visual evidence in GitHub issues.

## Run inside Docker (isolated headless desktop)

For a fully isolated environment with VNC access:

```bash
make build-docker-computer   # build once
make run-docker-computer     # start container (noVNC on :6080, gptme server on :8080)
```

Then connect a browser to `http://localhost:6080` to watch the agent work.
Use `gptme-util computer record` inside the container to capture what the agent does.

## Backend selection cheat sheet

| Situation | Tool to use |
|-----------|-------------|
| Read a web page (no screenshot) | `observe_web(url)` or `snapshot_url(url)` |
| Fill a form or click a link | `open_page(url)` + `click_element()` / `fill_element()` |
| Observe current desktop state | `observe_desktop()` |
| Visual layout check / canvas | `computer('screenshot')` |
| Inspect native app elements (Linux/macOS) | `computer('accessibility_tree')` |
| Click native element by role + name | `computer('click_accessible_element', text='push button:Save')` |
| Wait for UI to settle | `computer('wait_for_change')` |
| Act then observe in one call | `act_and_observe(action, ...)` |
| Click a native app by coordinate | `computer('left_click', coordinate=(x, y))` |
| Type text in native app | `computer('type', text='...')` |
| Focus a window by name | `computer('window_focus', text='pattern')` |
| Scroll in native UI | `computer('scroll', coordinate=(x,y), text='down')` |
| Multi-step task, keep parent context lean | `computer_task(task, timeout=N)` |
| Record session for demo / debugging | `start_recording(path)` / `record_screen(path, duration=N)` |

## Diagnosing delays

Two separate latency sources can cause slowness in computer-use sessions:

**Screenshot latency** — time to capture and decode one screen frame.
Usually 50–200 ms on a local Xvfb display; higher on remote X11 or under heavy load.

```bash
gptme-util computer latency          # 5-shot screenshot latency sample
gptme-util computer latency --shots 10  # more stable estimate
```

**Terminal window startup latency** — time from launching a terminal emulator to the
window being ready for input.  This is the "new terminal window delay" and is caused
by X11 font loading (fontconfig scanning system font directories) plus shell
initialization, not by the screenshot pipeline.  It can be 1–4 seconds in a fresh
Xvfb environment:

```bash
gptme-util computer latency --terminal   # measure xterm startup time
```

If terminal startup is slow (> 500 ms), try these mitigations in order:

1. **Use a bitmap font** — bypasses Xft/fontconfig entirely:

   ```bash
   xterm -fn fixed &
   ```

   Make it permanent with ``~/.Xdefaults``:

   ```
   XTerm*font: fixed
   ```

2. **Warm the font cache** — reduces the first-launch penalty:

   ```bash
   fc-cache -f
   ```

3. **Use a lighter terminal** — ``st`` (suckless terminal) starts in under 100 ms:

   ```bash
   sudo apt install stterm
   ```

The ``window_focus`` action and ``act_and_observe("window_focus", text="...")`` already
handle the synchronisation correctly: they wait for the window to appear and the screen
to settle before returning, so the agent never races against an unready terminal.  The
mitigations above reduce total session time by shortening the wait, not by fixing a race.

## Tips

- **Use the `computer-use` profile**: it sets the backend selection policy so the agent
  picks the right tool automatically without extra prompting.
- **Prefer `observe_web(url)` for web**: it captures a structured ARIA snapshot in one
  call; use `snapshot_url` directly when you need lower-level control.
- **Combine with `--non-interactive`**: add `-n` for scripted or CI use where you don't want
  prompts (but ensure the task is well-scoped first).
- **Describe visual outcomes**: "confirm the dialog closed" works better than "click OK and move on".
