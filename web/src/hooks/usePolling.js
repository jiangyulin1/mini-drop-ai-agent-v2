import { useEffect, useRef, useState } from "react";

/** Poll only after the previous async callback has settled, and pause when hidden. */
export default function usePolling(callback, { interval = 10000, enabled = true } = {}) {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  const [lastRefreshed, setLastRefreshed] = useState(null);
  const [inFlight, setInFlight] = useState(false);
  const timerRef = useRef(null);
  const runningRef = useRef(false);
  const visibleRef = useRef(document.visibilityState === "visible");

  useEffect(() => {
    const onVisibilityChange = () => {
      visibleRef.current = document.visibilityState === "visible";
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, []);

  useEffect(() => {
    let cancelled = false;

    const schedule = () => {
      if (!cancelled && enabled) timerRef.current = setTimeout(tick, interval);
    };
    const tick = async () => {
      if (cancelled || !enabled) return;
      if (!visibleRef.current || runningRef.current) {
        schedule();
        return;
      }
      runningRef.current = true;
      setInFlight(true);
      try {
        await callbackRef.current();
        if (!cancelled) setLastRefreshed(Date.now());
      } catch (err) {
        console.warn("usePolling tick failed:", err?.message || err);
      } finally {
        runningRef.current = false;
        if (!cancelled) setInFlight(false);
        schedule();
      }
    };

    if (enabled) schedule();
    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
      runningRef.current = false;
    };
  }, [enabled, interval]);

  return { lastRefreshed, isPolling: enabled && !inFlight, inFlight };
}
