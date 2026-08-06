import { test, expect } from '@playwright/test';

// Server-backed regression suite for gptme/gptme#3440.
//
// These tests require a live gptme-server running with the mock/echo provider
// (MODEL=mock/echo), so generation completes without real API credentials.
// They are skipped automatically when:
//   - The chat input stays disabled (no server connection), or
//   - The /api/v2/models endpoint is not accessible (auth required) or the
//     configured default is not the mock provider.
//
// In CI: the "dev" pass starts gptme-server with MODEL=mock/echo and
// GPTME_DISABLE_AUTH=1, so these tests always run there.  The "stable" pass
// uses a real provider with auth enabled — the models endpoint returns 401,
// checkMockServer returns false, and every test skips gracefully.
//
// Three UI-stability bugs from #3440 exercised here:
//
//   1. Model badge showed the wrong hardcoded model on load, switched after
//      chatConfig arrived.  Fix: skeleton pill while loading (PR #3441).
//
//   2. Scroll position jumped when assistant tokens streamed in.
//      Fix: scrollToBottom after virtualizer + rAF settling (PR #3450).
//
//   3. Multiple animate-spin elements per in-flight tool execution.
//      Fix: single Loader2 in header, timer beside it (PR #3441).

const CONNECT_TIMEOUT = 15_000;
const NAV_TIMEOUT = 15_000;
const GENERATION_TIMEOUT = 20_000;

// ─────────────────────────────────────────────────────────────────────────────
// Fixture: check for a live server and that mock/echo is responding
// ─────────────────────────────────────────────────────────────────────────────

// Shared helper: returns true when the server is connected AND the configured
// default model is the mock/echo provider.
//
// Two-step check:
//   1. UI: chat-input is enabled (server is reachable and a conversation can
//      be started).
//   2. API: GET /api/v2/models returns 200 and default contains "mock".
//      The stable CI pass runs with auth enabled — the endpoint returns 401
//      there, so the check correctly returns false and all tests skip.
//      The dev pass sets GPTME_DISABLE_AUTH=1, so the endpoint is open and
//      returns default="mock/echo".
//
// page.request is a Node-side HTTP client (no CORS); it can reach the
// gptme-server at localhost:5700 regardless of the webui origin.
async function checkMockServer(page: import('@playwright/test').Page): Promise<boolean> {
  await page.goto('/');
  await page.waitForLoadState('networkidle');
  const input = page.getByTestId('chat-input');
  await expect(input).toBeVisible({ timeout: 10_000 });
  const enabled = await input.isEnabled({ timeout: CONNECT_TIMEOUT }).catch(() => false);
  if (!enabled) return false;

  const resp = await page.request.get('http://localhost:5700/api/v2/models').catch(() => null);
  if (!resp?.ok()) return false;
  const data = await resp.json().catch(() => null);
  return typeof data?.default === 'string' && data.default.includes('mock');
}

