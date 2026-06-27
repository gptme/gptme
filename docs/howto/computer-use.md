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

For headless Linux environments, start an Xvfb display first:

```bash
Xvfb :1 -screen 0 1024x768x24 &
export DISPLAY=:1
fluxbox &   # or any window manager
```

## Start a computer-use session

The `computer-use` profile sets the right tool access and backend selection policy:

```bash
gptme --profile computer-use 'take a screenshot and describe what you see'
```

Or enable the computer tool for a one-off task:

```bash
gptme --tools +computer 'open Firefox, navigate to github.com, and take a screenshot'
```

## Web automation (structured-first)

For web targets, prefer structured ARIA snapshots over raw screenshots — they're faster,
cheaper, and more reliable when the page has a DOM you can address:

```bash
gptme --profile computer-use 'go to https://news.ycombinator.com, find the top story, and summarize it'
```

gptme will automatically:
1. Use `snapshot_url()` to read the page's ARIA/accessibility tree
2. Use `open_page()` + `click_element()` / `fill_element()` when it needs to interact
3. Fall back to screenshots only for canvas, layout verification, or image-heavy content

Fill a form without screenshots:

```bash
gptme --profile computer-use \
  'go to the login form at http://localhost:3000/login, fill username "alice" and password "hunter2", click submit'
```

## Desktop / native app control

For native apps or anything not reachable via a URL, the `computer` tool takes over:

```bash
gptme --tools +computer 'open the calculator app, compute 137 * 42, and tell me the result'
```

The observe-act-verify loop:

```bash
gptme --tools +computer \
  'take a screenshot to see the current state' \
  - 'click the "New File" button at the top-left' \
  - 'type "hello world" then press Control+S' \
  - 'take a screenshot and confirm the file was saved'
```

## Screenshot and visual verification

Take a screenshot and analyse what's on screen:

```bash
gptme --tools +computer,+vision 'screenshot the screen and describe any UI errors you see'
```

Verify a web page renders correctly:

```bash
gptme --profile computer-use \
  'open http://localhost:5173, take a screenshot, and check that the login button is visible'
```

## Efficient UI loops

Use `wait_for_change` after triggering actions so the agent waits for the UI to settle
instead of polling with repeated screenshots:

```bash
gptme --tools +computer \
  'click the submit button, then wait for the screen to change and describe the result'
```

Use `window_focus` when opening new windows so input goes to the right app:

```bash
gptme --tools +computer \
  'open a new terminal window, wait for it to appear, then run "ls -la"'
```

## Run inside Docker (isolated headless desktop)

For a fully isolated environment with VNC access:

```bash
make build-docker-computer   # build once
make run-docker-computer     # start container (noVNC on :6080, gptme server on :8080)
```

Then connect a browser to `http://localhost:6080` to watch the agent work.

## Backend selection cheat sheet

| Situation | Tool to use |
|-----------|-------------|
| Read a web page | `snapshot_url(url)` (no screenshot needed) |
| Fill a form or click a link | `open_page(url)` + `click_element()` / `fill_element()` |
| Visual layout check / canvas | `computer('screenshot')` |
| Wait for UI to settle | `computer('wait_for_change')` |
| Click a native app | `computer('left_click', coordinate=(x, y))` |
| Type text in native app | `computer('type', text='...')` |
| Focus a window by name | `computer('window_focus', text='pattern')` |
| Scroll in native UI | `computer('scroll', coordinate=(x,y), text='down')` |

## Tips

- **Use the `computer-use` profile**: it sets the backend selection policy so the agent
  picks the right tool automatically without extra prompting.
- **Prefer `snapshot_url` for web**: structured ARIA trees are faster and use no vision tokens.
- **Combine with `--non-interactive`**: add `-n` for scripted or CI use where you don't want
  prompts (but ensure the task is well-scoped first).
- **Describe visual outcomes**: "confirm the dialog closed" works better than "click OK and move on".
