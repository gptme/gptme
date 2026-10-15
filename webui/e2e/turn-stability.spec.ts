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
  await page.evaluate(async (id) => {
    const { replaceLog } = await import('/src/stores/conversations.ts');
    replaceLog(id, [
      { role: 'user', content: 'A different branch' },
      { role: 'assistant', content: 'Replacement reply' },
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
    const samples: { missing: boolean; blankReply: boolean; jump: number }[] = [];
    let running = true;
    let sawContent = false;
    let lastLength = 0;
    const sample = () => {
      const log = conversations$.get(id).data.log.peek();
      const last = log[log.length - 1];
      if (log.length !== lastLength) {
        sawContent = false;
        lastLength = log.length;
      }
      const reply = document.querySelector(`[data-message-index="${log.length - 1}"]`);
      // Any assistant message still streaming must stay painted. Keying on the
      // first content line covers tool turns ("Checking.") and the streamed
      // final reply alike. blankReply only fires after that row has painted at
      // least once, so a mount-lag frame is not a false positive.
      const firstLine = last.content.split('\n')[0];
      const painted = firstLine.length > 0 && !!reply?.textContent?.includes(firstLine);
      if (painted) sawContent = true;
      samples.push({
        missing: !prompt.isConnected,
        blankReply:
          last.role === 'assistant' &&
          !last.isComplete &&
          firstLine.length > 0 &&
          sawContent &&
          !painted,
        jump: Math.abs(prompt.getBoundingClientRect().top - top),
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
          samples: { missing: boolean; blankReply: boolean; jump: number }[];
          stop: () => void;
        };
      }
    ).turnFrames;
    state.stop();
    return state.samples;
  });
  expect(frames.length).toBeGreaterThan(10);
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
