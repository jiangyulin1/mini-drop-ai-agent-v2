import { useEffect, useRef, useCallback, useState } from "react";

/**
 * 可暂停的轮询 Hook。
 *
 * 特性：
 * - 页面隐藏时自动暂停轮询（减少服务端压力）
 * - 组件卸载时自动清理定时器
 * - enabled=false 时自动停止
 *
 * @param {() => Promise<void> | void} callback  每次轮询执行的回调
 * @param {object} options
 * @param {number} [options.interval=10000]  轮询间隔（毫秒）
 * @param {boolean} [options.enabled=true]   是否启用轮询
 * @returns {{ lastRefreshed: number | null, isPolling: boolean }}
 */
export default function usePolling(callback, { interval = 10000, enabled = true } = {}) {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  const [lastRefreshed, setLastRefreshed] = useState(null);
  const intervalRef = useRef(null);
  const visibleRef = useRef(true);

  const tick = useCallback(async () => {
    try {
      await callbackRef.current();
      setLastRefreshed(Date.now());
    } catch (err) {
      // 避免每 N 秒弹错误提示，但记录到 console 便于调试
      console.warn("usePolling tick failed:", err?.message || err);
    }
  }, []);

  useEffect(() => {
    const onVisibilityChange = () => {
      visibleRef.current = document.visibilityState === "visible";
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, []);

  useEffect(() => {
    if (!enabled) {
      if (intervalRef.current) clearInterval(intervalRef.current);
      return;
    }

    intervalRef.current = setInterval(() => {
      if (visibleRef.current) {
        tick();
      }
    }, interval);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [enabled, interval, tick]);

  return { lastRefreshed, isPolling: enabled };
}
