"""由 cluster_report.json 生成单文件可视化 HTML 报告。"""
import json
import os
import html

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "cluster_report.json"), encoding="utf-8") as f:
    R = json.load(f)

health = R["health"]
nodes = R["nodes"]
agents = R["agents"]
local_has = R["local_has"]

CAP_LABELS = {
    "perf_cpu": "perf CPU 采样", "continuous_perf": "持续 perf", "ebpf_io": "eBPF I/O",
    "pyspy": "py-spy 火焰图", "java_async": "async-profiler", "go_pprof": "Go pprof",
    "memory_smaps": "内存 smaps", "sys_metrics": "系统指标", "process_scan": "进程发现",
    "log_scan": "日志探针",
}
SVC_LABELS = {"mini-drop-server": "Server API", "mini-drop-s3": "S3 存储", "nginx": "HTTPS 入口", "mini-drop-agent": "Agent"}


def svc_badge(node):
    order = ["mini-drop-server", "mini-drop-s3", "nginx", "mini-drop-agent"]
    svcs = node["data"].get("SERVICES", "").split(",")
    out = []
    for i, name in enumerate(order):
        if i >= len(svcs):
            continue
        st = svcs[i]
        cls = {"active": "ok", "inactive": "muted", "failed": "bad", "activating": "warn"}.get(st, "warn")
        out.append(f'<span class="badge {cls}">{SVC_LABELS[name]}<b>{st}</b></span>')
    return "".join(out)


def agent_card(a):
    cls = "ok" if a["status"] == "ONLINE" else "bad"
    caps = "".join(
        f'<span class="cap {"newcap" if c in ("process_scan", "log_scan") else ""}">{CAP_LABELS.get(c, c)}</span>'
        for c in a["capabilities"]
    )
    hb = a.get("last_heartbeat_at", "")[:19].replace("T", " ")
    m = a.get("latest_metrics", {}).get("self", {})
    return f"""
    <div class="agent">
      <div class="agent-head"><span class="dot {cls}"></span>{a['id']} <i>({a['hostname']} · {a['ip_addr']})</i>
        <span class="badge {cls}">{a['status']}</span></div>
      <div class="agent-sub">版本 {a['version']} · 心跳 {hb}</div>
      <div class="agent-meta">Agent 自身 CPU {m.get('cpu_percent', '-')}% · RSS {m.get('rss_mb', '-')} MB</div>
      <div class="caps">{caps}</div>
    </div>"""


def node_card(n):
    d = n["data"]
    mem_total, mem_used, mem_avail = d.get("MEM", "?|?|?").split("|")
    disk_size, disk_used, disk_pct = d.get("DISK", "?|?|?").split("|")
    try:
        mem_pct = round(float(mem_used) / float(mem_total) * 100)
    except Exception:
        mem_pct = 0
    disk_num = int(disk_pct.strip("%"))
    return f"""
    <div class="node">
      <div class="node-head">
        <div><span class="nlabel">{n['label']}</span><div class="nrole">{n['role']}</div></div>
        <div class="nip">{n['ip']}</div>
      </div>
      <div class="nrow"><span>内核</span><b>{d.get('KERNEL', '-')}</b></div>
      <div class="nrow"><span>启动时间</span><b>{d.get('UPTIME', '-')}</b></div>
      <div class="nrow"><span>负载 (1/5/15m)</span><b>{d.get('LOAD', '-')}</b></div>
      <div class="bar-row"><span>内存 {mem_used}/{mem_total} MB</span><div class="bar"><div class="fill" style="width:{mem_pct}%"></div></div></div>
      <div class="bar-row"><span>磁盘 {disk_used}/{disk_size}（{disk_pct}）</span><div class="bar"><div class="fill disk" style="width:{disk_num}%"></div></div></div>
      <div class="svcs">{svc_badge(n)}</div>
      <div class="docker">Docker：<b class="{ 'ok' if d.get('DOCKER')=='active' else 'bad' }">{d.get('DOCKER', '-')}</b></div>
      <div class="active">代码目录：<code>{html.escape(d.get('ACTIVE', '-'))}</code></div>
    </div>"""


