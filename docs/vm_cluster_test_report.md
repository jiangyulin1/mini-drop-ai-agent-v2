# Mini-Drop 三节点实验集群部署与测试报告

测试日期：2026-07-20、2026-07-22、2026-08-10（Asia/Shanghai）

## 1. 实际部署

| 角色 | 地址 | 运行内容 |
|---|---|---|
| Control | `192.168.10.10` | Nginx HTTPS、Mini-Drop Server、SQLite、实验 S3 存储 |
| Worker 1 | `192.168.10.11` | `linux-worker-1` Agent、CPU/perf 负载测试 |
| Worker 2 | `192.168.10.12` | `linux-worker-2` Agent、I/O/eBPF 负载测试 |

应用目录均为各节点用户 Home 下的 `mini-drop`。服务单元：

- Control：`nginx`、`mini-drop-server`、`mini-drop-s3`
- Worker：`mini-drop-agent`

访问入口为 `https://192.168.10.10`。开发 CA 位于 Control 的
`/home/control/mini-drop/deploy/certs/ca.crt`，Windows 浏览器需要导入该 CA。
API Key 保存在 Control 的 `deploy/env/control-native.env`，未写入仓库或本报告。

## 2. 部署方式说明

Docker Hub、Quay、ECR 和 GHCR 均被当前 NAT 出口阻断；唯一可访问的镜像代理在镜像层
CDN 上超时。因此本次采用原生 systemd 部署：

- Python 包经阿里云 PyPI 镜像安装；
- Control 使用 SQLite 持久化业务状态；
- Web 使用 Windows 已验证的 Vite 构建产物和 Ubuntu Nginx；
- S3 接口临时由 Moto 提供，Agent 和 Server 仍通过 MinIO Python 客户端访问；
- gRPC Token、TLS、CA 校验及 Worker 产物上传链路保持完整。

Moto 仅用于实验调试，服务重启后对象数据不保证持久化，也不应视为生产 MinIO 替代品。
恢复镜像访问后，应切回 `docker-compose.control.yml` 中的 PostgreSQL 和 MinIO。

## 3. 安全边界实测

Windows `192.168.10.1` 对 Control 的实测结果：

| 端口 | 结果 | 说明 |
|---:|---|---|
| 22 | 可达 | 维护 SSH |
| 80 / 443 | 可达 | 80 跳转 HTTPS，443 页面和 API |
| 50051 | 不可达 | 仅允许两个 Worker IP |
| 9000 | 不可达 | 仅允许 Control 自身和两个 Worker IP |
| 8191 | 不可达 | Server 只绑定 `127.0.0.1` |
| 5432 / 9001 | 不可达 | 未对外提供 |

其他安全测试：

- HTTPS API 无 Key 返回 `401`，正确 Key 返回 `200`；
- 正确 gRPC Token + CA 调用成功；
- 错误 gRPC Token 返回 `UNAUTHENTICATED`；
- Control API 显示两个 Agent 均为 `ONLINE`；
- MinIO/S3 凭据没有通过 gRPC 下发。

## 4. 功能测试结果

### 4.1 真实采集与产物

| 场景 | Task ID | 结果 | 产物 |
|---|---|---|---|
| Worker 1 系统指标 | `task_20260720_133309_7e8010` | DONE | `sys_metrics` |
| Worker 2 系统指标 | `task_20260720_133309_5a3e98` | DONE | `sys_metrics` |
| Worker 1 CPU 热点 | `task_20260720_133309_40e2f0` | DONE | perf.data、火焰图 JSON/SVG、TopN、建议 |
| Worker 2 I/O 延迟 | `task_20260720_133309_26c774` | DONE | eBPF 指标、原始输出 |
| Worker 2 重连复测 | `task_20260720_134758_d3e66a` | DONE | `sys_metrics` |

所有任务产物均已验证可通过 Control 的 HTTPS 下载接口读取，Windows 不需要直连 9000。

### 4.2 AI 多实例诊断

拓扑：`service-a / worker1 → service-b / worker2`。

- 首次诊断 `diag_session_20260720_133423_8419a830` 完成 R1 和一次 R2 审批，但暴露远端
  产物没有回退对象存储的问题；
- 修复后诊断 `diag_session_20260720_134403_1f17847f` 为 `COMPLETED`，复用 14 条证据，
  判断为 `self_code_or_process_pressure`；
- 聚合复测 `diag_session_20260720_134831_ebfded3a` 为 `COMPLETED`，每个实例在
  `compared_targets` 中只出现一次，并合并多个采集器观测。

