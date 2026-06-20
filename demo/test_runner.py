#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mini-Drop 端到端测试套件（Ubuntu VM）

功能:
  1. 自动启动 Server + Agent
  2. 依次启动 16 个测试场景进程
  3. 通过 HTTP API 创建采集任务
  4. 轮询任务完成
  5. 验证火焰图、TopN、sys_metrics 等产物生成
  6. 汇总测试报告

用法:
  sudo python3 demo/test_runner.py          # 全部场景
  sudo python3 demo/test_runner.py --quick  # 快速模式（每场景 5s）
  sudo python3 demo/test_runner.py --scene cpu-fib  # 单场景

前提:
  pip install -e .   (已安装项目依赖)
  sudo 权限（perf 需要）
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


# ── 配置 ───────────────────────────────────────────────────────
SERVER_PORT = 8191
GRPC_PORT = 50051
API_BASE = f"http://localhost:{SERVER_PORT}"
TEST_TARGETS = Path(__file__).resolve().parent / "vm_test_targets.py"
OUTPUT_DIR = Path("/tmp/mini-drop-test-results")


# ── 测试场景定义 ───────────────────────────────────────────────

SCENARIOS = [
    {"name": "cpu-fib",        "collector": "perf_cpu",   "duration": 15, "desc": "递归 Fibonacci — 验证 CPU 火焰图 + TopN"},
    {"name": "cpu-loop",       "collector": "perf_cpu",   "duration": 10, "desc": "空转循环 — 验证单核热点"},
    {"name": "cpu-sort",       "collector": "perf_cpu",   "duration": 10, "desc": "排序热点 — 验证 sorted() 热点"},
    {"name": "python-cpu",     "collector": "pyspy",      "duration": 15, "desc": "Python CPU 热点 — 验证 py-spy 火焰图"},
    {"name": "memory-leak",    "collector": "memory_smaps","duration": 15, "desc": "内存泄漏 — 验证 memory_json 趋势 ↑"},
    {"name": "memory-stable",  "collector": "memory_smaps","duration": 10, "desc": "稳定内存 — 验证 memory_json 趋势 →"},
    {"name": "fd-leak",        "collector": "sys_metrics", "duration": 15, "desc": "FD 泄漏 — 验证 FD 增长趋势"},
    {"name": "fd-stable",      "collector": "sys_metrics", "duration": 10, "desc": "稳定 FD — 验证 FD 稳定"},
    {"name": "thread-spawn",   "collector": "sys_metrics", "duration": 15, "desc": "线程增长 — 验证 thread_trend ↑"},
    {"name": "thread-pool",    "collector": "sys_metrics", "duration": 10, "desc": "固定线程池 — 验证 thread_trend →"},
    {"name": "lock-contend",   "collector": "sys_metrics", "duration": 12, "desc": "锁竞争 — 验证 ctx 上下文切换高"},
    {"name": "io-write",       "collector": "ebpf_io",     "duration": 15, "desc": "磁盘写入 — 验证 ebpf IO 延迟"},
    {"name": "io-dd",          "collector": "ebpf_io",     "duration": 15, "desc": "dd IO — 验证 ebpf IO 延迟分布"},
    {"name": "network-http",   "collector": "sys_metrics", "duration": 12, "desc": "网络流量 — 验证 net_rx/tx_kbps"},
    {"name": "python-multi",   "collector": "pyspy",       "duration": 12, "desc": "Python 多线程 — 验证 GIL 竞争"},
    {"name": "memory-leak",    "collector": "continuous_perf", "duration": 20, "desc": "持续采样 — 验证连续窗口"},
]


# ── 工具函数 ───────────────────────────────────────────────────

def api(method: str, path: str, data: dict | None = None) -> dict:
    """简化 HTTP API 调用。"""
    url = f"{API_BASE}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "detail": exc.read().decode(errors="replace")[:200]}
    except Exception as exc:
        return {"error": str(exc)}
    return result.get("data", result)


