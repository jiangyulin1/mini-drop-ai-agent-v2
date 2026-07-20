# Mini-Drop 三节点实验集群部署与测试报告

测试日期：2026-07-20（Asia/Shanghai）

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

上述问题均已添加或通过相应回归测试、实际集群复测验证。

## 6. 后续测试建议

恢复生产对象存储后，按以下顺序继续：

1. PostgreSQL/MinIO 重启持久化和对象账实核对；
2. Worker 采集中断、Control 重启和任务恢复；
3. 两 Worker 同时运行 perf/eBPF 的并发预算与资源上限；
4. 真实调用链/变更事件接入后的下游根因定位；
5. AI Provider 的超时、限流、余额耗尽和降级故障注入测试；
6. SSH 密钥替换密码认证，并轮换当前实验密码和随机服务密钥。
