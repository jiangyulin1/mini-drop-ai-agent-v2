# Mini-Drop 未知拓扑跨主机自动发现与根因调查设计

> 设计日期：2026-08-21
> 目标：用户只提供一台机器上的 PID、容器或服务线索，系统在不知道完整集群拓扑的情况下，自动发现上下游主机与进程，在受控范围内递归采集、汇总证据并推断故障根因。

## 当前仓库落地状态（2026-08-21）

本仓库已经按现有 Python Agent、FastAPI Control、Case/Evidence 和 Pi Runtime 架构落地最小可用闭环，没有要求当前 macOS 开发机安装 Docker、本地模型或新的数据库：

- Agent 已实现 `network_discovery`：Linux 使用 `/proc/net/tcp{,6}` 与 FD/inode 归属，macOS 使用只读 `lsof` 降级。
- Control 已实现稳定进程身份、Endpoint 解析、有界 BFS、MembershipSnapshot、scope/revision fence 和 Case 级 discovery run。
- 发现任务仍全部经过 Collector Catalog、CollectionSupervisor、风险/能力/预算/幂等校验；未注册端点不会触发 SSH 或自动安装。
- 网络快照和最终依赖图会进入 CaseEvidence；工作区、Case API 和 Pi Agent 的 `get_dependency_graph` 读取同一聚合结果。
- Pi Agent 增加受控 `discover_topology` 工具，可从 Case scope 中的种子 PID 启动或推进发现；`COLLECTING` 后结束当前模型回合并等待 Evidence wakeup。
- 多快照聚合会把同一 TCP 四元组的 client/server 双端观察合并为一条边，并保留时间窗、观测点和全部 Evidence 引用。

当前明确未实现的长期增强包括：常驻 eBPF/SOCK_DIAG sidecar、DNS 历史、Kubernetes EndpointSlice/Docker Provider、L7/OTel Span 关联和真实多租户成员过滤。它们仍属于后续 Phase 3～5，不作为本次 MVP 已完成能力宣称。

### 当前环境的轻量真实验收

本次没有按未来 Phase 5 的完整测试集合执行，而是按照当前开发机和现有 DeepSeek/Pi 配置做了一轮最小真实验收：

- 输入只有同一台 macOS 上的 client PID，Agent 通过只读 `lsof` 降级路径发现 server PID 和一条 loopback TCP 依赖边。
- 发现到的 server 进程经过 `MembershipSnapshot + agent_id + boot_id + PID + process_start_time + entity_id` 校验后，才由 `CollectionSupervisor` 发起一次低开销补采；对应 Task 最终为 `DONE`，PID incarnation 校验为 `verified`。
- 4 条 canonical Evidence 被持久化，Case API、Workspace 和 Pi 工具读取到的依赖图 digest 完全一致。
- 真实 `deepseek-v4-flash` 回合完成 `discover_topology -> propose_collection -> Evidence analysis -> finish_investigation`；结论落库为 `PARTIALLY_CONFIRMED`。
- 由于现有证据只证明通信依赖，服务端门禁保持 CausalGraph 为空；最终结论没有声明根因，并保留 `insufficient_coverage`。
- 轮询间隔为 5 秒，本地 Artifact 上传关闭，未下载或启动本地模型。外部上传仅保留真实 provider prompt 所必需的流量。

本轮人工核验结论见
[`reports/evaluation/verified-20260821.md`](../reports/evaluation/verified-20260821.md)。后续运行产物默认写入被 Git 忽略的
`reports/eval/unknown-topology-<timestamp>/` 或
`reports/eval/unknown-topology-pi-<timestamp>/`，不再写入桌面。Pi 验收成功后只保留顶层结构化结果和脱敏日志；失败时保留 `runtime/` 供诊断，需要审计成功运行的原始 SQLite、事件 spool 或进程日志时显式传入 `--keep-runtime`。

```bash
python scripts/run_unknown_topology_e2e.py
python scripts/run_unknown_topology_pi_e2e.py
```

这轮结果只证明当前代码在“单机、单 Agent、两个真实 loopback TCP 进程”场景下闭环；它不等同于跨主机生产精度、完整集群覆盖、多轮稳定性或 PR 根因正确率。

