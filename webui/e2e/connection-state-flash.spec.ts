import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * Connection-state flash regression (reported on gptme.ai, 2026-09-30).
 *
 * On a normal load, and whenever the client reconnected (gptme.ai swaps in a
 * fresh API client on every hourly session-token refresh), the webui briefly
 * rendered its *disconnected* UI before the connection probe finished:
 *   - "Not connected to API. Use the connect button to load conversations."
 *     in the conversation list,
 *   - the amber "Cannot reach … / No gptme server connected" alert on the
 *     new-chat view,
 *   - the "Server not connected" banner in an open conversation,
 *   - "Connect to gptme to send messages" in the chat input.
 * They vanished within seconds, i.e. "not connected *yet*" was rendered as
 * "unreachable".
 *
 * The existing UI-stability tests never caught this: they only cover the model
 * selector, scroll anchoring and spinner count on demo conversations, and the
 * e2e server answers the connection probe in milliseconds, so the window was
 * too short to observe. The managed service goes through the fleet proxy and
 * auth, where the probe takes ~1-3 s. These tests delay the probe the same way
 * and record every DOM mutation, failing if a disconnected placeholder is
 * rendered at ANY point — not just at the end.
 */

const PROBE_DELAY_MS = 1500;
const CONV_ID = 'flash-regression-conv';

const DISCONNECTED_TEXTS = [
  'Not connected to API',
  'Server not connected',
  'Cannot reach',
  'No gptme server connected',
  'Local gptme server needs browser access',
  'Connect to gptme to send messages',
];

declare global {
  interface Window {
    __connectionFlashes: { text: string; at: number }[];
    __conversationDropped: number;
  }
}

async function recordDisconnectedFlashes(page: Page): Promise<void> {
  await page.addInitScript(
    ({ texts, convId }) => {
      window.__connectionFlashes = [];
      window.__conversationDropped = 0;
      let conversationShown = false;
      const check = () => {
        const body = document.body?.innerText ?? '';
        for (const text of texts) {
          if (body.includes(text)) {
            window.__connectionFlashes.push({ text, at: Math.round(performance.now()) });
          }
        }
        // Last-known-good: once the conversation is listed, a reconnect must
        // not blank the list and refill it.
        const listed = body.includes(convId);
        if (listed) conversationShown = true;
        else if (conversationShown) window.__conversationDropped += 1;
      };
      new MutationObserver(check).observe(document, {
        subtree: true,
        childList: true,
        characterData: true,
        attributes: true,
      });
    },
    { texts: DISCONNECTED_TEXTS, convId: CONV_ID }
  );
}

async function mockSlowServer(page: Page): Promise<{ probes: () => number }> {
  let probeCount = 0;
  const cors = (origin?: string) => ({
    'Access-Control-Allow-Origin': origin ?? '*',
    'Access-Control-Allow-Credentials': 'true',
    'Access-Control-Allow-Headers': 'Authorization, Content-Type',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
  });

  // Catch-all first (lowest priority, Playwright matches routes LIFO).
  await page.route(/127\.0\.0\.1:5700\//, (route) => {
    const origin = route.request().headers()['origin'];
    if (route.request().method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: cors(origin) });
    }
    return route.fulfill({ status: 404, headers: cors(origin), json: { error: 'not mocked' } });
  });

  const json = (body: unknown) => (route: Route) => {
    if (route.request().method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: cors(route.request().headers()['origin']) });
    }
    return route.fulfill({ headers: cors(route.request().headers()['origin']), json: body });
  };

  await page.route(
    '**/api/v2/models**',
    json({ default: 'mock/echo', models: [], recommended: [] })
  );
  await page.route('**/api/v2/user**', json({ username: 'e2e' }));
  await page.route('**/api/v2/user/settings**', json({ providers_configured: ['mock'] }));
  await page.route('**/api/v2/tasks**', json({ tasks: [] }));
  await page.route('**/api/v2/external-sessions**', json({ sessions: [] }));
  await page.route('**/api/v2/auth/cookie', json({ status: 'ok' }));
  await page.route(
    (url) => ['/api/v2/conversations', '/api/v2/conversations/'].includes(new URL(url).pathname),
    json({
      conversations: [
        {
          id: CONV_ID,
          name: CONV_ID,
          modified: Math.floor(Date.now() / 1000),
          message_count: 1,
        },
      ],
    })
  );

  // The connection probe: slow, like the managed service behind the fleet proxy.
  await page.route(
    (url) => new URL(url).pathname === '/api/v2',
    async (route) => {
      if (route.request().method() === 'OPTIONS') {
        return route.fulfill({ status: 204, headers: cors(route.request().headers()['origin']) });
      }
      probeCount += 1;
      await new Promise((resolve) => setTimeout(resolve, PROBE_DELAY_MS));
      return route.fulfill({
        headers: cors(route.request().headers()['origin']),
        json: { api_version: 2, contract_revision: 5 },
      });
    }
  );

  return { probes: () => probeCount };
}

async function expectNoFlashes(page: Page): Promise<void> {
  const flashes = await page.evaluate(() => window.__connectionFlashes);
  expect(flashes, `disconnected UI rendered: ${JSON.stringify(flashes.slice(0, 5))}`).toEqual([]);
}

test.describe('Connection state: no disconnected flash', () => {
  test('normal load against a slow server never renders disconnected placeholders', async ({
    page,
  }) => {
    await recordDisconnectedFlashes(page);
    const server = await mockSlowServer(page);

    await page.goto('/chat');
    // Settled: the probe finished and the conversation list loaded.
    await expect(page.getByText(CONV_ID).first()).toBeVisible({ timeout: 15_000 });
    expect(server.probes()).toBeGreaterThan(0);
    await expect(page.getByPlaceholder("What's on your mind...")).toBeVisible();

    await expectNoFlashes(page);
  });

  test('reconnect after a credential change keeps last-known-good UI (no flash)', async ({
    page,
  }) => {
    await recordDisconnectedFlashes(page);
    const server = await mockSlowServer(page);

    await page.goto('/chat');
    await expect(page.getByText(CONV_ID).first()).toBeVisible({ timeout: 15_000 });
    const probesBefore = server.probes();

    // What gptme.ai does on every session-token refresh: the active server's
    // token changes, so the client pool swaps in a fresh, not-yet-connected
    // client. Drive it through the registry's cross-tab sync path.
    await page.evaluate(() => {
      const key = 'gptme_servers';
      const registry = JSON.parse(localStorage.getItem(key) ?? '{}');
      registry.servers = (registry.servers ?? []).map((server: Record<string, unknown>) =>
        server.id === registry.activeServerId
          ? { ...server, authToken: `refreshed-${Date.now()}`, useAuthToken: true }
          : server
      );
      const newValue = JSON.stringify(registry);
      localStorage.setItem(key, newValue);
      window.dispatchEvent(new StorageEvent('storage', { key, newValue }));
    });

    // Give the swapped client time to be probed (slowly) and reconnect, then
    // check nothing flashed disconnected or blanked the list meanwhile.
    await page.waitForTimeout(PROBE_DELAY_MS * 2);
    await expectNoFlashes(page);
    expect(await page.evaluate(() => window.__conversationDropped)).toBe(0);
    // ...and it really did reconnect with the new credential.
    expect(server.probes()).toBeGreaterThan(probesBefore);
    await expect(page.getByPlaceholder("What's on your mind...")).toBeVisible({
      timeout: 15_000,
    });
  });
});