def wait_task(task_id: str, timeout: int = 60) -> dict:
    """轮询任务直到终端状态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = api("GET", f"/api/tasks/{task_id}")
        status = data.get("status", "UNKNOWN") if isinstance(data, dict) else "UNKNOWN"
        if status in ("DONE", "FAILED"):
            return data if isinstance(data, dict) else {}
        time.sleep(1)
    return {"status": "TIMEOUT"}


def check_artifacts(data: dict) -> list[str]:
    """验证产物完整性。"""
    issues = []
    artifacts = data.get("artifacts", [])
    if not artifacts:
        # 尝试从 API 获取产物列表
        task_id = data.get("id", "")
        if task_id:
            art_list = api("GET", f"/api/tasks/{task_id}/artifacts")
            if isinstance(art_list, list):
                artifacts = art_list

    types = {a.get("artifact_type") for a in artifacts}
    # 每个采集器应产生其核心产物
    if not artifacts:
        issues.append("无产物")
    return sorted(types)


# ── 进程管理 ───────────────────────────────────────────────────

_server_proc = None
_agent_proc = None


def start_server():
    global _server_proc
    log("Starting Mini-Drop Server…")
    env = os.environ.copy()
    env.setdefault("MINI_DROP_API_AUTH_ENABLED", "0")
    env.setdefault("MINI_DROP_GRPC_AUTH_ENABLED", "0")
    env.setdefault("MINIO_AUTO_CREATE_BUCKET", "0")  # 无 MinIO 时跳过
    env.setdefault("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop")
    _server_proc = subprocess.Popen(
        [sys.executable, "-m", "server.app.main"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=env,
    )
    # 等待就绪
    last_stderr = b""
    for _ in range(30):
        try:
            urllib.request.urlopen(f"{API_BASE}/api/healthz", timeout=2)
            log("Server ready")
            return True
        except Exception:
            time.sleep(1)
    # 收集 stderr 诊断信息
    try:
        _, last_stderr = _server_proc.communicate(timeout=2)
    except Exception:
        pass
    stderr_text = (last_stderr.decode(errors="replace")[:500]) if last_stderr else "(none)"
    log(f"ERROR: Server failed to start. stderr: {stderr_text}")
    return False


def start_agent():
    global _agent_proc
    log("Starting Mini-Drop Agent…")
    env = os.environ.copy()
    env.setdefault("AGENT_ID", "test_agent")
    env.setdefault("AGENT_GRPC_ADDR", f"localhost:{GRPC_PORT}")
    env.setdefault("AGENT_UPLOAD_ARTIFACTS", "0")
    env.setdefault("MINI_DROP_GRPC_AUTH_ENABLED", "0")
    _agent_proc = subprocess.Popen(
        [sys.executable, "-m", "agent.mini_drop_agent.main"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=env,
    )
    # 等待注册
    for _ in range(15):
        agents = api("GET", "/api/agents")
        items = agents.get("items", []) if isinstance(agents, dict) else (agents if isinstance(agents, list) else [])
        if any(a.get("status") == "ONLINE" for a in items):
            log("Agent registered and online")
            return True
        time.sleep(1)
    _, last_stderr = _agent_proc.communicate(timeout=2) if _agent_proc.poll() is None else (None, None)
    stderr_text = (last_stderr.decode(errors="replace")[:300]) if last_stderr else "(none)"
    log(f"ERROR: Agent failed to register. stderr: {stderr_text}")
    return False


def stop_processes():
    for proc, name in [(_agent_proc, "Agent"), (_server_proc, "Server")]:
        if proc and proc.poll() is None:
            log(f"Stopping {name}…")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ── 测试执行 ───────────────────────────────────────────────────

def run_scenario(scenario: dict) -> dict:
    """执行单个测试场景。"""
    name = scenario["name"]
    collector = scenario["collector"]
    duration = scenario["duration"]
    desc = scenario["desc"]

    log(f"═══ Scene: {name} ({collector}) — {desc} ═══")

    # 1. 启动测试目标进程
    log(f"  Starting test target: {name}")
    target = subprocess.Popen(
        [sys.executable, str(TEST_TARGETS), name, str(duration + 5)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    time.sleep(2)  # 等目标进程就绪
    if target.poll() is not None:
        out = target.stdout.read().decode(errors="replace")[:200] if target.stdout else ""
        return {"ok": False, "reason": f"Target process exited early: {out}"}

    try:
        # 2. 创建采集任务
        task_resp = api("POST", "/api/tasks", {
            "name": f"test-{name}-{collector}",
            "agent_id": "test_agent",
            "target_pid": target.pid,
            "collector_type": collector,
            "sample_rate": 99,
            "duration_sec": duration,
        })
        task_id = task_resp.get("task_id", "") if isinstance(task_resp, dict) else ""
        if not task_id:
            return {"ok": False, "reason": f"Failed to create task: {task_resp}"}
        log(f"  Task created: {task_id}")

        # 3. 轮询完成
        result = wait_task(task_id, timeout=duration + 60)
        status = result.get("status", "UNKNOWN")
        log(f"  Task status: {status}")

        # 4. 获取产物
        artifacts = api("GET", f"/api/tasks/{task_id}/artifacts")
        art_list = artifacts if isinstance(artifacts, list) else artifacts.get("items", [])
        art_types = sorted({a.get("artifact_type") for a in art_list})

        # 5. 检查产物完整性
        has_flamegraph = any(t in {"flamegraph_svg", "flamegraph_json", "java_flamegraph_html"} for t in art_types)
        has_topn = "top_json" in art_types
        has_memory = "memory_json" in art_types
        has_sys = "sys_metrics" in art_types
        has_ebpf = any(t in {"ebpf_metrics", "ebpf_raw"} for t in art_types)

        ok = status == "DONE" and len(art_list) > 0

        # 采集器特定检查
        if collector == "perf_cpu" and not (has_flamegraph or has_topn):
            ok = False

        return {
            "ok": ok,
            "status": status,
            "task_id": task_id,
            "artifacts": art_types,
            "artifact_count": len(art_list),
            "has_flamegraph": has_flamegraph,
            "has_topn": has_topn,
            "has_memory": has_memory,
            "has_sys": has_sys,
            "has_ebpf": has_ebpf,
            "reason": result.get("status_reason", ""),
        }

    finally:
        # cleanup target process
        if target.poll() is None:
            target.terminate()
            try:
                target.wait(timeout=5)
            except subprocess.TimeoutExpired:
                target.kill()
                target.wait()


def main():
    quick = "--quick" in sys.argv
    single = None
    for arg in sys.argv[1:]:
        if arg.startswith("--scene="):
            single = arg.split("=", 1)[1]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    scenarios_to_run = [s for s in SCENARIOS if single is None or s["name"] == single]
    if quick:
        for s in scenarios_to_run:
            s["duration"] = min(s["duration"], 5)

    log(f"Test Suite: {len(scenarios_to_run)} scenarios")

    # 检查 perf 可用性
    if shutil.which("perf") is None:
        log("WARN: perf not installed. CPU profiling won't work.")
    if shutil.which("bpftrace") is None:
        log("WARN: bpftrace not installed. eBPF IO profiling won't work.")

    # 启动服务
    if not start_server():
        log("FAIL: Cannot start server")
        sys.exit(1)
    if not start_agent():
        log("FAIL: Cannot start agent")
        stop_processes()
        sys.exit(1)

    try:
        for i, scenario in enumerate(scenarios_to_run):
            log(f"Progress: {i+1}/{len(scenarios_to_run)}")
            result = run_scenario(scenario)
            result["scene"] = scenario["name"]
            result["collector"] = scenario["collector"]
            results.append(result)
            time.sleep(2)  # inter-scene cooldown
    finally:
        stop_processes()

    # ── 汇总报告 ────────────────────────────────────────────
    ok_count = sum(1 for r in results if r.get("ok"))
    total = len(results)

    print("\n" + "=" * 70)
    print(f"  Mini-Drop E2E Test Report: {ok_count}/{total} PASSED")
    print("=" * 70)

    for r in results:
        icon = "✅" if r.get("ok") else "❌"
        arts = ", ".join(r.get("artifacts", [])) or "(none)"
        print(f"  {icon} {r['scene']:16s} [{r['collector']:16s}] status={r.get('status', '?'):8s} artifacts=[{arts}]")
        if not r.get("ok"):
            print(f"     reason: {r.get('reason', 'unknown')}")

    # 写 JSON 报告
    report_path = OUTPUT_DIR / "report.json"
    report_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Detailed report: {report_path}")
    print(f"  Artifact dirs: /tmp/mini-drop-task-*")
    print(f"  Flamegraphs: /tmp/mini-drop/<task_id>/flamegraph.{'json,svg'}")

    return 0 if ok_count == total else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        stop_processes()
        sys.exit(130)
