import { test, expect, type Page } from '@playwright/test';

// Browser counterpart to the TUI frame tests (gptme/gptme#4375, #4376).
// Drive the real observable store/rendering/virtualizer with deterministic token
// updates, not a live model. Unlike the static demo/CLS checks, sample every
// animation frame and retain DOM references across streaming and completion.
const conversationId = 'introduction';

async function nextFrames(page: Page, count = 2) {
  await page.evaluate(async (count) => {
    for (let i = 0; i < count; i++) {
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    }
  }, count);
}

async function openTurn(page: Page) {
  await page.goto('/');
  await page.getByText('Introduction to gptme').click();
  await expect(page.locator('[data-message-index]').first()).toBeVisible();
  await page.evaluate(async (id) => {
    const { replaceLog } = await import('/src/stores/conversations.ts');
    replaceLog(id, [
      { role: 'user', content: 'Keep this prompt in place.', timestamp: '2026-01-01T00:00:00Z' },
    ]);
  }, conversationId);
  await expect(page.locator('[data-message-index="0"]')).toContainText('Keep this prompt');
  await nextFrames(page);
}

async function appendMessage(page: Page, role: 'assistant' | 'system', content: string) {
  await page.evaluate(
    async ({ id, role, content }) => {
      const { addMessage } = await import('/src/stores/conversations.ts');
      addMessage(id, {
        role,
        content,
        timestamp: `2026-01-01T00:00:0${role === 'assistant' ? 1 : 2}Z`,
        ...(role === 'assistant' ? { isComplete: false } : {}),
      });
    },
    { id: conversationId, role, content }
  );
  await nextFrames(page);
}

async function streamTokens(page: Page, tokens: string[]) {
  for (const token of tokens) {
    await page.evaluate(
      async ({ id, token }) => {
        const { conversations$ } = await import('/src/stores/conversations.ts');
        const log$ = conversations$.get(id).data.log;
        const message$ = log$[log$.peek().length - 1];
        message$.content.set(message$.content.peek() + token);
      },
      { id: conversationId, token }
    );
    await nextFrames(page);
  }
}

test('streamed reply keeps its DOM when the server supplies the final timestamp', async ({
  page,
}) => {
  await openTurn(page);
  await appendMessage(page, 'assistant', 'A stable reply');
  const reply = page.locator('[data-message-index="1"]');
  await expect(reply).toContainText('A stable reply');
  const original = await reply.elementHandle();
  expect(original).not.toBeNull();

  // onMessageComplete applies the server timestamp, which differs from the
  // local timestamp assigned on the first token, then marks isComplete.
  await page.evaluate(async (id) => {
    const { conversations$ } = await import('/src/stores/conversations.ts');
    const message$ = conversations$.get(id).data.log[1];
    message$.timestamp.set('2026-01-01T00:00:09Z');
    message$.isComplete.set(true);
  }, conversationId);
  await nextFrames(page);
  // The next log append makes the virtualizer recompute its keys. Completion
  // alone may not trigger its parent, hiding a timestamp-key remount until then.
  await appendMessage(page, 'system', 'Turn finished.');
  await expect(reply).toContainText('A stable reply');
  expect(await original!.evaluate((element) => element.isConnected)).toBe(true);

  // Conversely, a branch/edit replacing the log at the same positions must
  // reset row-local state rather than retain the previous branch's parser.
  // Preserve the timestamp so only the log revision can invalidate this row.
  await page.evaluate(async (id) => {
    const { replaceLog } = await import('/src/stores/conversations.ts');
    replaceLog(id, [
      { role: 'user', content: 'A different branch' },
      {
        role: 'assistant',
        content: 'Replacement reply',
        timestamp: '2026-01-01T00:00:09Z',
      },
    ]);
  }, conversationId);
  await expect(reply).toContainText('Replacement reply');
  expect(await original!.evaluate((element) => element.isConnected)).toBe(false);
});

