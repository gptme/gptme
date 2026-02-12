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
    const serverSelector = page.getByRole('button', { name: /Local|Servers/i });
    await expect(serverSelector).toBeVisible();

    // Click the demo conversation
    await page.getByText('Introduction to gptme').click();

    // Should show the conversation content
    await expect(page.getByText(/Hello! I'm gptme, your AI programming assistant/)).toBeVisible();
    await page.goto('/');

    // Wait for successful connection (green dot in server selector confirms connected state)
    await expect(serverSelector.locator('.bg-green-500')).toBeVisible({
      timeout: 10000,
    });

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

    // Open server selector dropdown
    const serverSelector = page.getByRole('button', { name: /Local|Servers/i });
    await serverSelector.click();

    // Click "Add server" button (Plus icon) in the dropdown header
    // The button is inside a tooltip; locate it near the "Servers" heading
    await page.locator('[role="menu"]').getByRole('button').first().click();

    // Fill in an invalid server URL in the Add Server dialog
    await page.getByLabel('Server URL').fill('http://localhost:1');
    await page.getByLabel('Name').fill('Bad Server');

    // Submit the Add Server form
    await page.getByRole('button', { name: /Add & Connect/i }).click();

    // Wait for error toast to appear (connection fails for unreachable server)
    // Use .first() because the toast may render multiple matching elements
    await expect(
      page.getByText(/Failed to connect|Could not connect|Failed to switch|Failed to add/i).first()
    ).toBeVisible({
      timeout: 10000,
    });

    // Close dialog if still open
    await page.keyboard.press('Escape');

    // Should show demo conversations
    await expect(page.getByText('Introduction to gptme')).toBeVisible();

    // Should not show any API conversations from the bad server
    const conversationList = page.getByTestId('conversation-list');
    const conversationTitles = await conversationList
      .locator('[data-testid="conversation-title"]')
      .allTextContents();

    const apiConversations = conversationTitles.filter((title) => /^\d+$/.test(title));
    expect(apiConversations.length).toBe(0);
  });
});

test.describe('Conversation Flow', () => {
  test('should be able to create a new conversation and send a message', async ({ page }) => {
    await page.goto('/');

    // Wait for the page to load completely
    await page.waitForLoadState('networkidle');

    const message = 'Hello. We are testing, just say exactly "Hello world" without anything else.';

    // Make sure we can see the chat input
    await expect(page.getByTestId('chat-input')).toBeVisible({ timeout: 10000 });

    // Type a message
    await page.getByTestId('chat-input').fill(message);
    await page.keyboard.press('Enter');

    // Wait for the new conversation page to load
    await expect(page).toHaveURL(/\/chat\/.+$/, { timeout: 15000 });

    // Should show the message in the conversation
    // Look specifically for the user's message in a user message container
    await expect(
      page.locator('.role-user', {
        hasText: message,
      })
    ).toBeVisible({ timeout: 15000 });

    // Should show the AI's response
    await expect(
      page.locator('.role-assistant', {
        hasText: 'Hello world',
      })
    ).toBeVisible({ timeout: 30000 }); // AI response might take longer
  });
});
