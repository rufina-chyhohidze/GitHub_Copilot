import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 45_000,
  expect: { timeout: 12_000 },
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 960 },
      },
    },
  ],
  webServer: [
    {
      command: "../backend/.venv/bin/python tests/api_fixture.py",
      url: "http://127.0.0.1:18000/health",
      timeout: 30_000,
      reuseExistingServer: false,
    },
    {
      command: "npm run start -- --port 3100",
      url: "http://127.0.0.1:3100",
      env: { COPILOT_API_URL: "http://127.0.0.1:18000" },
      timeout: 30_000,
      reuseExistingServer: false,
    },
  ],
});
