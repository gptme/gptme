import { test, expect, type Page } from '@playwright/test';
import { visionAssert } from './helpers/visionAssert';

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
async function checkMockServer(page: Page): Promise<boolean> {
  await page.goto('/');
  await page.waitForLoadState('networkidle');
  const input = page.getByTestId('chat-input');
  await expect(input).toBeVisible({ timeout: 10_000 });
  // Retrying assertion, NOT locator.isEnabled(): isEnabled() samples the current
  // state once and would report "disabled" whenever it runs before the server
  // connection settles — silently skipping all five tests and turning this suite
  // into a no-op that still reports green.
  const enabled = await expect(input)
    .toBeEnabled({ timeout: CONNECT_TIMEOUT })
    .then(() => true)
    .catch(() => false);
  if (!enabled) return false;

  const resp = await page.request.get('http://localhost:5700/api/v2/models').catch(() => null);
  if (!resp?.ok()) return false;
  const data = await resp.json().catch(() => null);
  return typeof data?.default === 'string' && data.default.includes('mock');
}

// ─────────────────────────────────────────────────────────────────────────────
// Helper: send a message and navigate, returning a short conversation ID token
// ─────────────────────────────────────────────────────────────────────────────
async function sendMessageAndNavigate(page: Page, message: string): Promise<void> {
  const input = page.getByTestId('chat-input');
  await expect(input).toBeEnabled({ timeout: CONNECT_TIMEOUT });
  await input.fill(message);
  await input.press('Enter');
  await page.waitForURL(/\/chat\//, { timeout: NAV_TIMEOUT });
  // Wait for ConversationContent to actually mount.  MainLayout returns null when
  // the conversationId is in the URL but the conversation hasn't been loaded into
  // the store yet — without this wait, message-scroll-viewport and model-selector
  // are not yet in the DOM, causing all subsequent element checks to time out.
  await expect(page.getByTestId('message-scroll-viewport')).toBeVisible({ timeout: NAV_TIMEOUT });
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
async function waitForGenerationDone(page: Page): Promise<void> {
  // Scoped to the conversation pane so sidebar list spinners cannot be mistaken
  // for the generation indicator.
  const spinner = page.locator('[data-conversation-pane] .animate-spin').first();
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

    // Capture the badge text once it shows an actual model name.  The badge
    // renders a loading skeleton (no text content) while chatConfig is being
    // fetched; textContent() on the skeleton returns '' and the length check
    // below would fail.  toHaveText waits until the skeleton clears and the
    // button with the real model name is rendered.
    const badge = page.getByTestId('model-selector');
    await expect(badge).toBeVisible({ timeout: 5_000 });
    await expect(badge).toHaveText(/\S+/, { timeout: 10_000 });
    const modelAtStart = (await badge.textContent()) ?? '';
    expect(modelAtStart.trim().length).toBeGreaterThan(0);

    await waitForGenerationDone(page);

    // After chatConfig has loaded the badge must still show a non-empty model
    // name — and it must not have reverted to the hardcoded fallback sentinel.
    const modelAfterGeneration = (await badge.textContent()) ?? '';
    expect(modelAfterGeneration.trim().length).toBeGreaterThan(0);

    // With mock/echo the resolved conversation model must be shown. Checking
    // the expected model directly keeps this regression guard valid if the
    // hardcoded fallback changes again.
    expect(modelAfterGeneration).toContain('echo');

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
    // Scope to the conversation pane: a page-wide '.animate-spin' also matches
    // the sidebar's conversation-list loading indicators, which spin briefly
    // right after navigation and have nothing to do with generation.
    const paneSpinners = page.locator('[data-conversation-pane] .animate-spin');
    let maxSpinners = 0;
    let polling = true;
    const pollLoop = (async () => {
      while (polling) {
        const n = await paneSpinners.count().catch(() => 0);
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

  test('scroll is at the bottom after generation completes', async ({ page }) => {
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
    // Scoped to the conversation pane: the sidebar renders the same text as the
    // conversation's last-message preview, which would otherwise make this a
    // strict-mode violation (two matches) rather than a clean assertion.
    const pane = page.locator('[data-conversation-pane]');
    await expect(pane.getByText(new RegExp(`Echo: ${testMessage}`)).first()).toBeVisible({
      timeout: GENERATION_TIMEOUT,
    });

    // Opt-in vision assertion. No-op unless GPTME_VISION_ASSERT=1.
    // Playwright toBeVisible() only proves the Echo text is in the DOM and not
    // display:none — it does not prove the message is unclipped on screen.
    await visionAssert(
      page,
      `The assistant message containing "Echo: ${testMessage}" is fully visible in the conversation pane: the text is complete, not clipped, not truncated, and not covered by another UI element.`,
      { locator: pane, name: 'generation-echo-roundtrip' }
    );
  });
});
