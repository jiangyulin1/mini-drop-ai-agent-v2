/** Mini-Drop HTTP API 客户端。

所有 Web 请求通过此模块调用 Server REST API。
axios 拦截器统一处理错误码和响应格式。

认证方式（按优先级）:
  1. HttpOnly cookie (mini_drop_api_key) — 首选，XSS 无法窃取
  2. localStorage Bearer token — 兼容旧版
  3. X-API-Key header — 兼容直接调用
*/

import axios from "axios";

const API_KEY_STORAGE_KEY = "mini-drop-api-key";

function readableErrorDetail(detail, fallback = "请求失败") {
  if (detail === null || detail === undefined || detail === "") return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (typeof item === "string") return item;
      const location = Array.isArray(item?.loc) ? item.loc.join(".") : "";
      return [location, item?.msg || item?.message].filter(Boolean).join("：")
        || JSON.stringify(item);
    }).join("；");
  }
  if (typeof detail === "object") {
    return detail.message || detail.msg || detail.code || JSON.stringify(detail);
  }
  return String(detail);
}

function createApiError(message, { status, code, detail } = {}) {
  const error = new Error(message);
  error.name = "ApiError";
  error.status = status;
  error.code = code;
  error.detail = detail;
  return error;
}

function normalizeAxiosError(error) {
  const status = error.response?.status;
  const detail = error.response?.data?.detail ?? error.response?.data?.message;
  const message = status === 401
    ? "访问认证失败：请在系统设置中配置 Mini-Drop API Key"
    : readableErrorDetail(detail, error.message);
  return createApiError(message, {
    status,
    code: error.response?.data?.code,
    detail,
  });
}

const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
  withCredentials: true,  // 发送 HttpOnly cookie
});

api.interceptors.request.use((config) => {
  // cookie 会自动携带，不再需要手动设置 Authorization header
  // 但保留兼容：如果 cookie 不可用，fallback 到 localStorage
  const token = getStoredApiKey();
  if (token) {
    config.headers["X-API-Key"] = token;
  }
  return config;
});

/** 响应拦截：统一提取 data 字段，简化调用方代码 */
api.interceptors.response.use(
  (resp) => {
    const body = resp.data;
    if (body?.code === 0) return body.data;
    throw createApiError(readableErrorDetail(body?.message || body?.detail, "未知错误"), {
      status: resp.status,
      code: body?.code,
      detail: body?.detail,
    });
  },
  (err) => {
    throw normalizeAxiosError(err);
  },
);

// ── 通用 ────────────────────────────────────────────────────────

