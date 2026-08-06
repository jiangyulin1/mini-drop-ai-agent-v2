# Mini-Drop AI 企业级生产架构与治理基线

> 状态：目标架构、上线门禁与当前实现差距（2026-08-05）  
> 产品范围：多租户、多集群、多地域故障调查与受控恢复服务。  
> 环境说明：当前三节点 Hyper-V 环境仅是测试 Profile，不是容量、可用性或安全边界。

用户功能、Case 智能循环、模型逻辑角色、ContextPacket、候选图、知识记忆和 RecoveryPlan
的详细规格见 [`ai_functional_design_v3.md`](ai_functional_design_v3.md)。

## 1. 生产设计结论

Mini-Drop AI 不是一个直接持有生产权限的模型进程，而是六个相互制约的平面：

```text
Identity Plane      用户、服务主体、租户、角色、委托身份
Policy Plane        Grant、影响等级、预算、变更窗口、审批与全局熔断
Evidence Plane      Source Gateway、Connector、EvidenceEnvelope、血缘与保留
Reasoning Plane     Context Builder、Model Gateway、候选图与报告校验
Execution Plane     Probe Gateway、Action Registry、Actuation Sandbox、回滚
Governance Plane    审计、评测、发布门禁、SLO、合规与人工接管
```

模型只能在 Reasoning Plane 工作。凭据、授权决策、网络访问、资源范围、变更执行和成功
判定都由模型之外的确定性组件控制。

## 2. 当前已实现的生产形状

当前代码已经具备：

- 服务端派生 API 主体，不信任请求体自报的执行身份；
- `authorization_admin` 与 `operator` 角色边界；
- 内置 Source Registry，API 不返回 `credential_ref`；
- PostgreSQL/SQLite 持久化、可撤销、带使用次数和查询预算的 AuthorizationGrant；
- 对主体、租户、Source、Operation、Case、资源选择器、时间窗和结果大小的确定性判定；
- HMAC 签名、最长五分钟、绑定资源和参数哈希的内部 Capability Token；
- Agent Metrics、Diagnosis Evidence 和 Topology Context 三个只读 Connector；
- Source Gateway 统一执行授权、Connector、结果预算、Grant 原子消耗和审计；
- EvidenceEnvelope，包括查询指纹、原始内容哈希、模型投影哈希、脱敏统计和策略轨迹；
- 指标聚合、热点排序、日志去重、信号优先、敏感字段脱敏和上下文预算控制；
- Action Registry 与预检策略 API；所有 Action 仍为 `policy_only`，不允许真实执行；
- 生产认证模式下 Source Gateway 默认关闭，必须显式开启。

当前不能宣称已经具备：OIDC、用户委托 Token、外部 Observability Connector、多副本
Capability Key 轮换、防重放存储、Action Grant、Actuation Gateway、双人审批、自动回滚、
跨地域高可用和生产规模压测。

## 3. 身份、租户与权限

### 3.1 身份链

生产身份链必须为：

```text
OIDC / Workload Identity
  -> API Gateway 验签
  -> Mini-Drop 再校验 issuer/audience/expiry
  -> Principal + Tenant Membership + Roles
  -> 用户委托 Source Token 或受限服务身份
```

禁止从请求 JSON 接受 `principal_id` 作为实际执行身份。当前 API Key 指纹是过渡实现；
生产必须替换为可撤销、可轮换、可区分用户和工作负载的身份。

### 3.2 角色职责分离

| 角色 | 能力 |
|---|---|
| `viewer` | 查看获授权 Case 和脱敏 Evidence |
| `operator` | 发起调查、调用已授权 Source、提交 Action 评估 |
| `authorization_admin` | 创建和撤销 Grant，不直接执行变更 |
| `change_approver` | 审批 I2/I3 变更，不修改策略 |
| `platform_admin` | 管理 Registry、Connector 和全局熔断 |
| `auditor` | 只读审计与评测结果 |

生产中同一主体不得同时拥有策略管理员和高影响变更最终审批权，紧急破窗操作必须单独
记录原因、工单、时效和事后复核。