test('multi-step turn has no blank frames or prompt jumps while tokens arrive', async ({
  page,
}, testInfo) => {
  await openTurn(page);
  // A short turn fits in the viewport. Its prompt must never move or disappear;
  // growing content below it is legitimate, shifting completed content is not.
  await page.evaluate(async (id) => {
    const { conversations$ } = await import('/src/stores/conversations.ts');
    const prompt = document.querySelector('[data-message-index="0"]')!;
    const top = prompt.getBoundingClientRect().top;
    const samples: {
      missing: boolean;
      blankReply: boolean;
      jump: number;
      replyIndex: number | null;
    }[] = [];
    let running = true;
    let paintedPrefix = '';
    let lastLength = 0;
    const sample = () => {
      const log = conversations$.get(id).data.log.peek();
      const last = log[log.length - 1];
      if (log.length !== lastLength) {
        paintedPrefix = '';
        lastLength = log.length;
      }
      const reply = document.querySelector(`[data-message-index="${log.length - 1}"]`);
      // Once text has painted, it must remain through later token updates.
      // Latch the visible prefix, not the latest store text: React/the parser
      // may legitimately lag new tokens without losing previously painted text.
      // The first word stays stable as tokens extend the line, and can paint
      // before the parser flushes the trailing word on completion.
      const firstWord = last.content.split(/\s/)[0];
      if (!paintedPrefix && firstWord && reply?.textContent?.includes(firstWord)) {
        paintedPrefix = firstWord;
      }
      samples.push({
        missing: !prompt.isConnected,
        blankReply:
          last.role === 'assistant' &&
          !last.isComplete &&
          paintedPrefix.length > 0 &&
          !reply?.textContent?.includes(paintedPrefix),
        jump: Math.abs(prompt.getBoundingClientRect().top - top),
        replyIndex:
          last.role === 'assistant' && !last.isComplete && paintedPrefix ? log.length - 1 : null,
      });
      if (running) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
    Object.assign(window, {
      turnFrames: {
        samples,
        stop: () => {
          running = false;
        },
      },
    });
  }, conversationId);

  for (const command of ['pwd', 'ls']) {
    // Mount the row empty, then stream: a non-empty initial content finalizes
    // the markdown parser on mount, so later appends would never render.
    await appendMessage(page, 'assistant', '');
    await streamTokens(page, ['Checking.\n\n```shell\n', command, '\n', '```']);
    await page.evaluate(async (id) => {
      const { conversations$ } = await import('/src/stores/conversations.ts');
      const log$ = conversations$.get(id).data.log;
      log$[log$.peek().length - 1].isComplete.set(true);
    }, conversationId);
    await appendMessage(page, 'system', `Ran command: ${command}\n\n\`\`\`stdout\nDone\n\`\`\``);
  }
  // Stream the final answer too: appended all at once it would bypass the
  // per-frame blank check.
  await appendMessage(page, 'assistant', '');
  await streamTokens(page, ['All', ' clean', '.']);
  // Complete the reply so the streaming parser flushes its trailing buffer.
  await page.evaluate(async (id) => {
    const { conversations$ } = await import('/src/stores/conversations.ts');
    const log$ = conversations$.get(id).data.log;
    log$[log$.peek().length - 1].isComplete.set(true);
  }, conversationId);
  await nextFrames(page);
  const finalReply = page.locator('[data-message-index="5"]');
  await expect(finalReply).toContainText('All clean.');
  await expect(page.getByRole('button', { name: /2 steps/ })).toBeVisible();

  const frames = await page.evaluate(() => {
    const state = (
      window as unknown as {
        turnFrames: {
          samples: {
            missing: boolean;
            blankReply: boolean;
            jump: number;
            replyIndex: number | null;
          }[];
          stop: () => void;
        };
      }
    ).turnFrames;
    state.stop();
    return state.samples;
  });
  expect(frames.length).toBeGreaterThan(10);
  // A never-painted reply must not make the blank-frame check pass vacuously.
  expect([
    ...new Set(frames.flatMap((frame) => (frame.replyIndex === null ? [] : [frame.replyIndex]))),
  ]).toEqual([1, 3, 5]);
  expect(frames.filter((frame) => frame.missing || frame.blankReply)).toEqual([]);
  expect(Math.max(...frames.map((frame) => frame.jump))).toBeLessThanOrEqual(1);
  await testInfo.attach('turn-frames', {
    body: JSON.stringify(frames),
    contentType: 'application/json',
  });
  await page.screenshot({ path: testInfo.outputPath('collapsed-turn.png') });

  // Expansion must retain both tool steps in log order and the final reply.
  await page.getByRole('button', { name: /2 steps/ }).click();
  await expect(page.locator('[data-message-index]')).toHaveCount(6);
  await expect(finalReply).toContainText('All clean.');
  // Each expanded row must carry its own text in log order: counting rows
  // alone would pass on duplicated, swapped, or missing tool content.
  const expectedRows: [number, string][] = [
    [0, 'Keep this prompt in place.'],
    [1, 'pwd'],
    [2, 'Ran command: pwd'],
    [3, 'ls'],
    [4, 'Ran command: ls'],
    [5, 'All clean.'],
  ];
  for (const [index, text] of expectedRows) {
    await expect(page.locator(`[data-message-index="${index}"]`)).toContainText(text);
  }
  const renderedOrder = await page
    .locator('[data-message-index]')
    .evaluateAll((rows) => rows.map((row) => row.getAttribute('data-message-index')));
  expect(renderedOrder).toEqual(['0', '1', '2', '3', '4', '5']);
  await page.screenshot({ path: testInfo.outputPath('expanded-turn.png') });
});

// Progressive disclosure must bound outer scroll growth (gptme/gptme#5023).
// The same two-step shell turn is rendered with tiny and with huge synthetic
// outputs; hidden payload must not inflate the default or group-expanded view,
// and opening one output must grow the conversation by a bounded pane, not by
// its whole payload. Direct geometry on the real scroll viewport decides
// pass/fail, sampled on every animation frame through each click.
type DisclosureMetrics = {
  scrollHeight: number;
  clientHeight: number;
  scrollTop: number;
  virtualExtent: number;
  docOverflowX: number;
  openOutputs: number;
  outputBlocks: number;
};

type DisclosureStage = DisclosureMetrics & { stage: string; peakScrollHeight: number };

const LARGE_OUTPUT_LINES = 4000;

function syntheticOutput(size: 'short' | 'large'): string {
  // Three lines: outputs of ≤2 lines render inline rather than as a block.
  if (size === 'short') return 'README.md\nsrc/main.py\nDone';
  const lines = Array.from(
    { length: LARGE_OUTPUT_LINES },
    (_, i) => `line ${i}: ${'lorem ipsum '.repeat(4)}`
  );
  // A long unbroken line must not widen the document at narrow widths.
  lines.splice(10, 0, 'x'.repeat(5000));
  return lines.join('\n');
}

async function renderDisclosureTurn(page: Page, size: 'short' | 'large') {
  const output = syntheticOutput(size);
  await page.evaluate(
    async ({ id, output }) => {
      const { replaceLog } = await import('/src/stores/conversations.ts');
      const step = (command: string, n: number) => [
        {
          role: 'assistant' as const,
          content: `Let me look around.\n\n\`\`\`shell\n${command}\n\`\`\``,
          timestamp: `2026-01-01T00:00:0${n}Z`,
        },
        {
          role: 'system' as const,
          content: `Ran command: \`${command}\`\n\n\`\`\`stdout\n${output}\n\`\`\``,
          timestamp: `2026-01-01T00:00:0${n + 1}Z`,
        },
      ];
      replaceLog(id, [
        { role: 'user', content: 'Inspect the repo.', timestamp: '2026-01-01T00:00:00Z' },
        ...step('find . -type f', 1),
        ...step('cat README.md', 3),
        {
          role: 'assistant',
          content: 'Both commands finished; the repository looks healthy.',
          timestamp: '2026-01-01T00:00:05Z',
        },
      ]);
    },
    { id: conversationId, output }
  );
  await expect(page.locator('[data-message-index="5"]')).toContainText('looks healthy');
  await expect(page.getByRole('button', { name: /2 steps/ })).toBeVisible();
  await settle(page);
}

/** Wait until the viewport's scroll extent is unchanged for several frames. */
async function settle(page: Page) {
  await page.evaluate(async () => {
    const viewport = document.querySelector('[data-testid="message-scroll-viewport"]')!;
    let last = -1;
    let stable = 0;
    for (let i = 0; i < 120 && stable < 6; i++) {
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      stable = viewport.scrollHeight === last ? stable + 1 : 0;
      last = viewport.scrollHeight;
    }
  });
}

async function readDisclosureMetrics(page: Page): Promise<DisclosureMetrics> {
  return page.evaluate(() => {
    const viewport = document.querySelector<HTMLElement>(
      '[data-testid="message-scroll-viewport"]'
    )!;
    const extent = viewport.querySelector<HTMLElement>(':scope > div[style*="position: relative"]');
    const outputs = [...viewport.querySelectorAll<HTMLDetailsElement>('.chat-message details')];
    return {
      scrollHeight: viewport.scrollHeight,
      clientHeight: viewport.clientHeight,
      scrollTop: viewport.scrollTop,
      virtualExtent: extent?.getBoundingClientRect().height ?? -1,
      docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
      openOutputs: outputs.filter((details) => details.open).length,
      outputBlocks: outputs.length,
    };
  });
}

/**
 * Run one disclosure click while sampling scrollHeight every frame, then wait
 * for the virtualizer to settle. The peak catches a transient blow-up that a
 * settled-state assertion would miss.
 */
async function disclosureStep(
  page: Page,
  stage: string,
  action: () => Promise<void>
): Promise<DisclosureStage> {
  await page.evaluate(() => {
    const viewport = document.querySelector('[data-testid="message-scroll-viewport"]')!;
    const state = { peak: viewport.scrollHeight, running: true, samples: 0 };
    const sample = () => {
      state.peak = Math.max(state.peak, viewport.scrollHeight);
      state.samples++;
      if (state.running) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
    Object.assign(window, { disclosurePeak: state });
  });
  await action();
  // Park the pointer: hovering a message legitimately reveals its action bar,
  // which would otherwise be misread as disclosure growth.
  await page.mouse.move(0, 0);
  await settle(page);
  const { peak, samples } = await page.evaluate(() => {
    const state = (
      window as unknown as { disclosurePeak: { peak: number; running: boolean; samples: number } }
    ).disclosurePeak;
    state.running = false;
    return state;
  });
  expect(samples, `${stage}: frame sampler must observe the transition`).toBeGreaterThan(3);
  return { stage, ...(await readDisclosureMetrics(page)), peakScrollHeight: peak };
}

/** Click through default → group → one output → collapse output → collapse group. */
async function runDisclosureSequence(page: Page): Promise<DisclosureStage[]> {
  const stages: DisclosureStage[] = [];
  stages.push(await disclosureStep(page, 'default', async () => {}));
  const group = page.getByRole('button', { name: /2 steps/ });
  stages.push(await disclosureStep(page, 'group-expanded', () => group.click()));
  await expect(page.locator('[data-message-index]')).toHaveCount(6);
  // Open the first command's output (message 2), keep the reading anchor.
  const summary = page.locator('[data-message-index="2"] details > summary').first();
  await summary.scrollIntoViewIfNeeded();
  const anchorBefore = (await summary.boundingBox())!.y;
  stages.push(await disclosureStep(page, 'output-open', () => summary.click()));
  const anchorAfter = (await summary.boundingBox())!.y;
  expect(Math.abs(anchorAfter - anchorBefore), 'opened summary must stay put').toBeLessThanOrEqual(
    2
  );
  stages.push(await disclosureStep(page, 'output-closed', () => summary.click()));
  stages.push(await disclosureStep(page, 'group-collapsed', () => group.click()));
  return stages;
}

/**
 * The scroll-growth oracle. Budgets are fixture-specific and declared up front:
 * - hidden payload may add at most HIDDEN_SLACK px versus the tiny-output turn
 *   in the default and group-expanded views (summaries, not payload);
 * - one opened output may grow the viewport by at most one viewport height,
 *   even though the large payload is ~LARGE_OUTPUT_LINES lines;
 * - opening one output never opens its siblings;
 * - collapsing restores the compact height;
 * - nothing widens the document horizontally.
 */
const HIDDEN_SLACK = 4;
function scrollBudgetViolations(short: DisclosureStage[], large: DisclosureStage[]): string[] {
  const violations: string[] = [];
  const at = (stages: DisclosureStage[], stage: string) => stages.find((s) => s.stage === stage)!;
  for (const stage of ['default', 'group-expanded', 'group-collapsed']) {
    const growth = at(large, stage).peakScrollHeight - at(short, stage).peakScrollHeight;
    if (growth > HIDDEN_SLACK) {
      violations.push(`${stage}: hidden payload inflated scroll extent by ${growth}px`);
    }
  }
  for (const [label, stages] of [
    ['short', short],
    ['large', large],
  ] as const) {
    const grouped = at(stages, 'group-expanded');
    const open = at(stages, 'output-open');
    const growth = Math.max(open.scrollHeight, open.peakScrollHeight) - grouped.scrollHeight;
    if (growth > open.clientHeight) {
      violations.push(
        `${label} output-open: grew ${growth}px, budget ${open.clientHeight}px (one viewport)`
      );
    }
    if (open.openOutputs !== grouped.openOutputs + 1) {
      violations.push(
        `${label} output-open: ${open.openOutputs} open outputs, expected ${grouped.openOutputs + 1}`
      );
    }
    for (const [restored, before] of [
      ['output-closed', 'group-expanded'],
      ['group-collapsed', 'default'],
    ] as const) {
      const delta = Math.abs(at(stages, restored).scrollHeight - at(stages, before).scrollHeight);
      if (delta > HIDDEN_SLACK) {
        violations.push(`${label} ${restored}: did not restore ${before} height (Δ${delta}px)`);
      }
    }
    for (const stage of stages) {
      if (stage.docOverflowX > 1) {
        violations.push(`${label} ${stage.stage}: document overflows by ${stage.docOverflowX}px`);
      }
      // The virtualizer's content extent must exist and fit the scroll extent.
      if (stage.virtualExtent <= 0 || stage.virtualExtent > stage.scrollHeight) {
        violations.push(`${label} ${stage.stage}: virtualizer extent ${stage.virtualExtent}px`);
      }
    }
  }
  return violations;
}

for (const viewport of [
  { name: 'desktop', width: 1280, height: 800 },
  { name: 'narrow', width: 390, height: 844 },
]) {
  test(`opening tool output keeps scroll growth bounded (${viewport.name})`, async ({
    page,
  }, testInfo) => {
    // Open via the desktop sidebar, then resize: the narrow layout hides it.
    await openTurn(page);
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await renderDisclosureTurn(page, 'short');
    const short = await runDisclosureSequence(page);
    await renderDisclosureTurn(page, 'large');
    const large = await runDisclosureSequence(page);
    await testInfo.attach('disclosure-metrics', {
      body: JSON.stringify({ outputLines: LARGE_OUTPUT_LINES, short, large }, null, 2),
      contentType: 'application/json',
    });
    // The expanded group must actually hide a large payload behind closed blocks.
    expect(large[1]).toMatchObject({ stage: 'group-expanded', openOutputs: 0 });
    expect(large[1].outputBlocks).toBeGreaterThanOrEqual(2);
    expect(scrollBudgetViolations(short, large)).toEqual([]);

    // The full output stays reachable inside the bounded pane.
    const group = page.getByRole('button', { name: /2 steps/ });
    await group.click();
    const summary = page.locator('[data-message-index="2"] details > summary').first();
    await summary.click();
    const pane = page.locator('[data-message-index="2"] details[open] pre').first();
    const pane$ = await pane.evaluate(
      (pre, lastLine) => ({
        scrollable: pre.scrollHeight > pre.clientHeight,
        overflowY: getComputedStyle(pre).overflowY,
        hasLastLine: pre.textContent!.includes(lastLine),
      }),
      `line ${LARGE_OUTPUT_LINES - 1}:`
    );
    expect(pane$).toEqual({ scrollable: true, overflowY: 'auto', hasLastLine: true });
    await page.screenshot({ path: testInfo.outputPath(`output-open-${viewport.name}.png`) });

    // Negative control: unbound the pane (as before #5023) and auto-expand
    // every output. The same oracle must report both regressions.
    await summary.click();
    await group.click();
    await settle(page);
    await page.addStyleTag({
      content: '.chat-message details.tool-block pre { max-height: none !important; }',
    });
    const unbounded = await runDisclosureSequence(page);
    const unboundedViolations = scrollBudgetViolations(short, unbounded);
    expect(unboundedViolations.some((v) => v.startsWith('large output-open: grew'))).toBe(true);

    await page.getByRole('button', { name: /2 steps/ }).click();
    await settle(page);
    const autoExpanded = await disclosureStep(page, 'group-expanded', () =>
      page.evaluate(() => {
        document
          .querySelectorAll<HTMLDetailsElement>('.chat-message details')
          .forEach((details) => (details.open = true));
      })
    );
    const autoExpandedRun = large.map((stage) =>
      stage.stage === 'group-expanded' ? autoExpanded : stage
    );
    expect(
      scrollBudgetViolations(short, autoExpandedRun).some((v) =>
        v.startsWith('group-expanded: hidden payload inflated')
      )
    ).toBe(true);
    await testInfo.attach('negative-controls', {
      body: JSON.stringify({ unbounded, unboundedViolations, autoExpanded }, null, 2),
      contentType: 'application/json',
    });
  });
}

test('rendered result preview stays inside a bounded pane', async ({ page }, testInfo) => {
  // `result` blocks render through TabbedCodeBlock; its Markdown Preview tab is
  // a div, not a pre, so it needs the same bound as raw output.
  await openTurn(page);
  const items = Array.from({ length: 2000 }, (_, i) => `- finding ${i}: all good`).join('\n');
  await page.evaluate(
    async ({ id, items }) => {
      const { replaceLog } = await import('/src/stores/conversations.ts');
      replaceLog(id, [
        { role: 'user', content: 'Summarize.', timestamp: '2026-01-01T00:00:00Z' },
        {
          role: 'system',
          content: `Report:\n\n\`\`\`result\n# Report\n\n${items}\n\`\`\``,
          timestamp: '2026-01-01T00:00:01Z',
        },
      ]);
    },
    { id: conversationId, items }
  );
  await page.getByRole('button', { name: /1 step/ }).click();
  const summary = page.locator('[data-message-index="1"] details > summary').first();
  await expect(summary).toBeVisible();
  await settle(page);
  const closed = await readDisclosureMetrics(page);
  const opened = await disclosureStep(page, 'output-open', () => summary.click());
  const previewTab = page.locator('[data-message-index="1"]').getByRole('button', {
    name: /Preview/,
  });
  const preview = await disclosureStep(page, 'preview', () => previewTab.click());
  const pane = page.locator('[data-message-index="1"] .preview-content');
  await expect(pane).toContainText('finding 1999');
  await testInfo.attach('result-preview-metrics', {
    body: JSON.stringify({ closed, opened, preview }, null, 2),
    contentType: 'application/json',
  });
  for (const stage of [opened, preview]) {
    expect(
      Math.max(stage.scrollHeight, stage.peakScrollHeight) - closed.scrollHeight,
      `${stage.stage} growth`
    ).toBeLessThanOrEqual(stage.clientHeight);
  }
  expect(await pane.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
});