## 1. 结论与选型

推荐采用以下组合，而不是直接引入一套新的完整可观测平台：

1. **Mini-Drop 自有轻量发现核心**：每台已注册 Agent 运行连接发现组件，采集进程、监听端口、TCP/UDP 连接、网络命名空间、容器/cgroup 与 DNS 线索。
2. **事件与快照混合采集**：eBPF 捕获新建连接和连接状态变化；`NETLINK_SOCK_DIAG` 周期快照补齐 Agent 启动前已存在的长连接和事件丢失。
3. **中央身份与关系解析**：Control 将 `IP:Port`、PID、容器、Agent、主机和编排器资源合并成版本化资源身份图，并保存每条边的证据、时间范围和置信度。
4. **预算约束的递归调查**：从种子 PID 出发，分别沿出站连接发现下游、沿入站连接发现上游；只对能映射到已注册 Agent 的远端执行进一步采集。
5. **现有 Evidence-native RCA 负责结论**：发现结果只建立候选拓扑，不直接等于因果关系；根因仍需由指标、日志、Profile、连接质量、基线和时间先后共同证明。
6. **Beyla/OpenTelemetry 作为可插拔增强**：有条件时消费其 OTLP Span、RED 指标和网络流指标；没有时仍能依靠 Mini-Drop 的 L4 发现和现有采集器工作。

不建议把 Cilium/Hubble、Pixie 或 DeepFlow 作为 Mini-Drop 的必选运行时：

- Hubble 自动服务图能力强，但依赖 Cilium 数据面，不能作为未知 VM、Docker、Swarm 和普通 Linux 的统一前提。
- Pixie 主要面向 Kubernetes，并包含自己的集群查询和存储控制面，与 Mini-Drop 重叠较多。
- DeepFlow 的零代码服务图最接近目标，但整套采用会重复 Mini-Drop 的 Agent、存储、查询和诊断控制面。适合作为设计参考或可选数据源。
- Beyla 可运行于任意 Linux，适合快速补充 L7/RED/Span 数据，但它不负责 Mini-Drop 的远端 Agent 匹配、递归调查、证据审批与 RCA 闭环。

## 2. 参考系统调研摘要

| 系统 | 可借鉴能力 | 主要限制 | Mini-Drop 用法 |
|---|---|---|---|
| Cilium Hubble | 节点观测经 Relay 汇总；自动生成 L3/L4/L7 服务依赖图；支持集群及跨集群视角 | 通常需要 Cilium/CNI | 参考节点事件、集中 Relay 和服务图聚合模式；可做可选连接器 |
| Grafana Beyla / OTel eBPF Instrumentation | 自动检查进程可执行文件和 OS 网络层；支持任意 Linux；输出 OTLP/Prometheus；可按监听端口、进程、容器发现服务 | L7 关联受语言/异步模型限制；不提供 Mini-Drop 调查调度 | 首选可选增强源，不作为拓扑唯一真源 |
| OpenTelemetry Service Graph Connector | 从 client/server、producer/consumer Span 配对生成服务边和延迟/失败指标；支持虚拟未插桩节点 | 必须获得足够 Span；跨 Collector 分散会导致配对失败 | 作为高置信 L7 依赖证据，与 L4 连接图合并 |
| Kubernetes EndpointSlice | Service 到健康后端 IP/端口的权威映射；包含拓扑信息 | 仅 Kubernetes；切片间可能出现重复端点 | Kubernetes 身份解析器必须聚合所有 Slice 并去重 |
| Pixie | 每节点 eBPF 自动遥测、集群内汇总、服务图和请求/Profile 下钻 | Kubernetes 优先，体系较完整且偏重 | 参考 per-node edge collection 模式，不直接嵌入 |
| DeepFlow | 任意服务零代码 Universal Map；按连接/请求维护 Flow；进程、网络和 OTel 多观测点关联；1 秒/1 分钟聚合 | 引入完整系统成本高，与现有平台能力重叠 | 参考 Flow 模型、observation point 和 AutoTagging 设计 |
| Linux SOCK_DIAG | 内核提供 socket 列表、状态、地址、队列和扩展信息；比文本解析 `/proc/net/*` 更稳定 | 只提供当前快照，不能单独恢复完整生命周期 | 用于启动补偿、周期对账和长连接发现 |

