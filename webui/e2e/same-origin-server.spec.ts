import { test, expect, type Page } from '@playwright/test';

const origin = 'http://self-hosted.gptme.test:5700';
const token = 'same-origin-test-token';

test.use({ storageState: { cookies: [], origins: [] } });

test.afterEach(async ({ page }) => {
  // Lazy route chunks can still be forwarded when assertions finish. Drain
  // those handlers before Playwright disposes their API responses at teardown.
  await page.unrouteAll({ behavior: 'wait' });
});

// Serve the real frontend under a non-loopback origin while emulating only
// the API. This exercises startup ordering, registry persistence, ApiProvider's
// authenticated probe and the actual (unmocked) token dialog together.
async function serveSameOrigin(page: Page, frontend: string) {
  let loopbackRequests = 0;
  await page.route('http://127.0.0.1:5700/**', async (route) => {
    loopbackRequests++;
    await route.abort();
  });
  await page.route(`${origin}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/v2') {
      await route.fulfill({
        json: { api_version: 2, message: 'gptme v2 API', provider_configured: true },
      });
    } else if (url.pathname.startsWith('/api/')) {
      if (route.request().headers()['authorization'] !== `Bearer ${token}`) {
        await route.fulfill({ status: 401, json: { error: 'Unauthorized' } });
      } else if (url.pathname === '/api/v2/conversations') {
        await route.fulfill({ json: { conversations: [], total: 0 } });
      } else {
        await route.fulfill({ json: {} });
      }
    } else {
      const response = await route.fetch({ url: `${frontend}${url.pathname}${url.search}` });
      await route.fulfill({ response });
    }
  });
  return () => loopbackRequests;
}

test('fresh same-origin browser asks for a token, connects, and reconnects after reload', async ({
  page,
  baseURL,
}) => {
  const loopbackRequests = await serveSameOrigin(page, baseURL!);
  await page.goto(origin);
  const dialog = page.getByRole('dialog');
  await expect(
    dialog.getByRole('heading', { name: 'Connect to self-hosted.gptme.test:5700' })
  ).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Welcome to gptme' })).toHaveCount(0);
  await expect(dialog.getByText('gptme-server token')).toBeVisible();
  await page.screenshot({ path: 'test-results/same-origin-token-prompt.png' });
  await dialog.getByLabel('Server token').fill(token);
  const connected = page.waitForResponse(
    (response) => response.url().includes('/api/v2/conversations') && response.status() === 200
  );
  await dialog.getByRole('button', { name: 'Connect', exact: true }).click();
  await connected;
  await expect(dialog).toHaveCount(0);

  const registry = await page.evaluate(() => JSON.parse(localStorage.getItem('gptme_servers')!));
  expect(registry.servers).toHaveLength(1);
  expect(registry.servers[0]).toMatchObject({
    name: 'self-hosted.gptme.test:5700',
    baseUrl: origin,
    authToken: token,
  });
  expect(loopbackRequests()).toBe(0);

  const reconnected = page.waitForResponse(
    (response) => response.url().includes('/api/v2/conversations') && response.status() === 200
  );
  await page.reload();
  await reconnected;
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(loopbackRequests()).toBe(0);
});

for (const fragment of [
  `baseUrl=${encodeURIComponent(origin)}&userToken=${token}`,
  `userToken=${token}`,
]) {
  test(`same-origin connection fragment ${fragment.split('=')[0]} reuses its token without a wizard or duplicate Local`, async ({
    page,
    baseURL,
  }) => {
    await serveSameOrigin(page, baseURL!);
    const connected = page.waitForResponse(
      (response) => response.url().includes('/api/v2/conversations') && response.status() === 200
    );
    await page.goto(`${origin}/#${fragment}`);
    await connected;
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect
      .poll(async () =>
        page.evaluate(() => {
          const stored = localStorage.getItem('gptme_servers');
          return stored ? JSON.parse(stored).servers : [];
        })
      )
      .toEqual([
        expect.objectContaining({
          baseUrl: origin,
          authToken: token,
          name: 'self-hosted.gptme.test:5700',
        }),
      ]);
  });
}

for (const tokenLink of [true, false]) {
  test(`saved remote selection ${tokenLink ? 'cannot receive a serving token-only link' : 'is preserved on an ordinary reload'}`, async ({
    page,
    baseURL,
  }) => {
    await serveSameOrigin(page, baseURL!);
    const remote = 'https://saved-remote.gptme.test';
    const remoteTokens: (string | undefined)[] = [];
    await page.route(`${remote}/**`, async (route) => {
      remoteTokens.push(route.request().headers()['authorization']);
      await route.fulfill({ json: { conversations: [], total: 0 } });
    });
    await page.addInitScript(
      ({ origin, remote }) => {
        localStorage.setItem('gptme-settings', JSON.stringify({ hasCompletedSetup: true }));
        localStorage.setItem(
          'gptme_servers',
          JSON.stringify({
            activeServerId: 'remote',
            connectedServerIds: ['remote'],
            servers: [
              {
                id: 'serving',
                name: 'Serving',
                baseUrl: origin,
                authToken: null,
                useAuthToken: false,
                isPreset: true,
                createdAt: 1,
                lastUsedAt: 1,
              },
              {
                id: 'remote',
                name: 'Remote',
                baseUrl: remote,
                authToken: 'remote-token',
                useAuthToken: true,
                createdAt: 1,
                lastUsedAt: 1,
              },
            ],
          })
        );
      },
      { origin, remote }
    );
    const connected = page.waitForResponse(
      (response) =>
        response.url().startsWith(`${tokenLink ? origin : remote}/api/v2/conversations`) &&
        response.status() === 200
    );
    await page.goto(`${origin}/${tokenLink ? `#userToken=${token}` : ''}`);
    await connected;
    await expect(page.getByRole('dialog')).toHaveCount(0);
    const registry = await page.evaluate(() => JSON.parse(localStorage.getItem('gptme_servers')!));
    expect(registry.activeServerId).toBe(tokenLink ? 'serving' : 'remote');
    expect(
      registry.servers.find((server: { id: string }) => server.id === 'remote').authToken
    ).toBe('remote-token');
    expect(
      registry.servers.find((server: { id: string }) => server.id === 'serving').authToken
    ).toBe(tokenLink ? token : null);
    if (!tokenLink) expect(registry.connectedServerIds).toEqual(['remote']);
    expect(remoteTokens).not.toContain(`Bearer ${token}`);
  });
}
