/**
 * E3 sidecar tests: internal protocol surface and tool gating.
 * Run with: node --test test/
 */

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "../src/server.mjs";
import { RuntimeManager } from "../src/runtime.mjs";
import { buildToolCatalog, ALLOWED_TOOL_NAMES } from "../src/tools.mjs";

let server;
let base;

before(async () => {
  server = await createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(() => server.close());

test("health reports ready and model gating", async () => {
  const resp = await fetch(`${base}/internal/runtime/v1/health`);
  const body = await resp.json();
  assert.equal(body.ok, true);
  assert.equal(body.data.runtime_type, "pi");
  assert.equal(typeof body.data.model_ready, "boolean");
});

test("unknown internal route returns 404", async () => {
  const resp = await fetch(`${base}/internal/runtime/v1/cases/x/nope/extra`);
  assert.equal(resp.status, 404);
});

test("state for unknown case is NOT_STARTED", async () => {
  const resp = await fetch(`${base}/internal/runtime/v1/cases/unknown-case/state`);
  const body = await resp.json();
  assert.equal(body.data.status, "NOT_STARTED");
});

test("tool catalog exposes ONLY allowlisted read tools", () => {
  const tools = buildToolCatalog({ internalBase: "http://127.0.0.1:1" });
  const names = tools.map((t) => t.name);
  // no shell/file tools
  for (const forbidden of ["bash", "read", "write", "edit", "grep", "find", "ls"]) {
    assert.ok(!names.includes(forbidden), `forbidden tool present: ${forbidden}`);
  }
  assert.deepEqual(names.sort(), [...ALLOWED_TOOL_NAMES].sort());
  // every tool carries a parameter schema
  for (const tool of tools) {
    assert.ok(tool.parameters, `${tool.name} missing parameters schema`);
  }
});

test("no-tools session surface does not expose raw RPC", async () => {
  const resp = await fetch(`${base}/raw/rpc`, { method: "POST" });
  assert.equal(resp.status, 404);
});

test("shadow-plan route returns structured plan without creating task", async () => {
  const resp = await fetch(`${base}/internal/runtime/v1/cases/shadow-case/shadow-plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      context: {
        case_id: "shadow-case",
        case_goal: "定位支付超时",
        missing_facts: ["数据库连接池状态", "GC 频率"],
      },
    }),
  });
  assert.equal(resp.status, 200);
  const body = await resp.json();
  assert.equal(body.ok, true);
  assert.equal(body.data.source, "pi_shadow");
  assert.ok(Array.isArray(body.data.steps));
  assert.ok(body.data.steps.length >= 1);
  assert.ok(body.data.steps.every((step) => step.collector_id));
});

test("plan tool schema includes case/scope/plan revision and target fields", () => {
  const tools = buildToolCatalog({ internalBase: "http://127.0.0.1:1" });
  const planTool = tools.find((t) => t.name === "propose_plan_revision");
  assert.ok(planTool);
  const props = planTool.parameters.properties;
  assert.ok(props.expected_case_row_version);
  assert.ok(props.expected_scope_revision);
  assert.ok(props.expected_plan_revision);
  assert.ok(props.steps.items.properties.target_refs);
  assert.ok(props.steps.items.properties.hypothesis_refs);
  assert.ok(props.steps.items.properties.selection_strategy);
  assert.ok(props.steps.items.properties.depends_on);
});

test("READ_ONLY catalog contains no proposal tools", () => {
  const tools = buildToolCatalog({ internalBase: "http://127.0.0.1:1", sideEffectPolicy: "READ_ONLY" });
  const names = tools.map((t) => t.name);
  assert.ok(names.includes("get_evidence_projection"));
  assert.ok(names.includes("list_case_evidence"));
  assert.ok(names.includes("compare_evidence"));
  assert.ok(!names.includes("request_operation"));
  assert.ok(!names.includes("propose_plan_revision"));
  assert.ok(!names.includes("finish_investigation"));
});

test("internal tool calls send X-Internal-Token when configured", async () => {
  const originalFetch = globalThis.fetch;
  let capturedHeaders = null;
  globalThis.fetch = async (_url, options) => {
    capturedHeaders = options.headers;
    return new Response(JSON.stringify({ ok: true, data: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
    const tools = buildToolCatalog({ internalBase: "http://127.0.0.1:1" });
    const tool = tools.find((t) => t.name === "get_case_snapshot");
    await tool.execute("call-1", { case_id: "case-a" });
    assert.equal(capturedHeaders["X-Internal-Token"], "test-token");
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
  }
});

test("runtime forwards non-thinking events and drops private thinking", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return new Response(JSON.stringify({ ok: true, data: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
    const manager = new RuntimeManager({ modelRuntime: {}, internalBase: "http://127.0.0.1:8191" });
    manager.sessions.set("case-x", { generation: 2 });
    manager.lastSeq.set("case-x", 0);
    manager._observe("case-x", { type: "thinking.private", text: "secret" });
    assert.equal(manager.lastSeq.get("case-x"), 0);
    await manager._forwardEvent("case-x", { generation: 2 }, { type: "assistant_message", text: "hello" });
    assert.equal(requests.length, 0); // seq 0 never forwarded
    manager._observe("case-x", { type: "message_end", message: { role: "assistant", content: [{ type: "text", text: "hello" }] } });
    assert.equal(manager.lastSeq.get("case-x"), 1);
    await manager._forwardEvent("case-x", { generation: 2 }, { type: "message_end", message: { role: "assistant", content: [{ type: "text", text: "hello" }] } });
    await manager._forwardEvent("case-x", { generation: 2 }, { type: "message_end", message: { role: "assistant", content: [{ type: "text", text: "hello" }] } });
    assert.equal(requests.length, 1); // idempotency dedupe
    const body = JSON.parse(requests[0].options.body);
    assert.equal(body.runtime_generation, 2);
    assert.equal(body.events[0].event_type, "message_end");
    assert.equal(JSON.parse(body.events[0].payload.message).content[0].text, "hello");
    assert.equal(requests[0].options.headers["X-Internal-Token"], "test-token");
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
  }
});

test("case routes require X-Internal-Token when configured", async () => {
  const originalToken = process.env.MINI_DROP_PI_INTERNAL_TOKEN;
  process.env.MINI_DROP_PI_INTERNAL_TOKEN = "sidecar-test-token";
  try {
    const noToken = await fetch(`${base}/internal/runtime/v1/cases/x/state`);
    assert.equal(noToken.status, 401);
    const withToken = await fetch(`${base}/internal/runtime/v1/cases/x/state`, {
      headers: { "X-Internal-Token": "sidecar-test-token" },
    });
    assert.equal(withToken.status, 200);
  } finally {
    if (originalToken === undefined) delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    else process.env.MINI_DROP_PI_INTERNAL_TOKEN = originalToken;
  }
});

test("runtime caches last final answer for repeat-question stability", async () => {
  const manager = new RuntimeManager({ modelRuntime: {}, internalBase: "http://127.0.0.1:8191" });
  manager.sessions.set("case-x", { generation: 1 });
  manager.lastSeq.set("case-x", 1);
  process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ ok: true, data: {} }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
  try {
    await manager._forwardEvent("case-x", { generation: 1 }, {
      type: "turn_end",
      message: { role: "assistant", content: [{ type: "text", text: "结论：CPU 饱和" }] },
    });
    assert.equal(manager.lastAnswers.get("case-x"), "结论：CPU 饱和");
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
  }
});