## 3. 完成定义

输入可以只有：

```json
{
  "agent_id": "worker-1",
  "pid": 1842,
  "incident_window": {
    "start": "2026-08-21T10:00:00Z",
    "end": "2026-08-21T10:10:00Z"
  }
}
```

系统应能完成：

```text
种子 PID
  -> 本地进程/容器/监听端口身份
  -> 当前及窗口内的出站、入站连接
  -> IP:Port 到远端 Agent/主机/容器/进程/服务的解析
  -> 对已解析远端进行受控补采
  -> 按最大跳数继续扩展上下游
  -> 对齐窗口、去重、聚合和异常检测
  -> 输出带证据引用和不确定性的依赖图与根因候选
```

必须允许以下结果，而不是强行给出根因：

- 远端主机没有注册 Agent：输出 `external_unmanaged_endpoint`。
- NAT、代理或负载均衡导致真实服务不可解析：保留 `virtual_endpoint`。
- 只有连接相关性而没有故障证据：只输出依赖关系，不输出因果结论。
- 覆盖率、时钟质量或身份置信度不足：输出 `insufficient_coverage`。

## 4. 总体架构

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Control                                                              │
│                                                                      │
│ Seed Resolver -> Discovery Frontier -> CollectionSupervisor/Fanout   │
│                         |                                            │
│                         v                                            │
│ Identity Reconciler -> Versioned Dependency Graph                    │
│                         |                                            │
│                         v                                            │
│ Edge Aggregator -> EvidenceProjection -> InvestigationState/AI       │
└──────────────────────────────────────────────────────────────────────┘
               ^                                  |
               | gRPC discovery events/tasks      | controlled probes
               |                                  v
