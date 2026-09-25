/**
 * E2E tests for the tool confirmation flow (InlineToolConfirmation).
 *
 * These tests use page.route() to inject tool_pending SSE events into
 * the conversation view, bypassing the LLM entirely. No live gptme-server
 * is required — the tests mock the minimum API surface needed to render
 * a conversation and receive SSE events.
 *
 * Acceptance criteria covered:
 *   1.1 Confirmation panel appears when tool_pending SSE event is received
 *   1.2 Clicking Execute sends POST .../tool/confirm with action=confirm
 *   1.3 Clicking Skip sends POST .../tool/confirm with action=skip
 *
 * Related:
 *   - webui/src/components/InlineToolConfirmation.tsx — the component
 *   - gptme/hooks/server_confirm.py — the server-side hook
 *   - gptme/server/api_v2_sessions.py — /tool/confirm endpoint
 */

import { test, expect, type Page } from '@playwright/test';

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const CONV_ID = 'e2e-tool-confirm-test';
const SESSION_ID = 'sess-e2e-tool-confirm-1';
const TOOL_ID = 'tool-e2e-1';

const PENDING_TOOLUSE = {
  tool: 'shell',
  args: [],
  content: 'echo "hello from e2e test"',
};

const TOOL_CONFIRM_TIMEOUT = 15_000;

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Encode a list of objects as SSE event bodies. */
function buildSseBody(events: Record<string, unknown>[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
}

/**
 * Set up route mocks for the minimum API surface needed to render a
 * conversation view and receive SSE events.
 *
 * After calling this, navigate to `/chat/${conversationId}` and the webui
 * will:
 *  - Think it is connected to a server (via the mocked /api/v2 endpoint)
 *  - Load an empty conversation (via /api/v2/conversations/:id)
 *  - Receive a `connected` + `tool_pending` event from the SSE stream
 *
 * Reconnection attempts after the initial stream closes get only a
 * `connected` event so the test doesn't re-trigger the confirmation panel.
 */
async function setupMocks(
  page: Page,
  conversationId: string,
  tooluse = PENDING_TOOLUSE
): Promise<void> {
  let sseCallCount = 0;

  // Broad catch-all for the API server: return 404 for any endpoint we haven't
  // specifically mocked.  Added FIRST so it has lowest priority (Playwright LIFO).
  // Include CORS header so the browser doesn't flag it as a CORS violation.
  await page.route(/127\.0\.0\.1:5700\/api\//, (route) =>
    route.fulfill({
      status: 404,
      headers: { 'Access-Control-Allow-Origin': '*' },
      json: { error: 'not mocked' },
    })
  );

  // /api/v2 — version handshake + auth probe target
  await page.route('**/api/v2', (route) =>
    route.fulfill({ json: { api_version: 2, contract_revision: 5 } })
  );

  // /api/v2/models — connection quality check. Must return ModelInfo objects
  // (id, provider, …), not strings — useModels / ModelPicker read those fields.
  await page.route('**/api/v2/models**', (route) =>
    route.fulfill({
      json: {
        default: 'mock/echo',
        models: [
          {
            id: 'mock/echo',
            provider: 'mock',
            model: 'echo',
            context: 8192,
            supports_streaming: true,
            supports_vision: false,
            supports_reasoning: false,
            price_input: 0,
            price_output: 0,
          },
        ],
        recommended: [],
      },
    })
  );

  // /api/v2/user — general user endpoint (must come before /user/settings so
  // the more-specific route registered after it wins in Playwright LIFO order).
  await page.route('**/api/v2/user**', (route) =>
    route.fulfill({ json: { username: 'e2e', email: 'e2e@test.local' } })
  );

  // /api/v2/user/settings — must return providers_configured as string[] or
  // useSpeechToText calls .includes() on undefined (UserSettings interface).
  // Registered AFTER /api/v2/user** so it takes priority (Playwright LIFO).
  await page.route('**/api/v2/user/settings**', (route) =>
    route.fulfill({
      json: { providers_configured: [], default_model: null },
    })
  );

  // /api/v2/tasks — wrapped task list (code does data.tasks)
  await page.route('**/api/v2/tasks**', (route) => route.fulfill({ json: { tasks: [] } }));

  // /api/v2/external-sessions — wrapped sessions list (code does resp.sessions)
  await page.route('**/api/v2/external-sessions**', (route) =>
    route.fulfill({ json: { sessions: [] } })
  );

  // /api/v2/conversations (list) — return one entry so the sidebar has something.
  // Use a function predicate to match ONLY the list endpoint (no sub-path).
  await page.route(
    (url) => {
      const path = new URL(url).pathname;
      return path === '/api/v2/conversations' || path === '/api/v2/conversations/';
    },
    (route) =>
      route.fulfill({
        json: {
          conversations: [
            {
              id: conversationId,
              name: conversationId,
              modified: Math.floor(Date.now() / 1000),
              message_count: 0,
            },
          ],
        },
      })
  );

  // /api/v2/conversations/:id — empty conversation data (GET only, base path).
  // Added before sub-path routes so those take priority (Playwright LIFO).
  await page.route(
    (url) => {
      const path = new URL(url).pathname;
      return path === `/api/v2/conversations/${conversationId}`;
    },
    (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          json: {
            id: conversationId,
            name: conversationId,
            log: [],
            logfile: conversationId,
            branches: {},
            workspace: '.',
          },
        });
      }
      return route.continue();
    }
  );

  // /api/v2/conversations/:id/config
  await page.route(`**/api/v2/conversations/${conversationId}/config`, (route) =>
    route.fulfill({ json: { model: 'mock/echo', stream: true } })
  );

  // /api/v2/conversations/:id/events — SSE stream
  //   First call: connected + tool_pending (triggers the confirmation UI)
  //   Subsequent reconnects: connected only (prevents re-triggering)
  await page.route(`**/api/v2/conversations/${conversationId}/events**`, (route) => {
    sseCallCount += 1;
    const events: Record<string, unknown>[] =
      sseCallCount === 1
        ? [
            { type: 'connected', session_id: SESSION_ID },
            {
              type: 'tool_pending',
              tool_id: TOOL_ID,
              tooluse,
              auto_confirm: false,
            },
          ]
        : [{ type: 'connected', session_id: SESSION_ID }];

    // EventSource is created with withCredentials: true, so CORS requires the
    // exact origin echo (not '*') plus Allow-Credentials.
    const origin = route.request().headers()['origin'] ?? '*';
    return route.fulfill({
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Access-Control-Allow-Origin': origin,
        'Access-Control-Allow-Credentials': 'true',
      },
      body: buildSseBody(events),
    });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

test.describe('Tool Confirmation Flow (InlineToolConfirmation)', () => {
  /**
   * 1.1 — panel appears on tool_pending event
   *
   * Verifies that the InlineToolConfirmation panel is rendered when the SSE
   * stream emits a tool_pending event: tool name, code preview, and the
   * Execute / Skip buttons must all be visible.
   */
  test('1.1: confirmation panel appears on tool_pending SSE event', async ({ page }) => {
    await setupMocks(page, CONV_ID);

    // Absorb tool/confirm POSTs so the webui doesn't log 404s.
    await page.route(`**/api/v2/conversations/${CONV_ID}/tool/confirm`, (route) =>
      route.fulfill({ json: { status: 'ok' }, headers: { 'Access-Control-Allow-Origin': '*' } })
    );

    await page.goto(`/chat/${CONV_ID}`);

    // Target the panel's heading instead of a bare 'shell' substring, which
    // could match unrelated UI text and pass before the panel renders.
    await expect(page.getByRole('heading', { name: 'Tool Execution Confirmation' })).toBeVisible({
      timeout: TOOL_CONFIRM_TIMEOUT,
    });

    // Code preview should contain the command.
    await expect(page.getByText(PENDING_TOOLUSE.content, { exact: false })).toBeVisible({
      timeout: TOOL_CONFIRM_TIMEOUT,
    });

    // Both primary action buttons must be present.
    await expect(page.getByRole('button', { name: /execute/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /skip/i })).toBeVisible();
  });

  /**
   * 1.2 — Execute button POSTs action=confirm
   *
   * Clicks Execute and verifies the webui sent POST .../tool/confirm with
   * { action: 'confirm', tool_id: TOOL_ID, session_id: SESSION_ID }.
   */
  test('1.2: Execute sends POST with action=confirm', async ({ page }) => {
    await setupMocks(page, CONV_ID);

    const confirmRequests: { method: string; body: Record<string, unknown> }[] = [];
    await page.route(`**/api/v2/conversations/${CONV_ID}/tool/confirm`, async (route) => {
      const request = route.request();
      // The webui dev server (5701) calls the API (5700) cross-origin, so a
      // JSON POST triggers a CORS preflight OPTIONS. Answer it with CORS
      // headers and keep it out of the recorded requests.
      if (request.method() === 'OPTIONS') {
        await route.fulfill({
          status: 204,
          headers: {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
            'Access-Control-Max-Age': '600',
          },
        });
        return;
      }
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(request.postData() ?? '{}');
      } catch {
        // ignore parse errors
      }
      confirmRequests.push({ method: request.method(), body });
      await route.fulfill({
        json: { status: 'ok', message: 'Tool confirmed' },
        headers: { 'Access-Control-Allow-Origin': '*' },
      });
    });

    await page.goto(`/chat/${CONV_ID}`);

    const executeBtn = page.getByRole('button', { name: /execute/i });
    await expect(executeBtn).toBeVisible({ timeout: TOOL_CONFIRM_TIMEOUT });
    await executeBtn.click();

    // Wait for the POST to arrive.
    await expect.poll(() => confirmRequests.length, { timeout: 5_000 }).toBeGreaterThan(0);

    expect(confirmRequests[0].method).toBe('POST');
    expect(confirmRequests[0].body).toMatchObject({
      action: 'confirm',
      tool_id: TOOL_ID,
      session_id: SESSION_ID,
    });
  });

  /**
   * 1.3 — Skip button POSTs action=skip
   *
   * Clicks Skip and verifies the webui sent POST .../tool/confirm with
   * { action: 'skip', tool_id: TOOL_ID, session_id: SESSION_ID }.
   */
  test('1.3: Skip sends POST with action=skip', async ({ page }) => {
    await setupMocks(page, CONV_ID);

    const confirmRequests: { method: string; body: Record<string, unknown> }[] = [];
    await page.route(`**/api/v2/conversations/${CONV_ID}/tool/confirm`, async (route) => {
      const request = route.request();
      // CORS preflight handling — same rationale as test 1.2.
      if (request.method() === 'OPTIONS') {
        await route.fulfill({
          status: 204,
          headers: {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
            'Access-Control-Max-Age': '600',
          },
        });
        return;
      }
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(request.postData() ?? '{}');
      } catch {
        // ignore parse errors
      }
      confirmRequests.push({ method: request.method(), body });
      await route.fulfill({
        json: { status: 'ok', message: 'Tool skipped' },
        headers: { 'Access-Control-Allow-Origin': '*' },
      });
    });

    await page.goto(`/chat/${CONV_ID}`);

    const skipBtn = page.getByRole('button', { name: /skip/i });
    await expect(skipBtn).toBeVisible({ timeout: TOOL_CONFIRM_TIMEOUT });
    await skipBtn.click();

    // Wait for the POST to arrive.
    await expect.poll(() => confirmRequests.length, { timeout: 5_000 }).toBeGreaterThan(0);

    expect(confirmRequests[0].method).toBe('POST');
    expect(confirmRequests[0].body).toMatchObject({
      action: 'skip',
      tool_id: TOOL_ID,
      session_id: SESSION_ID,
    });
  });
});
