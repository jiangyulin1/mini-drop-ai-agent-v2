/**
 * Mini-Drop Pi Sidecar HTTP entry point (E3, plan section 4.4).
 *
 * Exposes ONLY the Mini-Drop internal protocol:
 *   POST /internal/runtime/v1/cases/{id}/turn
 *   POST /internal/runtime/v1/cases/{id}/steer
 *   POST /internal/runtime/v1/cases/{id}/follow-up
 *   POST /internal/runtime/v1/cases/{id}/abort
 *   POST /internal/runtime/v1/cases/{id}/resume
 *   GET  /internal/runtime/v1/cases/{id}/state
 *   GET  /internal/runtime/v1/health
 *
 * The raw Pi RPC is never bound to a network port.  Model credentials are read
 * from an allowlisted set of env vars only.
 */

import http from "node:http";
import { URL } from "node:url";
import { RuntimeManager } from "./runtime.mjs";

const PORT = Number(process.env.MINI_DROP_PI_SIDECAR_PORT || 8899);
const INTERNAL_BASE = process.env.MINI_DROP_PI_INTERNAL_BASE || "http://127.0.0.1:8191";

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const text = Buffer.concat(chunks).toString("utf-8");
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { _parse_error: true };
  }
}

function json(res, status, data) {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

let manager = null;

export async function createServer() {
  if (!manager) {
    // ModelRuntime is constructed lazily on first turn from allowlisted env
    // vars; the HTTP surface (health/state) must not block on model setup.
    manager = new RuntimeManager({ modelRuntime: null, internalBase: INTERNAL_BASE });
  }
  const server = http.createServer(async (req, res) => {
    try {
      await route(req, res);
    } catch (err) {
      json(res, 500, { ok: false, error: String(err && err.message || err) });
    }
  });
  return server;
}

async function route(req, res) {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const path = url.pathname;

  if (req.method === "GET" && path === "/internal/runtime/v1/health") {
    json(res, 200, { ok: true, data: {
      status: manager ? "ready" : "degraded",
      runtime_type: "pi",
      runtime_version: "pi-0.84.0",
      model_ready: Boolean(manager?.modelRuntime),
    }});
    return;
  }

  const m = path.match(/^\/internal\/runtime\/v1\/cases\/([^/]+)\/([^/]+)$/);
  if (!m) {
    json(res, 404, { ok: false, error: "unknown_internal_route" });
    return;
  }
  const caseId = decodeURIComponent(m[1]);
  const action = m[2];

  if (req.method === "GET" && action === "state") {
    json(res, 200, { ok: true, data: manager.state(caseId) });
    return;
  }

  if (req.method !== "POST") {
    json(res, 405, { ok: false, error: "method_not_allowed" });
    return;
  }
  const body = await readBody(req);
  if (body._parse_error) {
    json(res, 400, { ok: false, error: "invalid_json" });
    return;
  }

  switch (action) {
    case "resume": {
      const binding = await manager.startOrResume(body.context || { case_id: caseId });
      json(res, 200, { ok: true, data: binding });
      return;
    }
    case "turn": {
      const accepted = await manager.submitTurn(caseId, body);
      json(res, 200, { ok: true, data: accepted });
      return;
    }
    case "steer": {
      json(res, 200, { ok: true, data: await manager.steer(caseId, body) });
      return;
    }
    case "follow-up": {
      json(res, 200, { ok: true, data: await manager.followUp(caseId, body) });
      return;
    }
    case "abort": {
      json(res, 200, { ok: true, data: await manager.abort(caseId, body.reason) });
      return;
    }
    default:
      json(res, 404, { ok: false, error: "unknown_action" });
  }
}

import { pathToFileURL } from "node:url";

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  const server = await createServer();
  server.listen(PORT, () => {
    console.log(`[sidecar] listening on ${PORT}`);
  });
}