## 4. 信息源与数据治理

每个外部 Connector 上线前必须声明：Owner、数据分类、租户边界、资源选择器、认证
方式、网络目的地、查询模板、成本、超时、分页、速率限制、最大结果、脱敏规则、降级
策略和审计字段。

Source Gateway 必须执行：

1. 从可信身份建立 `principal + tenant`；
2. 校验 Source/Operation 已注册且 Connector 版本受信；
3. 计算 PolicyDecision；
4. 签发只在 Gateway 内部流转的 Capability Token；
5. Connector 使用最小权限凭据执行结构化查询；
6. 校验 Schema、数据分类、大小和敏感字段；
7. 原子消耗 Grant 查询预算；
8. 返回 EvidenceEnvelope 并写入审计。

Connector 不得接受任意 URL、任意查询语言或开放 Shell。PromQL、日志查询和数据库查询
需要模板化参数；资源和时间窗由服务端重新渲染。

### 4.1 数据保留

| 数据 | 推荐默认保留 | 说明 |
|---|---:|---|
| 原始高敏 Artifact | 1—7 天 | 加密、短期 URL、逐对象授权 |
| 结构化 Evidence | 30—90 天 | 不可原地改写，支持合规删除 |
| 模型上下文投影 | 7—30 天 | 不保存 Secret，绑定版本与哈希 |
| Policy/Grant/Action 审计 | 180—365 天 | 防篡改归档，按租户检索 |
| 聚合评测指标 | 长期 | 不包含用户原始文本和业务数据 |

## 5. AI 上下文工程

复杂系统数据不能直接拼接到 Prompt。Context Builder 按以下流水线处理：

```text
Schema Validate
 -> Data Classification
 -> Secret/PII Redaction
 -> Time Alignment
 -> Deduplication
 -> Metric Aggregation / Trend Extraction
 -> Top-K Signal Selection
 -> Cross-source Correlation
 -> Token/Character Budget Allocation
 -> Evidence ID + Projection Hash
 -> Model Input
```

必须保留三份对象：不可变原始 Evidence、确定性模型投影、模型输出。模型投影要记录
程序版本、预算、删除数量、脱敏数量和哈希。模型不得引用 `_context_meta` 作为事实。

后续应增加：

- 按症状和候选假设分配各 Source 的上下文预算；
- 指标异常区间检测，而不是只做全窗口平均；
- 日志模板聚类、频率突变和首次出现时间；
- Trace 关键路径压缩与错误边聚合；
- 拓扑子图抽取，默认只保留目标、同机、一跳上下游和共享依赖；
- 变更事件与异常时间窗的确定性关联；
- Context 质量评分和缺失数据清单。

## 6. Model Gateway

所有模型调用必须经过统一 Model Gateway：

- 模型白名单、版本固定和按环境路由；
- 每租户/Case 的 Token、费用、并发和超时预算；
- 请求/响应 Schema 校验；
- 不记录 Secret 的结构化调用日志；
- Provider 故障时规则降级，不重复执行探针和变更；
- 模型升级采用离线回放、影子流量、Canary 和可回滚发布；
- 高敏数据只允许进入获批准的部署区域和模型端点；
- Prompt、Context Builder、模型、Analyzer 和 Policy 版本全部进入 Case。

模型输出不是授权，也不是事实。事实来自 Evidence，授权来自 PolicyDecision，执行结果
来自 Gateway，结案来自 No-Regression Verifier。

## 7. Action 与受控恢复

Action Registry 上线执行前，每个 Action 必须具备：

- 固定执行入口、版本和签名；
- 严格参数 Schema 和服务端 Renderer；
- 适用环境、最大目标数和故障域；
- 拓扑新鲜度、冗余、容量、冻结窗口和并发变更预检；
- dry-run 或等价影响预估；
- 幂等键、租约、超时和外部请求 ID；
- 成功指标、保护指标和稳定观察窗口；
- 回滚动作、回滚验证和回滚失败升级路径。