2026-07-20 已通过隐藏终端输入接入 DeepSeek：

- Provider：`deepseek`；Base URL：`https://api.deepseek.com`；模型：`deepseek-v4-flash`；
- Key 仅写入 Control 的 `control-native.env`，文件权限为 `0600`，测试过程不输出 Key；
- 官方 `/v1/chat/completions` 实测 HTTP `200`，响应模型为 `deepseek-v4-flash`；
- Mini-Drop `/api/nlp/parse` 实测 HTTP `200`，能将用户指定的 mysqld、`ebpf_io`、17 秒、
  101Hz 原样解析为受约束结构化参数；
- Provider 异常时仍保留确定性降级链路，不影响基础采集和规则诊断。

在 Windows Web 的“AI 集群诊断”标题区新增“AI 服务检测”按钮，通过弹窗展示分项结果。
Control 实际运行
`ai_validation_f9b55950185e`，8/8 项通过，总耗时 7891ms：

- 配置与功能开关、账户可用性、模型发现、基础对话；
- Drop NLP Tool Call、集群诊断意图及禁止高风险探针/自动修复约束；
- 150 字硬限制的任务总结；
- RCA JSON Schema、证据引用和置信度校验（0 次修复重试）。

响应确认未包含 API Key、余额金额或模型原始思维链。

### 4.3 失败与恢复

- 不存在 PID：`task_20260720_134647_16abc1` 正确进入 `FAILED`，原因明确为目标 PID 不存在；
- 停止 Worker 2 Agent 后，Control 在离线窗口内将其标记为 `OFFLINE`；
- 重启 systemd Agent 后恢复 `ONLINE`，并再次完成采集与上传；
- 两台测试负载已在测试结束后停止并清理。

### 4.4 证据驱动流水线 v2 实测（2026-07-22）

在 Control 部署 12 节点诊断流水线、确定性 Analyzer、Knowledge、结构化 Action、Verifier 和 Golden Harness；在 Worker 部署真实 `service-a → service-b` 实验服务和负载发生器。

最终场景：service-a 位于 Worker 1，调用 Worker 2 的 service-b；Worker 2 同时运行 4 个 CPU 噪声进程。诊断会话 `diag_session_20260722_092502_1fd316d6` 的结果：

| 检查项 | 结果 |
|---|---|
| 新采集任务 | `task_20260722_092502_6a5572`、`task_20260722_092502_3a3bb2` |
| 流水线 | 12/12 节点 `COMPLETED` |
| 目标 service-a | CPU/load/memory/fd 均为 `false`；CPU user 0.1%，load1m 0.0 |
| 下游 service-b | CPU=`true`、load=`true`；CPU user 93.3%，load1m 9.43 |
| 跨节点分类 | `downstream_dependency` |
| 证据与知识 | 4 条 Evidence、2 条 Knowledge 引用 |
| 报告校验 | Verifier passed；检查 4 条证据、2 条知识、2 个 Action |
| 动作安全 | R0 inspect + R1 sys_metrics，均 `auto_execute=false` |

测试结束后，service-a、service-b、load generator 和所有 CPU noise transient unit 均已停止；两个 `mini-drop-agent` 保持 `active`。

### 4.5 核心不变量复测（2026-07-22 晚）

- 全新跨节点会话 `diag_session_20260722_130907_8827a3dd` 完成两目标 R1、12/12 节点和 `sys_metrics.v2` Evidence 校验；
- 修正 Host/Process 分域后，会话 `diag_session_20260722_131128_02666375` 输出 `downstream(service-b-1)` 与 `cpu/process_cpu_pressure`；
- 对 `diag_session_20260722_131418_7f9b93fa` 的同一 R2 step 并发批准返回 200/409，只生成一个 Task，最终明确进入 `INSUFFICIENT_EVIDENCE`；
- HISTORICAL 会话 `diag_session_20260722_131708_84d93ed2` 在没有历史 Evidence 时创建 0 个 Task/Probe，并进入 `INSUFFICIENT_EVIDENCE`；
- 实验进程清理后，两个 Agent 均保持 `ONLINE`。

## 5. 测试中发现并修复的问题

