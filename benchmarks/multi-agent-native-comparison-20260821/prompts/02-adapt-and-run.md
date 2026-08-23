# 子任务提示词：适配并串行运行一个 Agent

你只负责当前指定 Agent：`<AGENT_ID>`。不得同时安装或启动第二个 Agent。你必须通过 smoke gate 后才允许做 9 x 3 正式运行。

## 目标参数

```text
AGENT_ID=<mini-drop|holmesgpt|smolagents|itops-agent-platform|k8sgpt>
SOURCE_DIR=agents/<agent source>
SOURCE_SHA=<read SOURCE_SHA>
TRACK=<common_replay|common_acquisition|native_appendix|k8s_specialty>
RUN_ROOT=benchmark/runs/<AGENT_ID>/<SOURCE_SHA>
```

## 适配要求

在 `benchmark/adapters/<AGENT_ID>/` 创建：

```text
README.md                 # 原生入口、版本、依赖、限制和运行命令
adapter-manifest.json     # adapter sha、支持 tracks、支持工具、禁止工具
normalize_output.*        # 将原始回答变为统一 answer schema
run_case.*                # 仅运行一个 case/repeat 的受控入口
```

适配器必须让 Agent 仅看到 `system-prompt-common.md`、一个 public case 和 common tools。所有 Agent 使用相同远程模型身份、参数、上下文预算及 system prompt。若平台不能接受统一模型或工具调用，记录为可比性失败；不要偷偷改成不同模型后放进主榜。

## 专项约束

- Mini-Drop：主榜通过 Case/Evidence/replay adapter 运行；原生 Collector 结果只写 native appendix。
- HolmesGPT：禁用 Operator、Kubernetes Operator、Slack/Jira/GitHub、自动修复和外部 Toolset；保留 common tool wrapper。
- smolagents：使用 `ToolCallingAgent`，禁止 `CodeAgent` 的本地 Python 执行、web search、shell 和文件访问。
- ITOps Agent Platform：只允许 headless diagnosis adapter；禁止 Prometheus/Zabbix webhook、SSH、Docker 主机管理和 remediation。15 分钟无法健康启动则 `environment_blocked`。
- K8sGPT：仅 C6 及 Kubernetes 专项；不要报告为全 9 案例主榜参与者。

## 安装和 smoke gate

1. 在 `benchmark/work/<AGENT_ID>-<run-id>/` 创建独立 venv、Node prefix 或容器命名空间；不得使用全局依赖覆盖。
2. 记录安装命令、lockfile hash、依赖大小、耗时和资源峰值到 `install-manifest.json`。
3. 做一个不计分 smoke：C1 的只读 evidence list、一次定向查询、一次符合 schema 的回答。C7/C9 交互 smoke 至少任选一个。
4. Smoke 通过条件：无越权网络/shell；工具 trace 存在；输出可规范化；所有 evidence refs 有效；干预后不引用 EXCLUDED 证据。
5. 失败时保留诊断日志和资源快照，写 `comparisons/exclusions/<AGENT_ID>.json`，清理隔离环境，停止本任务。

## 正式运行

按 C1..C9，repeat 1..3 顺序运行。对每一个 run：

1. 创建唯一 run id；复制只读 public pack 到隔离输入目录。
2. 写入 manifest 与所有输入 hash；Oracle 不得被复制。
3. 启动单次 Agent；限制 16 tool calls、30 分钟、512 KB 总工具返回、64 KB 单次返回；记录 wall time、CPU、RSS、磁盘和网络字节。
4. C7/C8/C9 在状态触发点注入对应干预，并将干预前后状态写入 `interventions.jsonl`。
5. 捕获原始输出和完整 tool trace，规范化为统一 JSON。
6. 运行私有评分器生成 `score.json`；评分器异常时 `scoring_error`，不得手工填分。

状态值仅可为：`completed`、`agent_error`、`adapter_error`、`timeout`、`resource_failure`、`policy_violation`、`scoring_error`、`environment_blocked`。

## 封存、清理、验证

27 次运行后：

1. 汇总 run manifest 清单和 `sha256sum` 到 `archive-manifest.json`。
2. 停止仅由当前 run id 创建的进程、容器、网络和临时目录；不触及其他用户资源。
3. 验证没有本 Agent 残余 PID、监听端口或容器。
4. 保留 `RUN_ROOT`、adapter 和汇总日志；删除仅隔离安装环境和临时缓存。
5. 写 `comparisons/agent-summaries/<AGENT_ID>.json`，包括有效/预期运行数、状态分布、资源统计和不可比性说明。
