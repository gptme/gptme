import { test, expect } from '@playwright/test';

test.describe('Keyboard Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('conversation-list')).toBeVisible({ timeout: 10000 });
  });

  test('command palette: Ctrl+K opens, Esc closes', async ({ page }) => {
    const input = page.getByPlaceholder('Type a command or search conversations...');
    await expect(input).not.toBeVisible();

    await page.keyboard.press('Control+k');
    await expect(input).toBeVisible({ timeout: 3000 });

    await page.keyboard.press('Escape');
    await expect(input).not.toBeVisible({ timeout: 3000 });
  });

  test('shortcuts dialog: ? opens, Esc closes', async ({ page }) => {
    // The chat input autofocuses once the app connects to the server — blur
    // it so '?' opens the dialog instead of typing into the input. (A bare
    // body click is not enough to move focus away.)
    await page.getByTestId('chat-input').blur();

    const dialog = page.getByRole('dialog', { name: /keyboard shortcuts/i });
    await expect(dialog).not.toBeVisible();

    await page.keyboard.press('?');
    await expect(dialog).toBeVisible({ timeout: 3000 });
    await expect(dialog.getByText('Open command palette')).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible({ timeout: 3000 });
  });

  test('conversation rows: Tab-focusable and Enter selects', async ({ page }) => {
    const firstRow = page
      .getByTestId('conversation-list')
      .locator('[role="button"][tabindex="0"]')
      .first();
    await expect(firstRow).toBeVisible({ timeout: 5000 });

    // Focus and activate via keyboard
    await firstRow.focus();
    await expect(firstRow).toBeFocused();
    await page.keyboard.press('Enter');

    // After selection, aria-pressed flips to true
    await expect(firstRow).toHaveAttribute('aria-pressed', 'true');
  });

  test('focus trap: Tab stays inside an open dialog', async ({ page }) => {
    // Open the shortcuts dialog (Radix UI Dialog traps focus).
    // Blur the (auto-focused, enabled-when-connected) chat input first so
    // '?' reaches the global shortcut handler.
    await page.getByTestId('chat-input').blur();
    await page.keyboard.press('?');

    const dialog = page.getByRole('dialog', { name: /keyboard shortcuts/i });
    await expect(dialog).toBeVisible({ timeout: 3000 });

    // Tab through several cycles; focus must always stay inside the dialog
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Tab');
      const outsideFocus = await page.evaluate(() => {
        const active = document.activeElement;
        const dlg = document.querySelector('[role="dialog"]');
        return dlg ? !dlg.contains(active) : true;
      });
      expect(outsideFocus).toBe(false);
    }

    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible({ timeout: 3000 });
  });
});

test.describe('Keyboard Navigation — Settings Page', () => {
  test('Escape navigates back to /chat (with history)', async ({ page }) => {
    // Navigate to /chat first to establish history
    await page.goto('/chat');
    await page.waitForLoadState('networkidle');

    // Navigate to /settings
    await page.goto('/settings');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('h1', { hasText: 'Settings' })).toBeVisible({ timeout: 5000 });

    // Press Escape — should navigate back to /chat
    await page.keyboard.press('Escape');
    await expect(page).toHaveURL(/\/chat$/, { timeout: 5000 });
  });

  test('Escape falls back to /chat (no history)', async ({ page }) => {
    // Navigate directly to /settings (no prior history)
    await page.goto('/settings');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('h1', { hasText: 'Settings' })).toBeVisible({ timeout: 5000 });

    // Press Escape — should fall back to /chat since there's no history
    await page.keyboard.press('Escape');
    await expect(page).toHaveURL(/\/chat$/, { timeout: 5000 });
  });
});