┌──────────────────────────┐        ┌──────────────────────────┐
│ Agent A                  │        │ Agent B                  │
│ process inventory        │        │ process inventory        │
│ eBPF connect/accept/close│        │ eBPF connect/accept/close│
│ SOCK_DIAG reconciliation │        │ SOCK_DIAG reconciliation │
│ container/netns resolver │        │ container/netns resolver │
│ DNS observation          │        │ DNS observation          │
│ existing collectors      │        │ existing collectors      │
└──────────────────────────┘        └──────────────────────────┘
```

### 4.1 分层原则

分成四种不同语义的数据，禁止混用：

1. **DiscoveryEvent**：某时刻观察到的连接、监听、进程或 DNS 事件。
2. **IdentityAssertion**：某来源声明 IP、容器、PID 或服务之间的身份映射。
3. **DependencyEdge**：在时间窗内聚合得到的调用/连接关系。
4. **CausalAssessment**：经故障证据验证后的根因与传播关系。

连接边只能证明“通信发生过”，不能直接证明“对方导致了故障”。

## 5. Agent 侧设计

### 5.1 新增 Discovery Runtime

不建议继续用 Python 或 bpftrace 承担长期高频事件流。新增一个小型 `discovery-sidecar`，建议 Go + `cilium/ebpf` 或 Rust + libbpf-rs，编译为静态二进制，由现有 Python Agent 管理生命周期。

职责：

- 观察 `exec/exit`，维护 PID、启动时间、UID、cgroup、netns、容器 ID。
- 观察 TCP `connect/accept/close` 和失败结果。
- 记录 IPv4/IPv6、本地/远端地址、端口、PID/TGID、socket cookie、netns cookie。
- 可选记录 TCP reset、重传、RTT 与连接建立耗时。
- 在 Agent 本地按 1 秒窗口聚合，避免逐包或逐事件上传。
- 写入本地 spool，断网后可重放。

首版不解析完整报文，不采集 payload，降低安全、性能与协议维护成本。

### 5.2 事件 + SOCK_DIAG 对账

只使用 eBPF 会漏掉 Agent 启动前已建立的连接；只使用 SOCK_DIAG 会漏掉短连接。因此采用：

```text
启动时：SOCK_DIAG 全量快照
运行时：eBPF 增量事件
周期性：SOCK_DIAG 对账（建议 30~60 秒）
结束时：按 socket cookie / tuple / netns / process incarnation 合并
```

进程与 socket 的关联优先级：

1. eBPF 事件携带的 PID/TGID + socket cookie；
2. `/proc/<pid>/fd -> socket:[inode]` 与 SOCK_DIAG inode；
3. 只有网络 tuple、不能解析进程时，生成低置信 `host_endpoint`。

### 5.3 本机身份发现

每个进程生成稳定的 incarnation key：

```text
agent_id + boot_id + pid + process_start_time
```

附加信息包括：

- executable、comm、cmdline hash；
- container ID、container name/image；
- cgroup ID、netns inode；
- Docker/Swarm service/task labels；
- Kubernetes Pod UID、namespace、owner；
- listening ports；
- Agent 主机 IP 列表。

不能只用 PID 或 Pod 名作为稳定身份。

### 5.4 DNS 线索

DNS 用于增强 `IP:Port -> service name`，但不能作为唯一权威来源：

- 保存 A/AAAA 查询结果、TTL、查询进程和时间窗；
- 连接事件在同一 netns、同一时间窗内关联最近 DNS 回答；
- 遇到连接池和长 TTL 时保留多个候选；
- 不上传完整 DNS payload，只保存规范化域名和地址映射。

## 6. Control 侧身份解析

### 6.1 Identity Provider 插件

按现有 `ResourceIdentityGraph` 的来源优先级扩展：

```text
orchestrator > agent_discovery > trace > dns > user > model
```

新增 Provider：

- `AgentInventoryProvider`：Agent、主机 IP、进程、监听端口。
- `KubernetesProvider`：Pod、Node、Service、EndpointSlice、OwnerReference。
- `DockerProvider`：container/network/service/task。
- `DNSProvider`：域名与 IP 的时间化映射。
- `OTelProvider`：`service.name`、`service.instance.id`、`peer.service`、client/server Span。
- `StaticCMDBProvider`：可选 CMDB/IPAM 数据。

### 6.2 远端 Endpoint 解析流程

对于 `10.2.3.8:50051`：

1. 根据事件时间查询 IP 所属 Agent/主机。
2. 查询该 Agent 同一时间的监听 socket。
3. 通过 netns、端口、容器和进程 incarnation 找到服务实例。
4. 如存在 Kubernetes EndpointSlice，将 Pod IP 归一到 Service/Owner。
5. 如只命中 LB、代理、Ingress 或 Service VIP，创建 `virtual_endpoint`，并保存后端候选，不能伪装为唯一真实服务。
6. 保存所有 IdentityAssertion，并按来源和时间计算置信度。

解析结果示例：

```json
{
  "endpoint": "10.2.3.8:50051",
  "resolved_entity": "service:paymentservice",
  "instance": "pod:paymentservice-7d9f...",
  "agent_id": "worker-2",
  "confidence": 0.96,
  "sources": ["agent_listener", "k8s_endpointslice"],
  "valid_from": "...",
  "valid_to": "..."
}
```

## 7. 递归上下游发现

### 7.1 Discovery Frontier

新增 `DiscoveryFrontierRun`，使用有界 BFS，而不是一次性扫描整个集群。

```text
queue = [seed process]
while queue not empty and budget remains:
    entity = queue.pop()
    load inbound/outbound edges in incident window
    resolve peer endpoints
    for each high/medium confidence managed peer:
        schedule remote peer-resolution or diagnostic collection
        enqueue peer if hop limit permits
