import { test, expect } from '@playwright/test';

test.describe('Connecting', () => {
  test('connects to the API server (hard requirement)', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // The chat input only becomes enabled once the app has a live server
    // connection. If this fails, the suite is running offline against demo
    // data and NO other test is exercising the API server — fix the server
    // or the --cors-origin (must exactly match the Playwright origin;
    // localhost != 127.0.0.1) rather than softening this assertion.
    await expect(page.getByTestId('chat-input')).toBeEnabled({ timeout: 20000 });
  });

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

test.describe('Conversation Creation', () => {
  // Set E2E_SKIP_CREATE_CONVERSATION=1 to skip when testing against a server
  // known to reject webui creates (e.g. releases with the #2943 regression).
  test.skip(
    process.env.E2E_SKIP_CREATE_CONVERSATION === '1',
    'server under test has the workspace-containment create regression (fixed by #3319)'
  );

  test('should create a new conversation when submitting a message', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Type a message in the chat input and submit with Enter.
    // The input stays disabled until the app connects to the server — if this
    // times out, the webui never connected (check the server's --cors-origin
    // matches the Playwright origin exactly; localhost != 127.0.0.1).
    const input = page.getByTestId('chat-input');
    await expect(input).toBeVisible({ timeout: 10000 });
    await expect(input).toBeEnabled({ timeout: 20000 });
    await input.fill('Hello from the e2e test');
    await input.press('Enter');

    // Server-side creation must succeed and navigate to the new conversation.
    // Regression guard: the server once rejected every webui create with
    // "workspace escapes conversation logdir" because the webui sends an
    // explicit workspace ('.') with each create request.
    await page.waitForURL(/\/chat\/chat-/, { timeout: 15000 });

    // The submitted message should be rendered in the conversation
    await expect(page.getByText('Hello from the e2e test')).toBeVisible({ timeout: 10000 });

    // No creation-failure toast (generation itself may fail without API keys;
    // this test only covers conversation creation)
    await expect(page.getByText(/Failed to (create|start) conversation/)).not.toBeVisible();
  });
});

test.describe('Split View', () => {
  test('should render split panes when ?split= param is present', async ({ page }) => {
    // Navigate with the split parameter using two demo conversation IDs
    await page.goto('/chat/introduction?split=introduction,introduction');
    await page.waitForLoadState('networkidle');

    // Split view header should appear
    await expect(page.getByText('Split view')).toBeVisible({ timeout: 10000 });

    // Close button should be present
    await expect(page.getByTitle('Close split view')).toBeVisible({ timeout: 10000 });
  });

  test('should return to single-pane view when split is closed', async ({ page }) => {
    await page.goto('/chat/introduction?split=introduction,introduction');
    await page.waitForLoadState('networkidle');

    await expect(page.getByText('Split view')).toBeVisible({ timeout: 10000 });

    // Click the close button
    await page.getByTitle('Close split view').click();
    await page.waitForLoadState('networkidle');

    // Split view header should be gone
    await expect(page.getByText('Split view')).not.toBeVisible();
  });

  test('should toggle split view with keyboard shortcut', async ({ page }) => {
    // Navigate to a single conversation
    await page.goto('/chat/introduction');
    await page.waitForLoadState('networkidle');

    // Should see the conversation content
    await expect(page.getByText(/Hello! I'm gptme/)).toBeVisible({ timeout: 10000 });

    // Press keyboard shortcut (Ctrl+Shift+\) to open split view
    await page.keyboard.press('Control+Shift+\\');
    await page.waitForLoadState('networkidle');

    // Split view header should appear
    await expect(page.getByText('Split view')).toBeVisible({ timeout: 10000 });

    // Press keyboard shortcut again to close split view
    await page.keyboard.press('Control+Shift+\\');
    await page.waitForLoadState('networkidle');

    // Split view header should be gone
    await expect(page.getByText('Split view')).not.toBeVisible();
  });
});
