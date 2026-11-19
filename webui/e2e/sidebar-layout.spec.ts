import { test, expect } from '@playwright/test';

test('chat details leave conversations collapsed, including after reload', async ({ page }) => {
  await page.goto('/?demo=1');
  await page.getByText('Introduction to gptme').click();
  const conversations = page.locator('[data-panel-id="conversations"]');
  const collapsed = async () => {
    await expect.poll(async () => (await conversations.boundingBox())?.width).toBe(0);
  };

  await expect(conversations).toBeVisible();
  await page.getByTestId('toggle-conversations-sidebar').click();
  await collapsed();

  for (let i = 0; i < 2; i++) {
    await page.getByRole('button', { name: 'Chat Settings', exact: true }).click();
    await expect(page.locator('[data-panel-id="chat-details"]')).toBeVisible();
    await collapsed();
    await page.getByRole('button', { name: 'Chat Settings', exact: true }).click();
    await expect(page.locator('[data-panel-id="chat-details"]')).toHaveCount(0);
    await collapsed();
  }

  // Demo mode is deliberately URL-scoped across reloads.
  await page.goto(`${new URL(page.url()).pathname}?demo=1`);
  await collapsed();
  await page.screenshot({ path: 'test-results/sidebar-collapsed.png' });

  await page.getByTestId('toggle-conversations-sidebar').click();
  await expect.poll(async () => (await conversations.boundingBox())?.width ?? 0).toBeGreaterThan(0);
  await page.reload();
  await expect.poll(async () => (await conversations.boundingBox())?.width ?? 0).toBeGreaterThan(0);
});

test('navigation rail preference survives reload', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?demo=1');
  const toggle = page.getByTestId('toggle-nav-sidebar');
  await toggle.click();
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem('nav-sidebar-expanded')))
    .toBe('false');
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem('nav-sidebar-expanded')))
    .toBe('false');
  await toggle.click();
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem('nav-sidebar-expanded')))
    .toBe('true');
});
