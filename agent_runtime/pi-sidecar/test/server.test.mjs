/**
 * E3 sidecar tests: internal protocol surface and tool gating.
 * Run with: node --test test/
 */

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "../src/server.mjs";
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
