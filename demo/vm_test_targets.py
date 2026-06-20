#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mini-Drop 测试程序套件 —— 多场景 CPU/IO/Memory/FD/Thread/Lock 压测目标。

每个测试程序运行指定秒数后自动退出，方便 Mini-Drop Agent 采集。
用法: python3 vm_test_targets.py <scenario> [duration_sec]

场景:
  cpu-fib       递归 Fibonacci（纯 CPU，热点在递归函数）
  cpu-loop      空转循环（100% 单核 CPU）
  cpu-sort      排序热点（数组反复排序）
  io-write      磁盘顺序写入
  io-dd         调用 dd 产生块设备 IO
  memory-leak   持续分配内存（RSS 增长）
  memory-stable 稳定大内存占用（RSS 恒定）
  fd-leak       打开 FD 不关闭（FD 持续增长）
  fd-stable     打开 FD 后保持稳定
  thread-spawn  持续创建线程（线程数增长）
  thread-pool   固定线程池执行（线程数稳定）
  lock-contend  多线程锁竞争（上下文切换高）
  network-http  启动 HTTP 服务（产生网络流量）
  python-cpu    Python CPU 热点（用于 py-spy 测试）
  python-multi  Python 多线程混合负载
"""

import hashlib
import http.server
import os
import random
import socket
import sys
import threading
import time


# ═══════════════════════════════════════════════════════════════
# CPU 场景
# ═══════════════════════════════════════════════════════════════

def fib(n: int) -> int:
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)


def run_cpu_fib(duration: int):
    """递归 Fibonacci，热点集中在 fib() 函数。"""
    deadline = time.time() + duration
    while time.time() < deadline:
        fib(30)


def run_cpu_loop(duration: int):
    """空转循环——100% 单核 CPU。"""
    deadline = time.time() + duration
    while time.time() < deadline:
        _ = sum(i * i for i in range(10000))


def run_cpu_sort(duration: int):
    """反复生成大数组并排序——热点在 sorted() / list.sort()。"""
    deadline = time.time() + duration
    data = list(range(50000))
    while time.time() < deadline:
        random.shuffle(data)
        _ = sorted(data)


# ═══════════════════════════════════════════════════════════════
# IO 场景
# ═══════════════════════════════════════════════════════════════

def run_io_write(duration: int):
    """使用 Python 进行顺序磁盘写入（触发 vfs_write）。"""
    fname = "/tmp/mini_drop_io_test.bin"
    deadline = time.time() + duration
    chunk = os.urandom(1024 * 1024)  # 1MB 块
    with open(fname, "wb") as fh:
        while time.time() < deadline:
            fh.write(chunk)
            fh.flush()
    os.unlink(fname)


def run_io_dd(duration: int):
    """调用系统 dd 产生块设备 IO（触发 blk_mq）。"""
    deadline = time.time() + duration
    while time.time() < deadline:
        os.system("dd if=/dev/zero of=/tmp/mini_drop_dd_test bs=4M count=32 oflag=direct 2>/dev/null")
    try:
        os.unlink("/tmp/mini_drop_dd_test")
    except FileNotFoundError:
        pass


# ═══════════════════════════════════════════════════════════════
# 内存场景
# ═══════════════════════════════════════════════════════════════

def run_memory_leak(duration: int):
    """持续分配内存不释放——RSS 随时间增长。"""
    deadline = time.time() + duration
    chunks = []
    while time.time() < deadline:
        chunks.append(bytearray(1024 * 1024))  # 1MB per chunk
        time.sleep(0.5)


def run_memory_stable(duration: int):
    """分配大内存后保持稳定——RSS 恒定。"""
    big = bytearray(200 * 1024 * 1024)  # 200MB
    # 写入数据确保实际分配
    for i in range(0, len(big), 4096):
        big[i] = 0x5A
    print(f"Allocated {len(big) // (1024*1024)} MB, holding for {duration}s…")
    time.sleep(duration)
    _ = len(big)  # 防止优化掉


# ═══════════════════════════════════════════════════════════════
# FD 场景
# ═══════════════════════════════════════════════════════════════

def run_fd_leak(duration: int):
    """持续打开 FD 不关闭——FD 数量随时间增长。"""
    deadline = time.time() + duration
    fds = []
    while time.time() < deadline:
        try:
            fh = open("/dev/null", "r")
            fds.append(fh)
        except OSError:
            break
        time.sleep(0.1)
    # cleanup
    for fh in fds:
        fh.close()


def run_fd_stable(duration: int):
    """打开一批 FD 后保持稳定。"""
    fds = [open("/dev/null", "r") for _ in range(100)]
    time.sleep(duration)
    for fh in fds:
        fh.close()


# ═══════════════════════════════════════════════════════════════
# 线程场景
# ═══════════════════════════════════════════════════════════════

def _thread_worker(duration: int):
    deadline = time.time() + duration
    while time.time() < deadline:
        _ = sum(i * i for i in range(5000))
        time.sleep(0.01)


def run_thread_spawn(duration: int):
    """持续创建线程——线程数随时间增长。"""
    deadline = time.time() + duration
    threads = []
    while time.time() < deadline:
        t = threading.Thread(target=_thread_worker, args=(duration,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.2)
    for t in threads:
        t.join(timeout=2)


def run_thread_pool(duration: int):
    """固定线程池执行——线程数稳定。"""
    n = 16
    threads = [threading.Thread(target=_thread_worker, args=(duration,), daemon=True) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=duration + 5)


# ═══════════════════════════════════════════════════════════════
# 锁竞争场景
# ═══════════════════════════════════════════════════════════════

def run_lock_contend(duration: int):
    """多线程竞争同一把锁——高上下文切换率。"""
    lock = threading.Lock()
    counter = [0]

    def contend():
        deadline = time.time() + duration
        while time.time() < deadline:
            with lock:
                counter[0] += 1

    threads = [threading.Thread(target=contend, daemon=True) for _ in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=duration + 5)


# ═══════════════════════════════════════════════════════════════
# 网络场景
# ═══════════════════════════════════════════════════════════════

def run_network_http(duration: int):
    """启动一个简单的 HTTP 服务器并反复请求——产生网络流量。"""
    import urllib.request

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            data = b"X" * 65536  # 64KB response
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.HTTPServer(("127.0.0.1", 19999), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.5)

    deadline = time.time() + duration
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://127.0.0.1:19999/", timeout=2).read()
        except Exception:
            pass

    server.shutdown()


# ═══════════════════════════════════════════════════════════════
# Python CPU 场景（py-spy 测试）
# ═══════════════════════════════════════════════════════════════

def run_python_cpu(duration: int):
    """纯 Python CPU 热点——适合 py-spy 采集。"""
    deadline = time.time() + duration
    while time.time() < deadline:
        # 多个不同的 Python 函数形成调用栈
        data = [random.random() for _ in range(100000)]
        data.sort()
        _ = hashlib.sha256(str(data).encode()).hexdigest()


def _py_worker(duration: int):
    deadline = time.time() + duration
    while time.time() < deadline:
        run_python_cpu(1)


def run_python_multi(duration: int):
    """多线程 Python 混合负载（GIL 竞争）。"""
    threads = [threading.Thread(target=_py_worker, args=(duration,), daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=duration + 5)


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

SCENARIOS = {
    "cpu-fib":        run_cpu_fib,
    "cpu-loop":       run_cpu_loop,
    "cpu-sort":       run_cpu_sort,
    "io-write":       run_io_write,
    "io-dd":          run_io_dd,
    "memory-leak":    run_memory_leak,
    "memory-stable":  run_memory_stable,
    "fd-leak":        run_fd_leak,
    "fd-stable":      run_fd_stable,
    "thread-spawn":   run_thread_spawn,
    "thread-pool":    run_thread_pool,
    "lock-contend":   run_lock_contend,
    "network-http":   run_network_http,
    "python-cpu":     run_python_cpu,
    "python-multi":   run_python_multi,
}


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <scenario> [duration_sec]")
        print(f"Available scenarios: {', '.join(sorted(SCENARIOS))}")
        sys.exit(1)

    scenario = sys.argv[1]
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    if scenario not in SCENARIOS:
        print(f"Unknown scenario: {scenario}")
        print(f"Available: {', '.join(sorted(SCENARIOS))}")
        sys.exit(1)

    print(f"[test-target] scenario={scenario} duration={duration}s pid={os.getpid()}")
    sys.stdout.flush()
    SCENARIOS[scenario](duration)
    print(f"[test-target] done")


if __name__ == "__main__":
    main()
