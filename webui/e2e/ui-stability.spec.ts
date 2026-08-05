import { test, expect } from '@playwright/test';

// Regression suite for gptme/gptme#3440.
//
// Three classes of UI instability reported during multi-step generation:
//
//   1. Model selector shows the wrong model (hardcoded fallback) on page load,
//      then jumps to the correct one once chatConfig loads from the API.
//
//   2. Text / scroll position jumps as tool-confirmation prompts, in-progress
//      cards, and collapsible steps appear and disappear during generation.
//
//   3. Multiple spinner icons appear simultaneously — one per pending tool
//      execution — making the UI feel chaotic and slowing down interactions.
//
// These tests guard against regressions at the UI layer without requiring live
// LLM generation: they cover the model-selector sync timing, scroll-anchor
// stability across DOM mutations, and the maximum spinner count during rendering.

test.describe('UI stability: model selector (gptme#3440)', () => {
  // The model selector (chat-input badge) must not flash the wrong model.
  //
  // Regression: when chatConfig loaded asynchronously, the badge showed the
  // hardcoded fallback ('claude-sonnet-4-x') for 1-3 seconds before switching
  // to the conversation's real model.  These tests catch that flicker.

  test('model selector is visible and non-empty when a conversation is open', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Open a demo conversation (no API call for chatConfig; uses demo defaults)
    await page.getByText('Introduction to gptme').click();
    await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });

    // The model badge must be present and show something readable
    const badge = page.getByTestId('model-selector');
    await expect(badge).toBeVisible({ timeout: 5000 });
    const text = (await badge.textContent()) ?? '';
    expect(text.trim().length).toBeGreaterThan(0);
  });

  test('model selector does not change after the conversation finishes loading', async ({
    page,
  }) => {
    // Navigate to a demo conversation and wait for full idle — if chatConfig loads
    // async and changes the badge, we'll catch it here.
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await page.getByText('Introduction to gptme').click();
    await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });

    const badge = page.getByTestId('model-selector');
    await expect(badge).toBeVisible({ timeout: 5000 });

    // Record the model name immediately after the conversation is displayed
    const modelAtOpen = await badge.textContent();

    // Wait for any async chatConfig fetch / re-render to settle
    await page.waitForTimeout(2000);

    // The badge must show the same model — no flicker to a different model
    const modelAfterSettle = await badge.textContent();
    expect(modelAfterSettle).toBe(modelAtOpen);
  });

  test('model selector shows the same model before and after navigating away and back', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await page.getByText('Introduction to gptme').click();
    await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });

    const badge = page.getByTestId('model-selector');
    await expect(badge).toBeVisible({ timeout: 5000 });
    const modelBeforeNavigation = await badge.textContent();

    // Navigate away, then back — model must be stable across the round-trip
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await page.getByText('Introduction to gptme').click();
    await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('model-selector')).toBeVisible({ timeout: 5000 });

    const modelAfterNavigation = await page.getByTestId('model-selector').textContent();
    expect(modelAfterNavigation).toBe(modelBeforeNavigation);
  });
});

