import fs from 'fs';
import path from 'path';
import { type Locator, type Page } from '@playwright/test';
import {
  isVisionAssertEnabled,
  requestVisionVerdict,
  resolveVisionApiKey,
} from './visionAssertCore';

export {
  buildVisionAssertPrompt,
  isVisionAssertEnabled,
  parseVisionVerdict,
  resolveVisionApiKey,
} from './visionAssertCore';

export type VisionAssertOptions = {
  /** Screenshot this locator instead of the full viewport. */
  locator?: Locator;
  /** Artifact basename (no extension). */
  name?: string;
};

/**
 * Screenshot the page (or a locator) and ask a vision model to verify `claim`.
 *
 * No-op unless GPTME_VISION_ASSERT=1 so existing E2E tests stay free and
 * deterministic in CI. When opted in, missing credentials fail closed.
 *
 * Example:
 *   await visionAssert(page, 'The assistant message is fully visible and not clipped', {
 *     locator: page.locator('[data-conversation-pane]'),
 *     name: 'generation-echo',
 *   });
 */
export async function visionAssert(
  page: Page,
  claim: string,
  options: VisionAssertOptions = {}
): Promise<void> {
  if (!isVisionAssertEnabled()) {
    console.warn(`[vision_assert] skipped (GPTME_VISION_ASSERT!=1): ${claim}`);
    return;
  }

  const apiKey = resolveVisionApiKey();
  if (!apiKey) {
    throw new Error('GPTME_VISION_ASSERT=1 requires OPENROUTER_API_KEY or GPTME_VISION_API_KEY');
  }

  const artifactDir =
    process.env.VISION_ASSERT_DIR || path.join(process.cwd(), 'test-results', 'vision-assert');
  fs.mkdirSync(artifactDir, { recursive: true });

  const stem = sanitizeName(options.name || 'vision-assert');
  const screenshotPath = path.join(artifactDir, `${stem}.png`);
  if (options.locator) {
    await options.locator.screenshot({ path: screenshotPath });
  } else {
    await page.screenshot({ path: screenshotPath });
  }

  const imagePng = fs.readFileSync(screenshotPath);
  const verdict = await requestVisionVerdict({
    imagePng,
    claim,
    apiKey,
    model: process.env.GPTME_VISION_MODEL,
  });

  const verdictPath = path.join(artifactDir, `${stem}.json`);
  fs.writeFileSync(verdictPath, JSON.stringify({ claim, screenshotPath, verdict }, null, 2) + '\n');

  if (!verdict.pass) {
    throw new Error(
      `vision_assert failed: ${verdict.reason}\nclaim: ${claim}\nscreenshot: ${screenshotPath}`
    );
  }
}

function sanitizeName(name: string): string {
  const cleaned = name.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
  return cleaned || 'vision-assert';
}
