# SUT 公开 API 参考（外部 Evaluator 用）

> 本文档是机械层接口参考，**不包含任何 Oracle 内容**。所有路由与认证方式
> 均从 `server/app/main.py` 实际代码核实。被测服务（SUT）= control 上的
> mini-drop-server，入口 `https://192.168.2.203:8443`（或 `lenovo.local:8443`），
> 自签名证书，curl 加 `-k`。

## 1. 认证

- 公共 API 认证由服务端环境变量控制：
  - `MINI_DROP_API_AUTH_ENABLED=1` 时，`/api/*`（除 `/api/healthz` 等白名单）
    要求 API Key。
  - 传递方式（三选一）：`Authorization: Bearer <key>`、`X-API-Key: <key>`、
    cookie `mini_drop_api_key`。
  - Key 错误 → `401 {"detail":"无效 API Key"}`。
  - Key 正确但服务端角色配置不含 `operator` → `403 当前主体缺少角色: operator`
    （Case/Task 相关端点都要求 operator 角色）。
- 内部端点（`/internal/agent/tools/*`、`127.0.0.1:8899/internal/runtime/v1/*`）
  要求 `X-Internal-Token: <MINI_DROP_PI_INTERNAL_TOKEN>`。外部 Evaluator
  优先用公共端点；如确需内部只读投影，token 由用户单独交接。

curl 通用模板：

```bash
SUT=https://192.168.2.203:8443
KEY=$(cat /secure/sut-token.txt)
curl -ks -H "X-API-Key: $KEY" "$SUT/api/v1/cases"
```

## 2. 核心调用序列（六阶段）

```
baseline → inject → independent probe → observe → recover → cleanup
```

对应 API：

1. `POST /api/v1/cases` 建 Case（含 target_scope、环境、目标）
2. `POST /api/v1/cases/{id}/queries` 下发只读采集（process.list /
   system.metrics / service.connection / service.logs）作为基线/探针
3. 外部注入故障（见 §5，注入方式由外部 Evaluator 设计）
4. `POST /api/v1/cases/{id}/agent/turn` 驱动 Agent 调查（可多轮）
5. `GET /api/v1/cases/{id}/events`、`GET /api/v1/cases/{id}/evidence`、
   `GET /api/v1/cases/{id}/plans/current` 观察结论/证据/计划
6. `GET /api/v1/cases/{id}/agent/runtime-state` 轮询 Agent 进度
7. 恢复 + 清理后，用 `GET /api/readyz`、健康探针验证回到基线

## 3. 端点明细

### Case

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/cases` | 建 Case。请求体（示例）：`{"title":"...","problem_description":"...","recovery_goal":"...","run_mode":"COLLABORATE","environment":"vm","target_scope":{"service_id":"checkout"},"initial_tasks":[<task_id>]}`。返回 `data.case_id` |
| GET | `/api/v1/cases` | 列表（`?state=&limit=&offset=`），`data.items[]` |
| GET | `/api/v1/cases/{id}` | 详情，含 `data.agent_progress` |
| GET | `/api/v1/cases/{id}/events` | 事件流（审计） |
| GET | `/api/v1/cases/{id}/evidence` | 证据列表 |
| GET | `/api/v1/cases/{id}/plans/current` | 当前调查计划 |
| PUT | `/api/v1/cases/{id}/plans` | 修改计划（暂停/转向场景） |
| GET | `/api/v1/cases/{id}/agent/runtime-state` | Agent 运行时状态（轮询用） |
| POST | `/api/v1/cases/{id}/agent/turn` | **驱动 Agent 一轮**。请求体：`{"message":"...","intent":null,"references":[]}`。Case 处于 STOPPED/RESOLVED 时返回 409 `CASE_TERMINAL`。返回 `data.status`（如 `runtime_turn_accepted` / `runtime_unavailable`） |
| POST | `/api/v1/cases/{id}/queries` | 注册查询编译为原生 Task。请求体：`{"operation":"process.list","parameters":{},"idempotency_key":"..."}`。越界参数（executable/cwd/env/shell/未知键）在创建 Task 前 409 拒绝。返回 `data.task.id` |
| GET | `/api/v1/cases/{id}/campaigns/current` | 当前 Campaign（异构采集） |
| POST | `/api/v1/cases/{id}/campaigns` | 创建 Campaign |
| GET | `/api/v1/cases/{id}/attachments` | 附件列表 |
| POST | `/api/v1/cases/{id}/attachments` | 上传附件 |
| POST | `/api/v1/cases/{id}/deployment-assessment` | 两阶段容量评估。请求体：`{"deployment_requirements":{...},"execute_safe_tools":false}`，返回 `data.verdict`（`insufficient_data` / `ready` / …） |

### Task（采集任务）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/tasks` | 创建原生采集任务 |
| GET | `/api/tasks` | 列表 |
| GET | `/api/tasks/{id}` | 状态（`status`：PENDING/RUNNING/DONE/…） |
| POST | `/api/tasks/{id}/cancel` | 取消 |
| POST | `/api/tasks/{id}/retry` | 重试 |
| GET | `/api/tasks/{id}/events` | 任务事件 |
| GET | `/api/tasks/{id}/artifacts` | 产物列表 |
| GET | `/api/tasks/{id}/artifacts/{type}/content` | 产物内容 |
| GET | `/api/tasks/{id}/artifacts/{type}/download` | 产物下载（含 SHA-256） |
| POST | `/api/tasks/{id}/diagnose` | 触发诊断 |