1. Agent 本地文件不存在于 Control 时，结构化证据读取提前失败，没有回退对象存储。
2. 复用历史证据成功后，状态机缺少 `ANALYZING_EXISTING_DATA → ANALYZING` 迁移。
3. 全新进程首次创建 SQLAlchemy Session 时，普通 Lock 可能发生重入死锁。
4. 多采集器观测导致同一实例在 `compared_targets` 中重复展示。
5. systemd 的 `ProtectHome=true` 会阻止 Agent 读取 Home 目录中的代码和 CA。
6. Server 启动入口忽略 `SERVER_HOST`，导致 8191 无法限制为回环地址。
7. NLP Tool Call 默认 `auto` 偶发降级；改为非思考模式并强制指定受控函数。
8. AI 总结仅靠提示约束字数，模型可能返回 266 字；新增 150 字程序侧硬限制。
9. Windows 浏览器未保存 `MINI_DROP_API_KEY` 时 `/api/agents` 返回 401，但旧页面把失败结果
   清空成 0 个 Agent；现改为“状态未知”并明确提示在顶栏保存 Control API Key。
10. systemd 在创建 namespace 前要求 `ReadWritePaths` 已存在，导致 Worker 开机后 Agent 进入 `226/NAMESPACE` 重启循环；服务单元改为允许路径缺失并在启动前创建 `/tmp/mini-drop`。
11. `vmrss_mb / vmrss_mb_max` 和 `fd_count / fd_max` 把“采样窗口最大值”误当“系统限制”，稳定进程会被误报；改为绝对阈值与增长趋势组合，并增加回归测试。
12. 宽时间窗会复用十多分钟前的 Task，无法代表当前负载；改为默认 120 秒新鲜度、结构化产物校验和全目标覆盖，不完整时整组重新采集。
13. 多目标探针完成速度不同时，首个终态任务曾触发提前结论；增加全目标完成屏障，下游/同宿主归因也必须存在目标侧观测。
14. 旧 SQLite 不会由 `create_all()` 自动增加 v2 列，升级后 Agent 心跳查询失败；新增幂等的 additive schema migration。
15. `WAITING_APPROVAL` 页面重复轮询会再次分析已完成 R1 并触发非法迁移；改为审批等待态幂等返回。
16. LIVE 默认 requested window 的终点早于随后采集的 Evidence；新增受 deadline 约束的 effective collection window，并由 Verifier 使用该窗口。
17. 多核主机上单进程占满一个核心时宿主机总 CPU 可能低于 75%；改用进程 tick 增量的核使用量区分 `process_cpu_pressure` 与 `host_cpu_saturation`。

上述问题均已添加或通过相应回归测试、实际集群复测验证。

## 6. 后续测试建议

恢复生产对象存储后，按以下顺序继续：

1. PostgreSQL/MinIO 重启持久化和对象账实核对；
2. Worker 采集中断、Control 重启和任务恢复；
3. 两 Worker 同时运行 perf/eBPF 的并发预算与资源上限；
4. 真实调用链/变更事件接入后的下游根因定位；
5. AI Provider 的超时、限流、余额耗尽和降级故障注入测试；
6. SSH 密钥替换密码认证，并轮换当前实验密码和随机服务密钥。

## 7. 2026-07-27 展示环境复测

本次将 `main` 的最新版本部署到独立目录 `mini-drop-current`，保留 7 月 22 日的旧目录作为回退副本。
Control、两个 Worker、Nginx、实验 S3 和两个 Agent 均保持运行，访问入口仍为
`https://192.168.10.10`。由于 Docker Hub 及镜像层 CDN 继续超时，沿用原生 systemd
部署；Python 依赖由阿里云 PyPI 镜像安装。

### 7.1 新增展示能力

- 实验 Oracle 与模型输入隔离，只在报告通过 Verifier 后计算实例、位置、领域和分类四维得分；
- 前端新增“目标 → 结构化证据 → 候选假设 → 已校验结论”的全局因果证据图；
- 路径对比表同时展示探针数、R2 数、证据量、覆盖率、置信等级和 Oracle 客观得分；
- 目标、时间、证据域、动作风险与副作用幂等约束继续对所有分析路径生效。

### 7.2 实际发现并修复的问题

1. AI 在用户未指定时间时可能返回一个格式合法但已经过期的默认窗口，使 LIVE 请求被误判为
   HISTORICAL。修复后，只有明确的用户时间表达式可以补充时间；默认窗口必须由服务器当前时钟生成。
2. AI 将“未提供时间，已使用默认窗口”写入说明性歧义时，旧逻辑会无条件进入
   `NEEDS_SCOPE_CONFIRMATION`。修复后，只有确定性解析器无法建立可信目标锚点时才阻塞；说明性备注
   仍保留在回放中。

