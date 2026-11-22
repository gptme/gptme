import { writeFileSync } from 'node:fs';
import { test, expect, type BrowserContext, type Page } from '@playwright/test';
import {
  injectFault,
  installFrameOracle,
  markChunk,
  startRrweb,
  stopFrameOracle,
  stopRrweb,
  type Fault,
  type OracleResult,
} from './helpers/streamFrameOracle';
import { inkPixels, textClip } from './helpers/pixelProbe';
import {
  markRecording,
  mutationOffsets,
  openReplayer,
  replayMarkers,
  startRecording,
  stopRecording,
  type ReplayMarker,
  type RrwebEvent,
} from './helpers/rrwebReplay';

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

async function openTurn(page: Page, demo = false) {
  await page.goto(demo ? '/?demo=1' : '/');
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
  await appendMessage(page, 'assistant', 'A stable reply\n');
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
  // A marker in the rrweb timeline (if recording) at the settled state.
  await markRecording(page, 'stage', { stage });
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
    // The offline client is connected synchronously, so this exercises the
    // demo reload on remount even when no live backend is available.
    // Open via the desktop sidebar, then resize: the narrow layout hides it.
    await openTurn(page, true);
    const previousViewport = await page
      .locator('[data-testid="message-scroll-viewport"]')
      .elementHandle();
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.width < 768) {
      // Crossing MainLayout's breakpoint remounts the conversation. Wait for
      // both the old DOM to detach and useConversation to restore the demo
      // log before injecting once; a visible row alone can still be the old tree.
      await expect.poll(() => previousViewport!.evaluate((el) => el.isConnected)).toBe(false);
      await expect
        .poll(() =>
          page.evaluate(async (id) => {
            const { conversations$ } = await import('/src/stores/conversations.ts');
            const { getDemoMessages } = await import('/src/democonversations.ts');
            return (
              JSON.stringify(conversations$.get(id).data.log.peek()) ===
              JSON.stringify(getDemoMessages(id))
            );
          }, conversationId)
        )
        .toBe(true);
    }
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
    const unboundedStyle = await page.addStyleTag({
      content: '.chat-message details.tool-block pre { max-height: none !important; }',
    });
    const unbounded = await runDisclosureSequence(page);
    const unboundedViolations = scrollBudgetViolations(short, unbounded);
    expect(unboundedViolations.some((v) => v.startsWith('large output-open: grew'))).toBe(true);
    await unboundedStyle.evaluate((el) => el.remove());
    await settle(page);

    await page.getByRole('button', { name: /2 steps/ }).click();
    await settle(page);
    const autoExpanded = await disclosureStep(page, 'group-expanded', () =>
      page.evaluate(() => {
        document
          .querySelectorAll<HTMLDetailsElement>('.chat-message details')
          .forEach((details) => (details.open = true));
      })
    );
    // This control must exercise force-open with the production pane bound,
    // independently of the unbounded-output control above.
    const expandedPane = page.locator('[data-message-index="2"] details[open] pre').first();
    expect(await expandedPane.evaluate((el) => getComputedStyle(el).maxHeight)).not.toBe('none');
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
  // Pin the selector path as well as geometry: result previews must inherit
  // the tool-output bound, not happen to scroll for an unrelated reason.
  expect(await pane.evaluate((el) => el.closest('details')?.classList.contains('tool-block'))).toBe(
    true
  );
  expect(await pane.evaluate((el) => getComputedStyle(el).overflowY)).toBe('auto');
  expect(await pane.evaluate((el) => getComputedStyle(el).maxHeight)).not.toBe('none');
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

test('native tool payload streams collapsed without losing the preceding reply tail', async ({
  page,
}, testInfo) => {
  await page.goto('/?demo=1');
  await page.getByText('Introduction to gptme').click();
  await page.evaluate(async (id) => {
    const { replaceLog } = await import('/src/stores/conversations.ts');
    replaceLog(id, [
      { role: 'user', content: 'Inspect this.' },
      {
        role: 'assistant',
        content: 'The complete reply tail.\n@shell(call_1): {"command":"echo',
        isComplete: false,
      },
    ]);
  }, conversationId);
  const message = page.locator('[data-message-index="1"]');
  await expect(message).toContainText('The complete reply tail.');
  await expect(message).not.toContainText('@shell(call_1)');
  const details = message.locator('details');
  await expect(details).toHaveCount(1);
  await expect(details).not.toHaveAttribute('open', '');
  await page.evaluate(async (id) => {
    const { conversations$ } = await import('/src/stores/conversations.ts');
    conversations$
      .get(id)
      .data.log[1].content.set(
        'The complete reply tail.\n@shell(call_1): {"command":"echo hello"}'
      );
    conversations$.get(id).data.log[1].isComplete.set(true);
  }, conversationId);
  await expect(details).toHaveCount(1);
  await expect(details).not.toHaveAttribute('open', '');
  await details.locator('summary').click();
  await expect(details).toContainText('echo hello');
  await expect(message).toContainText('The complete reply tail.');
  await page.screenshot({ path: testInfo.outputPath('native-tool-stream.png') });
});

