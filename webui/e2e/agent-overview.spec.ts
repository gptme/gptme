import { test, expect } from '@playwright/test';

// Exercise the real router/layout against two servers with colliding names,
// paths and conversation IDs. No live server or private agent data is needed.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem(
      'gptme_servers',
      JSON.stringify({
        servers: [
          {
            id: 'one',
            name: 'Local',
            baseUrl: 'http://127.0.0.1:5700',
            isPreset: false,
            authToken: null,
            useAuthToken: false,
            createdAt: 0,
            lastUsedAt: 0,
          },
          {
            id: 'two',
            name: 'Remote',
            baseUrl: 'http://127.0.0.1:5799',
            isPreset: false,
            authToken: null,
            useAuthToken: false,
            createdAt: 0,
            lastUsedAt: 0,
          },
        ],
        activeServerId: 'one',
        connectedServerIds: ['one', 'two'],
      })
    );
  });
  await page.route(/127\.0\.0\.1:(5700|5799)\//, (route) => {
    const headers = {
      'Access-Control-Allow-Origin': route.request().headers()['origin'] || '*',
      'Access-Control-Allow-Headers': 'Authorization, Content-Type',
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
      'Access-Control-Allow-Credentials': 'true',
    };
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    const url = new URL(route.request().url());
    let body: unknown = {};
    if (url.pathname === '/api/v2') body = { api_version: 2, contract_revision: 5 };
    else if (url.pathname.startsWith('/api/v2/conversations')) {
      body = {
        conversations: [
          {
            id: 'shared',
            name: url.port === '5799' ? 'Remote planning' : 'Local planning',
            modified: 1795120000,
            messages: 4,
            agent_name: 'Builder',
            agent_path: '/agents/builder',
            agent_urls: { dashboard: 'https://example.com/dashboard' },
            last_message_preview: 'Reviewed the next release and its remaining work.',
          },
          {
            id: 'other',
            name: 'Other agent conversation',
            modified: 1795110000,
            messages: 2,
            agent_name: 'Builder',
            agent_path: '/other/builder',
          },
        ],
      };
    } else if (url.pathname === '/api/v2/user/settings') body = { providers_configured: ['mock'] };
    else if (url.pathname.startsWith('/api/v2/tasks')) body = { tasks: [] };
    else if (url.pathname.startsWith('/api/v2/models'))
      body = { models: [], recommended: [], default: 'mock/echo' };
    return route.fulfill({ headers, json: body });
  });
});

for (const theme of ['light', 'dark'] as const) {
  test(`agent overview navigation and identity (${theme})`, async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: theme });
    await page.goto('/agents');
    const card = page
      .getByRole('link', { name: 'Overview for Builder on Remote' })
      .filter({ hasText: '/agents/builder' });
    await expect(card).toBeVisible();
    await card.click();
    await expect(page).toHaveURL(/\/agents\/overview\?path=%2Fagents%2Fbuilder&server=two$/);
    await expect(
      page.getByRole('heading', { name: 'Builder', exact: true, level: 1 })
    ).toBeVisible();
    await expect(page.getByText('Server: Remote', { exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: /Remote planning/ })).toHaveAttribute(
      'href',
      '/chat/shared?server=two'
    );
    await expect(page.getByRole('heading', { name: 'Recent conversations' })).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`agent-overview-${theme}.png`),
      fullPage: true,
    });
    await testInfo.attach(`agent-overview-${theme}`, {
      path: testInfo.outputPath(`agent-overview-${theme}.png`),
      contentType: 'image/png',
    });

    // Reload proves the overview does not depend on selectedAgent$ in memory.
    await page.reload();
    await expect(page.getByText('Server: Remote', { exact: true })).toBeVisible();
    await page.getByRole('link', { name: 'View conversations' }).click();
    await expect(page).toHaveURL(/\/chat\?agentPath=%2Fagents%2Fbuilder&server=two$/);
    await expect(page.getByRole('heading', { name: 'Recent conversations' })).toHaveCount(0);
    const sidebar = page.getByRole('navigation', { name: 'Conversations' });
    await expect(sidebar.getByText('Remote planning', { exact: true })).toBeVisible();
    await expect(sidebar.getByText('Local planning', { exact: true })).toHaveCount(0);
    await expect(sidebar.getByText('Other agent conversation', { exact: true })).toHaveCount(0);
    await page.goBack();
    await expect(
      page.getByRole('heading', { name: 'Builder', exact: true, level: 1 })
    ).toBeVisible();
    await page.getByRole('link', { name: 'All agents' }).click();
    await expect(page).toHaveURL(/\/agents$/);
  });
}
