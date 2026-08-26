# AI 调查与 Evidence 工作区技术验收

验收日期：2026-08-26

本文只记录已经在当前代码库中核对到的行为，不把设计意图或 UI 文案当作已实现功能。

## 1. 总体架构

AI 调查以 Incident Case 为聚合根。前端入口为 `/cases`，主要页面由以下部分组成：

- `AIDiagnosisWorkspace`：会话列表、当前 Case、分支上下文、实时事件和 AI Turn 调度。
- `CaseConversation`：用户/AI 消息时间线、Markdown、Evidence/Task/知识片段引用。
- `CanonicalCaseWorkspace`：信息目标、Evidence、假设、依赖/因果图、结论、执行和恢复建议。
- `DiagnosisDataConsole`：人工采集批次、原始 Artifact 预览和批量下载。

后端以 Case Snapshot 作为工作区首屏聚合接口：

`GET /api/v1/cases/{case_id}/workspace?branch_id=...`

Snapshot 同时返回 Evidence、假设图、因果图、结论历史、采集请求、执行单元、调查树、消息和 Runtime Turn。

## 2. 角色显示验收

原实现的用户消息同时渲染头像文本“我”和作者名“你”，因此截图中出现了“我 / 你 / Mini-Drop”三个身份文本。

现已修正：

- 用户头像使用 `UserOutlined`，作者名为“你”。
- AI 头像使用 `RobotOutlined`，作者名为“Mini-Drop”。
- 头像只承担图形角色标识，不再产生第三个文字身份。

代码证据：`web/src/pages/ai-workspace/CaseConversation.jsx:44-53`。

## 3. 任务、Evidence 与数据台边界

### 3.1 “任务与证据”页面

`/tasks` 对应 `Dashboard.jsx`，面向全局运维任务：

- 调用 `/api/tasks` 列出任务，支持搜索、状态筛选和分页。
- 展示 Worker、任务状态、状态事件和任务级可视化。
- 创建单机/多机任务、取消、重试、删除终态任务。
- 进入 `/task/{task_id}` 查看完整 Artifact、尝试记录和任务事件。

### 3.2 Case 内 Evidence 数据台

`DiagnosisDataConsole.jsx` 也读取同一批 Task，但工作目标不同：

- 按 `collection_session_id` 聚合多个 Worker 的任务为一次采集批次。
- 只显示当前 Case 或全部采集数据。
- 以当前 Case 的目标范围快速创建多机采集。
- 预览原始任务产物、批量下载，并将批次通过 `attachCaseResources` 绑定到 Case。
- 绑定后调用 `/api/v1/cases/{case_id}/agent/turn` 请求 AI 分析。

### 3.3 是否重复

存在明显的操作层重复：两处都能看到 Task、Artifact、预览和下载，并都能创建采集。
但它们不是同一数据实体：

- Task 是采集执行记录。
- Artifact 是 Task 的原始产物。
- Evidence 是 Case 内经过投影、生命周期和审查治理的事实。
- Evidence 数据台是 Case 调查的采集入口，不是 Evidence canonical store 的编辑器。

当前问题是 UI 没有把“全局任务管理”和“当前 Case 采集入口”的边界解释清楚，用户会感到明显重复；后端数据链路本身不是简单复制。

另外，Evidence 数据台曾因 `.dataBody .console { flex: 0 0 auto; }` 没有占满外层工作区，造成右侧横向空白。现已改为可伸展宽度（`flex: 1 1 auto; width: 100%; min-width: 0`），浏览器实测数据台从会话栏右侧延伸到页面右边界。

## 4. 分支与调查树

### 4.1 已落地的分支隔离

前端维护 `activeBranchId`，切换分支时会把它传入：

- Workspace Snapshot
- Case 事件列表和 SSE
- AI Turn
- Evidence 批次分析

代码证据：`web/src/pages/AIDiagnosisWorkspace.jsx:196,337-341,443,798,831,854,963`。

后端对 branch-local Evidence、假设、Evidence 缺口、因果图、结论和 Runtime binding 均有分支过滤；Evidence 默认是 PUBLIC_SEED，跨分支共享必须经过显式 promote。

### 4.2 调查树模型

`InvestigationTreeNodeModel` 有以下树字段：

- `node_id`
- `parent_node_id`
- `branch_id`
- `run_id`
- `depth`
- `status`
- `evidence_refs`

创建节点时会校验父节点存在、同一 Run 且未关闭；子节点继承父节点 branch。Evidence 失效时，依赖节点会被 INVALIDATED，其后代递归 ABANDONED。

代码证据：`server/app/models/v6_core.py:67-115`、`server/app/sql_repository_v6.py:203-291`。

### 4.3 “新建隔离分支”实际创建什么

`POST /api/v1/cases/{case_id}/branches` 当前行为是：

