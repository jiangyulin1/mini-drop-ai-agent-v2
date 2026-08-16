# Mini-Drop 目标架构 v2（当前副本 `mini_drop_ideal`）

## 1. 架构原则
1. **公开 API 是合同**：URL、请求/响应 envelope、事件类型、`from server.app.main import app/repo` 不变化。
2. **组合根不承载业务**：`main.py` 只装配依赖、注册路由、暴露进程入口。
3. **按 bounded context 拆分**：Agent/Task、Case/Evidence、Plan/Campaign、Runtime、Causal/Conclusion、Web。
4. **显式依赖优于星号导入**：路由层明确 import 所需符号；组合根负责 re-export 兼容旧调用。
5. **数据库写路径有唯一 owner**：Case 派生执行最终只能由 Supervisor/ExecutionUnit 事务产生。
6. **适配器可替换**：AgentRuntimePort、SourceGateway、Storage、Repository 都通过接口或边界模块隔离。

## 2. 目标模块布局
```text
server/app/
  main.py                  # composition root + process entry
  container.py             # service singleton assembly
  http/
    app.py                 # FastAPI app + middleware
    deps.py                # request/tenant/role helpers
    health.py              # healthz/readyz/ai-validation
    routes/
      drop.py
      diagnoses.py
      cases.py
      plans.py
      runtime.py
  domain/
    case.py
    evidence.py
    plan.py
    campaign.py
    runtime_cycle.py
    causal.py
  application/
    case_supervisor.py
    evidence_ingestion.py
    runtime_dispatcher.py
    verifier.py
  ports/
    repository.py
    runtime.py
    storage.py
    source_gateway.py
  adapters/
    sql_repository/
    pi_sidecar.py
    minio.py
    source_gateway.py
  jobs/
    sweeper.py
    wakeup_loop.py
  models/
    ...
  v6_routes.py             # 过渡层，逐步迁入 http/routes/runtime.py
```

## 3. 当前副本已完成的架构动作
- 模型包化：`server/app/models/`。
- Repository v6 mixin：`server/app/sql_repository_v6.py`。
- 路由包化：`server/app/routes/`，且**已移除路由模块的 `from server.app.main import *`**，全部改为显式 import。
- v6 HTTP surface 独立：`server/app/v6_routes.py`，同样显式 import。
- Python 依赖锁定：`requirements.lock`。
- Candidate Manifest v2 记录逐文件 hash 与 `python_lock_sha256`。

## 4. 仍需演进（按优先级）
1. 组合根 `main.py` 底部的 route re-export 仍使用 star import；保留为兼容层，下一步改为显式 `__all__` 生成。
2. 抽出 `container.py`、`http/app.py`、`jobs/`，让 main 回到 500 行以内。
3. 路由模块中仍存在少量 `main` 命名空间依赖（历史 helper）；随 container 拆分逐步改注入。
4. SQL Repository 继续拆为 Drop/Task、Case、Evidence、Runtime 四个 mixin。
5. 前端大组件继续按 feature 拆分。

## 5. 行为不变保障
- 每次重构必须全量运行：
  - Python `pytest -q tests`
  - `npm --prefix agent_runtime/pi-sidecar test`
  - `npm --prefix web test`
  - `ruff check server agent analyzer`
  - `npm --prefix web run lint`
  - `scripts/check_migrations.py`
  - `scripts/run_local_gate.py --frontend`
  - `scripts/package_candidate.py --build-web --verify`
- 任何路由/模型/repository 调整必须保持现有 911 个 Python 测试全绿。
- 对外 API 变更必须提供 alias 或兼容层，禁止只改 canonical URL。
