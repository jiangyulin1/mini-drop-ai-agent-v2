import { expect, test } from "@playwright/test";

function observeCanonicalTraffic(page) {
  const urls = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) urls.push(`${request.method()} ${url.pathname}${url.search}`);
  });
  return urls;
}

async function dismissScopeEditor(page) {
  const dialog = page.getByRole("dialog", { name: /设置诊断范围/ });
  if (await dialog.isVisible().catch(() => false)) await page.keyboard.press("Escape");
}

test.describe.serial("C6 real-backend workspace", () => {
  test("question-driven entry survives refresh and reconnect without duplicate messages", async ({ page, context, request }, testInfo) => {
    const traffic = observeCanonicalTraffic(page);
    await page.goto("/ai-diagnosis");
    await page.getByRole("button", { name: /新建诊断/ }).first().click();
    const dialog = page.getByRole("dialog", { name: /新建诊断/ });
    await dialog.getByLabel(/发生了什么/).fill("C6 浏览器验证：checkout 延迟持续升高");
    await dialog.getByLabel(/目标服务/).fill("checkout-c6");
    const workspaceResponse = page.waitForResponse((response) => (
      response.url().includes("/api/v1/cases/") && response.url().endsWith("/workspace")
    ));
    await dialog.locator(".ant-modal-footer .ant-btn-primary").click();
    const response = await workspaceResponse;
    expect(response.ok()).toBeTruthy();
    const workspaceBody = await response.json();
    const caseId = workspaceBody.data.case.case_id;
    await dismissScopeEditor(page);

    await expect(page.getByTestId("canonical-workspace")).toBeVisible();
    await expect(page.getByRole("heading", { name: /Evidence/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Campaign \/ Execution/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Causal Graph" })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Gap \/ Conclusion/ })).toBeVisible();
    await expect(page.getByText(/^Accepted$/i)).toHaveCount(0);

    const turn = await request.post(`/api/v1/cases/${caseId}/agent/turn`, {
      data: { message: "C6 持久化消息校验", requested_disposition: "ANSWER_ONLY" },
    });
    expect(turn.ok()).toBeTruthy();
    const answer = (await turn.json()).data.assistant_message;
    await page.reload();
    await dismissScopeEditor(page);
    await expect(page.getByText(answer, { exact: true })).toHaveCount(1);

    await context.setOffline(true);
    await page.waitForTimeout(300);
    await context.setOffline(false);
    await page.reload();
    await dismissScopeEditor(page);
    await expect(page.getByText(answer, { exact: true })).toHaveCount(1);
    expect(traffic.some((item) => item.includes(`/api/v1/cases/${caseId}/workspace`))).toBeTruthy();
    expect(traffic.some((item) => item.includes(`/api/v1/cases/${caseId}/events/stream`))).toBeTruthy();
    await testInfo.attach("canonical-network-trace", {
      body: JSON.stringify({ case_id: caseId, requests: [...new Set(traffic)] }, null, 2),
      contentType: "application/json",
    });
  });

  test("data-driven task entry is visible in normal pages and opens the same Case chain", async ({ page }, testInfo) => {
    const taskId = process.env.MINI_DROP_C6_TASK_ID;
    expect(taskId, "MINI_DROP_C6_TASK_ID must identify the real seeded Task").toBeTruthy();
    const traffic = observeCanonicalTraffic(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "C6 data-driven task" })).toBeVisible();

    const workspaceResponse = page.waitForResponse((response) => (
      response.url().includes("/api/v1/cases/") && response.url().endsWith("/workspace")
    ));
    await page.goto(`/ai-diagnosis?fromTask=${encodeURIComponent(taskId)}`);
    expect((await workspaceResponse).ok()).toBeTruthy();
    await dismissScopeEditor(page);
    await expect(page.getByTestId("canonical-workspace")).toBeVisible();
    await expect(page.getByText(new RegExp(taskId))).toBeVisible();
    expect(traffic.some((item) => item === `GET /api/tasks/${taskId}`)).toBeTruthy();
    expect(traffic.some((item) => item.includes("/workspace"))).toBeTruthy();
    await testInfo.attach("data-entry-network-trace", {
      body: JSON.stringify({ task_id: taskId, requests: [...new Set(traffic)] }, null, 2),
      contentType: "application/json",
    });
  });
});
