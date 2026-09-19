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
   * Poll the sidecar readiness endpoint from the webview until it responds.
   * The PyInstaller sidecar can take 2–3 s to cold-start on Linux.
   *
   * Uses `/api/v2/server/health`, which is unauthenticated by design for
   * liveness/readiness probes (gptme#3701). The bearer-protected routes
   * (e.g. `/api/v2/models`) return 401 to this unauthenticated request, so
   * probing one of those would poll until timeout even on a healthy sidecar.
   */
  async function waitForSidecarReady(port, timeoutMs = 15000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      try {
        const result = await browser.execute(
          (url) =>
            fetch(url, { method: "GET" })
              .then((r) => r.status)
              .catch(() => 0),
          `http://127.0.0.1:${port}/api/v2/server/health`
        );
        if (result === 200) return;
      } catch (_e) {
        // ignore
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error(`Sidecar did not become ready on port ${port} within ${timeoutMs}ms`);
  }

  it("completes Local setup → Connect and reaches connected state", async () => {
    // The sidecar port is set via GPTME_SERVER_PORT in CI to avoid collisions.
    const sidecarPort = process.env.GPTME_SERVER_PORT || "5700";

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

    // 8. Wait for the connection to succeed. The button text changes to
    //    "Continue" when connected, and the wizard shows a "Connected to
    //    server" indicator; it may also auto-advance to the provider step,
    //    which removes the button. Accept any of those signals, plus the
    //    persisted loopback server URL (asserted in step 9).
    await browser.waitUntil(
      async () => {
        try {
          if (await (await $("button=Continue")).isExisting()) return true;
          if (await (await $("*=Connected to server")).isExisting()) return true;
          return await browser.execute(() => {
            try {
              const raw = localStorage.getItem("gptme_servers");
              if (!raw) return false;
              const registry = JSON.parse(raw);
              const active = registry.servers?.find(
                (s) => s.id === registry.activeServerId
              );
              return /^http:\/\/127\.0\.0\.1:\d+/.test(active?.baseUrl || "");
            } catch {
              return false;
            }
          });
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