export function getStoredApiKey() {
  try {
    return window.localStorage.getItem(API_KEY_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function setStoredApiKey(token) {
  try {
    const normalized = (token || "").trim();
    if (normalized) {
      window.localStorage.setItem(API_KEY_STORAGE_KEY, normalized);
    } else {
      window.localStorage.removeItem(API_KEY_STORAGE_KEY);
    }
  } catch {
    // Ignore unavailable localStorage in restricted browser contexts.
  }
}

/** 通过 HttpOnly cookie 设置 API Key（比 localStorage 更安全，XSS 无法读取）。*/
export async function setCookieApiKey(token) {
  try {
    await axios.post("/api/auth/set-cookie", { api_key: token }, {
      withCredentials: true,
      timeout: 10000,
    });
  } catch (error) {
    throw normalizeAxiosError(error);
  }
}

/** 清除 HttpOnly cookie。*/
export async function clearCookieApiKey() {
  try {
    await axios.post("/api/auth/clear-cookie", {}, {
      withCredentials: true,
      timeout: 10000,
    });
  } catch (error) {
    throw normalizeAxiosError(error);
  }
}

/**
 * 统一设置 API Key：优先 HttpOnly cookie，同时更新 localStorage 作为降级。
 * 非空 Key 会立即请求受保护的 /me 验证；验证失败时自动回滚，避免界面误报保存成功。
 */
export async function saveApiKey(token) {
  const trimmed = (token || "").trim();
  if (trimmed) {
    // Validate the candidate header before changing the current cookie/local
    // fallback. A typo must not erase a previously working credential.
    try {
      await axios.get("/api/me", {
        headers: { "X-API-Key": trimmed },
        withCredentials: true,
        timeout: 10000,
      });
    } catch (error) {
      throw normalizeAxiosError(error);
    }
    try {
      await setCookieApiKey(trimmed);
      // HttpOnly cookie succeeded: remove the XSS-readable legacy copy.
      setStoredApiKey("");
    } catch {
      // Restricted cookie environments retain the legacy fallback.
      setStoredApiKey(trimmed);
      console.warn("HttpOnly cookie 设置失败，使用 localStorage 降级方案");
    }
    await getCurrentUser();
  } else {
    // Do not report success while an HttpOnly credential remains active.
    await clearCookieApiKey();
    setStoredApiKey("");
  }
}

function safeDownloadFilename(disposition, fallback) {
  const encoded = String(disposition || "").match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = String(disposition || "").match(/filename\s*=\s*(?:"([^"]+)"|([^;]+))/i);
  let filename = fallback;
  if (encoded) {
    try { filename = decodeURIComponent(encoded); } catch { filename = encoded; }
  } else if (plain) {
    filename = plain[1] || plain[2] || fallback;
  }
  const sanitized = Array.from(String(filename), (character) => {
    const code = character.charCodeAt(0);
    return character === "/" || character === "\\" || code < 32 || code === 127
      ? "_"
      : character;
  }).join("");
  return sanitized
    .trim()
    .slice(0, 255) || fallback;
}

export function healthz() {
  // Health checks may wait on an unavailable object store. They must not keep
  // the operational task UI in a loading state for the global 30s timeout.
  return api.get("/healthz", { timeout: 5000 });
}

function itemsOf(value) {
  if (Array.isArray(value)) return value;
  return value?.items || [];
}

// ── Agent ────────────────────────────────────────────────────────

export function listAgents() {
  return api.get("/agents").then(itemsOf);
}

/** 在目标 Worker 上扫描进程，返回可选择的诊断目标候选。
 *
 * 这是“选进程而不是填 PID”的关键接口：
 * 输入进程名/服务名关键字，返回 pid/comm/cmdline/CPU/内存 候选。
 */
export function scanAgentProcesses(agentId, { query = "", timeoutSec = 15 } = {}) {
  return api.post(`/agents/${agentId}/processes/scan`, {
    query,
    timeout_sec: timeoutSec,
    max_results: 300,
  });
}

export function listAuditLogs() {
  return api.get("/audit-logs").then(itemsOf);
}

// ── 任务 ────────────────────────────────────────────────────────

function newIdempotencyKey(prefix = "web") {
  const random = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}

export function createTask(payload, idempotencyKey = newIdempotencyKey("create")) {
  return api.post("/tasks", payload, {
    headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function listTaskKinds(params = {}) {
  return api.get("/task-kinds", { params }).then(itemsOf);
}

export function listTasks(params = {}) {
  return api.get("/tasks", { params }).then(itemsOf);
}

export function getTask(taskId) {
  return api.get(`/tasks/${taskId}`);
}

export function deleteTask(taskId) {
  return api.delete(`/tasks/${taskId}`);
}

export function cancelTask(taskId, reason = "用户从 Web 取消任务") {
  return api.post(`/tasks/${taskId}/cancel`, { reason });
}

export function retryTask(taskId, payload = {}, idempotencyKey = newIdempotencyKey("retry")) {
  return api.post(`/tasks/${taskId}/retry`, payload, {
    headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function getTaskEvents(taskId) {
  return api.get(`/tasks/${taskId}/events`);
}

export function getTaskAttempts(taskId) {
  return api.get(`/tasks/${taskId}/attempts`);
}

export function getTaskAnalysisJobs(taskId) {
  return api.get(`/tasks/${taskId}/analysis-jobs`);
}

export function getTaskArtifacts(taskId, params = {}) {
  return api.get(`/tasks/${taskId}/artifacts`, { params });
}

export function getTaskArtifactContent(taskId, artifactType, params = {}) {
  return api.get(`/tasks/${taskId}/artifacts/${artifactType}/content`, { params });
}

export async function downloadTaskArtifact(taskId, artifactType, params = {}) {
  const token = getStoredApiKey();
  const response = await axios.get(
    `/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactType)}/download`,
    {
      params,
      responseType: "blob",
      withCredentials: true,
      headers: token ? { "X-API-Key": token } : {},
    },
  );
  const disposition = response.headers["content-disposition"] || "";
  const filename = safeDownloadFilename(disposition, `${artifactType}.bin`);
  return { blob: response.data, filename };
}

export async function downloadDiagnosisEvidence(diagnosisId, evidenceId) {
  const token = getStoredApiKey();
  const response = await axios.get(
    `/api/v1/diagnoses/${encodeURIComponent(diagnosisId)}/evidence/${encodeURIComponent(evidenceId)}/download`,
    {
      responseType: "blob",
      withCredentials: true,
      headers: token ? { "X-API-Key": token } : {},
    },
  );
  const disposition = response.headers["content-disposition"] || "";
  const filename = safeDownloadFilename(disposition, `evidence-${evidenceId}.json`);
  return { blob: response.data, filename };
}

export async function downloadDiagnosisEvidenceBundle(diagnosisId, evidenceId) {
  const token = getStoredApiKey();
  const response = await axios.get(
    `/api/v1/diagnoses/${encodeURIComponent(diagnosisId)}/evidence/${encodeURIComponent(evidenceId)}/bundle`,
    {
      responseType: "blob",
      withCredentials: true,
      headers: token ? { "X-API-Key": token } : {},
    },
  );
  const disposition = response.headers["content-disposition"] || "";
  const filename = safeDownloadFilename(disposition, `evidence-${evidenceId}-bundle.zip`);
  return { blob: response.data, filename };
}

export function triggerDiagnose(taskId) {
  return api.post(`/tasks/${taskId}/diagnose`);
}

export function listTaskDiagnoses(taskId) {
  return api.get(`/tasks/${taskId}/diagnoses`);
}

export function listDiagnoses(params = {}) {
  return api.get("/diagnoses", { params }).then(itemsOf);
}

export function getDiagnosis(diagnosisId) {
  return api.get(`/diagnoses/${diagnosisId}`);
}

export function submitDiagnosisFeedback(diagnosisId, payload) {
  return api.post(`/diagnoses/${diagnosisId}/feedback`, payload);
}

// ── AI 集群诊断会话 ──────────────────────────────────────────────

export function createDiagnosisSession(payload) {
  return api.post("/v1/diagnoses", payload);
}

export function listDiagnosisSessions(params = {}) {
  return api.get("/v1/diagnoses", { params }).then(itemsOf);
}

export function getDiagnosisSession(diagnosisId) {
  return api.get(`/v1/diagnoses/${diagnosisId}`);
}

export function approveDiagnosisProbe(diagnosisId, payload) {
  return api.post(`/v1/diagnoses/${diagnosisId}/approvals`, payload);
}

export function listProbeDefinitions() {
  return api.get("/v1/probes");
}

// ── AI Incident Case 协作层 ─────────────────────────────────────

export function createIncidentCase(payload) {
  return api.post("/v1/cases", payload);
}

export function listIncidentCases(params = {}) {
  return api.get("/v1/cases", { params });
}

export function getIncidentCase(caseId) {
  return api.get(`/v1/cases/${encodeURIComponent(caseId)}`);
}

export function listIncidentCaseEvents(caseId, params = {}) {
  return api.get(`/v1/cases/${encodeURIComponent(caseId)}/events`, { params });
}

export function appendIncidentCaseMessage(caseId, payload) {
  return api.post(`/v1/cases/${encodeURIComponent(caseId)}/messages`, payload);
}

export function correctIncidentCase(caseId, payload) {
  return api.post(`/v1/cases/${encodeURIComponent(caseId)}/corrections`, payload);
}

export function transitionIncidentCase(caseId, action, payload) {
  return api.post(`/v1/cases/${encodeURIComponent(caseId)}/${action}`, payload);
}

export function startIncidentCaseDiagnosis(caseId, payload = {}) {
  return api.post(`/v1/cases/${encodeURIComponent(caseId)}/diagnoses`, payload);
}

export function advanceAutonomousCase(caseId) {
  return api.post(`/v1/cases/${encodeURIComponent(caseId)}/agent/step`, {});
}

export function listCaseContextPackets(caseId, params = {}) {
  return api.get(`/v1/cases/${encodeURIComponent(caseId)}/context-packets`, { params });
}

export function listCaseModelAttempts(caseId, params = {}) {
  return api.get(`/v1/cases/${encodeURIComponent(caseId)}/model-attempts`, { params });
}

export function getCaseHypotheses(caseId) {
  return api.get(`/v1/cases/${encodeURIComponent(caseId)}/hypotheses`);
}

export function listCaseIterations(caseId, params = {}) {
  return api.get(`/v1/cases/${encodeURIComponent(caseId)}/iterations`, { params });
}

// ── NLP 自然语言采集 ────────────────────────────────────────────

export function nlpParse(query) {
  return api.post("/nlp/parse", { query });
}

export function nlpSummarize(taskId) {
  return api.post("/nlp/summarize", { task_id: taskId });
}

// ── 存储 ──────────────────────────────────────────────────────────

export function getPresignUrl(bucket, key, expires = 3600) {
  return api.get("/storage/presign", { params: { bucket, key, expires } });
}

// ── 配置 ──────────────────────────────────────────────────────────

export function getAIConfig() {
  return api.get("/ai-config");
}

export function runAIValidation() {
  return api.post("/ai-validation/runs", {}, { timeout: 180000 });
}

export function getCurrentUser() {
  return api.get("/me");
}

// ── SSE 事件 ──────────────────────────────────────────────────────

/**
 * 创建 SSE EventSource 连接。
 * @param {string} [since] - ISO 时间戳，只获取该时间之后的事件
 * @returns {EventSource}
 */
export function createEventSource(since = "") {
  const params = since ? `?since=${encodeURIComponent(since)}` : "";
  return new EventSource(`/api/events/stream${params}`);
}

/**
 * Native EventSource cannot attach X-API-Key headers. Before opening the
 * stream, migrate a legacy localStorage token into the HttpOnly auth cookie.
 */
export async function ensureEventSourceAuthCookie() {
  const token = getStoredApiKey();
  if (!token) return;
  try {
    await setCookieApiKey(token);
    setStoredApiKey("");
  } catch {
    console.warn("SSE 认证 Cookie 写入失败，将使用轮询兜底");
  }
}

// ── Prometheus 指标 ───────────────────────────────────────────────

export function getMetrics() {
  const token = getStoredApiKey();
  return axios.get("/api/metrics", {
    responseType: "text",
    withCredentials: true,
    headers: token ? { "X-API-Key": token } : {},
  }).then((response) => response.data);
}

// ── 受控修复动作（Actuation）─────────────────────────────────────

/** 对注册动作执行只读 dry-run，返回将影响的清单。 */
export function dryRunAction(actionId, payload = {}) {
  return api.post(`/v1/actions/${actionId}/dry-run`, payload);
}

/** 执行已通过 dry-run 与策略评估的修复动作（人工显式触发）。 */
export function executeAction(actionId, payload = {}) {
  return api.post(`/v1/actions/${actionId}/execute`, payload);
}

/** 回滚已执行的可逆动作。 */
export function rollbackAction(actionId, payload = {}) {
  return api.post(`/v1/actions/${actionId}/rollback`, payload);
}

/** 列出注册动作与执行状态。 */
export function listRegisteredActions() {
  return api.get("/v1/actions");
}

// ── 恢复验证与人工动作（多轮诊断闭环）────────────────────────

/** 触发一次验证采集，对比诊断基线判断是否恢复。 */
export function verifyCaseRecovery(caseId, payload = {}) {
  // Server-side verification may collect several samples for up to 90s.
  return api.post(`/v1/cases/${caseId}/verification`, payload, { timeout: 120000 });
}

/** 回填人工执行建议动作的结果。 */
export function recordCaseManualAction(caseId, payload = {}) {
  return api.post(`/v1/cases/${caseId}/manual-actions`, payload);
}
