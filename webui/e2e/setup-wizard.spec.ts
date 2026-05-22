import { test, expect } from '@playwright/test';

const freshStorageState = {
  cookies: [],
  origins: [],
};

test.describe('Setup wizard', () => {
  test.use({ storageState: freshStorageState });

  test('shows the first-run flow and lets the user switch setup modes', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await expect(page.getByRole('heading', { name: /welcome to gptme/i })).toBeVisible();

    await page.getByRole('button', { name: /get started/i }).click();
    await expect(page.getByRole('heading', { name: /choose your setup/i })).toBeVisible();

    await page.getByRole('button', { name: /cloud/i }).click();
    await expect(page.getByRole('heading', { name: /cloud setup/i })).toBeVisible();
    await expect(page.getByText(/you'll be redirected to gptme\.ai to sign in/i)).toBeVisible();

    await page.getByRole('button', { name: /^back$/i }).click();
    await expect(page.getByRole('heading', { name: /choose your setup/i })).toBeVisible();

    await page.getByRole('button', { name: /local/i }).click();
    await expect(
      page.getByRole('heading', { name: /local setup|remote server setup/i })
    ).toBeVisible();
    await expect(page.getByRole('button', { name: /connect/i })).toBeVisible();
  });

  test('skip persists setup completion', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await expect(page.getByRole('heading', { name: /welcome to gptme/i })).toBeVisible();
    await page.getByRole('button', { name: /skip for now/i }).click();

    await expect(page.getByRole('heading', { name: /welcome to gptme/i })).toHaveCount(0);

    const settingsJson = await page.evaluate(() => window.localStorage.getItem('gptme-settings'));
    expect(settingsJson).not.toBeNull();
    expect(JSON.parse(settingsJson ?? '{}')).toMatchObject({ hasCompletedSetup: true });
  });
});
