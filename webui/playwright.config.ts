/* eslint-env node */

import { defineConfig, devices } from '@playwright/test';

const port = process.env.PORT || 5701;

export default defineConfig({
  testDir: './e2e',
  testIgnore: ['**/helpers/**'],
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: 'http://127.0.0.1:' + port,
    trace: 'on-first-retry',
    // Failing frame/geometry oracles (turn-stability.spec.ts) report chunk, phase and
    // text; the screenshot is the nearest visual evidence of the final page state.
    screenshot: 'only-on-failure',
    // Skip the first-run setup wizard in all E2E tests by default.
    // Tests that need to verify the wizard itself must override storageState
    // at the test level (e.g. `test.use({ storageState: { cookies: [], origins: [] } })`).
    storageState: {
      cookies: [],
      origins: [
        {
          origin: 'http://127.0.0.1:' + port,
          localStorage: [
            {
              name: 'gptme-settings',
              value: JSON.stringify({ hasCompletedSetup: true }),
            },
          ],
        },
      ],
    },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port}`,
    url: 'http://127.0.0.1:' + port,
    reuseExistingServer: !process.env.CI,
  },
});
