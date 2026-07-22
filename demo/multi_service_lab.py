#!/usr/bin/env python3
"""Mini-Drop 三节点真实调用链实验负载（仅 Python 标准库）。

示例：
  worker2: python3 demo/multi_service_lab.py service-b --port 18082 --fault cpu
  worker1: python3 demo/multi_service_lab.py service-a --port 18081 --downstream http://192.168.10.12:18082/work
  worker1: python3 demo/multi_service_lab.py load --url http://127.0.0.1:18081/work --duration 60 --concurrency 4
  worker1: python3 demo/multi_service_lab.py noise --fault io --duration 60
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _fib(n: int) -> int:
    if n <= 1:
        return n
    return _fib(n - 1) + _fib(n - 2)


class LabHandler(BaseHTTPRequestHandler):
    server_version = "MiniDropLab/1.0"

    def do_GET(self):  # noqa: N802 - stdlib handler API
        started = time.perf_counter()
        if self.path == "/healthz":
            return self._json(200, {"status": "ok", "role": self.server.role})
        if self.path != "/work":
            return self._json(404, {"error": "not_found"})
        if self.server.role == "service-b":
            payload = self._run_downstream(started)
            return self._json(200, payload)
        return self._run_upstream(started)

    def _run_downstream(self, started: float) -> dict:
        fault = self.server.fault
        if fault == "cpu":
            _fib(self.server.fib_n)
        elif fault == "slow":
            time.sleep(self.server.delay_ms / 1000)
        elif fault == "mixed":
            _fib(self.server.fib_n)
            time.sleep(self.server.delay_ms / 1000)
        return {
            "service": "service-b",
            "fault": fault,
            "pid": os.getpid(),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    def _run_upstream(self, started: float):
        try:
            with urllib.request.urlopen(self.server.downstream, timeout=self.server.timeout_sec) as response:
                downstream = json.loads(response.read().decode("utf-8"))
            return self._json(200, {
                "service": "service-a",
                "pid": os.getpid(),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "downstream": downstream,
            })
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return self._json(502, {
                "service": "service-a",
                "error": type(exc).__name__,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            })

    def _json(self, status: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args):
        if self.server.verbose:
            super().log_message(fmt, *args)


def serve(args):
    server = ThreadingHTTPServer((args.bind, args.port), LabHandler)
    server.role = args.command
    server.fault = getattr(args, "fault", "none")
    server.delay_ms = getattr(args, "delay_ms", 300)
    server.fib_n = getattr(args, "fib_n", 30)
    server.downstream = getattr(args, "downstream", "")
    server.timeout_sec = getattr(args, "timeout_sec", 3.0)
    server.verbose = args.verbose
    print(json.dumps({
        "event": "service_started", "role": server.role, "pid": os.getpid(),
        "bind": args.bind, "port": args.port, "fault": server.fault,
        "downstream": server.downstream or None,
    }, ensure_ascii=False), flush=True)
    server.serve_forever()


def run_load(args):
    deadline = time.monotonic() + args.duration
    lock = threading.Lock()
    stats = {"requests": 0, "success": 0, "failed": 0, "latencies_ms": []}

    def worker():
        while time.monotonic() < deadline:
            started = time.perf_counter()
            ok = False
            try:
                with urllib.request.urlopen(args.url, timeout=args.timeout_sec) as response:
                    response.read()
                    ok = 200 <= response.status < 300
            except Exception:
                ok = False
            elapsed = (time.perf_counter() - started) * 1000
            with lock:
                stats["requests"] += 1
                stats["success" if ok else "failed"] += 1
                stats["latencies_ms"].append(elapsed)
            if args.interval_ms:
                time.sleep(args.interval_ms / 1000)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(worker) for _ in range(args.concurrency)]
        for future in futures:
            future.result()
    values = sorted(stats.pop("latencies_ms"))
    stats["p50_ms"] = round(values[int(len(values) * 0.50)], 2) if values else None
    stats["p95_ms"] = round(values[min(len(values) - 1, int(len(values) * 0.95))], 2) if values else None
    print(json.dumps(stats, ensure_ascii=False))


def run_noise(args):
    deadline = time.monotonic() + args.duration
    if args.fault == "cpu":
        while time.monotonic() < deadline:
            _fib(args.fib_n)
        return
    path = Path(args.io_path)
    block = os.urandom(1024 * 1024)
    try:
        with path.open("wb", buffering=0) as handle:
            while time.monotonic() < deadline:
                handle.write(block)
                handle.flush()
                os.fsync(handle.fileno())
    finally:
        path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("service-a", "service-b"):
        item = sub.add_parser(name)
        item.add_argument("--bind", default="0.0.0.0")
        item.add_argument("--port", type=int, default=18081 if name == "service-a" else 18082)
        item.add_argument("--verbose", action="store_true")
        if name == "service-a":
            item.add_argument("--downstream", required=True)
            item.add_argument("--timeout-sec", type=float, default=3.0)
        else:
            item.add_argument("--fault", choices=["none", "cpu", "slow", "mixed"], default="none")
            item.add_argument("--delay-ms", type=int, default=300)
            item.add_argument("--fib-n", type=int, default=30)
    load = sub.add_parser("load")
    load.add_argument("--url", required=True)
    load.add_argument("--duration", type=int, default=60)
    load.add_argument("--concurrency", type=int, default=4)
    load.add_argument("--timeout-sec", type=float, default=5.0)
    load.add_argument("--interval-ms", type=int, default=10)
    noise = sub.add_parser("noise")
    noise.add_argument("--fault", choices=["cpu", "io"], required=True)
    noise.add_argument("--duration", type=int, default=60)
    noise.add_argument("--fib-n", type=int, default=30)
    noise.add_argument("--io-path", default="/tmp/mini-drop-noise.bin")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command in {"service-a", "service-b"}:
        serve(args)
    elif args.command == "load":
        run_load(args)
    else:
        run_noise(args)


if __name__ == "__main__":
    main()
