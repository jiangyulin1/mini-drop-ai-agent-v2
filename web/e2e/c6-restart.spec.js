import { expect, test } from "@playwright/test";

test("server restart retains the canonical message exactly once", async ({ page, request }, testInfo) => {
  const caseId = process.env.MINI_DROP_C6_CASE_ID;
  expect(caseId, "MINI_DROP_C6_CASE_ID is required").toBeTruthy();
  const workspace = await request.get(`/api/v1/cases/${caseId}/workspace`);
  expect(workspace.ok()).toBeTruthy();
  const snapshot = (await workspace.json()).data;
  expect(snapshot.messages.length).toBeGreaterThan(0);
  const answer = snapshot.messages.at(-1).content;
  const traffic = [];
  page.on("request", (item) => {
    const url = new URL(item.url());
    if (url.pathname.startsWith("/api/")) traffic.push(`${item.method()} ${url.pathname}${url.search}`);
  });

  await page.goto(`/ai-diagnosis?caseId=${encodeURIComponent(caseId)}`);
  await expect(page.getByTestId("canonical-workspace")).toBeVisible();
  await expect(page.getByText(answer, { exact: true })).toHaveCount(1);
  await page.reload();
  await expect(page.getByText(answer, { exact: true })).toHaveCount(1);
  expect(traffic.some((item) => item.includes(`/api/v1/cases/${caseId}/workspace`))).toBeTruthy();
  await testInfo.attach("post-restart-network-trace", {
    body: JSON.stringify({ case_id: caseId, requests: [...new Set(traffic)] }, null, 2),
    contentType: "application/json",
  });
});
