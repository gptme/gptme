import { expect, test } from '@playwright/test';
import { isVisionAssertEnabled, visionAssert } from './helpers/visionAssert';

// Documents the failure class that Playwright/DOM assertions miss:
// getByText().toBeVisible() passes when the full string is in the DOM even if
// overflow:hidden clips it. A vision model judging pixels can catch that.
//
// Default: only the DOM half runs (cheap, no secrets).
// Opt in: GPTME_VISION_ASSERT=1 OPENROUTER_API_KEY=... npm run test:e2e -- e2e/vision-assert-gap.spec.ts

const CLIPPED_MESSAGE = 'Echo: roundtrip-check extra text that is clipped by overflow';
const COMPLETE_CLAIM =
  'The entire sentence "Echo: roundtrip-check extra text that is clipped by overflow" is fully readable in the screenshot, with no characters cut off.';

test.describe('vision_assert vs Playwright visibility', () => {
  test('clipped overflow: DOM visibility passes; vision should fail when enabled', async ({
    page,
  }) => {
    await page.setContent(`
      <main data-conversation-pane style="width:220px;height:22px;overflow:hidden;white-space:nowrap;font:16px/22px sans-serif">
        ${CLIPPED_MESSAGE}
      </main>
    `);

    // Playwright "visible" = not display:none / not empty box. Overflow clipping
    // does not fail this assertion — that is the coverage gap.
    await expect(page.getByText('Echo: roundtrip-check')).toBeVisible();

    if (!isVisionAssertEnabled()) {
      await visionAssert(page, COMPLETE_CLAIM, { name: 'gap-clipped-skipped' });
      return;
    }

    await expect(
      visionAssert(page, COMPLETE_CLAIM, {
        locator: page.locator('[data-conversation-pane]'),
        name: 'gap-clipped',
      })
    ).rejects.toThrow(/vision_assert failed/i);
  });

  test('unclipped layout: DOM visibility and vision both pass when enabled', async ({ page }) => {
    await page.setContent(`
      <main data-conversation-pane style="width:900px;height:80px;overflow:visible;font:16px/22px sans-serif">
        ${CLIPPED_MESSAGE}
      </main>
    `);

    await expect(page.getByText(CLIPPED_MESSAGE)).toBeVisible();
    await visionAssert(page, COMPLETE_CLAIM, {
      locator: page.locator('[data-conversation-pane]'),
      name: 'gap-unclipped',
    });
  });
});
