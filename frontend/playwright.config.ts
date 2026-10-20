import { existsSync } from 'fs';
import { defineConfig, devices } from '@playwright/test';

const python = process.env.E2E_PYTHON || (existsSync('../.venv/bin/python') ? '../.venv/bin/python' : 'python3');

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  workers: 2,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:18000',
    launchOptions: { slowMo: Number(process.env.E2E_SLOW_MO || 0) },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', testMatch: ['labs.spec.ts', 'dashboards.spec.ts'], use: { ...devices['Desktop Chrome'] } },
    { name: 'live', testMatch: ['labs.live.spec.ts', 'dashboards.live.spec.ts'], use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: `${python} ../manage.py migrate --noinput && ${python} ../manage.py runserver 127.0.0.1:18000 --noreload`,
    url: 'http://127.0.0.1:18000',
    reuseExistingServer: false,
    env: {
      FLOCI_DASHBOARD_DB: '/tmp/flocidashboard-playwright.sqlite3',
      FLOCI_AWS_ENDPOINT_URL: process.env.E2E_FLOCI_ENDPOINT || process.env.FLOCI_AWS_ENDPOINT_URL || 'http://localhost:4566',
      AWS_ACCESS_KEY_ID: 'test', AWS_SECRET_ACCESS_KEY: 'test', FLOCI_AWS_PROFILE: '',
      DJANGO_ALLOWED_HOSTS: '127.0.0.1,localhost,testserver',
    },
  },
});