test.describe('UI stability: scroll anchoring (gptme#3440)', () => {
  // When elements are added to or removed from the message list (tool confirmation
  // prompts, in-progress cards, step-group headers), the user's scroll position
  // must not jump.  These tests open a long conversation and verify that the
  // viewport stays stable while the page settles.

  test('scroll position stays at the bottom when a long conversation finishes rendering', async ({
    page,
  }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Stress test (200 messages)')).toBeVisible({ timeout: 10000 });

    await page.getByText('Stress test (200 messages)').click();
    await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });

    // Allow the virtualizer to settle
    await page.waitForTimeout(500);

    // The scroll viewport must be at (or very close to) the bottom — i.e. the latest
    // message is visible, not some arbitrary middle position.
    const viewport = page.getByTestId('message-scroll-viewport');
    const { scrollTop, scrollHeight, clientHeight } = await viewport.evaluate((el) => ({
      scrollTop: el.scrollTop,
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
    }));

    // Allow a 100-pixel tolerance for virtualizer overscan
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    expect(distanceFromBottom).toBeLessThan(100);
  });

  test('scroll position does not jump after settling on a long conversation', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Stress test (200 messages)')).toBeVisible({ timeout: 10000 });

    await page.getByText('Stress test (200 messages)').click();
    await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });

    // Let the first render pass complete
    await page.waitForTimeout(300);

    const viewport = page.getByTestId('message-scroll-viewport');

    // Sample the scroll position twice, 500 ms apart, after initial render.
    // If elements are jumping, the position will change significantly between samples.
    const pos1 = await viewport.evaluate((el) => ({
      scrollTop: el.scrollTop,
      scrollHeight: el.scrollHeight,
    }));

    await page.waitForTimeout(500);

    const pos2 = await viewport.evaluate((el) => ({
      scrollTop: el.scrollTop,
      scrollHeight: el.scrollHeight,
    }));

    // scrollHeight may grow slightly as virtualizer measures items, but
    // scrollTop must not shift by more than 50 px (a jump would be 100-500 px).
    const scrollTopDelta = Math.abs(pos2.scrollTop - pos1.scrollTop);
    expect(scrollTopDelta).toBeLessThan(50);
  });

  test('cumulative layout shift stays below 0.25 when opening a conversation', async ({
    page,
    browserName,
  }) => {
    test.skip(browserName !== 'chromium', 'CLS measurement requires Chromium CDP');
    test.setTimeout(30000);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Introduction to gptme')).toBeVisible({ timeout: 10000 });

    // Start CLS observer before clicking the conversation
    await page.evaluate(() => {
      (window as unknown as Record<string, unknown>).__clsAccumulator = 0;
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          const ls = entry as PerformanceEntry & { hadRecentInput: boolean; value: number };
          if (!ls.hadRecentInput) {
            (window as unknown as Record<string, number>).__clsAccumulator += ls.value;
          }
        }
      });
      observer.observe({ type: 'layout-shift', buffered: false });
      (window as unknown as Record<string, unknown>).__clsObserver = observer;
    });

    await page.getByText('Introduction to gptme').click();
    await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });

    // Wait for rendering to settle, then collect the CLS score
    await page.waitForTimeout(1500);

    const cls = await page.evaluate(() => {
      const observer = (window as unknown as Record<string, PerformanceObserver>).__clsObserver;
      if (observer) observer.disconnect();
      return (window as unknown as Record<string, number>).__clsAccumulator ?? 0;
    });

    // 0.25 is a generous ceiling (Web Vitals "poor" starts at 0.25).
    // A well-behaved virtualised list should be well under 0.1.
    // Catching anything ≥ 0.25 ensures major layout-jump regressions are flagged.
    expect(cls).toBeLessThan(0.25);
  });
});

test.describe('UI stability: spinner cardinality (gptme#3440)', () => {
  // The issue noted "not multiple spinner icons... that one has been driving me a bit
  // nuts".  During multi-step tool-use, each in-progress tool card showed its own
  // spinner, stacking up visually.  The following tests verify that static content
  // (demo conversations with completed tool calls) shows zero or at most one spinner,
  // and that any spinner present is associated with a clear, single status indicator.

  test('no spurious spinning elements appear in a fully-loaded demo conversation', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await page.getByText('Introduction to gptme').click();
    await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });

    // After the conversation finishes rendering, no element should still be spinning
    // (all tool calls are completed in the demo, so every status should be settled).
    await page.waitForTimeout(500);

    // Count DOM elements with Tailwind's animate-spin class.
    // Zero is expected; one might be a connection-retry indicator which is acceptable.
    const spinCount = await page.locator('.animate-spin').count();
    expect(spinCount).toBeLessThanOrEqual(1);
  });
});
