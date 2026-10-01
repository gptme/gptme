import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * Event-stream self-heal (gptme-cloud#1063 follow-up, reported 2026-09-30).
 *
 * A gptme.ai tab left open through the SSE outage burned its 5 fast reconnect
 * attempts, never received the `connected` event (the only source of the
 * session id), and then every send failed with "Session ID not found for
 * conversation" until a manual reload — even after the server was fixed.
 *
 * This test kills the event stream past the whole reconnect budget, restores
 * it, and sends a message: it must go through on the same page, with the
 * session id from the re-opened stream, and never surface the session error.
 */

const CONV_ID = 'e2e-session-self-heal';
const SESSION_ID = 'sess-e2e-self-heal';

const cors = (route: Route) => ({
  'Access-Control-Allow-Origin': route.request().headers()['origin'] ?? '*',
  'Access-Control-Allow-Credentials': 'true',
  'Access-Control-Allow-Headers': 'Authorization, Content-Type',
  'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
});

const json = (body: unknown) => (route: Route) =>
  route.request().method() === 'OPTIONS'
    ? route.fulfill({ status: 204, headers: cors(route) })
    : route.fulfill({ headers: cors(route), json: body });

async function mockServer(page: Page) {
  const state = {
    streamDown: true,
    streamAttempts: 0,
    stepBodies: [] as { session_id?: string }[],
  };

  await page.route(/127\.0\.0\.1:5700\//, (route) =>
    route.request().method() === 'OPTIONS'
      ? route.fulfill({ status: 204, headers: cors(route) })
      : route.fulfill({ status: 404, headers: cors(route), json: { error: 'not mocked' } })
  );
  await page.route(
    (url) => new URL(url).pathname === '/api/v2',
    json({ api_version: 2, contract_revision: 5 })
  );
  await page.route(
    '**/api/v2/models**',
    json({ default: 'mock/echo', models: [], recommended: [] })
  );
  await page.route('**/api/v2/user**', json({ username: 'e2e' }));
  await page.route('**/api/v2/user/settings**', json({ providers_configured: ['mock'] }));
  await page.route('**/api/v2/tasks**', json({ tasks: [] }));
  await page.route('**/api/v2/external-sessions**', json({ sessions: [] }));
  await page.route(
    (url) => ['/api/v2/conversations', '/api/v2/conversations/'].includes(new URL(url).pathname),
    json({
      conversations: [
        { id: CONV_ID, name: CONV_ID, modified: Math.floor(Date.now() / 1000), message_count: 2 },
      ],
    })
  );
  await page.route(
    (url) => new URL(url).pathname === `/api/v2/conversations/${CONV_ID}`,
    (route) => {
      if (route.request().method() === 'OPTIONS') {
        return route.fulfill({ status: 204, headers: cors(route) });
      }
      if (route.request().method() === 'POST') {
        // sendMessage
        return route.fulfill({ headers: cors(route), json: { status: 'ok' } });
      }
      return route.fulfill({
        headers: cors(route),
        json: {
          id: CONV_ID,
          name: CONV_ID,
          log: [
            { role: 'user', content: 'first message', timestamp: '2026-09-30T08:00:00Z' },
            { role: 'assistant', content: 'first reply', timestamp: '2026-09-30T08:00:01Z' },
          ],
          logfile: CONV_ID,
          branches: {},
          workspace: '.',
        },
      });
    }
  );
  await page.route(
    `**/api/v2/conversations/${CONV_ID}/config`,
    json({ chat: { model: 'mock/echo', stream: true } })
  );
  await page.route(`**/api/v2/conversations/${CONV_ID}/step`, (route) => {
    if (route.request().method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: cors(route) });
    }
    const body = route.request().postDataJSON() as { session_id?: string };
    state.stepBodies.push(body);
    if (body.session_id !== SESSION_ID) {
      return route.fulfill({
        status: 404,
        headers: cors(route),
        json: { error: `Session not found: ${body.session_id}` },
      });
    }
    return route.fulfill({
      headers: cors(route),
      json: { status: 'ok', message: 'Step started', session_id: SESSION_ID },
    });
  });
  await page.route(`**/api/v2/conversations/${CONV_ID}/events**`, (route) => {
    state.streamAttempts += 1;
    if (state.streamDown) {
      // What the browser saw during the outage: the proxy rejecting the stream.
      return route.fulfill({
        status: 401,
        headers: cors(route),
        body: 'Authorization token required in query parameter for SSE connections',
      });
    }
    return route.fulfill({
      status: 200,
      headers: {
        ...cors(route),
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
      },
      body: `data: ${JSON.stringify({ type: 'connected', session_id: SESSION_ID })}\n\n`,
    });
  });
  return state;
}

test('a send after the stream exhausted its reconnect budget heals the stream instead of failing', async ({
  page,
}) => {
  await page.clock.install();
  const server = await mockServer(page);

  await page.goto(`/chat/${CONV_ID}`);
  await expect(page.getByText('first reply')).toBeVisible({ timeout: 15_000 });

  // Burn through the whole fast reconnect budget (1+2+4+8+16 s back-off plus
  // the 5 s session timeouts) while the stream is down.
  for (let i = 0; i < 12; i++) {
    await page.clock.fastForward(5_000);
  }
  const attemptsWhileDown = server.streamAttempts;
  expect(attemptsWhileDown).toBeGreaterThanOrEqual(6);

  // The server recovers. No reload.
  server.streamDown = false;

  const input = page.getByRole('textbox').last();
  await input.fill('second message after the outage');
  await input.press('Enter');

  // The send re-opened the stream, got the session id, and stepped with it.
  await expect
    .poll(() => server.stepBodies.map((b) => b.session_id), { timeout: 15_000 })
    .toContain(SESSION_ID);
  await expect(page.getByText('second message after the outage')).toBeVisible();
  await expect(page.getByText(/Session ID not found/i)).toHaveCount(0);
  expect(server.streamAttempts).toBeGreaterThan(attemptsWhileDown);
});
