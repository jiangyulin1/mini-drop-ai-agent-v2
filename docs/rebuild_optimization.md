# Mini-Drop 仓库复制重建与结构化优化

## 位置
- 原仓库：`/Users/szjyl/Documents/mini_drop`
- 重建副本：`/Users/szjyl/Documents/mini_drop_rebuild`

## 优化目标
在不改变公开 API、迁移和测试语义的前提下，把原先集中于少数超大文件的后端拆成可维护的 bounded-context 模块，并补上 Python 依赖锁定。

## 主要变化

### 1. ORM 模型拆分
- `server/app/models.py`（3310 行）拆为 `server/app/models/` 包：
  - `base.py`
  - `agent_task.py`
  - `case_plan.py`
  - `artifact_diagnosis.py`
  - `runtime_core.py`
  - `v6_core.py`
- 保留 `from server.app.models import X` 兼容；Alembic metadata 不变。

### 2. Repository 拆分
- `server/app/sql_repository.py` 从 5815 行降到 4693 行。
- v6 持久化方法抽到 `server/app/sql_repository_v6.py` 的 `SqlRepositoryV6Mixin`。

### 3. HTTP 路由层拆分
- `server/app/main.py` 从 6659 行降到约 1300 行，只保留：
  - 依赖装配、全局服务对象、中间件、lifespan/后台任务；
  - 需要 monkeypatch 兼容的健康检查与 AI 校验端点；
  - 共享 artifact/query helper；
  - 底部路由注册。
- 新增 `server/app/routes/`：
  - `common.py`
  - `agents_process.py`
  - `tasks.py`
  - `diagnoses.py`
  - `cases.py`
  - `plans_control.py`
  - `recovery.py`
  - `fanout.py`
  - `actuation.py`
  - `nlp.py`
- v6 Agent HTTP surface 单独拆到 `server/app/v6_routes.py`（约 1000 行）。

### 4. Python 依赖锁定
- 新增 `requirements.lock`（`pip freeze` 快照）。
- `scripts/package_candidate.py` 的 candidate manifest 现在记录 `python_lock_sha256`。

## 兼容性说明
- 旧的 `from server.app.main import app, repo` 继续可用。
- 所有 911 个 Python 测试、12 个 Sidecar 测试、58 个 Web 测试在重建副本上通过。
- `alembic check` 无 drift，单 head 仍为 `0023_v6_agent_core`。
- 本地总门禁 12/12 通过。

## 当前重建副本候选
以 `reports/implementation/ai-agent-runtime-state.json` 中的最新 `current_candidate` 为准；候选 manifest/archive 均通过 `--verify`。

## 后续建议
1. 把 `routes/*.py` 的 star-import 逐步替换为显式 import（当前 star import 是兼容层，不建议长期保留）。
2. 继续把 `sql_repository.py` 中历史 Drop/Task 方法拆成 mixin。
3. 前端超大组件继续拆分。
4. 在 VM 恢复后按 v6 状态文件继续 R4/Formal 验收。
