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
    test.setTimeout(60000);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Introduction to gptme')).toBeVisible({ timeout: 10000 });

    // Open the demo conversation to populate the Observable store with messages
    await page.getByText('Introduction to gptme').click();
    // With virtualization only viewport messages are in the DOM; check any message rendered.
    await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });

    // Use CDP Performance.getMetrics instead of the removed page.metrics() API
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Performance.enable');
    const getHeapUsed = (metrics: { name: string; value: number }[]) =>
      metrics.find((m) => m.name === 'JSHeapUsedSize')?.value ?? 0;
    const sampleHeapUsed = async () => {
      await cdp.send('HeapProfiler.collectGarbage');
      const result = await cdp.send('Performance.getMetrics');
      return getHeapUsed(result.metrics);
    };
    const baseHeap = await sampleHeapUsed();

    // Switch back to the conversation list and re-open 10 times.
    // Pre-fix: each round-trip grew the JS heap substantially because the sidebar
    // re-subscribed every row to the loaded store on each render pass.
    for (let i = 0; i < 10; i++) {
      await page.goto('/', { waitUntil: 'domcontentloaded' });
      await expect(page.getByTestId('conversation-list')).toBeVisible();
      await page.getByText('Introduction to gptme').click();
      await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });
    }

    const afterHeap = await sampleHeapUsed();
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

test.describe('Performance: streaming code block rendering', () => {
  // Regression suite for gptme/gptme#3362 (second hot path).
  //
  // Root cause: markdownRenderer.ts `add_text` rebuilt data.code.innerHTML from
  // the full accumulated text on every streaming token — O(N) work per token,
  // O(N²) total. After the fix, streamed fragments update one text node (O(1))
  // and innerHTML is written exactly once when syntax highlighting runs.

  test('streamed code tokens keep bounded DOM writes and one text node', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    const result = await page.evaluate(async () => {
      const [{ customRenderer }, smd] = await Promise.all([
        import('/src/utils/markdownRenderer.ts'),
        import('/src/utils/smd.js'),
      ]);
      const root = document.createElement('div');
      document.body.appendChild(root);
      const parser = smd.parser(customRenderer(root));

      let codeInnerHTMLWrites = 0;
      const innerHTMLDescriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML')!;
      Object.defineProperty(Element.prototype, 'innerHTML', {
        configurable: true,
        get() {
          return innerHTMLDescriptor.get!.call(this);
        },
        set(value: string) {
          if (this instanceof HTMLElement && this.tagName === 'CODE') {
            codeInnerHTMLWrites++;
          }
          innerHTMLDescriptor.set!.call(this, value);
        },
      });

      try {
        const body = 'const value = "<safe> & fast";\n'.repeat(100);
        for (const char of `\`\`\`typescript\n${body}`) {
          smd.parser_write(parser, char);
        }

        const streamingCode = root.querySelector('code')!;
        const duringStream = {
          childNodes: streamingCode.childNodes.length,
          innerHTMLWrites: codeInnerHTMLWrites,
          text: streamingCode.textContent,
        };

        for (const char of '```\n') {
          smd.parser_write(parser, char);
        }
        smd.parser_end(parser);

        return {
          duringStream,
          finalInnerHTMLWrites: codeInnerHTMLWrites,
          finalText: root.querySelector('code')?.textContent,
        };
      } finally {
        Object.defineProperty(Element.prototype, 'innerHTML', innerHTMLDescriptor);
        root.remove();
      }
    });

    expect(result.duringStream.childNodes).toBe(1);
    expect(result.duringStream.innerHTMLWrites).toBe(0);
    expect(result.duringStream.text).toContain('<safe> & fast');
    expect(result.finalInnerHTMLWrites).toBeLessThanOrEqual(1);
    expect(result.finalText).toContain('<safe> & fast');
  });
});

test.describe('Performance: message list virtualization', () => {
  // Regression suite for gptme/gptme#3379.
  //
  // Root cause: before virtualization, every message in a conversation was
  // mounted in the DOM simultaneously. A 200-message conversation rendered
  // 200 ChatMessage components, creating tens of thousands of DOM nodes and
  // causing multi-second layout jank.
  //
  // Fix: ConversationContent uses @tanstack/react-virtual to render only the
  // messages visible in the scroll viewport (plus overscan), keeping DOM node
  // count bounded regardless of total conversation length.
  //
  // These tests guard against regressions that would re-mount all messages.

  test('mounts only a bounded subset of DOM nodes for a 200-message conversation', async ({
    page,
    browserName,
  }) => {
    test.skip(browserName !== 'chromium', 'Virtualization behavior is browser-independent');
    test.setTimeout(30000);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Stress test (200 messages)')).toBeVisible({ timeout: 10000 });

    await page.getByText('Stress test (200 messages)').click();
    await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });

    // Count rendered message rows. With virtualization only ~viewport + overscan
    // rows are in the DOM. A non-virtualized list would mount all 200.
    const renderedRows = await page.locator('[data-message-index]').count();

    // The virtualizer renders viewport items + overscan (5). On a standard
    // CI viewport (1280x720) this is well under 30. Use 50 as a generous
    // ceiling that still catches a full 200-message regression.
    expect(renderedRows).toBeLessThan(50);
    expect(renderedRows).toBeLessThan(200);
  });

  test('scrolling reveals new messages without mounting all at once', async ({
    page,
    browserName,
  }) => {
    test.skip(browserName !== 'chromium', 'Virtualization behavior is browser-independent');
    test.setTimeout(30000);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Stress test (200 messages)')).toBeVisible({ timeout: 10000 });

    await page.getByText('Stress test (200 messages)').click();
    await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });

    // Conversations open at the bottom. Scroll the actual message viewport to
    // the top rather than sending a wheel event to the sidebar item we clicked.
    const messageViewport = page.getByTestId('message-scroll-viewport');
    const initialIndices = await page
      .locator('[data-message-index]')
      .evaluateAll((els) => els.map((el) => Number(el.getAttribute('data-message-index'))));
    await messageViewport.evaluate((element) => element.scrollTo({ top: 0 }));

    // Wait for the virtualizer to replace the bottom rows with top rows.
    await expect
      .poll(async () =>
        page
          .locator('[data-message-index]')
          .evaluateAll((els) => els.map((el) => Number(el.getAttribute('data-message-index'))))
      )
      .not.toEqual(initialIndices);

    const scrolledIndices = await page
      .locator('[data-message-index]')
      .evaluateAll((els) => els.map((el) => Number(el.getAttribute('data-message-index'))));

    // The top of the conversation is now rendered, while the DOM stays bounded.
    expect(scrolledIndices).toContain(0);
    expect(scrolledIndices.length).toBeLessThan(50);
  });

  test('total DOM node count stays bounded for a long conversation', async ({
    page,
    browserName,
  }) => {
    test.skip(browserName !== 'chromium', 'DOM metrics require Chromium');
    test.setTimeout(30000);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Stress test (200 messages)')).toBeVisible({ timeout: 10000 });

    await page.getByText('Stress test (200 messages)').click();
    await expect(page.locator('[data-message-index]').first()).toBeVisible({ timeout: 10000 });

    // Count total DOM elements. Pre-virtualization, 200 messages with markdown
    // rendering produced 8000+ nodes. With virtualization it should be well
    // under 2000 even with the sidebar and chrome.
    const totalElements = await page.evaluate(() => document.querySelectorAll('*').length);

    // 3000 is a generous ceiling that still catches a non-virtualized regression
    // (which would produce 8000+ nodes for 200 rendered messages).
    expect(totalElements).toBeLessThan(3000);
  });
});
