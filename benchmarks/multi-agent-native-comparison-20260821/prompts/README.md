# 执行提示词包

将 [00-master-executor.md](00-master-executor.md) 全文作为执行 Agent 的首条任务。它负责把本工作区从“设计完成”推进到“可验收的真实结果”。

执行过程中按需将下列提示词作为后续消息发送。不要同时启动多个 Agent 平台；必须完成一个 Agent 的封存和清理后，才能开始下一个。

| 文件 | 使用时机 | 目标 |
|---|---|---|
| `00-master-executor.md` | 第一条，必用 | 总控、环境检查、完整流水线、停止条件 |
| `01-build-real-cases.md` | 案例包尚未存在时 | 建立 9 个公开包与私有 Oracle，防止泄题 |
| `02-adapt-and-run.md` | 每个被测 Agent | 安装、适配、smoke、3 x 9 运行、封存与清理 |
| `03-score-and-report.md` | 有运行产物后 | 评分、失败归因、三赛道结果、可复现报告 |
| `04-acceptance.md` | 最后一步，必用 | 机器可检查的验收门和最终结论格式 |
| `system-prompt-common.md` | 统一回放时 | 给所有被测 Agent 的相同系统提示词 |

敏感信息规则：不从环境说明、聊天记录、源码 README 或 shell history 中复制密码、token、IP、API key 到运行日志、结果包、Prompt 或 Git。执行 Agent 只能使用已在目标环境安全注入的认证方式；缺少授权时记录阻塞原因，不猜测或泄露凭据。
