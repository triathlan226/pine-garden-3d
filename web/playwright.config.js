import { defineConfig } from '@playwright/test';

const gpuArgs = ['--use-angle=d3d11', '--enable-gpu', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader'];
const PORT = 4173;

export default defineConfig({
  testDir: './tests',
  testMatch: /.*\.spec\.js/,
  timeout: 240000,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: '../renders/acceptance/results.json' }]],
  use: {
    baseURL: process.env.PG_URL || `http://127.0.0.1:${PORT}/`,
    launchOptions: { args: gpuArgs },
  },
  webServer: process.env.PG_URL ? undefined : {
    command: `npx vite preview --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: true,
    timeout: 60000,
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 } },
    {
      name: 'mobile',
      use: {
        viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
        userAgent: 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Mobile Safari/537.36',
      },
    },
  ],
});
