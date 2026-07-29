import { useCallback, useEffect, useRef, useState } from "react";
import { createEventSource } from "../api/client";

const subscribers = new Set();
let sharedEventSource = null;
let reconnectTimer = null;
let retryCount = 0;
let lastEventId = "";
let sharedConnected = false;
let authListenerAttached = false;
const maxRetryDelay = 30000;

function notifyConnection(connected) {
  sharedConnected = connected;
  subscribers.forEach((subscriber) => subscriber.onConnectionChange?.(connected));
}

function dispatchEvent(eventType, event) {
  if (event.lastEventId) lastEventId = event.lastEventId;
  try {
    const raw = JSON.parse(event.data);
    const data = raw.data || raw;
    if (eventType === "task_changed") {
      subscribers.forEach((subscriber) => subscriber.onTaskChanged?.(data));
    } else if (eventType === "agent_status") {
      subscribers.forEach((subscriber) => subscriber.onAgentStatus?.(data));
    } else if (eventType === "diagnosis_complete") {
      subscribers.forEach((subscriber) => subscriber.onDiagnosisComplete?.(data));
    }
  } catch {
    // Ignore malformed events and keep the stream alive.
  }
}

function closeSharedEventSource() {
  sharedEventSource?.close();
  sharedEventSource = null;
}

function connectSharedEventSource() {
  if (sharedEventSource || subscribers.size === 0) return;

  const eventSource = createEventSource(lastEventId);
  sharedEventSource = eventSource;

  eventSource.onopen = () => {
    retryCount = 0;
    notifyConnection(true);
  };
  eventSource.addEventListener("task_changed", (event) => dispatchEvent("task_changed", event));
  eventSource.addEventListener("agent_status", (event) => dispatchEvent("agent_status", event));
  eventSource.addEventListener(
    "diagnosis_complete",
    (event) => dispatchEvent("diagnosis_complete", event),
  );
  eventSource.onmessage = (event) => {
    if (event.lastEventId) lastEventId = event.lastEventId;
    try {
      const raw = JSON.parse(event.data);
      const eventType = raw.event || raw.type;
      if (eventType) dispatchEvent(eventType, event);
    } catch {
      // Ignore malformed compatibility events.
    }
  };
  eventSource.onerror = () => {
    if (sharedEventSource !== eventSource) return;
    closeSharedEventSource();
    notifyConnection(false);
    if (subscribers.size === 0) return;
    const delay = Math.min(1000 * (2 ** retryCount), maxRetryDelay);
    retryCount += 1;
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connectSharedEventSource, delay);
  };
}

function subscribe(handlers) {
  subscribers.add(handlers);
  if (!authListenerAttached) {
    window.addEventListener("mini-drop:auth-changed", reconnectSharedEventSource);
    authListenerAttached = true;
  }
  handlers.onConnectionChange?.(sharedConnected);
  clearTimeout(reconnectTimer);
  connectSharedEventSource();

  return () => {
    subscribers.delete(handlers);
    if (subscribers.size === 0) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
      retryCount = 0;
      closeSharedEventSource();
      sharedConnected = false;
      window.removeEventListener("mini-drop:auth-changed", reconnectSharedEventSource);
      authListenerAttached = false;
    }
  };
}

function reconnectSharedEventSource() {
  retryCount = 0;
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  closeSharedEventSource();
  notifyConnection(false);
  connectSharedEventSource();
}

/**
 * Server-Sent Events 实时事件 Hook。
 *
 * 连接后台 /api/events/stream，接收任务状态变更、
 * Agent 上下线、诊断完成等事件，触发回调。
 *
 * 特性：
 * - 自动重连（指数退避，最多 30s 间隔）
 * - 页面隐藏时保持连接
 * - 组件卸载时自动关闭
 *
 * @param {object} handlers
 * @param {(data: object) => void} [handlers.onTaskChanged]
 * @param {(data: object) => void} [handlers.onAgentStatus]
 * @param {(data: object) => void} [handlers.onDiagnosisComplete]
 * @param {(connected: boolean) => void} [handlers.onConnectionChange]
 * @returns {{ connected: boolean, reconnect: () => void }}
 */
export default function useSSE({
  onTaskChanged,
  onAgentStatus,
  onDiagnosisComplete,
  onConnectionChange,
} = {}) {
  const [connected, setConnected] = useState(sharedConnected);

  const handlersRef = useRef({ onTaskChanged, onAgentStatus, onDiagnosisComplete, onConnectionChange });
  handlersRef.current = { onTaskChanged, onAgentStatus, onDiagnosisComplete, onConnectionChange };

  useEffect(() => {
    const subscriber = {
      onTaskChanged: (data) => handlersRef.current.onTaskChanged?.(data),
      onAgentStatus: (data) => handlersRef.current.onAgentStatus?.(data),
      onDiagnosisComplete: (data) => handlersRef.current.onDiagnosisComplete?.(data),
      onConnectionChange: (value) => {
        setConnected(value);
        handlersRef.current.onConnectionChange?.(value);
      },
    };
    return subscribe(subscriber);
  }, []);

  const reconnect = useCallback(() => {
    reconnectSharedEventSource();
  }, []);

  return { connected, reconnect };
}
