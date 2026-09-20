# Spec: Real First-Run E2E for gptme Tauri

**Goal**: A CI test that fails when the Tauri app cannot complete its first-run "Local → Connect" flow.

**Background**: The existing `tauri/e2e` tests run against a mock `index.html` served on port 5701. They verify chat UI interactions but cannot see first-run regressions like the `tauri://localhost` retarget bug (gptme/gptme#3606 → fixed in #3882) or a frozen sidecar missing hook modules (#3883/#3884). Both bugs shipped in stable v0.34.0 with green E2E.

---

## Decision: Option A — Full Integration, Focused Scope

Build the real webui `dist`, the real PyInstaller sidecar, and the Tauri debug binary in the E2E job. Add one focused test that walks through the SetupWizard Local flow and asserts a connected server.

### Why not Option B (mock server)
A mock server would have caught #3882 (the frontend rewrote the URL to `tauri://localhost`), but it would NOT have caught #3883/#3884 (the sidecar fails to start because hook modules are missing from the frozen binary). The value of this test is end-to-end assurance that the shipped artifact works; mocking the backend defeats the purpose.

### Why not Option C (release build)
Release builds are slower and already covered by the `build` matrix job. The debug binary is sufficient for connectivity testing, and `tauri-driver` works with it.

### Scope boundary
- **In scope**: Local setup → Connect → green connected indicator.
- **Out of scope**: Chat message send/receive, Cloud setup, provider configuration, first reply generation.
- **One test file**: `test/specs/first_run.test.js`.

The old `chat_flow.test.js` exercised a hand-written mock page rather than the
shipped app and is removed by this change. Real desktop chat coverage remains
follow-up work.

---

## CI Integration Plan

### `tauri.yml` E2E job changes

1. **Replace the "Create webui placeholder for E2E" step** with a real build:
   ```yaml
   - name: Build webui dist
     run: cd webui && npm ci && npm run build
   ```

2. **Replace the "Create placeholder sidecar" step** with a real build:
   ```yaml
   - name: Build sidecar binary
     run: make tauri-build-sidecar
   ```
   This runs `tauri/scripts/build-sidecar.sh`, which uses PyInstaller with the corrected spec (post-#3883).

3. **Build the Tauri debug binary with `tauri/custom-protocol`** so it embeds the real `frontendDist` instead of loading an unserved Vite `devUrl`.

4. **Assign `GPTME_SERVER_PORT` dynamically in `wdio.conf.js`'s `onPrepare` hook** before `tauri-driver` starts. The driver, app, and test inherit the same isolated port, avoiding collisions without killing unrelated listeners. `beforeSession` is too late because it runs in the worker after the launcher has spawned `tauri-driver`.

### Runtime estimate
- `npm ci` in `webui/`: ~1 min (cached)
- `npm run build`: ~30 s
- `make tauri-build-sidecar`: ~2 min (uv sync + PyInstaller)
- `cargo build`: ~3 min (cached via rust-cache)
- **Total delta over current E2E job**: ~3–4 min.

The current E2E job finishes in ~7 min (build + driver install + test). This would push it to ~10–11 min, still well within the longest job in the workflow (Build Android at ~13 min).

---

## Test Design (`first_run.test.js`)

### Prerequisites
- A fresh Tauri app launch after the E2E hook clears its WebKit profile.
- The sidecar starts automatically (Tauri manages `externalBin`).
- The app opens to the SetupWizard because setup is incomplete.

### Steps
1. Wait for the SetupWizard dialog to appear.
2. Select the stable `setup-wizard-get-started` test hook.
3. Select the stable `setup-wizard-local` test hook.
4. Click "Connect" immediately after the Local step renders; this intentionally exercises the race where the Tauri status effect may not have persisted the managed sidecar URL/token yet.
5. Wait for an `isConnected`-derived UI signal or the provider/completion step.

### Negative control
A build with #3882 reverted (or a manually broken `getBundledLoopbackOrigin()`) should fail step 4 or 5. This validates the test's sensitivity.

---

## Risk: Sidecar startup time

The PyInstaller sidecar has a ~2–3 s cold-start on Linux. The test's `before` hook should wait for the sidecar to be ready before clicking Connect. Options:
- Poll `fetch('http://127.0.0.1:15700/api/v2/models')` from the test context until it 200s.
- Or increase the Connect timeout in the test.

Polling is preferred because it makes the test deterministic.

---

## Acceptance Criteria

- [ ] `first_run.test.js` is added and passes on a build that includes #3882 and #3883.
- [ ] Reverting #3882 causes the test to fail (negative control).
- [ ] The E2E job runtime stays under 12 min.
- [ ] `smoke.test.js` still passes.
