import { devices, expect, test } from '@playwright/test';

const conversationId = 'gallery-test';
const models = [
  {
    id: 'moonshot/kimi-k3',
    provider: 'moonshot',
    model: 'kimi-k3',
    context: 256_000,
    max_output: 32_000,
    supports_streaming: true,
    supports_vision: false,
    supports_reasoning: true,
    reasoning_effort_levels: ['low', 'high', 'max'],
    price_input: 0.6,
    price_output: 2.5,
  },
  {
    id: 'openai/gpt-4o',
    provider: 'openai',
    model: 'gpt-4o',
    context: 128_000,
    max_output: 16_384,
    supports_streaming: true,
    supports_vision: true,
    supports_reasoning: false,
    price_input: 2.5,
    price_output: 10,
  },
];

const appearances = [
  { theme: 'light', layout: 'desktop', device: devices['Desktop Chrome'] },
  { theme: 'dark', layout: 'mobile', device: devices['Pixel 7'] },
] as const;

for (const { theme, layout, device } of appearances) {
  test.describe(`${theme} ${layout}`, () => {
    test.use({
      viewport: device.viewport,
      deviceScaleFactor: device.deviceScaleFactor,
      isMobile: device.isMobile,
      hasTouch: device.hasTouch,
      userAgent: device.userAgent,
      colorScheme: theme,
    });

    test('browse, filter and select a model from the composer gallery', async ({
      page,
    }, testInfo) => {
      let favorites: string[] = [];
      let defaultModel = models[0].id;
      await page.addInitScript((theme) => localStorage.setItem('theme', theme), theme);
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
          return reply({ default: defaultModel, recommended: [models[0].id], favorites, models });
        if (path.endsWith('/user/favorites') && request.method() === 'POST') {
          favorites = request.postDataJSON().favorites;
          return reply({ favorites });
        }
        if (path.endsWith('/user/default-model') && request.method() === 'POST') {
          defaultModel = request.postDataJSON().model;
          return reply({ restart_required: false });
        }
        if (path.endsWith('/user/settings')) return reply({ providers_configured: ['openai'] });
        if (path.endsWith('/user')) return reply({ username: 'gallery-test' });
        if (path.endsWith('/events'))
          return route.fulfill({
            headers: { ...headers, 'Content-Type': 'text/event-stream' },
            body: `data: ${JSON.stringify({ type: 'connected', session_id: 'gallery-session' })}\n\n`,
          });
        if (path.endsWith('/config')) return reply({ chat: { model: models[0].id } });
        if (path.endsWith(`/conversations/${conversationId}`)) {
          if (request.method() !== 'GET') return reply({ status: 'ok' });
          return reply({
            id: conversationId,
            logfile: conversationId,
            name: 'Model gallery test',
            workspace: '.',
            branches: {},
            log: [],
          });
        }
        if (path.endsWith('/conversations')) return reply({ conversations: [] });
        return reply({ tasks: [], sessions: [], files: [], workspaces: [], agents: [] });
      });

      await page.goto(`/chat/${conversationId}`);
      const selector = page.getByTestId('model-selector');
      await expect(selector).toHaveAccessibleName('Model: kimi-k3');
      await selector.click();
      await expect(page.getByRole('option', { name: /gpt-4o/ })).toBeVisible();
      const browse = page.getByRole('button', { name: 'Browse all models', exact: true });
      await expect(browse).toBeVisible();
      await browse.click();

      const gallery = page.getByRole('dialog', { name: 'Model gallery', exact: true });
      const kimi = gallery.getByRole('button', { name: 'Select kimi-k3', exact: true });
      const gpt = gallery.getByRole('button', { name: 'Select gpt-4o', exact: true });
      await expect(gallery).toBeVisible();
      await expect(page.locator('html')).toHaveClass(new RegExp(`\\b${theme}\\b`));
      await expect(gallery.getByRole('searchbox', { name: 'Search model gallery' })).toBeFocused();
      if (layout === 'mobile') {
        // Layout dimensions ignore the Radix entry animation's transform.
        await expect(gallery).toHaveCSS('width', `${device.viewport.width}px`);
        await expect(gallery).toHaveCSS('height', `${device.viewport.height}px`);
      }
      await expect(kimi).toBeVisible();
      await expect(gpt).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath(`gallery-${theme}-${layout}.png`),
        animations: 'disabled',
      });

      await page.keyboard.press('Escape');
      await expect(gallery).toBeHidden();
      await expect(selector).toBeFocused();
      await expect(selector).toHaveAccessibleName('Model: kimi-k3');

      await selector.click();
      await browse.click();
      const available = gallery.getByRole('checkbox', { name: 'Available only' });
      await available.check();
      await expect(
        gallery.getByRole('button', { name: 'moonshot (0)', exact: true })
      ).toBeVisible();
      await available.uncheck();
      await expect(
        gallery.getByRole('button', { name: 'moonshot (1)', exact: true })
      ).toBeVisible();
      await gallery.getByRole('button', { name: 'openai (1)', exact: true }).click();
      await expect(kimi).toBeHidden();
      await expect(gpt).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath(`gallery-${theme}-${layout}-openai.png`),
        animations: 'disabled',
      });
      const star = gallery.getByRole('button', { name: 'Favorite gpt-4o' });
      await star.click();
      await expect(star).toHaveAttribute('aria-pressed', 'true');
      await expect.poll(() => favorites).toEqual(['openai/gpt-4o']);
      await gallery.getByRole('button', { name: 'Set gpt-4o as default' }).click();
      await expect(gallery.getByRole('button', { name: 'gpt-4o is the default' })).toBeDisabled();
      expect(defaultModel).toBe('openai/gpt-4o');

      await gpt.click();
      await expect(gallery).toBeHidden();
      await expect(selector).toHaveAccessibleName('Model: gpt-4o');
      await expect(selector).toBeFocused();
      await selector.click();
      await expect(page.getByRole('group', { name: 'Favorites' })).toContainText('gpt-4o');
      await expect(page.getByTestId('set-default-model')).toBeDisabled();
    });
  });
}
