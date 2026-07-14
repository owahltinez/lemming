import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './src/lemming/web',
  testMatch: /.*\.spec\.js/,
  use: {
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      testIgnore: /screenshots\.spec\.js/,
    },
    // Regenerates docs/screenshots; run via `npm run screenshots`.
    {
      name: 'screenshots',
      use: { ...devices['Desktop Chrome'] },
      testMatch: /screenshots\.spec\.js/,
    },
  ],
});
