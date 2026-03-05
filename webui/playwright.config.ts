/* eslint-env node */

import { defineConfig, devices } from '@playwright/test';

const port = process.env.PORT || 5701;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:' + port,
    trace: 'on-first-retry',
    // Skip the first-run setup wizard in all E2E tests by default.
    // Tests that need to verify the wizard itself must override storageState
    // at the test level (e.g. `test.use({ storageState: { cookies: [], origins: [] } })`).
    storageState: {
      cookies: [],
      origins: [
        {
          origin: 'http://localhost:' + port,
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
    command: 'npm run dev',
    url: 'http://localhost:' + port,
    reuseExistingServer: !process.env.CI,
  },
});
