import { test, expect } from '@playwright/test';

// The event-stream reconnect banner used to be an absolute overlay above the
// composer. It covered the queued-message badge and its inline editor and
// intercepted clicks on "Save queued message" while the stream reconnected.
test('reconnect banner does not block editing a queued message', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = {
      'Access-Control-Allow-Origin': request.headers()['origin'] ?? '*',
      'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': 'Authorization, Content-Type',
      'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
    };
    const reply = (json: unknown) => route.fulfill({ headers, json });
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (path === '/api/v2') return reply({ api_version: 2, contract_revision: 5 });
    if (path.endsWith('/models'))
      return reply({
        default: 'openai/gpt-4o',
        recommended: [],
        models: [{ id: 'openai/gpt-4o', provider: 'openai', model: 'gpt-4o' }],
      });
    if (path.endsWith('/user/settings')) return reply({ providers_configured: ['openai'] });
    if (path.endsWith('/user')) return reply({ username: 'reconnect-test' });
    // A stream that ends after its body keeps the client reconnecting.
    if (path.endsWith('/events'))
      return route.fulfill({
        headers: { ...headers, 'Content-Type': 'text/event-stream' },
        body:
          `data: ${JSON.stringify({ type: 'connected', session_id: 'reconnect-session' })}\n\n` +
          `data: ${JSON.stringify({ type: 'generation_started' })}\n\n` +
          `data: ${JSON.stringify({ type: 'generation_progress', token: 'Working' })}\n\n`,
      });
    if (path.endsWith('/step')) return reply({ status: 'ok', session_id: 'reconnect-session' });
    if (path.endsWith('/config')) return reply({ chat: { model: 'openai/gpt-4o' } });
    if (/\/conversations\/[^/]+$/.test(path)) {
      if (request.method() !== 'GET')
        return reply({ status: 'ok', session_id: 'reconnect-session' });
      return reply({
        id: 'reconnect-a',
        name: 'Reconnect test',
        logfile: 'reconnect-a',
        workspace: '.',
        branches: {},
        log: [{ role: 'user', content: 'Start', timestamp: '2026-11-20T21:00:00Z' }],
      });
    }
    if (path.endsWith('/conversations')) return reply({ conversations: [] });
    return reply({ tasks: [], sessions: [], files: [], workspaces: [], agents: [] });
  });

  await page.goto('/chat/reconnect-a');
  await page.getByRole('textbox').last().fill('Follow-up prompt');
  await page.getByRole('button', { name: 'Queue message' }).click({ timeout: 15000 });
  await expect(page.getByText(/^Queued:/)).toBeVisible();

  await page.getByRole('button', { name: 'Edit queued message' }).click();
  await page.getByRole('textbox', { name: 'Edit queued message' }).fill('Edited follow-up');
  await expect(page.getByText(/Reconnecting event stream/)).toBeVisible({ timeout: 15000 });
  if (process.env.SCREENSHOTS_DIR)
    await page.screenshot({ path: `${process.env.SCREENSHOTS_DIR}/reconnect-banner-queue.png` });

  await page.getByRole('button', { name: 'Save queued message' }).click({ timeout: 5000 });
  await expect(page.getByText(/^Queued:/)).toHaveAttribute('title', 'Edited follow-up');
});
