# 仓库维护与清理

仓库清理的目标是让当前实现、可复现输入和长期文档清晰可辨，而不是追求最少文件数。
删除前必须确认没有运行时、测试、发布脚本或契约引用；不使用无目标的
`git clean -fdx`、递归删除仓库根目录或重置用户工作树。

## 内容分类

| 类别 | 位置 | 处理规则 |
|---|---|---|
| 当前产品合同 | 根 `README.md`、`docs/evidence_native_agent_unified_architecture.md` | 保持与代码一致，替代旧方案时直接更新 |
| 环境与运行手册 | `docs/environment-setup.md`、`docs/deployment-profiles.md`、release/security runbook | 只写通用路径和占位符，不写某台机器的 Secret |
| Env 模板 | `.env.example`、`deploy/env/*.example` | 必须无真实凭据；每个模板只服务一种明确拓扑 |
| 一次性评测与审计 | `reports/` | 带日期、写明范围；不能作为“当前实现”真源 |
| Benchmark/Testset | `benchmarks/`、`testsets/` | 保留版本化 manifest、Oracle 边界和可复现 runner |
| 生成物和本机状态 | `.venv`、`node_modules`、`web/dist`、缓存、SQLite、runtime spool | Git 忽略；需要时重新生成 |
| 机器私有资料 | `.env`、证书私钥、SSH、个人访问说明 | 保存在仓库外；发现入库立即撤销并轮换凭据 |

历史设计、固定测试数字、某次云主机/Hyper-V IP 和带日期的部署状态不应继续放在根目录
或当前文档索引中。确需保留的事实写入 `reports/`；已经被当前架构吸收的长篇草稿由 Git
历史和 release tag 保留，不在工作树维护第二份真源。

## 清理流程

1. 查看工作树，区分用户未提交改动与清理目标。
2. 用 `rg` 搜索文件名、API 名、环境变量和脚本入口的所有引用。
3. 确认目标属于生成物、历史报告或已被替代的实现，而不是 lock、migration、schema、
   manifest、fixture 或部署输入。
4. 只删除已列明的精确路径；用户改动和无关文件保持不变。
5. 更新 README、文档索引、CI、Makefile 和发布清单中的引用。
6. 运行与变更范围匹配的静态检查、定向测试和 Compose `config`。
7. 检查 staged diff、凭据、空白错误和意外生成物后再提交。

常用只读检查：

```bash
git status --short --ignored
git ls-files
rg -n '<file-or-symbol>' .
git diff --check
uv lock --check
npm --prefix web ls --depth=0
npm --prefix agent_runtime/pi-sidecar ls --omit=dev --depth=0
```

## 可复现依赖

- Python 以 `uv.lock` 为唯一解析真源；`requirements.lock` 只能由 lock 导出。
- Web 与 Pi Sidecar 分别维护自己的 `package-lock.json`，使用 `npm ci`。
- Pi SDK 版本从已安装 package metadata 读取，不在文档或健康响应中另造版本号。
- 发布包不包含 `.venv`、`node_modules`、Secret、个人 Pi 配置或本机 SQLite。

## 报告与测试数据

一次性运行目录应包含输入摘要、环境、断言、流量和限制。失败或 blocked 记录必须明确
标注，不能把预生成的轮次行当成已经完成的评测。真实运行结果只声明实际覆盖的主机、
Agent、Collector、Provider 和轮次；单机 macOS 结果不能外推为跨主机生产精度。

长期文档不保存易失的“当前有多少测试通过”或“某服务正在运行”。这些信息属于带日期的
报告或 CI。文档只保存可重复的命令、通过标准和能力边界。

## 凭据与贡献者元数据

- 不提交真实 API Key、Token、密码、私钥、Cookie 或带凭据的 URL。
- 不在日志、截图、测试 fixture 或命令行参数中回显 Provider Key。
- `.claude/`、个人助手配置和本机 IDE 状态不是项目交付物，不进入 Git。
- 提交消息不添加未经本人确认的 `Co-authored-by` trailer。GitHub Contributors 来自 Git
  提交作者历史；删除文件或 `.mailmap` 不能删除历史贡献者。需要改写作者历史时必须先
  备份、评估所有协作者和发布标签影响，并将其作为单独的、明确授权的维护操作。

## 提交前最小门禁

文档或环境配置变更至少运行：

```bash
docker compose --env-file .env.example config --no-interpolate --quiet
docker compose --env-file deploy/env/control.env.example \
  -f docker-compose.control.yml config --no-interpolate --quiet
docker compose --env-file .env.example \
  -f docker-compose.yml -f docker-compose.local.yml \
  config --no-interpolate --quiet
bash -n deploy/scripts/*.sh
git diff --check
```

代码、依赖、迁移或前端行为发生变化时，继续执行
[`release-baseline-runbook.md`](release-baseline-runbook.md) 中相应门禁。

## Evidence-native 控制链维护

专家介入相关变更必须保持以下所有权边界：

- `server/app/diagnosis/case_control.py` 只解析有限的聊天控制语法并通知 Runtime；Case、Scope、Plan 和 Revision 的真实提交仍由确定性服务完成。
- `POST /api/v1/cases/{case_id}/commands` 与 `POST /api/v1/cases/{case_id}/agent/turn` 的控制路径必须共享 Case Command 语义；暂停、停止、恢复不能退化为普通模型 Turn。
- Service/Process/Dependency Edge focus 必须经过 scope/control CAS；Process 只能使用当前 Discovery/Membership Evidence 证明的身份。Dependency Graph 不能被维护成 Causal Graph 的替代品。
- focus 或 Evidence review 导致的 revision/generation 变化必须保留旧事件和 Artifact，但拒绝旧 revision 的新业务写入；Runtime 不可用时，Case 状态仍应先持久化，并留下可恢复通知状态。
- `GET /api/v1/cases/{case_id}/investigation-summary`、Workspace 和 `CaseContextSnapshot` 的 focus 字段必须保持同一结构，前端只能消费这些投影，不能自行拼接控制状态。

涉及上述边界的提交至少运行：

```bash
./.venv/bin/pytest -q tests/test_expert_intervention.py \
  tests/test_agent_runtime_turn_endpoint.py \
  tests/test_network_discovery_projection.py \
  tests/test_evidence_governance.py \
  tests/test_blind_gap_dynamic_evidence.py
./.venv/bin/pytest -q
git diff --check
```

低带宽环境下发布时，先确认 `git status --short` 只包含预期文件，再使用普通
`git push origin main`。若 HTTPS/HTTP2 传输失败，不使用 `--force` 覆盖远端；保留本地提交
和错误信息，待网络恢复后重试，或由有权限的维护者按同一父提交发布 Git Data API 提交。
发布后核对：

```bash
git fetch origin main
git rev-parse HEAD
git rev-parse origin/main
git diff --stat origin/main..HEAD
```

远端未包含本地提交时，维护记录必须明确写出本地 commit、远端 commit 和失败原因，不能把
“已提交”表述成“已发布”。
