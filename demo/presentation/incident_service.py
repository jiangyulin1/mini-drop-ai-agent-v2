#!/usr/bin/env python3
"""Bounded checkout-like service used by the live incident demonstration."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import signal
import threading
import time
from typing import Any
from urllib.parse import urlsplit


CATALOG = [
    {
        "sku": f"demo-{index:04d}",
        "price": (index * 17) % 997 + 10,
        "category": f"category-{index % 12}",
        "inventory": (index * 31) % 200,
        "labels": ["live-demo", f"group-{index % 7}", "checkout"],
    }
    for index in range(420)
]


def calculate_discount_matrix(seed: int = 17) -> int:
    """Deliberately expensive, deterministic application calculation."""
    score = seed
    for row in range(2800):
        score = (score * 1103515245 + 12345 + row) & 0x7FFFFFFF
        score ^= (row * row) % 65521
    return score


def serialize_catalog(rounds: int = 7) -> int:
    """Repeated JSON serialization creates a second explainable hotspot."""
    total = 0
    for index in range(rounds):
        payload = {
            "request_id": f"demo-{index}",
            "catalog": CATALOG,
            "discount_revision": calculate_discount_matrix(index + 11),
        }
        total += len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return total


def cpu_hotspot() -> int:
    return serialize_catalog() + calculate_discount_matrix()


class DemoState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._anomaly = threading.Event()
        self._stop = threading.Event()
        self._cycles = 0
        self._mode_changed_at = time.time()

    @property
    def anomaly(self) -> bool:
        return self._anomaly.is_set()

    def set_anomaly(self, enabled: bool) -> None:
        with self._lock:
            self._mode_changed_at = time.time()
            if enabled:
                self._anomaly.set()
            else:
                self._anomaly.clear()

    def stop(self) -> None:
        self._stop.set()
        self._anomaly.set()

    def background_loop(self) -> None:
        while not self._stop.is_set():
            if not self._anomaly.is_set():
                self._anomaly.wait(0.2)
                continue
            cpu_hotspot()
            with self._lock:
                self._cycles += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "service": "checkout-demo",
                "mode": "anomaly" if self.anomaly else "normal",
                "cpu_hotspot_cycles": self._cycles,
                "mode_changed_at": self._mode_changed_at,
            }


def build_handler(state: DemoState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MiniDropIncidentDemo/1.0"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/health":
                self._send(200, {"ok": True, **state.snapshot()})
                return
            if path == "/metrics":
                self._send(200, state.snapshot())
                return
            if path == "/mode/anomaly":
                state.set_anomaly(True)
                self._send(200, {"changed": True, **state.snapshot()})
                return
            if path == "/mode/normal":
                state.set_anomaly(False)
                self._send(200, {"changed": True, **state.snapshot()})
                return
            if path == "/work":
                started = time.perf_counter()
                checksum = len(json.dumps(CATALOG[:24], ensure_ascii=False))
                if state.anomaly:
                    checksum += cpu_hotspot()
                elapsed_ms = (time.perf_counter() - started) * 1000
                self._send(
                    200,
                    {
                        "ok": True,
                        "mode": "anomaly" if state.anomaly else "normal",
                        "latency_ms": round(elapsed_ms, 2),
                        "checksum": checksum,
                    },
                )
                return
            self._send(404, {"ok": False, "error": "not_found"})

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()

    state = DemoState()
    worker = threading.Thread(target=state.background_loop, name="checkout-hotspot", daemon=True)
    worker.start()
    server = ThreadingHTTPServer((args.host, args.port), build_handler(state))

    def shutdown(_signum, _frame) -> None:
        state.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        state.stop()
        server.server_close()
        worker.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