只有 Action Grant、Capability Token、Actuation Sandbox 和验证闭环全部实现后，某个
Action 才能从 `policy_only` 提升为 `executable`。提升必须逐 Action、逐环境进行，不能
通过一个全局开关批量开放。

## 8. 高可用与一致性

生产控制面要求：

- API、Orchestrator、Connector Worker 和 Analyzer 均可水平扩展；
- PostgreSQL 为 Case/Grant/Attempt 真相源，队列只负责唤醒；
- 写操作使用幂等键、期望版本、租约和唯一约束；
- Capability 签名密钥通过 KMS/Secret Manager 管理，支持 `kid` 和双密钥轮换窗口；
- Grant 撤销在所有副本的传播延迟有明确 SLO；
- Connector 超时和部分失败不得扩大到其他 Source；
- Region 故障时默认停止自动变更，调查可切换只读降级；
- 全局 Red Button 能冻结新 Action、撤销未使用 Token 并保留 Case 状态。

## 9. 可观测性与 SLO

### 9.1 服务 SLO 建议

| 指标 | 初始目标 |
|---|---:|
| Source Policy 判定可用性 | 99.99% |
| Grant 撤销生效 P99 | < 5 秒 |
| Source 查询额外策略延迟 P95 | < 50 ms |
| Case 状态持久化成功率 | 99.99% |
| 未授权数据返回次数 | 0 |
| Secret 进入模型次数 | 0 |
| 未经授权的变更执行次数 | 0 |
| 用户 Stop 生效 P99 | < 3 秒 |

### 9.2 必备遥测

- Policy 决策计数和原因；
- Grant 创建、撤销、过期、耗尽和并发冲突；
- Connector 请求量、延迟、超时、结果大小和降级；
- Context 原始/投影大小、脱敏、丢弃、异常信号数量；
- 模型调用量、费用、超时、Schema 失败和重试；
- Action 预检、执行、验证、回滚和保护指标；
- 租户、集群和 Source 维度的容量与限流。

日志和指标标签不得包含完整 Prompt、Token、Secret、原始日志正文或高基数 Artifact ID。

## 10. 生产评测与发布门禁

评测分为五轨：

1. 诊断质量：Top-1/Top-3、Evidence 引用、开放集拒答；
2. 工具规划：下一 Source/Probe 正确率、无效调用率、预算；
3. 安全授权：越权、Scope 扩大、Grant 撤销、Token 篡改和重放；
4. 恢复闭环：动作成功、错误结案、回滚、No-Regression；
5. 生产韧性：多副本、队列积压、数据库故障、Connector 部分失败和区域切换。

任何版本出现以下结果都禁止发布：

- 未授权 Source 返回数据；
- Secret 或高敏字段进入模型；
- Oracle/故障注入信息泄漏到模型上下文；
- 未注册或未授权 Action 执行；
- 用户 Stop/Red Button 未在 SLO 内生效；
- 回滚失败但 Case 被标记成功；
- 数据源缺失时生成确定性根因。

## 11. 分阶段上线

### 阶段 A：只读生产影子

- OIDC、租户、真实 Prometheus/Topology Connector；
- 只读 Grant 和 Source Gateway；
- 影子 Case，不影响值班决策；
- 验证权限、脱敏、成本和诊断质量。

### 阶段 B：值班辅助

- 日志、Trace、发布与知识 Connector；
- Case 协作、暂停、停止、纠正和人工动作回填；
- 只生成 Action 评估，不执行变更。

### 阶段 C：单动作审批执行

- Actuation Gateway、Action Grant、Capability 防重放；
- 一个 Mini-Drop 自身可逆动作；
- 每次人工批准，自动预检、执行、观察和回滚。

### 阶段 D：低风险自动执行

- 单个无状态实例摘流等窄场景；
- 仅在固定授权包络内 `AUTO_REVIEWED`；
- 持续评测达标后才能逐动作晋升 `AUTO_GRANTED`。

### 阶段 E：多集群规模化

- 多地域控制面、租户配额、跨集群只读关联；
- 高影响动作仍由外部 Change System 审批；
- 三节点实验 Profile 继续作为回归环境之一，而不是生产模板。
