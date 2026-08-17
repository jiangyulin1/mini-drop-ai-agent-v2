import { expect, test } from "@playwright/test";

test.describe("Mini-Drop V2 live three-node console", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1000 });
    const apiKey = process.env.MINI_DROP_LIVE_API_KEY;
    expect(apiKey, "MINI_DROP_LIVE_API_KEY is required for the real backend gate").toBeTruthy();
    await page.addInitScript((key) => window.localStorage.setItem("mini-drop-api-key", key), apiKey);
  });

  test("overview reports runtime, workers, and historical task semantics", async ({ page }, testInfo) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "运行态势总览" })).toBeVisible();
    await expect(page.getByText("2 / 2").first()).toBeVisible();
    await expect(page.getByText(/0\.84\.2/).first()).toBeVisible();
    await expect(page.getByText(/历史失败记录.*pending=0、running=0.*Analyzer 健康/)).toBeVisible();
    await expect(page.getByText("Auto READ_LOW")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("overview.png"), fullPage: true });
  });

  test("agents and runtime are backed by the deployed API", async ({ page }, testInfo) => {
    await page.goto("/agents");
    await expect(page.getByRole("heading", { name: "节点与 Agent" })).toBeVisible();
    await expect(page.getByText("linux-worker-1").first()).toBeVisible();
    await expect(page.getByText("linux-worker-2").first()).toBeVisible();
    await expect(page.getByText(/历史演示注册.*不计入有效 Worker/)).toBeVisible();
    await page.goto("/runtime");
    await expect(page.getByRole("heading", { name: "Runtime 与设置" })).toBeVisible();
    await expect(page.getByText("0.84.2").first()).toBeVisible();
    await expect(page.getByText("已配置（值已隐藏）").first()).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("runtime.png"), fullPage: true });
  });

  test("active case opens canonical plan, hypothesis, evidence, and causal workspace", async ({ page }, testInfo) => {
    await page.goto("/cases");
    const caseButton = page.locator("button").filter({ hasText: /vm-agent-beta|vm-fault-gate|Case/ }).first();
    await expect(caseButton).toBeVisible();
    await caseButton.click();
    await expect(page.getByTestId("canonical-workspace")).toBeVisible();
    await expect(page.getByRole("tab", { name: /调查计划/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /假设/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /Evidence/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /因果链与结论/ })).toBeVisible();
    await page.getByRole("tab", { name: /Evidence/ }).click();
    const evidenceDetails = page.getByRole("button", { name: "详情与审查" }).first();
    await expect(evidenceDetails).toBeVisible();
    await evidenceDetails.click();
    await expect(page.getByText("Evidence 详情")).toBeVisible();
    await expect(page.getByRole("button", { name: "标记可信" })).toBeVisible();
    await expect(page.getByRole("button", { name: "排除" })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("case-workspace.png"), fullPage: true });
  });
});
