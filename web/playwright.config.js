import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 12_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["json", { outputFile: "test-results/c6-results.json" }]],
  use: {
    baseURL: process.env.MINI_DROP_WEB_BASE_URL || "http://127.0.0.1:5173",
    // The isolated VM lab terminates TLS with its pinned self-signed certificate.
    ignoreHTTPSErrors: Boolean(process.env.MINI_DROP_WEB_BASE_URL?.startsWith("https://192.168.")),
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
