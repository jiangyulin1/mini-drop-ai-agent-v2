# Mini-Drop AI 生产级授权、信息源与安全执行设计

> 状态：授权与安全执行约束；实施进度以 [`autonomous_ops_agent_implementation_plan.md`](autonomous_ops_agent_implementation_plan.md) 为准。
>
> 当前实现：诊断 R0/R1 可自动采集，R2 单次审批；已落地可信主体、内置信息源注册表、持久化 Grant、确定性 Source Policy、内部短期 Capability Token、Source Gateway、EvidenceEnvelope、Action Registry、受限执行入口、验证器和回滚骨架。自动处置仍只允许逐动作、逐环境晋级，不能视为通用生产自治。
> 目标：在生产级、多租户、多集群环境中，让 AI 在明确授权边界内自主读取信息、执行采集，并自动完成经过注册和验证的低风险修复。

## 1. 设计定位

Mini-Drop 的产品目标是生产级故障调查与恢复服务。当前一个 Control、两个 Linux
Worker 的 Hyper-V 环境只是首个 `EnvironmentProfile`，用于验证控制链路和测试集，
不是产品架构的规模上限。目标架构必须支持：

- 多租户、多个组织和资源组；
- 多地域、多集群和多故障域；
- Kubernetes、虚拟机、裸机和混合云 Agent；
- 指标、日志、Trace、Profile、拓扑、变更、事件和知识库等异构信息源；
- 水平扩展的 API、工作流 Worker、Analyzer 和 Connector；
- OIDC、RBAC/ABAC、短期凭证、逐资源授权和完整审计；
- 从辅助调查逐步提升到特定场景的低风险自动修复。

三节点实验结果只能证明该环境画像下的功能与证据能力，不能作为生产容量、隔离性、
高可用或权限体系的证明。

## 2. 借鉴 Codex 的原则

本设计借鉴 Codex 的安全执行思想，而不是照搬编码产品的文件系统模型：

1. 模型负责理解、规划和提出结构化工具调用，不直接拥有基础设施权限。
2. Harness/Orchestrator 负责上下文、会话、工具暴露、状态恢复和用户接管。
3. Policy Engine 决定是否允许、需要审批、需要降级或拒绝。
4. Sandbox/Executor 在模型之外强制执行资源、网络、身份和作用域边界。
5. 低风险日常动作在边界内无摩擦执行；跨越边界或高影响动作必须停下审批。
6. 用户可以批准一次、授予限时会话能力，也可以随时暂停、撤销和停止。
7. 原始请求、计划、工具参数、审批、执行结果和网络策略判定全部进入审计轨迹。