agents_by_host = {a["hostname"]: a for a in agents}
node_cards = ""
agent_cards = ""
for n in nodes:
    node_cards += node_card(n)
for a in agents:
    agent_cards += agent_card(a)

local_rows = "".join(
    f"<tr><td>{html.escape(k)}</td><td class='{'ok' if v else 'bad'}'>{'已实现 ✓' if v else '缺失 ✗'}</td></tr>"
    for k, v in local_has.items()
)

cap_diff = ""
missing_on_cluster = [c for c in ("process_scan", "log_scan") if c not in agents[0]["capabilities"]]
if missing_on_cluster:
    cap_diff = f"""
    <div class="warnbox">
      <b>⚠ 集群 Agent 尚未注册新能力：</b>{', '.join(CAP_LABELS.get(c, c) for c in missing_on_cluster)}。
      需要将本地最新代码部署到集群并重启 Agent 后才会出现（部署后这些采集器将出现在能力列表中）。
    </div>"""

html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Mini-Drop 三节点集群状态报告</title>
<style>
  :root {{ --ok:#16a34a; --bad:#dc2626; --warn:#d97706; --muted:#6b7280; --line:#e5e7eb; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif; margin: 0; background: #f3f4f6; color: #111827; }}
  .wrap {{ max-width: 1180px; margin: 0 auto; padding: 24px 16px 60px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }} h2 {{ font-size: 16px; margin: 28px 0 12px; border-left: 4px solid #2563eb; padding-left: 8px; }}
  .sub {{ color: var(--muted); font-size: 13px; margin-bottom: 16px; }}
  .healthbar {{ display:flex; align-items:center; gap:14px; background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px 18px; margin-bottom:8px; }}
  .bigdot {{ width:14px; height:14px; border-radius:50%; }}
  .ok {{ color: var(--ok); }} .bad {{ color: var(--bad); }} .warn {{ color: var(--warn); }} .muted {{ color: var(--muted); }}
  .healthbar .bigdot {{ background: var(--ok); }}
  .chips {{ display:flex; gap:8px; flex-wrap:wrap; }}
  .chip {{ background:#f0fdf4; border:1px solid #bbf7d0; color:#166534; border-radius:20px; padding:3px 12px; font-size:12px; }}
  .chip.warn {{ background:#fffbeb; border-color:#fde68a; color:#92400e; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap:16px; margin-top:8px; }}
  .node, .agent {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:16px; }}
  .node-head {{ display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:10px; }}
  .nlabel {{ font-size:17px; font-weight:600; }}
  .nrole {{ color:var(--muted); font-size:12px; margin-top:2px; }}
  .nip {{ font-family: Consolas, monospace; color:#2563eb; font-size:13px; }}
  .nrow {{ display:flex; justify-content:space-between; font-size:13px; padding:3px 0; color:var(--muted); }}
  .nrow b {{ color:#111827; font-weight:500; }}
  .bar-row {{ font-size:12px; color:var(--muted); margin-top:8px; }}
  .bar {{ height:6px; background:#eef2f7; border-radius:3px; overflow:hidden; margin-top:3px; }}
  .fill {{ height:100%; background:#2563eb; }} .fill.disk {{ background:#7c3aed; }}
  .svcs {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }}
  .badge {{ font-size:11px; border-radius:6px; padding:2px 8px; display:inline-flex; gap:6px; align-items:center; }}
  .badge.ok {{ background:#f0fdf4; color:#166534; border:1px solid #bbf7d0; }}
  .badge.bad {{ background:#fef2f2; color:#991b1b; border:1px solid #fecaca; }}
  .badge.warn {{ background:#fffbeb; color:#92400e; border:1px solid #fde68a; }}
  .badge.muted {{ background:#f9fafb; color:#6b7280; border:1px solid #e5e7eb; }}
  .docker {{ font-size:12px; margin-top:8px; color:var(--muted); }}
  .active {{ font-size:11px; margin-top:8px; color:var(--muted); }} .active code {{ background:#f3f4f6; padding:1px 6px; border-radius:4px; }}
  .agent {{ margin-bottom:12px; }}
  .agent-head {{ font-weight:600; font-size:14px; display:flex; align-items:center; gap:8px; }}
  .agent-head i {{ color:var(--muted); font-weight:400; font-size:12px; }}
  .dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; }}
  .dot.ok {{ background:var(--ok); }} .dot.bad {{ background:var(--bad); }}
  .agent-sub, .agent-meta {{ color:var(--muted); font-size:12px; margin-top:4px; }}
  .caps {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }}
  .cap {{ font-size:11px; background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; border-radius:6px; padding:2px 8px; }}
  .cap.newcap {{ background:#fef3c7; color:#92400e; border-color:#fde68a; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
  th, td {{ text-align:left; padding:8px 12px; font-size:13px; border-bottom:1px solid var(--line); }}
  th {{ background:#f9fafb; }}
  .warnbox {{ background:#fffbeb; border:1px solid #fde68a; color:#92400e; border-radius:12px; padding:12px 16px; font-size:13px; margin:12px 0; }}
  .okbox {{ background:#f0fdf4; border:1px solid #bbf7d0; color:#166534; border-radius:12px; padding:12px 16px; font-size:13px; margin:12px 0; }}
  .concl {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:16px; font-size:14px; line-height:1.8; }}
  .concl li {{ margin:6px 0; }}
</style></head><body><div class="wrap">
  <h1>🖥 Mini-Drop 三节点集群状态报告</h1>
  <div class="sub">生成时间 {R['generated_at']} · 集群 192.168.10.10/24 · 密码登录已连通</div>

  <div class="healthbar">
    <div class="bigdot"></div>
    <div><b>API 健康检查：healthy = {str(health['healthy']).lower()}</b>
    <span class="sub" style="margin:0 0 0 8px">数据库 {health['checks']['database']['status']} · 对象存储 {health['checks']['storage']['status']} · 分析器 {health['checks']['analyzer']['status']}（{health['checks']['analyzer'].get('workers_online','-')} worker 在线）</span></div>
  </div>

  <h2>节点状态</h2>
  <div class="grid">{node_cards}</div>

  <h2>Agent 注册与在线状态</h2>
  {agent_cards}
  {cap_diff}

  <h2>代码版本对比（本地开发版 vs 集群部署版）</h2>
  <table><tr><th>功能</th><th>本地 mini-drop-new</th></tr>{local_rows}</table>
  <div class="warnbox" style="margin-top:12px">
    <b>⚠ 集群部署版本</b>：control → <code>{html.escape(R['cluster_active'])}</code>；worker1/worker2 → <code>mini-drop-release-20260805-fix-v1</code>。
    两者均<b>不含</b>上方表格中的本地最新功能（进程发现、日志探针、动作网关、恢复验证、多轮引导、两页前端）。
    如需在集群上运行这些功能，需将本地最新代码打包部署并重启 Server 与 Agent。
  </div>

  <h2>结论</h2>
  <div class="concl">
    <ul>
      <li>✅ 三台虚拟机均已启动，SSH（密码认证）全部连通，网络互通正常。</li>
      <li>✅ control 的 Server / S3 / Nginx 均为 <b>active</b>，/api/healthz 返回 healthy=true。</li>
      <li>✅ worker1、worker2 的 Agent 均为 <b>ONLINE</b>，心跳正常，Docker 可用，根分区剩余约 62-63 GB。</li>
      <li>✅ 两个真实 worker 共注册 8 种采集能力；<span class="warn">demo-worker 已离线（历史演示节点，可忽略）</span>。</li>
      <li>⚠ worker1 声明了 <b>java_async</b> 能力但未检测到 async-profiler 可执行文件——旧版 Agent 为固定声明，部署新版后会自动纠正。</li>
      <li>⚠ 集群部署的是 <b>旧版本代码</b>（20260805/20260806），不含本地最新功能，需部署同步后才能跑 GitHub 评测集与多轮诊断。</li>
    </ul>
  </div>
</div></body></html>"""

out = os.path.join(HERE, "cluster_report.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html_doc)
print("written:", out, len(html_doc), "bytes")