// A wider synthetic fence can rebuild the parser while a save payload streams.
// Check painted frames, not DOM identity: a synchronous rebuild need not flash.
test('backticks in a streamed native save preserve the painted reply on every frame', async ({
  page,
}, testInfo) => {
  await openTurn(page);
  const tail = 'The full reply tail stays visible.';
  await appendMessage(page, 'assistant', `${tail}\n\n`);
  const message = page.locator('[data-message-index="1"]');
  await expect(message).toContainText(tail);
  await page.evaluate((tail) => {
    const samples: { tailVisible: boolean; payloadVisible: boolean }[] = [];
    const state = { running: true, samples };
    const sample = () => {
      const message = document.querySelector<HTMLElement>('[data-message-index="1"]');
      const text = message?.innerText ?? '';
      samples.push({
        tailVisible: text.includes(tail),
        payloadVisible: text.includes('PAYLOAD_SENTINEL') || text.includes('@save(call_1)'),
      });
      if (state.running) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
    Object.assign(window, { nativeSaveFrames: state });
  }, tail);
  await streamTokens(page, [
    '@save(call_1): {"path":"/tmp/example.md","content":"PAYLOAD_SENTINEL ',
    '`',
    '`',
    '`',
    'python\\nx = 1\\n',
    '`',
    '`',
    '`',
    '"}',
  ]);
  await page.evaluate(async (id) => {
    const { conversations$ } = await import('/src/stores/conversations.ts');
    conversations$.get(id).data.log[1].isComplete.set(true);
  }, conversationId);
  await nextFrames(page);
  const frames = await page.evaluate(() => {
    const state = (
      window as unknown as {
        nativeSaveFrames: {
          running: boolean;
          samples: { tailVisible: boolean; payloadVisible: boolean }[];
        };
      }
    ).nativeSaveFrames;
    state.running = false;
    return state.samples;
  });
  expect(frames.length).toBeGreaterThan(15);
  expect(frames.filter((frame) => !frame.tailVisible || frame.payloadVisible)).toEqual([]);
  const details = message.locator('details');
  await expect(details).toHaveCount(1);
  await expect(details).not.toHaveAttribute('open', '');
  await details.locator('summary').click();
  await expect(details).toContainText('PAYLOAD_SENTINEL ```python');
  await expect(message).toContainText(tail);
  await testInfo.attach('native-save-frames', {
    body: JSON.stringify(frames),
    contentType: 'application/json',
  });
  await page.screenshot({ path: testInfo.outputPath('native-save-backticks.png') });
});

// --- Boundary-split native tool streaming (gptme/gptme#5023) -----------------
//
// Stream a native call into a reply, chunk by chunk, while an in-page frame
// oracle samples every animation frame. A raw header or argument marker must
// never be visible in the collapsed message. The prose tail must stay visible
// and unclipped once it has painted. A literal fenced example of native syntax
// must stay visible, so an implementation that hides all `@...` text fails.

interface NativeFixture {
  name: string;
  tool: string;
  callId: string;
  json: string;
  /** Argument marker that only the raw transcript would show. */
  argMarker: string;
}

const NATIVE_FIXTURES: NativeFixture[] = [
  {
    name: 'shell',
    tool: 'shell',
    callId: 'call_9fQx2LmZ7rT4vW8yB1nC6kD3hJ5sA0eG',
    // Escaped quotes and backslashes inside JSON strings.
    json: '{"command":"printf \'%s\\\\n\' \\"a \\\\\\"quoted\\\\\\" word\\" && echo done"}',
    argMarker: '{"command"',
  },
  {
    name: 'mcp',
    tool: 'goldfish.search_memory',
    callId: 'toolu_01HzVq8PkW3mN5bR7tY2cX9dLfJ4gA6s',
    json: '{"query":"release notes","limit":5,"filter":{"tag":"a \\"b\\" c"}}',
    argMarker: '{"query"',
  },
];

const NATIVE_TAIL = 'I will check the release notes before answering, then summarize.';
// Different tool than either fixture: it must stay visible as literal text.
const NATIVE_LITERAL = '@save(call_example): {"path":"example.txt"}';
const NATIVE_PROSE = `Here is the syntax, for reference:\n\n\`\`\`text\n${NATIVE_LITERAL}\n\`\`\`\n\n${NATIVE_TAIL}\n`;

function nativeCall(fixture: NativeFixture): string {
  return `@${fixture.tool}(${fixture.callId}):  ${fixture.json}`;
}

/** Phase of the streamed transcript after `length` characters have arrived. */
function nativePhase(full: string, length: number): string {
  const header = full.indexOf('\n@', NATIVE_PROSE.length - 1) + 1;
  const brace = full.indexOf('{', header);
  if (length <= header) return 'prose';
  if (length <= brace) return 'header';
  if (length < full.length) return 'payload';
  return 'call-complete';
}

/** Chunk boundaries: fixed-size chunks, or deliberately chosen split points. */
function nativeSplits(fixture: NativeFixture, mode: '20-char' | 'boundary'): number[] {
  const full = NATIVE_PROSE + nativeCall(fixture);
  if (mode === '20-char') {
    const splits = [];
    for (let i = 20; i < full.length; i += 20) splits.push(i);
    return [...splits, full.length];
  }
  const at = (needle: string, offset = 0) => full.indexOf(needle, NATIVE_PROSE.length) + offset;
  const header = NATIVE_PROSE.length;
  const open = at('(');
  const close = at('):');
  const brace = at('{');
  const escape = full.indexOf('\\"', brace);
  const points = [
    header, // after prose, before '@'
    header + 1, // '@' alone
    header + 3, // inside the tool name
    header + Math.min(fixture.tool.length, 9), // later inside the name (or '.' for MCP)
    open, // name complete, '(' pending
    open + 1,
    open + 8, // inside the long call ID
    open + 20,
    close, // ID complete, '):' pending
    close + 1, // between ')' and ':'
    close + 2, // ':' arrived, whitespace pending
    close + 3, // inside whitespace before '{'
    brace, // whitespace complete, '{' pending
    brace + 1,
    brace + 4, // inside the first key
    escape, // just before an escape sequence
    escape + 1, // between '\' and '"'
    full.length - 1, // before the final '}'
    full.length,
  ];
  return [...new Set(points)].sort((a, b) => a - b);
}

async function streamNativeFixture(
  page: Page,
  fixture: NativeFixture,
  start: 'empty' | 'mounted',
  mode: '20-char' | 'boundary',
  fault?: { kind: Fault; atPhase: string },
  hideUntilComplete = false
) {
  const hidden = hideUntilComplete
    ? await page.addStyleTag({ content: '.chat-message { visibility: hidden !important; }' })
    : null;
  const full = NATIVE_PROSE + nativeCall(fixture);
  const splits = nativeSplits(fixture, mode);
  // 'mounted' mounts the message mid-stream: prose plus a partial header.
  const mountedAt = start === 'mounted' ? NATIVE_PROSE.length + 4 : 0;
  await page.evaluate(
    async ({ id, content }) => {
      const { replaceLog, setConnected, updateConversation } =
        await import('/src/stores/conversations.ts');
      const { getPrimaryClient } = await import('/src/stores/serverClients.ts');
      const { getClientGeneration } = await import('/src/utils/api.ts');
      // The responsive layout remounts useConversation. Give this synthetic
      // fixture a settled subscription so its demo loader cannot reset the log.
      updateConversation(id, { streamClientGeneration: getClientGeneration(getPrimaryClient()) });
      setConnected(id, true);
      replaceLog(id, [
        { role: 'user', content: 'Check the notes.', timestamp: '2026-01-01T00:00:00Z' },
        { role: 'assistant', content, isComplete: false, timestamp: '2026-01-01T00:00:01Z' },
      ]);
    },
    { id: conversationId, content: full.slice(0, mountedAt) }
  );
  await nextFrames(page);
  await installFrameOracle(page, {
    messageSelector: '[data-message-index="1"]',
    tail: NATIVE_TAIL,
    literal: NATIVE_LITERAL,
    leakMarkers: [fixture.callId.slice(-10), fixture.argMarker],
    leakPattern: `@${fixture.tool.slice(0, 2).replace(/[.]/g, '\\.')}`,
  });
  await markChunk(page, -1, mountedAt ? nativePhase(full, mountedAt) : 'empty');
  await nextFrames(page, 3);
  let previous = mountedAt;
  let chunk = 0;
  let faulted = false;
  for (const split of splits) {
    if (split <= previous) continue;
    const phase = nativePhase(full, split);
    await markChunk(page, chunk++, phase);
    await streamTokens(page, [full.slice(previous, split)]);
    previous = split;
    if (fault && !faulted && phase === fault.atPhase) {
      await injectFault(page, fault.kind);
      await nextFrames(page, 3);
      faulted = true;
    }
  }
  // Negative control: revealing only at completion must not satisfy streaming coverage.
  await hidden?.evaluate((element) => element.remove());
  // Completion: the server finalizes the message and supplies its timestamp.
  await markChunk(page, chunk, 'complete');
  await page.evaluate(async (id) => {
    const { conversations$ } = await import('/src/stores/conversations.ts');
    const message$ = conversations$.get(id).data.log[1];
    message$.timestamp.set('2026-01-01T00:00:09Z');
    message$.isComplete.set(true);
  }, conversationId);
  await nextFrames(page, 4);
  return stopFrameOracle(page);
}

function nativeVisibilityViolations(result: OracleResult, start: 'empty' | 'mounted'): string[] {
  const violations: string[] = [];
  const visible = (sample: OracleResult['samples'][number]) =>
    sample.tailVisible && sample.tailClip === null && sample.literalVisible === true;
  // Do not let completion-only rendering pass the once-painted latches. Require
  // positive evidence while the native header/payload is still streaming.
  for (const phase of ['header', 'payload', 'call-complete']) {
    if (!result.samples.some((sample) => sample.phase === phase && visible(sample))) {
      violations.push(`${phase}: tail and literal never visible together while streaming`);
    }
  }
  // Mounted fixtures already contain the full prose before the first update.
  if (start === 'mounted' && !result.samples.some((s) => s.chunk === -1 && visible(s))) {
    violations.push('mounted: initial tail and literal never visible before first update');
  }
  return violations;
}

test('native stream coverage rejects prose hidden until completion', async ({ page }) => {
  await openTurn(page, true);
  for (const start of ['empty', 'mounted'] as const) {
    const result = await streamNativeFixture(
      page,
      NATIVE_FIXTURES[0],
      start,
      'boundary',
      undefined,
      true
    );
    // The final DOM is correct, and the latch-only oracle misses the regression.
    await expect(page.locator('[data-message-index="1"]')).toContainText(NATIVE_TAIL);
    expect(result.failures).toEqual([]);
    const violations = nativeVisibilityViolations(result, start);
    expect(violations).toContain('header: tail and literal never visible together while streaming');
    expect(violations).toContain(
      'payload: tail and literal never visible together while streaming'
    );
    if (start === 'mounted') {
      expect(violations).toContain(
        'mounted: initial tail and literal never visible before first update'
      );
    }
  }
});

function describeFailures(result: OracleResult): string {
  return JSON.stringify(result.failures.slice(0, 5), null, 1);
}

for (const viewport of [
  { name: 'desktop', width: 1280, height: 800 },
  { name: 'narrow', width: 390, height: 844 },
]) {
  for (const fixture of NATIVE_FIXTURES) {
    test(`native ${fixture.name} call streams without raw-header flashes or tail loss (${viewport.name})`, async ({
      page,
    }, testInfo) => {
      // Keep this synthetic store/rendering test independent of a live API.
      // Open via the desktop sidebar, then resize: the narrow layout hides it.
      await openTurn(page, true);
      await page.setViewportSize(viewport);
      const recording = await startRrweb(page);
      const evidence: Record<string, unknown> = {};
      try {
        for (const start of ['empty', 'mounted'] as const) {
          for (const mode of ['20-char', 'boundary'] as const) {
            const label = `${start}/${mode}`;
            const result = await streamNativeFixture(page, fixture, start, mode);
            evidence[label] = {
              samples: result.samples,
              phases: result.phases,
              failures: result.failures,
              mutationLeaks: result.mutationLeaks,
            };
            // At least two frames per streamed chunk (streamTokens waits two frames).
            const chunks = new Set(result.samples.map((s) => s.chunk)).size;
            expect(chunks, `${label}: chunks sampled`).toBeGreaterThan(3);
            expect(result.samples.length, `${label}: sample count`).toBeGreaterThanOrEqual(
              2 * chunks
            );
            const expected =
              start === 'empty' ? ['prose', 'header', 'payload'] : ['header', 'payload'];
            for (const phase of [...expected, 'call-complete', 'complete']) {
              expect(result.phases, `${label}: phase ${phase} observed`).toContain(phase);
            }
            expect(result.failures, `${label}: ${describeFailures(result)}`).toEqual([]);
            expect(
              nativeVisibilityViolations(result, start),
              `${label}: visibility coverage`
            ).toEqual([]);
            // The literal example and the tail survived; the call is one collapsed block.
            const message = page.locator('[data-message-index="1"]');
            await expect(message).toContainText(NATIVE_LITERAL);
            await expect(message).toContainText(NATIVE_TAIL);
            const details = message.locator('details');
            await expect(details).toHaveCount(1);
            await expect(details).not.toHaveAttribute('open', '');
          }
        }
      } finally {
        // Keep frame evidence even when a streaming assertion fails, without rrweb.
        await testInfo.attach('native-stream-frames', {
          body: JSON.stringify(evidence),
          contentType: 'application/json',
        });
        // Keep the replay on failure: that is when it is useful for diagnosis.
        if (recording) {
          const path = testInfo.outputPath('rrweb-events.json');
          writeFileSync(path, JSON.stringify(await stopRrweb(page)));
          await testInfo.attach('rrweb-events', { path, contentType: 'application/json' });
        }
      }
      // Expanding the call reveals the payload without displacing the tail.
      const message = page.locator('[data-message-index="1"]');
      await message.locator('details summary').click();
      await expect(message.locator('details')).toContainText(fixture.argMarker.slice(2, -1));
      await expect(message).toContainText(NATIVE_TAIL);
      await page.screenshot({ path: testInfo.outputPath(`native-${fixture.name}-expanded.png`) });
    });
  }
}

test('native stream oracle catches one-frame payload flashes and tail clipping', async ({
  page,
}, testInfo) => {
  await openTurn(page, true);
  const recording = await startRrweb(page);
  const fixture = NATIVE_FIXTURES[0];
  const evidence: Record<string, unknown> = {};
  for (const [kind, atPhase, expected] of [
    ['payload-flash', 'payload', /@sh|call_|\{"command"/],
    ['tail-clip', 'header', /clipped|no area|occluded|hides/],
  ] as const) {
    const result = await streamNativeFixture(page, fixture, 'mounted', 'boundary', {
      kind,
      atPhase,
    });
    evidence[kind] = result.failures;
    // Exactly the faulty frame fails, and it carries chunk/phase/timestamp.
    expect(result.failures.length, `${kind} must be detected`).toBeGreaterThan(0);
    expect(result.failures.every((f) => f.fault === kind)).toBe(true);
    const failure = result.failures[0];
    expect(failure.phase).toBe(atPhase);
    expect(failure.chunk).toBeGreaterThanOrEqual(0);
    expect(String(kind === 'payload-flash' ? failure.leak : failure.tailClip)).toMatch(expected);
  }
  if (recording) {
    const path = testInfo.outputPath('rrweb-events.json');
    writeFileSync(path, JSON.stringify(await stopRrweb(page)));
    await testInfo.attach('rrweb-events', { path, contentType: 'application/json' });
  }
  await testInfo.attach('oracle-negative-controls', {
    body: JSON.stringify(evidence),
    contentType: 'application/json',
  });
});

test('native tool call inside a grouped step keeps the tail visible when expanded', async ({
  page,
}) => {
  await openTurn(page, true);
  const fixture = NATIVE_FIXTURES[0];
  const result = await streamNativeFixture(page, fixture, 'empty', 'boundary');
  expect(result.failures, describeFailures(result)).toEqual([]);
  await page.evaluate(async (id) => {
    const { addMessage } = await import('/src/stores/conversations.ts');
    addMessage(id, {
      role: 'system',
      content: 'Ran command\n\n```stdout\na "quoted" word\ndone\nok\n```',
      timestamp: '2026-01-01T00:00:10Z',
    });
    addMessage(id, {
      role: 'assistant',
      content: 'The notes are up to date.',
      timestamp: '2026-01-01T00:00:11Z',
    });
  }, conversationId);
  const toggle = page.getByRole('button', { name: /\d+ steps?/ });
  await expect(toggle).toBeVisible();
  // Collapsed group hides the step; expanding must restore the full tail, collapsed.
  await toggle.click();
  const message = page.locator('[data-message-index="1"]');
  await expect(message).toContainText(NATIVE_TAIL);
  await expect(message).not.toContainText('@shell(');
  await expect(message.locator('details').first()).not.toHaveAttribute('open', '');
  await installFrameOracle(page, {
    messageSelector: '[data-message-index="1"]',
    tail: NATIVE_TAIL,
    literal: NATIVE_LITERAL,
    leakMarkers: [fixture.callId.slice(-10), fixture.argMarker],
    leakPattern: '@sh',
  });
  await markChunk(page, 0, 'group-expanded');
  await nextFrames(page, 6);
  const expanded = await stopFrameOracle(page);
  expect(expanded.samples.length).toBeGreaterThan(3);
  expect(expanded.failures, describeFailures(expanded)).toEqual([]);
  await toggle.click();
  await expect(page.locator('[data-message-index="3"]')).toContainText('up to date');
});

// ---------------------------------------------------------------------------
// rrweb record → real `Replayer` (gptme/gptme#5023). The recording of the
// synthetic page is replayed in rrweb's own Replayer and the same oracles are
// evaluated on the *reconstructed* DOM at every recorded state, so a replay
// artifact is shown to reproduce both a clean run and a controlled regression.
// ---------------------------------------------------------------------------

type ReplayFrame = { offset: number; phase: string; leak: string | null; tailVisible: boolean };

function lastMarkerAt(markers: ReplayMarker[], tag: string, offset: number): ReplayMarker | null {
  let found: ReplayMarker | null = null;
  for (const marker of markers) if (marker.tag === tag && marker.offset <= offset) found = marker;
  return found;
}

/** Evaluate the stream oracle on the replayed message at every recorded DOM state. */
async function scanReplayedStream(
  context: BrowserContext,
  events: RrwebEvent[],
  fixture: NativeFixture
) {
  const session = await openReplayer(context, events);
  try {
    const markers = replayMarkers(events);
    const firstChunk = markers.find((m) => m.tag === 'chunk');
    expect(firstChunk, 'recording carries chunk markers').toBeTruthy();
    const config = {
      markers: [fixture.callId.slice(-10), fixture.argMarker],
      pattern: `@${fixture.tool.slice(0, 2).replace(/[.]/g, '\\.')}`,
      tail: NATIVE_TAIL,
    };
    const frames: ReplayFrame[] = [];
    for (const offset of mutationOffsets(events)) {
      if (offset < firstChunk!.offset) continue;
      await session.seek(offset);
      const seen = await session.inReplay((doc, config) => {
        const text = doc.querySelector<HTMLElement>('[data-message-index="1"]')?.innerText ?? '';
        const pattern = new RegExp(config.pattern);
        const leak =
          config.markers.find((m) => text.includes(m)) ?? pattern.exec(text)?.[0] ?? null;
        return { leak, tailVisible: text.includes(config.tail) };
      }, config);
      frames.push({
        offset,
        phase: String(lastMarkerAt(markers, 'chunk', offset)?.payload.phase ?? 'init'),
        ...seen,
      });
    }
    const last = frames.at(-1)!;
    await session.seek(last.offset);
    const final = await session.inReplay((doc, literal) => {
      const message = doc.querySelector<HTMLElement>('[data-message-index="1"]');
      const details = [...(message?.querySelectorAll('details') ?? [])];
      return {
        detailsCount: details.length,
        anyOpen: details.some((d) => d.open),
        hasLiteral: (message?.innerText ?? '').includes(literal),
      };
    }, NATIVE_LITERAL);
    return { frames, final };
  } finally {
    await session.close();
  }
}

test.describe('rrweb Replayer', () => {
  test('replayed native stream has no flashes or tail loss, and shows a controlled flash', async ({
    page,
    context,
  }, testInfo) => {
    const fixture = NATIVE_FIXTURES[0];
    const runs: Record<string, { frames: ReplayFrame[]; live: OracleResult }> = {};
    for (const kind of ['clean', 'payload-flash'] as const) {
      await openTurn(page, true);
      await startRecording(page);
      const live = await streamNativeFixture(
        page,
        fixture,
        'mounted',
        'boundary',
        kind === 'clean' ? undefined : { kind, atPhase: 'payload' }
      );
      const events = await stopRecording(page);
      const path = testInfo.outputPath(`rrweb-${kind}.json`);
      writeFileSync(path, JSON.stringify(events));
      await testInfo.attach(`rrweb-${kind}`, { path, contentType: 'application/json' });
      const replay = await scanReplayedStream(context, events, fixture);
      runs[kind] = { frames: replay.frames, live };
      expect(replay.frames.length, `${kind}: replayed states`).toBeGreaterThan(10);
      // Replay and live agree about what the final message looks like.
      expect(replay.final).toEqual({ detailsCount: 1, anyOpen: false, hasLiteral: true });
      expect(replay.frames.at(-1)!.tailVisible, `${kind}: replayed tail`).toBe(true);
      expect(replay.frames.at(-1)!.leak, `${kind}: replay settles clean`).toBeNull();
    }
    // Clean: no replayed state shows raw header text; the tail never disappears once seen.
    expect(runs.clean.live.failures).toEqual([]);
    expect(runs.clean.frames.filter((f) => f.leak)).toEqual([]);
    const firstTail = runs.clean.frames.findIndex((f) => f.tailVisible);
    expect(firstTail, 'tail replayed').toBeGreaterThanOrEqual(0);
    expect(runs.clean.frames.slice(firstTail).filter((f) => !f.tailVisible)).toEqual([]);
    // Fault: the live oracle flags the flash and the Replayer reproduces it in
    // the same phase, then recovers.
    const liveFlash = runs['payload-flash'].live.failures;
    expect(liveFlash.length).toBeGreaterThan(0);
    const replayedFlash = runs['payload-flash'].frames.filter((f) => f.leak);
    expect(replayedFlash.length, 'replay reproduces the flash').toBeGreaterThan(0);
    expect(new Set(replayedFlash.map((f) => f.phase))).toEqual(
      new Set(liveFlash.map((f) => f.phase))
    );
    await testInfo.attach('replay-stream-verdicts', {
      body: JSON.stringify(
        {
          clean: runs.clean.frames.length,
          flashReplayed: replayedFlash,
          flashLive: liveFlash.map(({ chunk, phase, leak }) => ({ chunk, phase, leak })),
        },
        null,
        2
      ),
      contentType: 'application/json',
    });
  });
});

/** Same measurements as readDisclosureMetrics, taken inside the Replayer's document. */
async function readReplayedDisclosure(
  session: Awaited<ReturnType<typeof openReplayer>>
): Promise<DisclosureMetrics> {
  return session.inReplay((doc) => {
    const viewport = doc.querySelector<HTMLElement>('[data-testid="message-scroll-viewport"]')!;
    const extent = viewport.querySelector<HTMLElement>(':scope > div[style*="position: relative"]');
    const outputs = [...viewport.querySelectorAll<HTMLDetailsElement>('.chat-message details')];
    return {
      scrollHeight: viewport.scrollHeight,
      clientHeight: viewport.clientHeight,
      scrollTop: viewport.scrollTop,
      virtualExtent: extent?.getBoundingClientRect().height ?? -1,
      docOverflowX: 0,
      openOutputs: outputs.filter((details) => details.open).length,
      outputBlocks: outputs.length,
    };
  }, undefined);
}

test.describe('rrweb Replayer', () => {
  test('recorded disclosure clicks replay to the same bounded scroll extent', async ({
    page,
    context,
  }, testInfo) => {
    await openTurn(page, true);
    const live: Record<'short' | 'large', DisclosureStage[]> = { short: [], large: [] };
    const replayed: Record<'short' | 'large', DisclosureStage[]> = { short: [], large: [] };
    for (const size of ['short', 'large'] as const) {
      await renderDisclosureTurn(page, size);
      await startRecording(page);
      live[size] = await runDisclosureSequence(page);
      const events = await stopRecording(page);
      const path = testInfo.outputPath(`rrweb-disclosure-${size}.json`);
      writeFileSync(path, JSON.stringify(events));
      await testInfo.attach(`rrweb-disclosure-${size}`, { path, contentType: 'application/json' });

      const session = await openReplayer(context, events);
      try {
        const stages = replayMarkers(events).filter((m) => m.tag === 'stage');
        expect(
          stages.map((m) => m.payload.stage),
          `${size}: stage markers recorded in click order`
        ).toEqual(live[size].map((s) => s.stage));
        // The recording must contain the clicks themselves, not only their effects.
        const clicks = events.filter((e) => e.type === 3 && e.data.source === 2).length;
        expect(clicks, `${size}: recorded click interactions`).toBeGreaterThanOrEqual(5);
        for (const marker of stages) {
          await session.seek(marker.offset);
          const metrics = await readReplayedDisclosure(session);
          replayed[size].push({
            stage: String(marker.payload.stage),
            ...metrics,
            peakScrollHeight: metrics.scrollHeight,
          });
        }
      } finally {
        await session.close();
      }
    }
    await testInfo.attach('replayed-disclosure-metrics', {
      body: JSON.stringify({ live, replayed }, null, 2),
      contentType: 'application/json',
    });
    for (const size of ['short', 'large'] as const) {
      live[size].forEach((stage, i) => {
        const copy = replayed[size][i];
        // The Replayer reconstructs which outputs are open, step by step.
        expect(copy.openOutputs, `${size} ${stage.stage}: open outputs`).toBe(stage.openOutputs);
        expect(copy.outputBlocks, `${size} ${stage.stage}: output blocks`).toBe(stage.outputBlocks);
        // ...and the scroll extent, which is what the budget is about.
        expect(
          Math.abs(copy.scrollHeight - stage.scrollHeight),
          `${size} ${stage.stage}: replayed scrollHeight ${copy.scrollHeight} vs live ${stage.scrollHeight}`
        ).toBeLessThanOrEqual(HIDDEN_SLACK);
      });
    }
    // The scroll-growth oracle, evaluated purely on the replayed DOM.
    expect(scrollBudgetViolations(replayed.short, replayed.large)).toEqual([]);

    // Negative control: record the pre-#5029 unbounded output pane. The replay
    // alone must be enough to see the scroll blow-up.
    await renderDisclosureTurn(page, 'large');
    const unboundedStyle = await page.addStyleTag({
      content: '.chat-message details.tool-block pre { max-height: none !important; }',
    });
    await startRecording(page);
    await runDisclosureSequence(page);
    const events = await stopRecording(page);
    await unboundedStyle.evaluate((el) => el.remove());
    const session = await openReplayer(context, events);
    try {
      const unbounded: DisclosureStage[] = [];
      for (const marker of replayMarkers(events).filter((m) => m.tag === 'stage')) {
        await session.seek(marker.offset);
        const metrics = await readReplayedDisclosure(session);
        unbounded.push({
          stage: String(marker.payload.stage),
          ...metrics,
          peakScrollHeight: metrics.scrollHeight,
        });
      }
      const violations = scrollBudgetViolations(replayed.short, unbounded);
      expect(violations.some((v) => v.startsWith('large output-open: grew'))).toBe(true);
    } finally {
      await session.close();
    }
  });
});

// Geometry cannot see text that is in the right place but not painted. Probe the
// pixels where the finished reply's tail should be, then apply controlled faults.
test('pixel probe sees the painted reply tail and catches clipping/occlusion geometry misses', async ({
  page,
}, testInfo) => {
  await openTurn(page, true);
  const result = await streamNativeFixture(page, NATIVE_FIXTURES[0], 'empty', 'boundary');
  expect(result.failures, describeFailures(result)).toEqual([]);
  const selector = '[data-message-index="1"]';
  const clip = await textClip(page, selector, NATIVE_TAIL);
  expect(clip, 'tail located').not.toBeNull();
  await page.mouse.move(0, 0);
  await nextFrames(page, 3);

  const baseline = await inkPixels(page, clip!);
  // A line of text covers hundreds of pixels; require a wide margin over noise.
  expect(baseline.ink, 'painted tail has ink').toBeGreaterThan(200);
  await page.screenshot({ path: testInfo.outputPath('tail-painted.png'), clip: clip! });

  // Each fault leaves the tail's box where it was; only the paint changes.
  const faults = ['collapsed-box', 'transparent-text', 'click-through-overlay'] as const;
  const report: Record<string, unknown> = { baseline };
  for (const kind of faults) {
    await page.evaluate(
      ({ selector, kind, bg, area }) => {
        const message = document.querySelector<HTMLElement>(selector)!;
        if (kind === 'collapsed-box') {
          // The oracle's own tail-clip fault: the reply box collapses to a sliver.
          const target = message.querySelector<HTMLElement>('.chat-message, .markdown') ?? message;
          target.style.maxHeight = '4px';
          target.style.overflow = 'hidden';
        } else if (kind === 'transparent-text') {
          // Geometry-blind: layout and hit-testing are unchanged.
          message.style.color = 'transparent';
          message
            .querySelectorAll<HTMLElement>('*')
            .forEach((el) => (el.style.color = 'transparent'));
        } else {
          // Geometry-blind: elementFromPoint skips a pointer-events:none overlay.
          const cover = document.createElement('div');
          Object.assign(cover.style, {
            position: 'fixed',
            left: `${area.x - 2}px`,
            top: `${area.y - 2}px`,
            width: `${area.width + 4}px`,
            height: `${area.height + 4}px`,
            background: bg,
            pointerEvents: 'none',
            zIndex: '2147483647',
          });
          document.body.appendChild(cover);
        }
      },
      { selector, kind, bg: baseline.background, area: clip! }
    );
    await nextFrames(page, 3);
    const faulty = await inkPixels(page, clip!);
    report[kind] = faulty;
    await page.screenshot({ path: testInfo.outputPath(`tail-${kind}.png`), clip: clip! });
    // Hit-testing at the tail centre: the geometry-only check still passes for
    // the paint-only faults, which is exactly the gap the probe closes.
    const hitsMessage = await page.evaluate(
      ({ selector, area }) => {
        const hit = document.elementFromPoint(area.x + area.width / 2, area.y + area.height / 2);
        return !!hit && !!document.querySelector(selector)?.contains(hit);
      },
      { selector, area: clip! }
    );
    if (kind !== 'collapsed-box') {
      expect(hitsMessage, `${kind}: geometry hit-test is blind to this fault`).toBe(true);
    }
    expect(faulty.ink, `${kind}: pixel probe sees no tail`).toBeLessThan(baseline.ink * 0.05);
    // Undo: reload the turn so the next fault starts from a painted tail.
    await openTurn(page, true);
    await streamNativeFixture(page, NATIVE_FIXTURES[0], 'empty', 'boundary');
    const again = await textClip(page, selector, NATIVE_TAIL);
    await page.mouse.move(0, 0);
    await nextFrames(page, 3);
    const restored = await inkPixels(page, again!);
    expect(restored.ink, `${kind}: undo restores ink`).toBeGreaterThan(200);
  }
  await testInfo.attach('pixel-probe', {
    body: JSON.stringify(report, null, 2),
    contentType: 'application/json',
  });
});
