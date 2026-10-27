import { test, expect } from '@playwright/test';

for (const kind of ['existing', 'new']) {
  test(`rejected upload preserves the draft for deliberate retry (${kind})`, async ({ page }) => {
    let rejectUpload = true;
    const messages: { content: string; files?: string[] }[] = [];
    let steps = 0;
    let releasePost!: () => void;
    const postGate = new Promise<void>((resolve) => {
      releasePost = resolve;
    });
    await page.route('**/api/**', async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      const headers = {
        'Access-Control-Allow-Origin': request.headers()['origin'] ?? '*',
        'Access-Control-Allow-Credentials': 'true',
        'Access-Control-Allow-Headers': 'Authorization, Content-Type',
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
      };
      const reply = (json: unknown, status = 200) => route.fulfill({ headers, status, json });
      if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
      if (path === '/api/v2') return reply({ api_version: 2, contract_revision: 5 });
      if (path.endsWith('/models'))
        return reply({ default: 'mock/echo', models: [], recommended: [] });
      if (path.endsWith('/user/settings')) return reply({ providers_configured: ['mock'] });
      if (path.endsWith('/user')) return reply({ username: 'upload-test' });
      if (path.endsWith('/events'))
        return route.fulfill({
          headers: { ...headers, 'Content-Type': 'text/event-stream' },
          body: `data: ${JSON.stringify({ type: 'connected', session_id: 'upload-session' })}\n\n`,
        });
      if (path.endsWith('/workspace/upload'))
        return rejectUpload
          ? reply({ error: 'File too large' }, 413)
          : reply({ files: [{ name: 'receipt.txt', path: 'attachments/receipt.txt' }] });
      if (path.endsWith('/step')) {
        steps++;
        return reply({ status: 'ok', session_id: 'upload-session' });
      }
      if (/\/conversations\/[^/]+$/.test(path)) {
        if (request.method() === 'POST') {
          messages.push(request.postDataJSON());
          if (kind === 'new') await postGate;
          return reply({ status: 'ok' });
        }
        if (request.method() === 'PUT')
          return reply({ status: 'ok', session_id: 'upload-session' });
        return reply({
          id: 'upload-browser',
          name: 'Upload regression',
          logfile: 'upload-browser',
          workspace: '.',
          branches: {},
          log: [],
        });
      }
      if (path.endsWith('/config')) return reply({ chat: { model: 'mock/echo' } });
      if (path.endsWith('/conversations')) return reply({ conversations: [] });
      return reply({ tasks: [], sessions: [], files: [], workspaces: [], agents: [] });
    });
    await page.goto(kind === 'existing' ? '/chat/upload-browser' : '/');
    const input = page.getByRole('textbox', { name: 'Chat message' });
    await expect(input).toBeEnabled();
    await input.fill('Keep this exact draft and attachment');
    await page.locator('input[type=file][aria-label="Attach files"]').setInputFiles({
      name: 'receipt.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('original attachment'),
    });
    await input.press('Enter');
    await expect(
      page.getByText('Check the attached files and try Send again.', { exact: true })
    ).toBeVisible();
    await expect(input).toHaveValue('Keep this exact draft and attachment');
    await expect(page.getByRole('button', { name: 'Remove file receipt.txt' })).toBeVisible();
    expect(messages).toHaveLength(0);
    expect(steps).toBe(0);
    await page.screenshot({ path: `test-results/upload-rejection-${kind}.png`, fullPage: true });
    rejectUpload = false;
    await input.press('Enter');
    await expect.poll(() => messages.length).toBe(1);
    if (kind === 'new') {
      await expect(input).toBeDisabled();
      await expect(input).toHaveValue('Keep this exact draft and attachment');
      await expect(page.getByRole('button', { name: 'Remove file receipt.txt' })).toBeDisabled();
      await expect(page.locator('input[type=file][aria-label="Attach files"]')).toBeDisabled();
      await expect(page).toHaveURL(/\/$/);
      await page.screenshot({ path: 'test-results/upload-post-pending-new.png', fullPage: true });
      releasePost();
      await expect(page).toHaveURL(/\/chat\//);
    }
    await expect.poll(() => steps).toBe(1);
    expect(messages[0]).toMatchObject({
      content: 'Keep this exact draft and attachment',
      files: ['attachments/receipt.txt'],
    });
  });
}
