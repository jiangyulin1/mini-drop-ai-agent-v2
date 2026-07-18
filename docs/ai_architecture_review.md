# Mini-Drop AI 架构审核与实施说明

## 审核结论

现有工程已经具备 Drop 轻量复刻所需的任务调度、Agent 采集、Artifact 元数据、Analyzer、Web 和任务级 RCA，但原来的 AI 能力本质上是“对一个已完成 Task 做事后归因”，不能承载 `AI功能设计.md` 中跨 Task、跨实例、可审批、可恢复的诊断会话。

本轮没有改变 Drop 的确定性采集内核，而是在它上面增加 `server/app/diagnosis/` 控制层。实现定位为 MVP-0 闭环和 MVP-1 的数据/安全骨架，不声称已经具备腾讯内部生产系统的完整权限、拓扑和基线基础设施。

## 联网核验依据

- [OWASP LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) 建议把外部内容视为不可信数据、确定性校验输出、执行最小权限并对高风险动作增加人工审批。
- [OWASP LLM06:2025 Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/) 强调工具最小化、在用户授权上下文中执行以及高影响动作的人机协同。
- [Pydantic Model Config](https://docs.pydantic.dev/2.1/api/config/) 明确 `extra=forbid` 可拒绝未知字段；新诊断 API 和模型输出边界据此使用严格模型。
- [gRPC Python Basics](https://grpc.io/docs/languages/python/basics/) 要求使用 `grpcio-tools` 从 Proto 生成 Python 接口；本项目进一步固定生成器与运行时次版本，避免生成代码的运行时版本保护导致服务无法导入。

## 发现的问题与处理

| 问题 | 原实现风险 | 本轮处理 |
|---|---|---|
| 诊断与 Task 一一绑定 | 无法按中间证据动态追加多个 Task | 新增持久化 `DiagnosisSession` 与 `child_task_ids` |
| 没有历史拓扑对象 | 容易用当前实例关系解释历史故障 | 创建诊断时冻结 `TopologySnapshot`，记录有效时间和来源质量 |
| NLP 直接选择采集器 | 缺少服务范围、假设、预算和风险层 | 新增严格诊断意图、目标范围、假设图和策略预算 |
| 未知模型字段被静默忽略 | 工具参数可能绕过预期契约 | 新诊断边界模型统一 `extra=forbid` |
| 深度采样没有诊断级审批 | perf/eBPF 可被 AI 路径直接扩散 | 固定 Probe Registry；R2 只能 `single_execution` 审批 |
| 工作流依赖单次 HTTP 进程 | 崩溃后可能重复创建探针 | 持久化步骤、`diagnosis_step_id` 幂等键、数据库短租约和后台推进 |
| 只有任务级证据路径 | 跨 Task 结论无法稳定引用 | 新增不可变 Evidence 摘要、SHA-256、Artifact/Task 引用和 Claim 引用 |
| 伪精确置信度 | 未校准小数容易被当作概率 | 新会话报告仅输出高/中/低/不可判断和可解释分量 |
| `sys_metrics` 未进入 RCA 主链路 | 系统指标规则在 HTTP 诊断中实际失效 | 修复 `main.py → run_diagnosis_context → collect_evidence` 参数链 |
| gRPC 生成器/运行时漂移 | 本地测试导入即失败 | 将 grpcio 与 grpcio-tools 约束在同一 1.80 次版本 |
| Python 3.9 路由注解不兼容 | 声明支持 3.9，但 FastAPI 无法注册路由 | 公开路由改用 `Optional[int]` |

## 新增数据流

```text
自然语言 + 明确服务实例上下文
→ 严格意图（不生成命令）
→ 冻结拓扑快照
→ 建立多个候选假设
→ 复用时间窗内已有 Task/Artifact
→ 从 Probe Registry 选择最小成本证据
→ R1 自动调度 / R2 单次审批
→ Analyzer 结构化结果
→ Evidence ID + SHA-256
→ 规则候选排序
→ 等级置信报告 / INSUFFICIENT_EVIDENCE
```

所有新建 Task 都写入：

```json
{
  "options": {
    "diagnosis_id": "diag_session_...",
    "diagnosis_step_id": "step_...",
    "probe_id": "host_process_metrics",
    "registered_probe": true
  }
}
```

恢复时先按 `diagnosis_step_id` 查找已有任务，再决定是否创建，避免工作流重启造成重复采样。

## API

```text
POST /api/v1/diagnoses
GET  /api/v1/diagnoses
GET  /api/v1/diagnoses/{diagnosis_id}
POST /api/v1/diagnoses/{diagnosis_id}/approvals
GET  /api/v1/probes
```

创建诊断必须提供可信的服务实例映射，最小示例：

```json
{
  "query": "服务 service-a CPU 飙高，请定位原因",
  "context": {
    "service_id": "service-a",
    "environment": "production",
    "instances": [
      {
        "service_id": "service-a",
        "instance_id": "service-a-1",
        "host_id": "host-1",
        "agent_id": "agent-1",
        "pid": 1234,
        "environment": "production"
      }
    ],
    "dependencies": []
  },
  "budget_profile": "production_safe"
}
```

## 当前边界

以下能力需要腾讯侧或后续基础设施支持，当前轻量探索版只保留接口或明确降级：

- 真实 OIDC、多用户 RBAC、资源组和 Artifact 逐对象授权；当前仍是 API Key + 可选服务白名单。
- 来自 CMDB/Kubernetes/Service Mesh 的历史拓扑；当前使用请求上下文生成快照。
- Prometheus/Trace/日志/发布记录的统一基线服务和时间偏差估计。
- TaskAttempt、Transactional Outbox、独立 Analyzer 队列和完整对象存储对账。
- 多跳 Trace 因果分析和经过演练集校准的概率输出。
- 自动修复；当前 R3 始终只提供人工建议。

这些边界不会被 LLM 猜测填补。缺少实例映射时进入 `NEEDS_SCOPE_CONFIRMATION`，缺少区分性证据时进入 `INSUFFICIENT_EVIDENCE`。

## 交付目录安全提醒

工作区根目录的 `final/.env` 不是本 Git 仓库的一部分，但包含非空的 AI Provider 凭据、对象存储 Secret 和 ChatOps 配置。没有读取或记录具体值，也没有自动改动该部署文件。若 `final/` 曾被打包、邮件发送或上传，应立即轮换相关凭据，并将交付物改为只包含 `.env.example`；真实 Secret 应由部署环境或 Secret Manager 注入。

## 验证

- 后端全量测试：314 passed。
- 新增诊断控制层测试：覆盖范围确认、注册探针、R2 审批、证据引用、证据不足、未知字段拒绝、服务白名单。
- Web：Vite production build 通过。