// ─────────────────────────────────────────────────────────────────────────────
// Helper: send a message and navigate, returning a short conversation ID token
// ─────────────────────────────────────────────────────────────────────────────
async function sendMessageAndNavigate(
  page: import('@playwright/test').Page,
  message: string
): Promise<void> {
  const input = page.getByTestId('chat-input');
  await expect(input).toBeEnabled({ timeout: CONNECT_TIMEOUT });
  await input.fill(message);
  await input.press('Enter');
  await page.waitForURL(/\/chat\//, { timeout: NAV_TIMEOUT });
}

// ─────────────────────────────────────────────────────────────────────────────
// Helper: wait for generation to complete.
// Signal: the .animate-spin generation indicator appears then disappears.
//
// The chat textarea is NOT disabled during generation — its disabled state only
// reflects connection and read-only status, so watching it would race.  The
// Tailwind .animate-spin class on the Loader2 header icon is the correct signal.
//
// If mock/echo responds before the first spinner check (extremely fast round
// trip), toBeVisible times out and the .catch() swallows it; toBeHidden then
// passes immediately because the spinner is already gone.  This is correct: if
// there was no visible spinner, generation was already done.
// ─────────────────────────────────────────────────────────────────────────────
async function waitForGenerationDone(page: import('@playwright/test').Page): Promise<void> {
  const spinner = page.locator('.animate-spin').first();
  await expect(spinner)
    .toBeVisible({ timeout: 5_000 })
    .catch(() => null);
  await expect(spinner).toBeHidden({ timeout: GENERATION_TIMEOUT });
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

test.describe('Live generation: UI stability with mock/echo provider (gptme#3440)', () => {
  test('model badge is non-empty and stable during and after generation', async ({ page }) => {
    const connected = await checkMockServer(page);
    test.skip(!connected, 'chat-input disabled — no live server');

    await sendMessageAndNavigate(page, 'hello');

    // Capture the badge text immediately after navigation (before chatConfig
    // arrives from the server).  Must be non-empty (skeleton or real model).
    const badge = page.getByTestId('model-selector');
    await expect(badge).toBeVisible({ timeout: 5_000 });
    const modelAtStart = (await badge.textContent()) ?? '';
    expect(modelAtStart.trim().length).toBeGreaterThan(0);

    await waitForGenerationDone(page);

    // After chatConfig has loaded the badge must still show a non-empty model
    // name — and it must not have reverted to the hardcoded fallback sentinel.
    const modelAfterGeneration = (await badge.textContent()) ?? '';
    expect(modelAfterGeneration.trim().length).toBeGreaterThan(0);

    // The old regression: badge flipped to 'claude-sonnet-4-5' on first load
    // because that was the in-memory fallback before chatConfig resolved.
    // With mock/echo the correct model is 'mock/echo', never the old fallback.
    expect(modelAfterGeneration).not.toContain('claude-sonnet-4-5');

    // Badge must not change between the two observation points (no flicker).
    // Allow 2 seconds of settling to detect any delayed re-render.
    await page.waitForTimeout(1_000);
    const modelAfterSettle = (await badge.textContent()) ?? '';
    expect(modelAfterSettle).toBe(modelAfterGeneration);
  });

  test('at most one spinner visible at any time during text generation', async ({ page }) => {
    const connected = await checkMockServer(page);
    test.skip(!connected, 'chat-input disabled — no live server');

    await sendMessageAndNavigate(page, 'spin-count-check');

    // Start spinner polling from just before generation begins.
    let maxSpinners = 0;
    let polling = true;
    const pollLoop = (async () => {
      while (polling) {
        const n = await page
          .locator('.animate-spin')
          .count()
          .catch(() => 0);
        if (n > maxSpinners) maxSpinners = n;
        await page.waitForTimeout(100).catch(() => null);
      }
    })();

    await waitForGenerationDone(page);
    polling = false;
    await pollLoop;

    // Before #3441 each in-flight tool card added its own Loader2 icon;
    // for plain text streaming (mock/echo) only the single generation
    // indicator should appear.
    expect(maxSpinners).toBeLessThanOrEqual(1);
  });

  test('scroll stays at the bottom while tokens stream in', async ({ page }) => {
    const connected = await checkMockServer(page);
    test.skip(!connected, 'chat-input disabled — no live server');

    await sendMessageAndNavigate(page, 'scroll-anchor-test');
    await waitForGenerationDone(page);

    // Allow a brief layout-settle before sampling.
    await page.waitForTimeout(300);

    const viewport = page.getByTestId('message-scroll-viewport');
    await expect(viewport).toBeVisible({ timeout: 5_000 });

    const { scrollTop, scrollHeight, clientHeight } = await viewport.evaluate((el) => ({
      scrollTop: el.scrollTop,
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
    }));

    // Auto-scroll must have followed the new assistant message to the bottom.
    // A 100 px tolerance covers virtualizer overscan without hiding real jumps.
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    expect(distanceFromBottom).toBeLessThan(100);
  });

  test('scroll does not jump between two samples taken during streaming', async ({ page }) => {
    const connected = await checkMockServer(page);
    test.skip(!connected, 'chat-input disabled — no live server');

    await sendMessageAndNavigate(page, 'scroll-stability-test');

    const viewport = page.getByTestId('message-scroll-viewport');
    await expect(viewport).toBeVisible({ timeout: 5_000 });

    // Gate the first sample on the generation spinner being visible so that
    // both samples are taken while streaming is actually in flight.  If the
    // provider is so fast that the spinner is gone before we reach this line,
    // the .catch() swallows the timeout and both samples land post-generation —
    // the delta will then be ~0, which still satisfies the assertion.
    await expect(page.locator('.animate-spin').first())
      .toBeVisible({ timeout: 5_000 })
      .catch(() => null);

    const pos1 = await viewport.evaluate((el) => ({
      scrollTop: el.scrollTop,
      scrollHeight: el.scrollHeight,
    }));

    await page.waitForTimeout(300);

    const pos2 = await viewport.evaluate((el) => ({
      scrollTop: el.scrollTop,
      scrollHeight: el.scrollHeight,
    }));

    // scrollHeight may grow as tokens arrive, but scrollTop must not leap
    // backwards by a visible amount (the pre-#3450 bug: InlineToolExecution
    // appearing caused scrollHeight to grow, scrollTop to lag, and
    // autoScrollAborted to fire — freezing auto-scroll for the rest of the run).
    const scrollTopDelta = Math.abs(pos2.scrollTop - pos1.scrollTop);
    expect(scrollTopDelta).toBeLessThan(50);

    await waitForGenerationDone(page);
  });

  test('mock/echo response appears and the assistant message is visible', async ({ page }) => {
    const connected = await checkMockServer(page);
    test.skip(!connected, 'chat-input disabled — no live server');

    const testMessage = 'roundtrip-check';
    await sendMessageAndNavigate(page, testMessage);
    await waitForGenerationDone(page);

    // With mock/echo the response is deterministically "Echo: <input>".
    // This test is the most basic sanity check: if the Echo: prefix doesn't
    // appear the provider is not mock/echo and the generation tests may give
    // misleading results.  Other tests guard individually via skip conditions,
    // but this one is explicit.
    await expect(page.getByText(new RegExp(`Echo: ${testMessage}`))).toBeVisible({
      timeout: GENERATION_TIMEOUT,
    });
  });
});