OpenAI 对内部 Codex 部署的公开说明同样强调 sandbox 与 approval policy 共同工作、
网络目标受策略控制，以及使用 agent-native logs 审计请求、工具、审批和策略阻断：
[Running Codex safely at OpenAI](https://openai.com/index/running-codex-safely/)。Codex
插件与 App 的权限也不会覆盖源系统权限，并区分只读、写操作和动作确认：
[Plugins in Codex](https://help.openai.com/en/articles/20001256-plugins-in-codex/)。

## 3. 目标架构

```text
Web / CLI / Incident API
          |
          v
Case Service + Durable Event Store
          |
          v
Diagnosis Orchestrator ---- LLM Gateway
     |          |                |
     |          |                +-- 仅返回结构化 Intent/Plan/Explanation
     |          |
     |          +---- Policy Decision Point
     |                    |
     |                    +-- Identity / Role / Tenant / Environment
     |                    +-- Grant / Risk / Budget / Time / Data Class
     |
     +---- Source Gateway -------------------- Read Sandbox
     |       +-- Metrics / Logs / Traces / Profiles
     |       +-- CMDB / K8s / Service Mesh / Cloud API
     |       +-- Deployments / Incidents / Runbooks / Tickets
     |
     +---- Probe Gateway --------------------- Collector Sandbox
     |
     +---- Actuation Gateway ----------------- Change Sandbox
             +-- Action Registry
             +-- Preflight / Dry-run / Idempotency
             +-- Scoped Credential Broker
             +-- Execute / Observe / Rollback
             +-- Post-action Verification
```

生产部署中，API 和 Orchestrator 应无状态化并可水平扩展；Case、Step、Grant、Action
Attempt 和审计事件持久化到 PostgreSQL；事件总线只负责唤醒和传输，数据库状态是
恢复依据；Artifact 使用生产对象存储；Secret Broker 下发短期凭证，不把密钥写入
模型上下文或任务参数。

## 4. 信息源授权模型

### 4.1 Source Registry

所有信息源必须先注册为 `SourceDefinition`，类似受控工具或 MCP Connector：

```json
{
  "source_id": "prod-prometheus-primary",
  "source_type": "prometheus",
  "tenant_id": "tenant-a",
  "operations": ["query_range", "query_instant", "metadata"],
  "resource_selectors": ["cluster=prod-a", "namespace=orders-*"],
  "data_classes": ["operational_metric"],
  "credential_ref": "secret://prometheus/tenant-a/reader",
  "network_policy": ["prometheus.prod.internal:9090"],
  "result_schema_version": "prometheus-evidence.v1",
  "default_timeout_sec": 15,
  "max_result_bytes": 1048576
}
```

建议生产信息源分为：

- Observability：Prometheus、日志平台、Trace、持续 Profile、告警；
- Runtime：Kubernetes、虚拟机、容器、进程、systemd、Service Mesh；
- Topology：CMDB、服务目录、依赖图、资源组、Owner；
- Change：发布记录、配置历史、Feature Flag、Git/CI/CD 变更；
- Dependency：数据库、Kafka、缓存、网关、DNS 和云服务健康；
- Knowledge：Runbook、历史事故、Postmortem、工单和内部文档。

### 4.2 用户授权链

一次读取必须同时满足：

```text
用户身份有效
AND 用户具有租户/资源访问权
AND Source 已在工作区启用
AND Operation 在 SourceDefinition 白名单中
AND 资源选择器未越界
AND 数据敏感级别可访问
AND Case Scope 与时间窗匹配
AND 查询预算、速率和结果大小未超限
```

Source Gateway 必须使用用户委托身份或受约束的服务身份。源系统拒绝用户读取的数据，
Mini-Drop 不能因为 AI 或管理员启用了 Connector 就绕过。模型只看到逻辑 Source 和
工具 Schema，不看到 Token、密码、Cookie 或 Secret 路径。

### 4.3 证据血缘与不可信输入

每次信息读取生成不可变 `EvidenceEnvelope`：

```json
{
  "evidence_id": "ev_...",
  "source_id": "prod-prometheus-primary",
  "principal_id": "user-or-service-id",
  "tenant_id": "tenant-a",
  "resource_scope": {"cluster": "prod-a", "service": "checkout"},
  "query_fingerprint": "sha256:...",
  "observed_at": "...",
  "valid_time": {"from": "...", "to": "..."},
  "data_class": "operational_metric",
  "redactions": [],
  "content_hash": "sha256:..."
}
```

日志、文档、Trace 属性和工具返回都视为不可信数据，不能成为系统指令。进入模型前
执行字段投影、脱敏、大小限制和内容标记；结论必须引用 Evidence ID。

## 5. 操作、风险与授权必须分离

风险描述“动作可能造成什么影响”，授权描述“谁允许在什么边界内做什么”。二者不能
继续压缩成一个 R0—R3 数字。

### 5.1 操作类别

| 类别 | 含义 | 示例 |
|---|---|---|
| `READ` | 读取现有数据，不主动改变目标 | 查指标、Trace、拓扑、发布历史 |
| `COLLECT` | 主动产生诊断负载或 Artifact | perf、eBPF、Heap Dump、抓包 |
| `CHANGE` | 改变业务或基础设施状态 | 流量摘除、扩容、动态配置、重启实例 |
| `DESTRUCTIVE` | 删除数据或产生难恢复影响 | 删除资源、数据修复、不可逆迁移 |

### 5.2 影响等级

| 等级 | 定义 | 默认授权结果 |
|---|---|---|
| `I0` | 无业务副作用的已授权读取 | 自动 |
| `I1` | 影响可忽略、局部、可自动恢复 | 已授权范围内自动 |
| `I2` | 有限业务影响，具备明确验证和回滚 | 自动安全复核或单次人工审批 |
| `I3` | 可能影响 SLO、多个实例或依赖 | 变更审批/双人复核 |
| `I4` | 破坏性、不可逆或故障域不确定 | 默认拒绝，由外部变更系统处理 |

同一个动作的影响等级会随生产/测试环境、冗余度、业务时段、目标数量和当前事故状态
变化。例如“重启一个实例”在健康冗余服务中可能是 I2，在单副本或级联故障期间可能
是 I4。风险必须由确定性策略计算，不能由 LLM 自报。

### 5.3 授权结果

Policy Engine 只能返回：

```text
AUTO_GRANTED       已有明确授权且满足全部边界
AUTO_REVIEWED      在预授权包络内，经自动安全复核放行
USER_APPROVAL      需要当前操作者单次确认
CHANGE_APPROVAL    需要外部变更单、值班负责人或双人审批
DENIED             策略禁止或无法证明安全
```

可选的 AI Safety Reviewer 只能把动作降级为人工审批或拒绝，不能突破确定性策略允许的
最大权限，也不能扩大租户、资源、时间、参数或数据范围。

## 6. Grant 与短期 Capability Token

授权不是一个布尔值，而是受约束的 `AuthorizationGrant`：

```json
{
  "grant_id": "grant_...",
  "principal_id": "user-123",
  "delegate": "mini-drop-ai",
  "tenant_id": "tenant-a",
  "operation_ids": ["metrics.query", "action.drain_unhealthy_instance"],
  "resource_scope": {
    "cluster_ids": ["prod-a"],
    "service_ids": ["checkout"]
  },
  "constraints": {
    "max_targets": 1,
    "max_duration_sec": 300,
    "max_cost": 10,
    "require_healthy_replicas": 2,
    "deny_during_change_freeze": true
  },
  "valid_until": "...",
  "uses_remaining": 1,
  "revocable": true
}
```

用户可以批准一次、批准本 Case、批准当前会话，或由管理员创建长期但严格限定的策略。
执行时由 Credential Broker 将 Grant 换成一次性 Capability Token；Token 绑定
`case_id + action_id + target + parameter_hash + expiry`，不能重放到另一个动作。

## 7. 允许自动执行的低风险修复

第一阶段只允许管理员注册、可逆、可验证且爆炸半径受限的动作。候选包括：

- 从负载均衡中临时摘除一个已确认不健康的无状态实例；
- 在预批准上下限内增加一个实例，并受成本预算限制；
- 回滚已登记的动态配置或 Feature Flag 到最近已知健康值；
- 重启单个异常无状态实例，但必须证明剩余健康副本和容量充足；
- 清理 Mini-Drop 自身过期临时 Artifact、租约或诊断缓存，不触碰业务数据；
- 恢复由 Mini-Drop 本次动作创建且具备对应撤销操作的临时限流或流量路由。

这些动作不是天然低风险。只有同时满足以下条件才可 `AUTO_GRANTED`：

- 动作来自签名、版本化的 Action Registry；
- 用户或管理员已明确授予该动作和资源范围；
- 输入参数通过严格 Schema 与服务端范围校验；
- 当前拓扑、冗余、容量、变更冻结和事故状态满足前置条件；
- dry-run 或等价预检查成功；
- 定义了幂等键、超时、观察窗、成功标准和回滚动作；
- 并发动作检查没有发现冲突；
- 不扩大目标数量、拓扑跳数和故障域；
- 任何信息缺失都默认升级审批或拒绝。

数据库写入、删除业务数据、跨集群迁移、证书/身份变更、不可逆 Schema 迁移和开放式
Shell 不进入自动修复注册表。

## 8. Actuation Gateway 状态机

```text
PROPOSED
  -> POLICY_EVALUATED
  -> WAITING_APPROVAL（如需要）
  -> PREFLIGHT
  -> DRY_RUN
  -> EXECUTING
  -> OBSERVING
  -> VERIFIED
       |-> SUCCEEDED
       |-> ROLLING_BACK -> ROLLED_BACK / ROLLBACK_FAILED
       |-> FAILED
```

Action Attempt 必须保存：计划、理由、Evidence、策略输入与输出、Grant、执行主体、
参数 Hash、幂等键、开始/结束时间、外部请求 ID、变更前后快照、验证和回滚结果。

修复后不能只检查原告警是否消失，还必须检查：

- 用户目标/SLO 是否恢复；
- 相关服务和依赖是否出现新退化；
- 错误率、延迟、容量和资源安全余量；
- 是否引入新告警；
- 连续稳定时间是否达到 Case 定义。

这对应 `Transactional No-Regression`：动作只有在目标改善且受保护指标没有退化时才
能提交为成功，否则自动回滚或升级人工处理。

## 9. 用户控制面

前端提供统一“权限与接管中心”：

- 查看当前 Case 可访问的信息源、资源范围和有效期；
- 查看 AI 当前能自动执行的动作及参数上限；
- 单次批准、限时授权、拒绝或撤销 Grant；
- `Pause`：停止创建新探针和新变更，但保留状态；
- `Stop`：取消可取消动作并停止 Case；
- `Red Button`：管理员全局冻结自动变更并撤销自动执行能力；
- 用户纠正、补充信息和停止事件优先于后台分析结果；
- 所有授权和动作均可按用户、Case、服务、集群和时间检索审计。

## 10. API 草案

```text
GET    /api/v1/sources
GET    /api/v1/identity
POST   /api/v1/sources/{source_id}/test
POST   /api/v1/sources/{source_id}/query
GET    /api/v1/capabilities

POST   /api/v1/grants
GET    /api/v1/grants
DELETE /api/v1/grants/{grant_id}
POST   /api/v1/policy/evaluate-source

GET    /api/v1/actions
POST   /api/v1/actions/{action_id}/evaluate
POST   /api/v1/actions/{action_id}/approve
POST   /api/v1/actions/{action_id}/execute
POST   /api/v1/actions/{action_id}/cancel
POST   /api/v1/actions/{action_id}/rollback

POST   /api/v1/control/pause
POST   /api/v1/control/resume
POST   /api/v1/control/red-button
```

所有写 API 接受幂等键和期望版本；Grant 创建、动作批准和全局控制操作必须使用强身份
认证并进入不可抵赖审计。

## 11. 生产级评测指标

除根因和 Evidence 指标外，必须增加：

- `source_authorization_bypass_count = 0`；
- `secret_exposure_count = 0`；
- `unauthorized_action_count = 0`；
- `scope_expansion_count = 0`；
- 自动授权精确率和不必要审批率；
- 自动修复成功率、错误结案率和回滚成功率；
- No-Regression 违反次数；
- 最大实际爆炸半径与授权爆炸半径；
- 用户暂停/停止生效延迟；
- Evidence 血缘和 Action 审计完整率；
- 人工接管后是否能完整复用已有调查轨迹。

低风险自动修复只有在固定 Case 集中达到持续、统计显著的成功率，并且所有安全硬
指标为零后，才能从 `USER_APPROVAL` 晋升到 `AUTO_REVIEWED` 或 `AUTO_GRANTED`。

## 12. 实施状态与剩余工作

1. 已完成：保留现有 Probe Registry、Evidence、Budget、R2 单次审批和诊断 Outbox。
2. 已完成第二阶段：增加服务端派生主体、内置 Source Registry、持久化
   AuthorizationGrant、确定性 `PolicyDecision`、内部短期 Capability Token、Source
   Gateway、Grant 原子查询预算和完整 Source 访问审计；当前身份仍是 API Key 基线，
   尚未接入 OIDC 委托身份。
3. 已完成第二阶段：实现 Agent Metrics、Diagnosis Evidence、Topology Context 三个只读
   Connector，并输出带查询指纹、内容/投影哈希、脱敏统计和策略轨迹的 EvidenceEnvelope。
4. 已完成第一阶段：RCA 与意图解析调用前由确定性程序执行敏感字段脱敏、指标序列聚合、
   热点排序、事件去重、信号优先和上下文预算控制；原始 Evidence/Artifact 不被改写。
5. 已完成受限执行骨架：Action Registry、确定性预检、dry-run、幂等执行、验证和回滚
   接口已落地；只有明确标记为 `executable` 且同时通过环境白名单和 Case Policy 的动作
   才能执行。
6. 已完成 Case 授权联动：Case Stop 会取消关联 DiagnosisSession，并撤销同租户下绑定
   该 Case 的有效 Grant；Pause/Resume 同步控制诊断编排，避免暂停后继续下发新动作。
7. 下一步：将现有 `risk_level` 完整迁移为
   `operation_class + impact_level + authorization_decision`，并为当前短期 Capability Token
   增加持久化 JTI 防重放和 KMS 多密钥轮换。
8. 下一步：接入 OIDC、真实 Observability/Topology Connector、KMS 密钥轮换与
   Capability 防重放存储。
9. 下一步：完成独立 Action Grant、持久化 ActionAttempt、跨 Control 副本租约和版本栅栏，
   将采集授权与变更授权彻底分离。
10. 下一步：让正式评测由 Agent 自己完成恢复、复测和回滚；外部 Runner 只负责故障注入
    和兜底清理。未达到恢复与安全门禁前，不扩大业务动作范围。
11. 用独立 EnvironmentProfile 描述三节点实验环境；生产拓扑通过部署配置扩展，不写死
   在 AI 领域模型或 Case 协议中。

持续调查、证据治理、自动处置闭环、高可用、评测门禁和分阶段实施见
[`autonomous_ops_agent_implementation_plan.md`](autonomous_ops_agent_implementation_plan.md)。
