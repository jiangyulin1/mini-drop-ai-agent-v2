/**
 * E3 sidecar tests: internal protocol surface and tool gating.
 * Run with: node --test test/
 */

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "../src/server.mjs";
import { RuntimeManager } from "../src/runtime.mjs";
import { EventSpool } from "../src/event-spool.mjs";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { buildToolCatalog, fetchToolCatalog, ALLOWED_TOOL_NAMES } from "../src/tools.mjs";

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
  let capturedBody = null;
  globalThis.fetch = async (_url, options) => {
    capturedHeaders = options.headers;
    capturedBody = options.body;
    return new Response(JSON.stringify({ ok: true, data: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
    const tools = buildToolCatalog({
      internalBase: "http://127.0.0.1:1",
      getEnvelope: () => ({
        side_effect_policy: "READ_ONLY",
        runtime_generation: 7,
        expected_control_revision: 3,
        expected_scope_revision: 4,
      }),
    });
    const tool = tools.find((t) => t.name === "get_case_snapshot");
    await tool.execute("call-1", { case_id: "case-a" });
    assert.equal(capturedHeaders["X-Internal-Token"], "test-token");
    const sent = JSON.parse(capturedBody);
    assert.equal(sent.side_effect_policy, "READ_ONLY");
    assert.equal(sent.runtime_generation, 7);
    assert.equal(sent.expected_control_revision, 3);
    assert.equal(sent.expected_scope_revision, 4);
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
  }
});

test("canonical catalog metadata builds tools but cannot elevate runtime policy", () => {
  const catalog = {
    schema_version: "tool-catalog.v1",
    tools: ALLOWED_TOOL_NAMES.map((name) => ({
      name,
      description: `canonical ${name}`,
      parameters: { type: "object", additionalProperties: false },
      internal_path: `/internal/agent/tools/${name}`,
      enabled_by_default: true,
    })),
  };
  const tools = buildToolCatalog({
    catalog,
    runtimePolicy: { effective_tools: ["get_case_snapshot"] },
  });
  assert.deepEqual(tools.map((tool) => tool.name), ["get_case_snapshot"]);
  assert.equal(tools[0].description, "canonical get_case_snapshot");
});

test("catalog fetch validates the complete compatibility set and auth", async () => {
  const originalFetch = globalThis.fetch;
  process.env.MINI_DROP_PI_INTERNAL_TOKEN = "catalog-test";
  let receivedHeaders;
  globalThis.fetch = async (_url, options) => {
    receivedHeaders = options.headers;
    return new Response(JSON.stringify({ ok: true, data: {
      schema_version: "tool-catalog.v1",
      tools: ALLOWED_TOOL_NAMES.map((name) => ({
        name,
        internal_path: `/internal/agent/tools/${name}`,
      })),
    } }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    const catalog = await fetchToolCatalog({ internalBase: "http://control" });
    assert.equal(catalog.tools.length, ALLOWED_TOOL_NAMES.length);
    assert.equal(receivedHeaders["X-Internal-Token"], "catalog-test");
  } finally {
    globalThis.fetch = originalFetch;
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
  }
});

test("event spool survives restart and deletes only after ACK", () => {
  const root = mkdtempSync(join(tmpdir(), "mini-drop-spool-"));
  const path = join(root, "events.jsonl");
  try {
    const first = new EventSpool(path);
    first.append({
      case_id: "case-spool",
      runtime_generation: 2,
      event_seq: 9,
      event_type: "turn_end",
      idempotency_key: "event-spool-key",
    });
    const restarted = new EventSpool(path);
    assert.equal(restarted.pending("case-spool").length, 1);
    restarted.ack("event-spool-key");
    assert.equal(new EventSpool(path).pending().length, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("restarted runtime replays spooled event and ACKs exactly once", async () => {
  const root = mkdtempSync(join(tmpdir(), "mini-drop-spool-replay-"));
  const path = join(root, "events.jsonl");
  const originalFetch = globalThis.fetch;
  try {
    new EventSpool(path).append({
      case_id: "case-replay",
      runtime_generation: 4,
      event_id: "evt-replay",
      event_seq: 11,
      event_type: "turn_end",
      payload: { text: "durable answer" },
      idempotency_key: "runtime-event:case-replay:4:11:turn_end",
    });
    const requests = [];
    globalThis.fetch = async (url, options) => {
      requests.push({ url, options });
      return new Response(JSON.stringify({ ok: true, data: { accepted: 1 } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
    const manager = new RuntimeManager({
      modelRuntime: {},
      internalBase: "http://127.0.0.1:8191",
      eventSpool: new EventSpool(path),
    });
    manager.sessions.set("case-replay", { generation: 4, lastError: "" });
    await manager.replayPending("case-replay");
    await manager.replayPending("case-replay");
    assert.equal(requests.length, 1);
    assert.equal(JSON.parse(requests[0].options.body).events[0].event_id, "evt-replay");
    assert.equal(new EventSpool(path).pending().length, 0);
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true });
  }
});

test("snapshot refresh keeps one session and updates active tool catalog", async () => {
  const manager = new RuntimeManager({
    modelRuntime: {},
    eventSpool: new EventSpool(null),
  });
  const activated = [];
  const session = {
    setActiveToolsByName(names) { activated.push(names); },
  };
  manager.sessions.set("case-refresh", {
    session,
    generation: 3,
    subscribed: true,
    toolEnvelope: { current: {} },
  });
  const binding = await manager.startOrResume({
    case_id: "case-refresh",
    runtime_generation: 3,
    side_effect_policy: "READ_ONLY",
    control_revision: 8,
    scope_revision: 9,
  });
  assert.equal(binding.runtime_generation, 3);
  assert.equal(manager.get("case-refresh").session, session);
  assert.equal(manager.get("case-refresh").subscribed, true);
  assert.equal(activated.length, 1);
  assert.equal(activated[0].includes("request_operation"), false);
  assert.equal(manager.get("case-refresh").toolEnvelope.current.runtime_generation, 3);
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
    const manager = new RuntimeManager({
      modelRuntime: {},
      internalBase: "http://127.0.0.1:8191",
      eventSpool: new EventSpool(null),
    });
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
  const manager = new RuntimeManager({
    modelRuntime: {},
    internalBase: "http://127.0.0.1:8191",
    eventSpool: new EventSpool(null),
  });
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

test("message_end event carries per-call model usage and cost delta", async () => {
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
    const manager = new RuntimeManager({
      modelRuntime: {},
      internalBase: "http://127.0.0.1:8191",
      eventSpool: new EventSpool(null),
    });
    const firstStats = { tokens: { input: 1000, output: 200, cacheRead: 50, cacheWrite: 20 }, cost: 0.01 };
    manager.sessions.set("case-usage", {
      generation: 1,
      currentTurnId: "turn-u1",
      context: {
        context_packet_id: "packet-u1",
        context_snapshot_id: "snap-u1",
        diagnostic_strategy_id: "hybrid",
        runtime_options: { reasoning_effort: "high" },
      },
      session: {
        model: { provider: "deepseek", id: "deepseek-v4-flash", name: "DeepSeek V4 Flash" },
        getSessionStats: () => firstStats,
      },
    });
    manager.lastSeq.set("case-usage", 0);
    manager._observe("case-usage", { type: "message_start" });
    await manager._forwardEvent("case-usage", manager.sessions.get("case-usage"), {
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "first answer" }] },
    });
    assert.equal(requests.length, 1);
    const firstBody = JSON.parse(requests[0].options.body);
    const firstAttempt = firstBody.events[0].payload.model_attempt;
    assert.ok(firstAttempt, "message_end should carry model_attempt");
    assert.equal(firstAttempt.provider, "deepseek");
    assert.equal(firstAttempt.model, "deepseek-v4-flash");
    assert.equal(firstAttempt.input_tokens, 1000);
    assert.equal(firstAttempt.output_tokens, 200);
    assert.equal(firstAttempt.cache_read_tokens, 50);
    assert.equal(firstAttempt.cache_write_tokens, 20);
    assert.equal(firstAttempt.cost, 0.01);
    assert.equal(firstAttempt.turn_id, "turn-u1");
    assert.equal(firstAttempt.context_packet_id, "packet-u1");
    assert.equal(firstAttempt.context_snapshot_id, "snap-u1");
    assert.equal(firstAttempt.tool_catalog_version, "tool-catalog.v1");
    assert.ok(firstAttempt.config_fingerprint);
    assert.ok(firstAttempt.started_at);
    assert.ok(firstAttempt.finished_at);

    // Second model response: stats are cumulative, so the audit must be delta.
    const secondStats = { tokens: { input: 1600, output: 500, cacheRead: 80, cacheWrite: 40 }, cost: 0.023 };
    manager.sessions.get("case-usage").session.getSessionStats = () => secondStats;
    manager._observe("case-usage", { type: "message_start" });
    await manager._forwardEvent("case-usage", manager.sessions.get("case-usage"), {
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "second answer" }] },
    });
    assert.equal(requests.length, 2);
    const secondBody = JSON.parse(requests[1].options.body);
    const secondAttempt = secondBody.events[0].payload.model_attempt;
    assert.equal(secondAttempt.input_tokens, 600);
    assert.equal(secondAttempt.output_tokens, 300);
    assert.equal(secondAttempt.cache_read_tokens, 30);
    assert.equal(secondAttempt.cache_write_tokens, 20);
    assert.ok(Math.abs(secondAttempt.cost - 0.013) < 1e-9);
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
  }
});

test("internal tool error includes server response detail", async () => {
  const originalFetch = globalThis.fetch;
  let capturedBody = null;
  globalThis.fetch = async (_url, options) => {
    capturedBody = options.body;
    return new Response(JSON.stringify({ detail: "OPERATION_DISABLED_BY_RUNTIME_POLICY" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
    const tools = buildToolCatalog({
      internalBase: "http://127.0.0.1:1",
      getEnvelope: () => ({ side_effect_policy: "PROPOSE_ONLY" }),
    });
    const tool = tools.find((t) => t.name === "request_operation");
    const result = await tool.execute("call-1", { case_id: "case-a", operation: "system.metrics" });
    const text = result.content[0].text;
    assert.match(text, /HTTP 403/);
    assert.match(text, /OPERATION_DISABLED_BY_RUNTIME_POLICY/);
    assert.equal(result.details.http_status, 403);
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
  }
});