1. 生成新的 `branch_id`。
2. 创建新的 `investigation_run`。
3. 创建 `AgentCycle`，触发类型为 `USER_BRANCH_CREATED`。
4. 创建一个 `CYCLE` 根节点，`parent_node_id = null`。
5. 写入 `investigation_branch_created` Case 事件。

代码证据：`server/app/routes/cases.py:1441-1484`、`server/app/sql_repository_v6.py:509-578`。

因此当前按钮创建的是“Case 级隔离探索根”，不是“在当前 Evidence、当前假设或当前树节点下面创建子分支”。

真正的父子节点只能由内部 Agent Tree 工具传入 `parent_node_id` 创建：
`POST /internal/agent/tools/investigation-tree/node`。

前端当前 Branch Select 只展示扁平列表，没有父分支、当前节点或 Evidence 节点选择器。这是用户理解“树结构”时产生困惑的直接原因。

### 4.4 会话消息分支跟踪

原实现存在缺口：普通用户消息没有保存 `branch_id`，确定性 Agent 回复有一处也没有保存，导致分支事件流可能串线或缺消息。

现已补齐：

- `CaseMessageRequest` 支持 `branch_id`。
- `append_case_message` 将 branch 写入用户消息事件 payload。
- 确定性 Agent 回复保存 branch。
- Runtime `assistant.message` 和 `turn.completed` 事件 payload 携带 branch。
- 数据台的人工批次说明消息也传入当前 branch。

代码证据：`server/app/case_collaboration.py`、`server/app/sql_repository.py:1348-1374`、`server/app/routes/plans_control.py:1000-1007,1180-1187,1591-1615`、`server/app/v6_routes.py:2915-2938`。

## 5. 报告与知识库

### 5.1 报告

前端入口位于右侧工作区“结论修订”Tab，只有当前 Case 已存在 `conclusion` 时才显示：

- “导出报告”打开 Markdown 预览。
- “下载 Markdown”下载 `case-{case_id}-report.md`。
- 报告包含结论、原因分组、Claim-Evidence 绑定、恢复建议和局限性。

代码证据：`web/src/pages/ai-workspace/CanonicalCaseWorkspace.jsx:613-665`；后端 `server/app/routes/cases.py:923-1005`。

### 5.2 写入知识库

“写入知识库”按钮先刷新 Case Memory，再 promote：

1. `POST /api/v1/cases/{case_id}/memory/refresh`
2. `POST /api/v1/cases/{case_id}/memory/promote`

刷新内容来自 Case 事件、Assistant 消息、Evidence、活跃假设、结论和恢复建议；promote 会创建一个 `GLOBAL`、`kind=MEMORY` 的知识文档，并记录 `promoted_document_id`。

代码证据：`server/app/diagnosis/knowledge_memory.py:274-410`、`server/app/routes/knowledge_memory.py:225-320`。

知识检索明确标记 `knowledge_is_evidence=false`，知识片段不能替代当前运行时 Evidence。

### 5.3 当前缺口

报告接口和 Case Memory 接口目前没有 `branch_id` 参数：

- 报告使用默认 `repo.get_conclusion(case_id, tenant_id)`。
- Memory refresh 汇总的是 Case 级数据。
- promote 后写入的是 GLOBAL 文档。

所以“报告/知识库功能”确实落地，但还不是“当前活动分支的独立报告/独立记忆”。这是验收时必须标记为部分完成的功能。

## 6. 验证结果

- 调查树、分支隔离和 Agent Tree Gateway：49 个后端测试通过。
- Knowledge Memory、结论报告和分支状态：25 个后端测试通过。
- 前端 CaseConversation / CanonicalCaseWorkspace：11 个测试通过。
- 前端 ESLint：通过。
- 前端生产构建：通过。

## 7. 最终状态判定

| 功能 | 状态 | 结论 |
| --- | --- | --- |
| AI 调查主链路 | 已落地 | Case Snapshot、Evidence、Agent Turn、结论链路完整 |
| 用户/AI 角色显示 | 已修正 | 只显示“你”和“Mini-Drop”两种文字身份 |
| 分支读取与 Evidence 隔离 | 已落地 | 分支上下文会进入 Workspace、Turn、Runtime 和 Evidence 过滤 |
| 调查树持久化 | 已落地 | 后端有 parent-child、依赖传播和失效递归 |
| 用户可视化树形分支创建 | 未完整落地 | UI 只有 Case 级新根和扁平 Branch Select |
| 分支会话消息跟踪 | 已补齐主链路 | 用户/确定性回复/Runtime 事件均写入 branch |
| Evidence 数据台与任务页边界 | 部分完成 | 数据实体有边界，入口和操作存在重复 |
| 完成会话导出报告 | 已落地但 Case 级 | 结论存在时可预览并下载 Markdown |
| 完成会话归入知识库 | 已落地但 Case 级 | 生成 GLOBAL Memory 文档，不是分支独立记忆 |
