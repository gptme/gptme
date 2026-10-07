import { test, expect } from '@playwright/test';

test('global MCP settings save and reload through the shared server API', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem(
      'gptme_servers',
      JSON.stringify({
        servers: [
          {
            id: 'test',
            name: 'Test',
            baseUrl: 'http://127.0.0.1:5700',
            authToken: null,
            useAuthToken: false,
            isPreset: false,
            createdAt: 1,
            lastUsedAt: 1,
          },
        ],
        activeServerId: 'test',
        connectedServerIds: ['test'],
      })
    );
  });
  let config = {
    enabled: true,
    auto_start: false,
    path: '/tmp/test/config.toml',
    servers: [
      {
        name: 'filesystem',
        enabled: true,
        command: 'npx',
        args: ['-y', '@modelcontextprotocol/server-filesystem', '/tmp'],
        env: {},
        url: '',
        headers: {},
      },
    ],
  };
  await page.route('**/api/v2**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const headers = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': '*',
      'Access-Control-Allow-Methods': '*',
    };
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (pathname === '/api/v2/user/config/mcp') {
      if (route.request().method() === 'PUT')
        config = { ...config, ...route.request().postDataJSON() };
      return route.fulfill({ headers, json: config });
    }
    const data = pathname.endsWith('/models')
      ? { models: [], recommended: [] }
      : pathname.endsWith('/tools')
        ? { tools: [] }
        : pathname.endsWith('/settings')
          ? { providers_configured: [] }
          : pathname.endsWith('/conversations')
            ? { conversations: [] }
            : pathname.endsWith('/tasks')
              ? { tasks: [] }
              : pathname.endsWith('/external-sessions')
                ? { sessions: [] }
                : { api_version: 2, contract_revision: 5 };
    return route.fulfill({ headers, json: data });
  });
  await page.goto('/settings');
  await page.getByRole('button', { name: 'Servers Manage server connections' }).click();
  await page.getByRole('button', { name: 'Global MCP settings', exact: true }).click();
  await expect(page.getByText('/tmp/test/config.toml')).toBeVisible();
  await page.getByRole('button', { name: /MCP Servers.*Add or remove/ }).click();
  await expect(page.getByLabel('Server Name')).toHaveValue('filesystem');
  await page.screenshot({ path: 'test-results/global-mcp-settings.png', fullPage: true });
  await page.getByRole('switch', { name: 'Auto-Start MCP Servers' }).click();
  await page.getByRole('button', { name: 'Save global MCP settings' }).click();
  await expect(page.getByText('Global MCP settings saved.', { exact: false })).toBeVisible();
  expect(config.auto_start).toBe(true);
  await page.reload();
  await page.getByRole('button', { name: 'Servers Manage server connections' }).click();
  await page.getByRole('button', { name: 'Global MCP settings', exact: true }).click();
  await expect(page.getByRole('switch', { name: 'Auto-Start MCP Servers' })).toBeChecked();
});
