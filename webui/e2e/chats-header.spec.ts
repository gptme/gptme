import { test, expect } from '@playwright/test';

for (const theme of ['light', 'dark'] as const) {
  test(`Chats search disclosure and keyboard navigation (${theme})`, async ({ page }) => {
    await page.addInitScript((value) => localStorage.setItem('theme', value), theme);
    await page.goto('/chat?demo=1');
    const list = page.getByTestId('conversation-list');
    await expect(list).toBeVisible();
    const search = page.getByRole('button', { name: 'Search conversations', exact: true });
    const input = list.getByRole('textbox', { name: 'Search conversations' });
    await expect(input).not.toBeVisible();
    const initialCount = await list.getByTestId('conversation-title').count();
    expect(initialCount).toBeGreaterThan(1);
    await search.focus();
    await expect(page.getByRole('tooltip')).toContainText('Cmd+K / Ctrl+K');
    await page.keyboard.press('Enter');
    await expect(input).toBeFocused();
    await input.fill('Introduction');
    await expect(list.getByTestId('conversation-title')).toHaveCount(1);
    await expect(page).toHaveURL(/search=Introduction/);
    await page.keyboard.press('Escape');
    await expect(input).toBeVisible();
    await expect(input).toHaveValue('Introduction');
    await expect(search).toBeFocused();
    await page.screenshot({ path: `test-results/chats-header-${theme}-active.png` });
    await page.getByRole('button', { name: 'Clear conversation search' }).click();
    await expect(input).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(input).not.toBeVisible();
    await expect(search).toBeFocused();
    await expect(list.getByTestId('conversation-title')).toHaveCount(initialCount);
    await page.keyboard.press('Control+k');
    const palette = page.getByPlaceholder('Type a command or search conversations...');
    await expect(palette).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(palette).not.toBeVisible();
    await search.focus();
    await page.keyboard.press('Meta+k');
    await expect(palette).toBeFocused();
    await page.keyboard.press('Escape');
    await search.focus();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: 'Import conversation JSON' })).toBeFocused();
    await expect(
      page.getByRole('tooltip', { name: 'Import conversation JSON', exact: true })
    ).toBeVisible();
    await page.keyboard.press('Tab');
    const newChat = page.getByRole('button', { name: 'New conversation', exact: true });
    await expect(newChat).toBeFocused();
    await expect(
      page.getByRole('tooltip', { name: 'New conversation', exact: true })
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await newChat.blur();
    await page.mouse.move(900, 600);
    await search.hover();
    await expect(page.getByRole('tooltip', { name: /command palette/ })).toBeVisible();
    await page.screenshot({ path: `test-results/chats-header-${theme}-compact.png` });
  });
}