### 7.3 三节点端到端结果

真实火焰图任务 `task_20260727_081136_6b67b5` 在 Worker 1 完成，生成：

- `raw`
- `flamegraph_json`
- `flamegraph_svg`
- `top_json`
- `suggestions_md`

跨节点案例 `diag_session_20260727_081839_35d170f5` 使用
`service-a (Worker 1) → service-b (Worker 2)`：

- 两个目标均完成 R1 采集；
- 12/12 流水线节点完成；
- 4 条结构化 Evidence 通过完整性与同域校验；
- 归因为 `downstream / service-b-1`；
- 领域原因为 `cpu / process_cpu_pressure`；
- 分类为 `downstream_dependency`；
- Oracle 四维全部命中，得分 `100%`；
- 生成缓解、优化和复测三类证据关联建议。

### 7.4 同类故障的三路径对比

| 路径 | 会话 | 探针 | R2 | 证据 | Oracle |
|---|---|---:|---:|---:|---:|
| 受约束混合 | `diag_session_20260727_083237_2794726f` | 1 | 0 | 2 | 100% |
| 固定决策树 | `diag_session_20260727_083301_222e6f22` | 2 | 1 | 4 | 100% |
| 广度探索 | `diag_session_20260727_082854_4cb19299` | 2 | 0 | 4 | 100% |

三条路径均定位到 `self / cpu / self_code_or_process_pressure`。固定决策树使用了经人工单次审批
的 CPU Profile；广度探索使用系统指标和内存映射；受约束混合路径在低风险证据已经足够时没有继续
申请 R2。因此课堂展示可以直接比较“相同正确率下的取证成本、风险和可解释性”，而不是比较模型
自报置信度。

在线 AI 验证 `ai_validation_b7f60cd8cb22` 的 8 项检查全部通过，包括模型发现、基础对话、
受约束 NLP Tool Call、集群诊断意图、安全动作约束、摘要长度和 RCA 证据引用校验。

展示负载由以下 transient systemd unit 提供：

- Worker 1：`mini-drop-demo-service-a`、`mini-drop-demo-load`、
  `mini-drop-demo-path-hybrid/tree/explore`
- Worker 2：`mini-drop-demo-service-b`

展示结束后可在对应 Worker 执行：

```bash
sudo systemctl stop 'mini-drop-demo-*'
```

## 8. 2026-08-10 当前 AI/Agent 基线发布与实测

新版本以同一 SHA-256 校验包部署到 control、worker1 和 worker2，三个节点的
`mini-drop-active` 均切换到 `mini-drop-release-20260810-ai-agent-v1`。切换前已使用
SQLite online backup API 生成 18,923,520 字节的数据库备份，2026-08-06 发布目录
继续保留作为回滚点。

发布后结果：

- `mini-drop-server`、`mini-drop-analyzer`、`mini-drop-s3`、`nginx` 和两个
  `mini-drop-agent` 均为 `active`；
- `/api/healthz` 返回 `healthy=true`，数据库、对象存储和 Analyzer 均为 `ok`；
- 实际 SQLite 从 `0008_case_investigation` 升级到 `0014_profile_windows`，
  空库 `0001 -> 0014` 与 schema drift 检查也通过；
- `linux-worker-1` 和 `linux-worker-2` 均为 `ONLINE`，真实 `sys_metrics`
  任务 `task_20260810_120821_8becef` 和 `task_20260810_120830_d1eada`
  均为 `DONE`，分析状态为 `SUCCEEDED`；
- worker1 的 `continuous_perf` 任务 `task_20260810_120937_a24762`
  完成 1/1 窗口，产生 perf.data、flamegraph JSON/SVG 和 Top JSON；
- 上述持续剖析任务已建立 `profile_window_07e1b7d0adae4345` 索引；
  同时间低严重度信号正确关联该窗口、保持 `RECORDED`、不误创建 Case，
  验证用 target session 随后已归档；
- 直接以 Agent 自身 PID 为剖析目标时任务被安全策略拒绝，改用非 Agent
  系统进程后成功，证明自剖析保护未被发布破坏。

Online Boutique 在 worker1 完成 Compose `config --quiet` 和所有 shell 脚本
`bash -n` 检查。运行态仍未开始：VM 和 Mac 访问
`us-central1-docker.pkg.dev` 均超时，worker 也没有固定 v0.10.3 镜像。因此没有
把任何案例晋级为 `verified_vm`，也没有在不完整环境中执行故障注入。
