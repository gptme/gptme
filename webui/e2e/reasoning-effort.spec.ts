import { test, expect } from '@playwright/test';

for (const theme of ['light', 'dark']) {
  test(`composer effort is scoped, persistent and model-aware (${theme})`, async ({
    page,
  }, testInfo) => {
    const steps: Record<string, unknown>[] = [];
    await page.addInitScript((theme) => {
      localStorage.setItem('gptme-theme', theme);
    }, theme);
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
          default: 'openai/o3',
          recommended: [],
          models: [
            {
              id: 'openai/o3',
              provider: 'openai',
              model: 'o3',
              supports_reasoning: true,
              reasoning_effort_levels: ['low', 'medium', 'high'],
            },
            {
              id: 'moonshot/kimi-k3',
              provider: 'moonshot',
              model: 'kimi-k3',
              supports_reasoning: true,
              reasoning_effort_levels: ['low', 'high', 'max'],
            },
            {
              id: 'openai/gpt-4o',
              provider: 'openai',
              model: 'gpt-4o',
              supports_reasoning: false,
              reasoning_effort_levels: [],
            },
          ],
        });
      if (path.endsWith('/user/settings')) return reply({ providers_configured: ['openai'] });
      if (path.endsWith('/user')) return reply({ username: 'effort-test' });
      if (path.endsWith('/events'))
        return route.fulfill({
          headers: { ...headers, 'Content-Type': 'text/event-stream' },
          body: `data: ${JSON.stringify({ type: 'connected', session_id: 'effort-session' })}\n\n`,
        });
      if (path.endsWith('/step')) {
        steps.push(request.postDataJSON());
        return reply({ status: 'ok', session_id: 'effort-session' });
      }
      if (path.endsWith('/config')) return reply({ chat: { model: 'openai/o3' } });
      if (/\/conversations\/[^/]+$/.test(path)) {
        if (request.method() !== 'GET')
          return reply({ status: 'ok', session_id: 'effort-session' });
        return reply({
          id: 'effort-a',
          name: 'Effort test',
          logfile: 'effort-a',
          workspace: '.',
          branches: {},
          log: [],
        });
      }
      if (path.endsWith('/conversations')) return reply({ conversations: [] });
      return reply({ tasks: [], sessions: [], files: [], workspaces: [], agents: [] });
    });
    await page.goto('/chat/effort-a');
    await page.evaluate((theme) => {
      document.documentElement.classList.toggle('dark', theme === 'dark');
    }, theme);
    const effort = page.getByRole('combobox', { name: 'Reasoning effort' });
    await expect(effort).toBeEnabled();
    await expect(effort).toContainText('Default');
    await effort.click();
    await expect(page.getByRole('option', { name: 'max', exact: true })).toHaveCount(0);
    await page.getByRole('option', { name: 'medium', exact: true }).click();
    await expect(effort).toContainText('medium');
    await page.reload();
    await expect(effort).toContainText('medium');
    await page.evaluate((theme) => {
      document.documentElement.classList.toggle('dark', theme === 'dark');
    }, theme);
    await page.screenshot({ path: testInfo.outputPath(`effort-${theme}.png`), fullPage: true });
    const input = page.getByRole('textbox', { name: 'Chat message' });
    await input.fill('Use medium reasoning');
    await input.press('Enter');
    await expect.poll(() => steps.length).toBe(1);
    expect(steps[0].reasoning_effort).toBe('medium');

    // A separate conversation never picks up A's local draft choice.
    await page.goto('/chat/effort-b');
    await expect(effort).toContainText('Default');
    await page.goto('/chat/effort-a');
    await expect(effort).toContainText('medium');
    await page.getByTestId('model-selector').click();
    await page.getByRole('option', { name: /kimi-k3/ }).click();
    await expect(effort).toContainText('Default');
    await expect(page.getByRole('status').filter({ hasText: 'medium unsupported' })).toBeVisible();
    await effort.click();
    await expect(page.getByRole('option', { name: 'medium', exact: true })).toHaveCount(0);
    await page.getByRole('option', { name: 'max', exact: true }).click();
    await expect(effort).toContainText('max');
    await page.getByTestId('model-selector').click();
    await page.getByRole('option', { name: /gpt-4o/ }).click();
    await expect(effort).toBeDisabled();
    await expect(effort).toContainText('Default');
    await input.fill('No reasoning parameter for this model');
    await input.press('Enter');
    await expect.poll(() => steps.length).toBe(2);
    expect(steps[1]).toMatchObject({ model: 'openai/gpt-4o', reasoning_effort: '' });
  });
}

test('missing model catalog preserves saved effort rather than sending a reset', async ({
  page,
}) => {
  const steps: Record<string, unknown>[] = [];
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = {
      'Access-Control-Allow-Origin': request.headers()['origin'] ?? '*',
      'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': 'Authorization, Content-Type',
      'Access-Control-Allow-Methods': 'GET, POST, PUT, OPTIONS',
    };
    const reply = (json: unknown, status = 200) => route.fulfill({ headers, status, json });
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (path === '/api/v2') return reply({ api_version: 2, contract_revision: 5 });
    if (path.endsWith('/models')) return reply({ error: 'temporarily unavailable' }, 503);
    if (path.endsWith('/user/settings')) return reply({ providers_configured: ['openai'] });
    if (path.endsWith('/user')) return reply({ username: 'effort-test' });
    if (path.endsWith('/events'))
      return route.fulfill({
        headers: { ...headers, 'Content-Type': 'text/event-stream' },
        body: `data: ${JSON.stringify({ type: 'connected', session_id: 'effort-session' })}\n\n`,
      });
    if (path.endsWith('/config'))
      return reply({ chat: { model: 'openai/o3', reasoning_effort: 'high' } });
    if (path.endsWith('/step')) {
      steps.push(request.postDataJSON());
      return reply({ status: 'ok' });
    }
    if (/\/conversations\/[^/]+$/.test(path))
      return request.method() === 'GET'
        ? reply({
            id: 'effort-unavailable',
            logfile: 'effort-unavailable',
            name: 'Effort test',
            workspace: '.',
            branches: {},
            log: [],
          })
        : reply({ status: 'ok' });
    if (path.endsWith('/conversations')) return reply({ conversations: [] });
    return reply({ tasks: [], sessions: [], files: [], workspaces: [], agents: [] });
  });
  await page.goto('/chat/effort-unavailable');
  const effort = page.getByRole('combobox', { name: 'Reasoning effort' });
  await expect(effort).toContainText('high');
  await expect(effort).toBeDisabled();
  const input = page.getByRole('textbox', { name: 'Chat message' });
  await input.fill('Retain saved effort');
  await input.press('Enter');
  await expect.poll(() => steps.length).toBe(1);
  expect(steps[0].reasoning_effort).toBe('high');
});