```

默认预算建议：

| 项 | 默认值 |
|---|---:|
| 最大拓扑跳数 | 2 |
| 最大主机数 | 12 |
| 最大进程/实例数 | 40 |
| 最大依赖边 | 200 |
| 最大并行远端任务 | 8 |
| 每个远端端口候选 | 3 |
| 调查时间预算 | 180 秒 |
| 发现事件结果预算 | 每 Agent 2 MiB |

### 7.2 上游与下游

- 出站 `connect()`：调用方到下游的强方向证据。
- 入站 `accept()`：上游到当前服务的强方向证据。
- 只有主机级 flow、缺少进程角色时：方向可信但实体置信度降低。
- 消息队列、UDP 和异步任务不能简单按 TCP client/server 推断业务因果，需要 OTel producer/consumer、协议解析或用户补充。

### 7.3 与现有 Fanout 的关系

`DiscoveryFrontierRun` 负责“找到谁”；`FanoutCollectionRun` 继续负责“对已冻结目标采什么”。

```text
DiscoveryFrontierRun
  -> Membership/Identity snapshot
  -> Investigation Plan revision
  -> CollectionProposal
  -> CollectionSupervisor
  -> FanoutCollectionRun / native Task
```

不得由发现组件直接创建普通采集 Task，以免绕过 scope、授权和预算。

## 8. 关系聚合和 AI 预处理

逐连接事件不直接进入 AI。按以下 key 聚合：

```text
window + source_entity + destination_entity + protocol + destination_port
```

每条边保留：

- connection count、active connections；
- success/failure/reset/timeout；
- bytes sent/received；
- connect latency、RTT、retransmission；
- first_seen、last_seen；
- client/server observation coverage；
- source/target identity confidence；
- baseline delta；
- supporting Evidence IDs。

模型上下文只投影：

1. 种子实体的一到两跳子图；
2. 异常评分最高的边；
3. 健康对照边；
4. 被排除或未解析的关键 endpoint；
5. 每条候选根因的 required-fact coverage。

必须对高基数地址做压缩：

- 已解析实例聚合到 service/owner，同时保留异常实例明细；
- 外部 IP 按明确 CIDR/域名归组；
- 短连接按窗口聚合；
- 不把完整 socket 列表、原始报文或所有 Span 发送给模型。

## 9. 根因推断规则

依赖图回答“谁与谁通信”，根因判断至少需要同时验证以下部分：

### 9.1 下游服务自身故障

- 调用方到下游的错误率/延迟上升；
- 下游服务自身 CPU、内存、锁、GC、IO 或 Profile 异常；
- 同时段其他调用方也受到影响，或下游健康对照实例正常；
- 网络路径没有更强异常证据。

### 9.2 网络路径故障

- client 侧连接/请求延迟上升，但 server 处理时间正常；
- retransmission、reset、connect failure、RTT 或丢包异常；
- 同路径/故障域出现相关异常；
- 下游进程资源没有足以解释症状的异常。

### 9.3 上游流量或重试风暴

- 当前服务入站连接/QPS 突升；
- 一个或多个上游重试、短连接或错误请求显著增加；
- 当前服务资源压力是结果而不是最早异常；
- 时间先后显示上游变化领先于目标服务饱和。

### 9.4 同宿主噪声

- 目标和异常邻居映射到同一 host；
- host-level pressure 与邻居资源消耗异常；
- 远端依赖边没有更强故障信号；
- 目标服务 Profile 不支持自身代码热点。

所有结论都必须区分：`primary_cause`、`contributing_cause`、`amplifier` 和 `propagation`。

## 10. 数据契约建议

### 10.1 DiscoveryEvent

```json
{
  "schema_version": "network-discovery-event.v1",
  "event_id": "...",
  "agent_id": "worker-1",
  "boot_id": "...",
  "observed_at": "...",
  "event_type": "tcp_connect",
  "process": {
    "pid": 1842,
    "start_time": 9213381,
    "cgroup_id": 9912,
    "netns": 4026533156
  },
  "socket": {
    "cookie": 881273,
    "local": "10.2.3.7:41230",
    "remote": "10.2.3.8:50051",
    "protocol": "tcp",
    "result": "success"
  }
}
```

### 10.2 DependencyEdgeProjection

```json
{
  "schema_version": "dependency-edge-projection.v1",
  "source_entity": "service:checkoutservice",
  "target_entity": "service:paymentservice",
  "relation": "calls",
  "window": {"start": "...", "end": "..."},
  "metrics": {
    "connections": 1240,
    "failure_rate": 0.18,
    "connect_p95_ms": 210,
    "retransmissions": 82
  },
  "identity_confidence": 0.96,
  "direction_confidence": 1.0,
  "evidence_refs": ["ev_...", "ev_..."]
}
```

## 11. 对当前仓库的改造映射

### 11.1 Agent

新增：

- `agent_runtime/discovery-sidecar/`：长期 eBPF + SOCK_DIAG 运行时。
- `agent/mini_drop_agent/collectors/network_discovery.py`：按 Case 窗口读取 sidecar spool，生成受控 Artifact。
- `agent/mini_drop_agent/collectors/peer_process_resolver.py`：在远端 Agent 上由 endpoint 反查监听进程。

扩展：

- `process_scan.py`：补充 boot ID、process start time、netns、listen ports。
- `connection_probe.py`：从“调用已知 endpoint”调整为既可接受已知 endpoint，也可接受 `discovered_edge_id`。
- Agent heartbeat：上报主机地址、网络命名空间能力、Discovery Runtime 版本和事件丢失计数。

### 11.2 Control

新增：

- `diagnosis/discovery_frontier.py`
- `diagnosis/identity_providers.py`
- `diagnosis/dependency_graph.py`
- `diagnosis/network_evidence.py`
- `diagnosis/network_baseline.py`

扩展：

- `resource_identity.py`：增加 `ip_endpoint`、`virtual_endpoint`、`dns_name` 节点类型和时间化 assertion。
- `cluster_scope.py`：让 `DEPENDENCY_FRONTIER` 真正消费发现图，而不是依赖调用方 `target_refs`。
- `collection_supervisor.py`：注册两个新 CollectorSpec，并继承现有 proposal/request/task 约束。
- `evidence_projection.py`：增加 flow/edge 聚合投影。
- `evidence_guard.py`：按 socket cookie、tuple、窗口和 observation point 去重，识别 client/server 非独立重复观测。
- `causal_graph.py`：传播边绑定 DependencyEdge Evidence，不再从 `compared_targets` 推造通用传播边。

### 11.3 数据库

建议新增版本化表：

- `resource_identity_assertions`
- `network_discovery_events`（短保留或对象存储索引）
- `dependency_edge_windows`
- `discovery_frontier_runs`
- `discovery_frontier_members`

原始高频事件建议压缩后写对象存储；PostgreSQL 保存索引、聚合边和 Evidence lineage。

## 12. 分阶段实施

### Phase 0：数据契约和可回放测试

- 固定 DiscoveryEvent、IdentityAssertion、DependencyEdge schema。
- 建立合成 replay：单机 client/server、双机连接、NAT、连接池、PID 复用、Agent 重启。
- 不接 AI，先验证身份和边构建的确定性。

退出门禁：同一 replay 重放得到完全一致的图 digest。

### Phase 1：L4 未知拓扑 MVP

- 实现 TCP connect/accept/close eBPF 事件。
- 实现 SOCK_DIAG 启动和周期对账。
- 实现本机进程、容器、netns、监听端口身份。
- Control 根据已注册 Agent IP 与远端监听端口完成双机映射。
- 从 PID 自动发现一跳上下游。

退出门禁：三节点实验环境中，不提供依赖边，只提供 PID，能正确发现至少 90% 的稳定 TCP 服务边；零错误跨租户映射。

### Phase 2：有界递归与 Case 集成

- DiscoveryFrontier BFS。
- 与 CollectionSupervisor/Fanout 集成。
- 发现边转 canonical Evidence。
- AI 只消费有界图投影。

退出门禁：两跳场景能自动找到真正远端 Agent/进程；未注册 endpoint 正确拒答；超预算稳定停止。

### Phase 3：编排器与 DNS 身份增强

- Kubernetes EndpointSlice/Pod/Owner Provider。
- Docker/Swarm Provider。
- DNS 时间化映射。
- Service VIP、Ingress、代理和后端实例区分。

退出门禁：滚动发布、Pod IP 变化和重复 EndpointSlice 不产生幽灵边或错误复用。

### Phase 4：L7 与 Trace 增强

- 接入 Beyla/OTel eBPF Instrumentation OTLP。
- 接入 OTel Service Graph 结果或直接消费 Span 配对证据。
- 支持 HTTP/gRPC、数据库、消息队列的语义关系。

退出门禁：L4 与 Span 结论冲突时保留冲突，不静默覆盖；未插桩节点保留 virtual node。

### Phase 5：RCA 与生产门禁

- client/server latency 分解。
- 网络、下游资源、上游流量、同宿主原因契约。
- 30+ 未知拓扑故障案例，每例至少三次重复。

建议晋级指标：

- 服务边 precision ≥ 95%，recall ≥ 90%；
- 远端进程定位 precision ≥ 95%；
- 严格根因命中 ≥ 80%；
- 正确拒答 ≥ 95%；
- 三次图结构 Jaccard ≥ 0.9；
- Agent CPU 平均开销 ≤ 2%，丢事件有明确计数；
- 无未授权跨主机采集。

## 13. 关键风险与控制

| 风险 | 控制 |
|---|---|
| NAT/LB/代理隐藏真实后端 | 显式 virtual endpoint；接编排器和双端观测；不强行唯一解析 |
| eBPF 丢事件或 Agent 晚启动 | ring buffer drop 指标 + SOCK_DIAG 对账 |
| PID 复用 | boot ID + process start time + PID |
| IP/Pod 重用 | 所有身份和边带有效时间；使用 Pod UID/container ID |
| 高基数和存储爆炸 | Agent 窗口聚合；只保存异常/Case 相关明细；原始事件短保留 |
| 把相关性误判为因果 | DependencyEdge 与 CausalEdge 分离；EvidenceContract 和反证门禁 |
| 未知远端没有 Agent | 只探测连通和保存外部 endpoint，不尝试越权登录或自动安装 |
| TLS/自定义协议不可解析 | L4 仍可建立关系；L7 作为增强而非硬依赖 |
| 不同 Agent 时钟偏差 | 心跳估计 clock quality；时间不对齐降级为 insufficient coverage |

## 14. 最小可应用版本建议

第一版不要追求完整 L7 AutoTracing。最有价值且能复用现有代码的交付是：

1. sidecar 捕获 TCP connect/accept/close；
2. SOCK_DIAG 补偿长连接；
3. 通过已注册 Agent 地址和远端监听端口完成跨机 PID 映射；
4. 从一个 PID 自动展开一跳上下游；
5. 自动对这些远端调用现有 `sys_metrics`、`runtime_snapshot`、`perf_cpu`、`connection_probe`；
6. 将边聚合和远端证据送入现有 Evidence/因果图链路。

这已经能覆盖用户最关心的场景：

```text
只知道 frontend PID
  -> 自动发现 checkout IP:port
  -> 映射到 worker-2 上 checkout PID
  -> 发现 checkout 继续调用 payment/redis
  -> 按预算采集相关机器
  -> 判断是调用方、网络、下游服务还是同宿主资源故障
```

## 15. 参考资料

- Cilium Hubble Network Observability：<https://docs.cilium.io/en/stable/observability/hubble/>
- Cilium Hubble Service Map：<https://docs.cilium.io/en/stable/observability/hubble/hubble-ui/>
- Grafana Beyla：<https://grafana.com/docs/beyla/latest/>
- Beyla Service Discovery：<https://grafana.com/docs/beyla/latest/configure/service-discovery/>
- Beyla Network Metrics：<https://grafana.com/docs/beyla/latest/network/>
- OpenTelemetry Service Graph Connector：<https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/connector/servicegraphconnector>
- Kubernetes EndpointSlice：<https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/>
- Pixie Overview：<https://docs.px.dev/about-pixie/what-is-pixie/>
- DeepFlow Overview：<https://deepflow.io/docs/about/overview/>
- DeepFlow AutoMetrics：<https://deepflow.io/docs/features/universal-map/auto-metrics/>
- Linux `sock_diag(7)`：<https://man7.org/linux/man-pages/man7/sock_diag.7.html>
