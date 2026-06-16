import { test, expect } from '@playwright/test';

// Regression suite for gptme/gptme-cloud#420.
//
// Root cause: ConversationList subscribed each sidebar row to the full loaded
// conversation store via Observable.get() — a subscribing read. When a
// conversation was opened its messages landed in the store and every sidebar
// row re-subscribed and re-rendered, creating a hot loop that eventually froze
// the browser.
//
// Fix: use Observable.peek() (non-subscribing) for sidebar-level reads, and
// remove the per-row message-breakdown scan that walked loaded logs on each render.

test.describe('Performance: sidebar hot-loop prevention', () => {
  test('heap does not grow unboundedly when switching back to a loaded conversation', async ({
    page,
    browserName,
  }) => {
    // CDP heap metrics require Chromium
    test.skip(browserName !== 'chromium', 'CDP heap metrics require Chromium');

    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await expect(page.getByText('Introduction to gptme')).toBeVisible({ timeout: 10000 });

    // Open the demo conversation to populate the Observable store with messages
    await page.getByText('Introduction to gptme').click();
    await expect(page.getByText(/Hello! I'm gptme/)).toBeVisible({ timeout: 10000 });

    // Use CDP Performance.getMetrics instead of the removed page.metrics() API
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Performance.enable');
    const baseResult = await cdp.send('Performance.getMetrics');
    const getHeapUsed = (metrics: { name: string; value: number }[]) =>
      metrics.find((m) => m.name === 'JSHeapUsedSize')?.value ?? 0;
    const baseHeap = getHeapUsed(baseResult.metrics);

    // Switch back to the conversation list and re-open 10 times.
    // Pre-fix: each round-trip grew the JS heap substantially because the sidebar
    // re-subscribed every row to the loaded store on each render pass.
    for (let i = 0; i < 10; i++) {
      await page.goto('/');
      await page.waitForLoadState('networkidle');
      await expect(page.getByTestId('conversation-list')).toBeVisible();
      await page.getByText('Introduction to gptme').click();
      await expect(page.getByText(/Hello! I'm gptme/)).toBeVisible({ timeout: 10000 });
    }

    const afterResult = await cdp.send('Performance.getMetrics');
    const afterHeap = getHeapUsed(afterResult.metrics);
    const growthMB = (afterHeap - baseHeap) / (1024 * 1024);

    // 25 MB over 10 round-trips is a generous gate that catches genuine regressions
    // without false positives from normal GC jitter. Pre-fix, each switch added
    // multi-MB of retained subscriptions with no upper bound.
    expect(growthMB).toBeLessThan(25);
  });

  test('conversation list renders quickly after navigating away from a loaded conversation', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await expect(page.getByText('Introduction to gptme')).toBeVisible({ timeout: 10000 });

    // Open a conversation so the store is populated
    await page.getByText('Introduction to gptme').click();
    await expect(page.getByText(/Hello! I'm gptme/)).toBeVisible({ timeout: 10000 });

    // Navigate back to the root and measure how quickly the sidebar becomes visible.
    // Pre-fix: the store subscription triggered cascading re-renders that slowed the sidebar
    // after every switch and became progressively worse as load time accumulated.
    //
    // We measure only the DOM-visible portion (after the browser fires 'load') to isolate
    // React render latency from network/CI variability.
    await page.goto('/');
    const start = Date.now();
    await expect(page.getByTestId('conversation-list')).toBeVisible({ timeout: 5000 });
    const elapsed = Date.now() - start;

    // Gate: sidebar must be visible promptly once the page is loaded.
    // 3 s gives CI runners headroom while still catching a genuine hot-loop regression
    // (pre-fix, this grew linearly with message count and could take tens of seconds).
    expect(elapsed).toBeLessThan(3000);
  });

  test('hovering over conversation list items after loading a conversation does not cause layout thrash', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await expect(page.getByText('Introduction to gptme')).toBeVisible({ timeout: 10000 });

    // Load a conversation so every subsequent sidebar render has a populated store
    await page.getByText('Introduction to gptme').click();
    await expect(page.getByText(/Hello! I'm gptme/)).toBeVisible({ timeout: 10000 });

    // Go back to the list
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    const convList = page.getByTestId('conversation-list');
    await expect(convList).toBeVisible({ timeout: 5000 });

    // Hover repeatedly over the conversation title — this was the direct trigger in prod
    const titleLocator = convList.locator('[data-testid="conversation-title"]').first();
    await expect(titleLocator).toBeVisible({ timeout: 5000 });

    // Time 10 hover cycles; the page must stay responsive throughout
    const start = Date.now();
    for (let i = 0; i < 10; i++) {
      await titleLocator.hover();
    }
    const elapsed = Date.now() - start;

    // 10 hovers should complete in < 2 s total; a hot loop would blow past this
    expect(elapsed).toBeLessThan(2000);
  });
});