### 健康

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/healthz` | `{"data":{"healthy":true,"checks":{...}}}`（白名单，无需 Key） |
| GET | `/api/readyz` | 就绪（白名单） |

## 4. 查询网关注册操作（只读，共 4 个）

来源：`server/app/diagnosis/query_registry.py`。

| operation | 用途 |
|---|---|
| `process.list` | 进程列表 |
| `system.metrics` | 系统指标 |
| `service.connection` | 服务连接 |
| `service.logs` | 服务日志 |

> 这些是**只读探测**，不能用于注入故障。注入由外部 Evaluator 自行设计（§5）。

## 5. 故障注入（诚实边界）

SUT 公开 API **没有**故障注入端点。可行的注入途径（由外部 Evaluator 选择，
属于 Oracle 设计的一部分）：

- 直接 SSH 到 worker1/worker2 使用 `docker` / `tc` / 压力工具注入
  （需用户提供 worker SSH 凭据）
- 复用仓库 `golden_scenarios/` 与 `deploy/scripts/` 中现有注入脚本（外部作者
  可自行阅读并适配；仓库侧 AI 不参与 Oracle 设计）
- 服务离线类（如 A03 worker2 agent 离线）：在 worker 上 `systemctl stop
  mini-drop-agent`，恢复用 `start`

注入后必须用独立探针（§2 第 3 步）证明故障生效，否则该 Case 标
`HARNESS_INVALID`。

## 6. Agent 轮询模式（observe 阶段）

- `POST /agent/turn` 返回 `data.status == "runtime_turn_accepted"` 表示本轮被
  Agent 运行时接受；随后轮询 `GET /api/v1/cases/{id}/agent/runtime-state`
  直至出现结论（可参考仓库 `scripts/run_agent_beta_public_cases.py` 中
  `state.get("detail")` 的轮询写法，该脚本是公开机械层示例）。
- `runtime_unavailable` 表示 Pi 运行时不可用，本轮 fail-closed，不启动调查。
- Case 终态后不能继续 `agent/turn`（409）。

## 7. 常见错误码速查

| 码 | detail | 处理 |
|---|---|---|
| 401 | 无效 API Key | 检查 Key / header |
| 401 | INTERNAL_TOKEN_REQUIRED | 内部端点缺 X-Internal-Token |
| 403 | 当前主体缺少角色: operator | Key 正确但角色不符，检查服务端角色配置 |
| 404 | Case 不存在 / TARGET_SESSION_NOT_FOUND | 检查 case_id |
| 409 | CASE_TERMINAL | Case 已终态，不可再下发 |
| 409 | 越界查询参数 | 查询参数被网关拒绝（预期行为） |
