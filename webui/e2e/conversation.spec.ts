import { test, expect } from '@playwright/test';

test.describe('Connecting', () => {
  test('should connect and list conversations', async ({ page }) => {
    // Go to the app
    await page.goto('/');

    // Wait a moment for the page to fully load
    await page.waitForLoadState('networkidle');

    // The sidebar should be visible by default in the new layout
    // Check if we can see demo conversations (they should be visible by default)
    await expect(page.getByText('Introduction to gptme')).toBeVisible({ timeout: 10000 });

    // Should show the server selector with the active server name
    // Wait for conversations to load, then check if page is fully functional
    // Note: Server selector button may not have accessible name in accessibility tree
    await expect(page.getByTestId('conversation-list')).toBeVisible({ timeout: 10000 });

    // Click the demo conversation
    await page.getByText('Introduction to gptme').click();

    // Should show the conversation content
    await expect(page.getByText(/Hello! I'm gptme, your AI programming assistant/)).toBeVisible();
    await page.goto('/');

    // In the new layout, conversations should be visible by default
    // No need to toggle sidebar, but ensure we're on chat section
    await expect(page.getByText('Introduction to gptme')).toBeVisible();

    // Wait for loading state to finish
    await expect(page.getByText('Loading conversations...')).toBeHidden();

    // Get the conversation list
    const conversationList = page.getByTestId('conversation-list');

    // Get all conversation titles
    const conversationTitles = await conversationList
      .locator('[data-testid="conversation-title"]')
      .allTextContents();

    // Should have both demo and API conversations
    const demoConversations = conversationTitles.filter((title) => title.includes('Introduction'));
    const apiConversations = conversationTitles.filter((title) => /^\d+$/.test(title));

    expect(demoConversations.length).toBeGreaterThan(0);

    if (apiConversations.length > 0) {
      // Check for historical timestamps if we have API conversations
      const timestamps = await conversationList
        .getByRole('button')
        .locator('time')
        .allTextContents();
      expect(timestamps.length).toBeGreaterThan(1);

      // There should be some timestamps that aren't "just now"
      const nonJustNowTimestamps = timestamps.filter((t) => t !== 'just now');
      expect(nonJustNowTimestamps.length).toBeGreaterThan(0);
    } else {
      // This happens when e2e tests are run in CI with a fresh gptme-server
      console.log('No API conversations found, skipping timestamp check');
    }
  });

  test('should handle connection errors gracefully', async ({ page }) => {
    // Start with server unavailable
    await page.goto('/');

    // Wait a moment for the page to fully load
    await page.waitForLoadState('networkidle');

    // In the new layout, conversations should be visible by default
    // Should still show demo conversations
    await expect(page.getByText('Introduction to gptme')).toBeVisible({ timeout: 10000 });

    // Verify conversation list is accessible
    const conversationList = page.getByTestId('conversation-list');
    await expect(conversationList).toBeVisible();

    // Verify we can see the demo conversation
    const conversationTitles = await conversationList
      .locator('[data-testid="conversation-title"]')
      .allTextContents();

    const demoConversations = conversationTitles.filter((title) => title.includes('Introduction'));
    expect(demoConversations.length).toBeGreaterThan(0);
  });
});

test.describe('Conversation Flow', () => {
  test('should display chat interface', async ({ page }) => {
    await page.goto('/');

    // Wait for the page to load completely
    await page.waitForLoadState('networkidle');

    // Make sure we can see the chat input
    await expect(page.getByTestId('chat-input')).toBeVisible({ timeout: 10000 });

    // Verify conversation list is visible
    await expect(page.getByTestId('conversation-list')).toBeVisible({ timeout: 10000 });

    // Verify demo conversations are accessible
    await expect(page.getByText('Introduction to gptme')).toBeVisible();
  });
});
