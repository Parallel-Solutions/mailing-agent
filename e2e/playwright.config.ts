import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.E2E_BASE_URL || 'http://web:9806';

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  timeout: 60_000,
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      maxDiffPixels: 120,
      animations: 'disabled',
    },
  },
  reporter: [
    ['list'],
    ['html', { outputFolder: '/artifacts/report', open: 'never' }],
    ['junit', { outputFile: '/artifacts/results/junit.xml' }],
  ],
  outputDir: '/artifacts/results',
  use: {
    baseURL,
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
    viewport: { width: 1280, height: 720 },
    colorScheme: 'light',
  },
  projects: [
    {
      name: 'setup',
      testMatch: /.*\.setup\.ts/,
    },
    {
      name: 'chromium',
      testIgnore: [/.*\.setup\.ts/, /.*\.live\.spec\.ts/],
      use: {
        ...devices['Desktop Chrome'],
        storageState: '/artifacts/auth/user.json',
        channel: undefined,
      },
      dependencies: ['setup'],
    },
    {
      name: 'firefox-smoke',
      testIgnore: [/.*\.setup\.ts/, /.*\.live\.spec\.ts/],
      grep: /@smoke/,
      use: {
        ...devices['Desktop Firefox'],
        storageState: '/artifacts/auth/user.json',
      },
      dependencies: ['setup'],
    },
    {
      name: 'webkit-smoke',
      testIgnore: [/.*\.setup\.ts/, /.*\.live\.spec\.ts/],
      grep: /@smoke/,
      use: {
        ...devices['Desktop Safari'],
        storageState: '/artifacts/auth/user.json',
      },
      dependencies: ['setup'],
    },
  ],
});
