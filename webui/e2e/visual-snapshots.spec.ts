import { test } from '@playwright/test';
import path from 'path';
import fs from 'fs';

// Visual snapshot tests for before/after PR comparison.
//
// These are NOT assertions — they capture the UI state so that
// .github/workflows/webui-visual-diff.yml can compare PR branch vs master
// and post a diff comment when visual changes are detected.
//
// Run via:
//   SCREENSHOTS_DIR=/tmp/shots npx playwright test e2e/visual-snapshots.spec.ts

const SCREENSHOTS_DIR = process.env.SCREENSHOTS_DIR
  ? path.resolve(process.env.SCREENSHOTS_DIR)
  : path.join(process.cwd(), 'visual-snapshots');

test.beforeAll(() => {
  fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
});

test('01-home', async ({ page }) => {
  await page.goto('/?demo=1');
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(SCREENSHOTS_DIR, '01-home.png') });
});

test('02-demo-conversation', async ({ page }) => {
  await page.goto('/?demo=1');
  await page.waitForLoadState('networkidle');
  await page.getByText('Introduction to gptme').click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(1000);
  await page.screenshot({ path: path.join(SCREENSHOTS_DIR, '02-demo-conversation.png') });
});
