# 轻量 AI 评测包（VM 优先）

这套评测包用于高频开发回归，不替代 `ai_ops_v2` 的三节点 Hyper-V 正式验收。

## 设计取舍

- `smoke`：8 个高区分度结构化证据案例，无网络、容器、模型调用和数据库依赖，适合每次修改后运行。
- `quick`：12 个结构化证据案例，再叠加 MCP、授权、动作、治理和恢复安全测试。
- `vm-smoke`：从现有三节点套件抽取 7 个关键案例，覆盖自身 CPU、下游 Redis、支付恢复、同机噪声、跨 Worker 网络、跨 Worker 复合故障和健康拒答。
- `vm-release`：正式发布仍执行现有 30 个案例 × 3 次，Hyper-V + Docker Swarm 是权威环境。

轻量案例吸收了 OpenTelemetry Demo 的公开故障开关（支付不可达、CPU、缓存增长、网络延迟、应用失败），采用 AIOpsLab 的“故障—任务—证据—评价”拆分思想，并增加反事实与复合故障。这里只转写故障模式和测试思想，没有复制外部数据集或代码。

参考来源：

- [OpenTelemetry Demo Feature Flags](https://opentelemetry.io/docs/demo/feature-flags/)
- [Microsoft AIOpsLab](https://github.com/microsoft/AIOpsLab)
- [DeathStarBench](https://github.com/delimitrou/DeathStarBench)
- [MCP Tools Specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

## 运行

本机最快检查：

```bash
python scripts/run_lightweight_ai_eval.py --profile smoke
```

本机完整快速检查：

```bash
python scripts/run_lightweight_ai_eval.py --profile quick
```

三节点 VM 抽检（PowerShell）：

```powershell
$env:MINI_DROP_VM_PASSWORD = '<VM 密码>'
python scripts\run_lightweight_ai_eval.py `
  --profile vm-smoke `
  --resume `
  --output-dir reports\eval\lightweight\vm-smoke
```

正式发布：

```powershell
$env:MINI_DROP_VM_PASSWORD = '<VM 密码>'
python scripts\run_lightweight_ai_eval.py `
  --profile vm-release `
  --resume `
  --output-dir reports\eval\ai-ops-v2\release
```

执行 VM Profile 前，先确认 Control 为 `192.168.10.10`、Worker 为 `.11/.12`，两 Agent 在线且 Online Boutique 12/12。运行器沿用现有精确回滚、恢复探活、私有 Oracle 隔离和断点续跑机制。

## 何时跑哪一层

| 时机 | Profile | 目标耗时/代价 |
|---|---|---|
| 本地修改诊断规则 | `smoke` | 秒级 |
| 修改 MCP、权限、动作或恢复 | `quick` | 秒到分钟级 |
| 合并前或候选构建 | `vm-smoke` | 只跑 7 个真实案例 |
| 发布/模型切换/策略大改 | `vm-release` | 30×3，完整正式结果 |

`smoke` 或 `quick` 通过只能说明确定性推理和安全边界没有明显回归，不能声称真实诊断准确率通过。最终准确率、稳定性和恢复闭环以 VM 结果为准。

每次执行都会在输出目录的 `run_manifests/` 下创建不可覆盖的
`experiment-run.v1` 清单，同时更新便于查看的 `run_manifest.json`。清单记录代码状态、
数据集输入哈希、Reasoner、规则/特征/规划器/工具集版本、随机种子和运行环境，不读取或
保存 API Key、VM 密码等环境变量。需要可复用的外部运行标识时传入 `--run-id`。

## 数据维护

- 场景及 Profile 均在 `manifest.json` 中版本化。
- 新故障先添加一个轻量证据回放案例，再映射到 `ai_ops_v2` VM 注入。
- 失败案例不得删除；修复后保留为回归案例，并增加服务名或噪声不同的反事实变体。
- Oracle 只存在于本地场景预期或正式 private 目录，不进入被测系统上下文。
