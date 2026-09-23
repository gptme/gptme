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
      `Sidecar did not become ready on ${url} within ${timeoutMs}ms (last: ${lastError})`,
    );
  }

  /**
   * The port the app launches its managed sidecar on. wdio.conf.js assigns an
   * isolated port before launching the app, and the Rust side reads the same
   * GPTME_SERVER_PORT variable (`server_port()` in tauri/src-tauri/src/lib.rs),
   * so the env var is the single source of truth.
   *
   * Do NOT try to read this back from the app via
   * `browser.execute(() => window.__TAURI__.core.invoke(...))`: WebDriver's
   * script context is isolated from page globals (and page storage), so
   * `window.__TAURI__` is undefined there. The previous probe therefore
   * always logged "get_server_status reported no port" and fell through to the
   * fallback, which only looked like it was asking the app.
   */
  function resolveSidecarPort() {
    const port = Number(process.env.GPTME_SERVER_PORT);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      throw new Error("wdio.conf.js did not assign a valid GPTME_SERVER_PORT");
    }
    return port;
  }

  /**
   * What the webview actually shows, for failure messages. A bare "element did
   * not appear" timeout cannot tell "feature broken" from "page never loaded"
   * (e.g. a dev-mode binary pointing at an unserved devUrl).
   */
  async function describeWebview() {
    const url = await browser
      .getUrl()
      .catch((e) => `<getUrl failed: ${e.message}>`);
    const body = await $("body")
      .getText()
      .then((t) => t.replace(/\s+/g, " ").slice(0, 500))
      .catch((e) => `<body text failed: ${e.message}>`);
    return `url=${url}, body=${JSON.stringify(body)}`;
  }

  it("completes Local setup → Connect and reaches connected state", async () => {
    const sidecarPort = resolveSidecarPort();

    // 1. Wait for the app to load
    await browser.waitUntil(
      async () =>
        (await browser.execute(() => document.readyState)) === "complete",
      {
        timeout: 30000,
        timeoutMsg: "App did not reach readyState=complete within 30s",
      },
    );

    // 2. Wait for the wizard's "Get started" button. The wizard auto-opens when
    //    `hasCompletedSetup` is false (the default on a clean profile).
    //    We do NOT probe the sidecar here: it is only needed at step 6 (Connect),
    //    and probing before the UI renders means a lingering sidecar from a prior
    //    Tauri session would cause the probe to return immediately — before React
    //    has mounted the wizard. Give React time with an explicit 30s wait.
    //
    //    Forcing `hasCompletedSetup` via `browser.execute(() => localStorage...)`
    //    is not possible: WebDriver's script context cannot access page storage on
    //    tauri:// origins (SecurityError: "The operation is insecure.").
    const getStartedBtn = await $("[data-testid='setup-wizard-get-started']");
    try {
      await getStartedBtn.waitForExist({ timeout: 30000 });
    } catch (err) {
      throw new Error(
        "SetupWizard 'Get started' button did not appear within 30s: " +
          (await describeWebview()),
      );
    }
    await getStartedBtn.click();

    // 5. In "Choose your setup", click the Local option through its stable
    //    test contract rather than matching user-facing copy.
    const localBtn = await $("[data-testid='setup-wizard-local']");
    try {
      await localBtn.waitForExist({ timeout: 30000 });
    } catch (err) {
      throw new Error(
        "SetupWizard 'Local' button did not appear within 30s after clicking 'Get started': " +
          (await describeWebview()),
      );
    }
    await localBtn.click();

    // 6. Wait for the sidecar to be ready before clicking Connect. Now that we
    //    are past the wizard's first step the UI is fully mounted, so the probe
    //    is no longer racing against React rendering.
    await waitForSidecarReady(sidecarPort);

    // 7. In "Local setup", click "Connect" if we are still on that step.
    // Auto-advance can already have moved to the provider/complete step while
    // we waited for the sidecar: isConnected becomes true, the local-step
    // button relabels to "Continue", and checkProviderAndAdvance() skips
    // Connect entirely. Treat that as the same success as clicking Connect.
    const connectBtn = await $("button=Connect");
    const continueBtn = await $("button=Continue");
    const providerStep = await $("[data-testid='setup-wizard-provider']");
    try {
      await browser.waitUntil(
        async () => {
          try {
            if (await connectBtn.isExisting()) return true;
            if (await continueBtn.isExisting()) return true;
            if (await providerStep.isExisting()) return true;
            if (await (await $("*=You're all set!")).isExisting()) return true;
            return false;
          } catch (_e) {
            return false;
          }
        },
        { timeout: 15000 },
      );
    } catch (err) {
      throw new Error(
        "SetupWizard did not reach Connect/Continue/provider within 15s: " +
          (await describeWebview()),
      );
    }
    if (await connectBtn.isExisting()) {
      await connectBtn.click();
    } else if (await continueBtn.isExisting()) {
      await continueBtn.click();
    }

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
    //
    //    Use a stable test contract for the provider step. WebDriver's partial
    //    link-text selector only matches anchors, so it cannot find the
    //    existing "Bring your own API key" paragraph even when connection and
    //    provider detection both succeeded.
    try {
      await browser.waitUntil(
        async () => {
          try {
            if (await (await $("button=Continue")).isExisting()) return true;
            if (await (await $("*=Connected to server")).isExisting())
              return true;
            if (await (await $("*=You're all set!")).isExisting()) return true;
            if (
              await (
                await $("[data-testid='setup-wizard-provider']")
              ).isExisting()
            )
              return true;
            return false;
          } catch (_e) {
            return false;
          }
        },
        { timeout: 15000 },
      );
    } catch (err) {
      // Same reason as the wizard wait above: name what the UI showed (a
      // connect error toast/message, or the wizard stuck on the Local step).
      throw new Error(
        "Connect did not succeed within 15s (no connected signal appeared): " +
          (await describeWebview()),
      );
    }

    // 10. The gptme#3606 → #3882 regression (Local preset retargeted to
    //    `tauri://localhost`) is guarded behaviourally: with that bug the
    //    connect fails, `isConnected` stays false, and step 8 times out with
    //    no "Connected to server" / "Continue" signal.
    //
    //    An explicit baseUrl assertion is not possible from here: it lived in
    //    `localStorage.gptme_servers`, and WebDriver's isolated script context
    //    cannot read page storage (SecurityError: "The operation is insecure.").
    //    The connected signal asserted in step 7 is the observable proxy.
  });
});
