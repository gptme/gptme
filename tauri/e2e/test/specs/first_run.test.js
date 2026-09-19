/**
 * Real first-run E2E test for the gptme Tauri app.
 *
 * This test runs against the actual debug binary with the real webui dist
 * and the real PyInstaller sidecar (not the mock HTML). It verifies that
 * a fresh install can complete the "Local → Connect" onboarding flow.
 *
 * Regression guard for:
 *   - gptme/gptme#3606 → #3882 (tauri://localhost retarget bug)
 *   - gptme/gptme#3883/#3884 (frozen sidecar missing hook modules)
 *
 * Prerequisites:
 *   - Tauri debug binary built with real frontendDist and externalBin
 *   - Sidecar starts automatically (Tauri manages externalBin)
 *   - Clean profile (no persisted localStorage)
 */

describe("Real first-run flow", () => {
  /**
   * Poll the sidecar readiness endpoint from the host (Node), not from the
   * webview. A webview-side probe collapses two different failures into a
   * bare `0`: "the sidecar is not listening yet" and "the webview refused a
   * cross-origin fetch to http://127.0.0.1". Probing from Node sees the real
   * socket state, so the error can name what actually went wrong.
   *
   * Uses `/api/v2/server/health`, which is unauthenticated by design for
   * liveness/readiness probes (gptme#3701).
   *
   * The PyInstaller onefile sidecar must extract its whole bundle before it
   * can bind the port, which is slow on cold CI runners; hence the generous
   * budget. (It is started by the Tauri app's setup hook, not by this test.)
   */
  async function waitForSidecarReady(port, timeoutMs = 60000) {
    const url = `http://127.0.0.1:${port}/api/v2/server/health`;
    const deadline = Date.now() + timeoutMs;
    let lastError = "no attempt made";
    let lastLogged = null;
    while (Date.now() < deadline) {
      try {
        const res = await fetch(url, { method: "GET" });
        if (res.status === 200) return;
        lastError = `HTTP ${res.status}`;
      } catch (err) {
        lastError = err instanceof Error ? err.message : String(err);
      }
      if (lastError !== lastLogged) {
        console.log(`[e2e] waiting for sidecar on ${url}: ${lastError}`);
        lastLogged = lastError;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error(
      `Sidecar did not become ready on ${url} within ${timeoutMs}ms (last: ${lastError})`
    );
  }

  /**
   * Read the port the Tauri shell actually launched the sidecar on, straight
   * from the app (`get_server_status`, exposed via withGlobalTauri). Probing a
   * guessed port silently fails when the app does not inherit the CI's
   * GPTME_SERVER_PORT; asking the app removes that guess. Falls back to the
   * env var / default when the IPC is unavailable.
   */
  async function resolveSidecarPort() {
    const fallback = process.env.GPTME_SERVER_PORT || "5700";
    try {
      const status = await browser.execute(() => {
        const invoke =
          window.__TAURI__?.core?.invoke ?? window.__TAURI_INTERNALS__?.invoke;
        if (typeof invoke !== "function") return null;
        return invoke("get_server_status").then(
          (s) => ({ running: s?.running ?? null, port: s?.port ?? null }),
          () => null
        );
      });
      if (status?.port) {
        console.log(
          `[e2e] app reports sidecar port ${status.port} (running=${status.running})`
        );
        return status.port;
      }
      console.log("[e2e] get_server_status reported no port; falling back to", fallback);
    } catch (err) {
      console.log(
        "[e2e] get_server_status failed:",
        err instanceof Error ? err.message : String(err)
      );
    }
    return fallback;
  }

  it("completes Local setup → Connect and reaches connected state", async () => {
    const sidecarPort = await resolveSidecarPort();

    // 1. Wait for the app to load
    await browser.waitUntil(
      async () => (await browser.execute(() => document.readyState)) === "complete",
      { timeout: 30000, timeoutMsg: "App did not reach readyState=complete within 30s" }
    );

    // 2. Wait for the sidecar to be ready before clicking Connect
    await waitForSidecarReady(sidecarPort);

    // 3. Open the setup wizard directly to the Local step.
    //    The wizard does not auto-open on first run; it is triggered from
    //    WelcomeView which varies by build configuration (Tauri vs browser,
    //    local-managed vs remote-only). Driving via JS avoids fragility.
    await browser.execute(() => {
      // gptme's setupWizard$ is a Legendapp observable in window scope
      // because the webui bundle loads it as a module. We expose it via
      // the global window.__setupWizard hook that the store sets up,
      // or we can dispatch a custom event that the app listens for.
      // Fallback: mutate localStorage to simulate a first-run state
      // and reload, then the WelcomeView CTA appears.
      //
      // Simpler: directly open the wizard by simulating the observable set.
      // The observable object is not on window by default, but we can
      // trigger it through React devtools or a custom event.
      //
      // Most robust: set localStorage to force first-visit, reload,
      // then click the CTA button.
      const settings = JSON.parse(localStorage.getItem("gptme-settings") || "{}");
      settings.hasCompletedSetup = false;
      localStorage.setItem("gptme-settings", JSON.stringify(settings));
    });

    // Reload so WelcomeView reads the first-visit state
    await browser.execute(() => {
      window.location.reload();
    });

    await browser.waitUntil(
      async () => (await browser.execute(() => document.readyState)) === "complete",
      { timeout: 30000, timeoutMsg: "App did not reload within 30s" }
    );

    // 4. Wait for sidecar again after reload
    await waitForSidecarReady(sidecarPort);

    // 5. Click "Get started" to open the wizard
    const getStartedBtn = await $("button=Get started");
    await expect(getStartedBtn).toExist();
    await getStartedBtn.click();

    // 6. In "Choose your setup", click "Local"
    const localBtn = await $("//button[contains(., 'Local')]");
    await expect(localBtn).toExist();
    await localBtn.click();

    // 7. In "Local setup", click "Connect"
    const connectBtn = await $("button=Connect");
    await expect(connectBtn).toExist();
    await connectBtn.click();

    // 8. Wait for a genuine *connected* signal. Do NOT accept the persisted
    //    server registry as proof: `ApiContext.connect()` calls
    //    `updateServer()` (which persists the active baseUrl) and only then
    //    runs `checkConnection()`, so a failed connect still leaves a
    //    loopback URL saved in localStorage. Only the isConnected-derived UI
    //    can distinguish "reachable" from "stored":
    //      - "Connected to server" indicator / "Continue" button label (both
    //        render only while isConnected is true), or
    //      - the wizard advancing past the Local step (checkProviderAndAdvance
    //        runs only on a successful connect): provider step or complete step.
    await browser.waitUntil(
      async () => {
        try {
          if (await (await $("button=Continue")).isExisting()) return true;
          if (await (await $("*=Connected to server")).isExisting()) return true;
          if (await (await $("*=You're all set!")).isExisting()) return true;
          if (await (await $("*=Bring your own API key")).isExisting()) return true;
          return false;
        } catch (_e) {
          return false;
        }
      },
      {
        timeout: 15000,
        timeoutMsg: "Connect did not succeed within 15s (no connected signal appeared)",
      }
    );

    // 9. Verify the active server URL is the real loopback, NOT tauri://localhost
    //    (regression guard for gptme#3606 → #3882). This is a URL-correctness
    //    assertion, not evidence of connection — step 8 owns that.
    const serverUrl = await browser.execute(() => {
      try {
        const raw = localStorage.getItem("gptme_servers");
        if (!raw) return null;
        const registry = JSON.parse(raw);
        const active = registry.servers?.find(
          (s) => s.id === registry.activeServerId
        );
        return active?.baseUrl || null;
      } catch {
        return null;
      }
    });

    expect(serverUrl).toBeTruthy();
    expect(serverUrl).toMatch(/^http:\/\/127\.0\.0\.1:\d+/);
    expect(serverUrl).not.toContain("tauri://");
  });
});
