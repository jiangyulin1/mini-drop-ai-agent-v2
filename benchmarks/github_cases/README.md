# GitHub 真实项目评测集（三节点 VM）— native 变体

> 目标：证明"基础用户 → 面向服务 → AI 多轮定位根因"的端到端能力，
> 使用 **GitHub 真实开源项目**作为被测服务，故障、采集、证据全部真实。

## 1. 被测项目选型（2026-08-06 修订）

| 项 | 选择 | 理由 |
|---|---|---|
| 服务 | **OpenTelemetry Demo `product-catalog`**（Go，tag 2.2.0） | 真实开源生产形态微服务；Go 便于 perf profile 定位热点；**依赖 PostgreSQL**（lib/pq），可注入"数据库 down"下游故障 |
| 下游依赖 | **PostgreSQL 15**（apt 安装） | 可注入"连接拒绝"下游故障 |
| 负载 | **eval-load**（本评测集自研 gRPC 压测器，复用 product-catalog 的 genproto） | 高并发 ListProducts/GetProduct 打 CPU 热点；下游故障时输出失败统计 |
| 仓库 | https://github.com/open-telemetry/opentelemetry-demo（tag 2.2.0） | 可复现；Apache-2.0 |

**为何替换原 checkoutservice 方案**：
- `v1.11.0` tag 实际不存在（README 旧记录有误）；
- 现代版本 checkout 需 go 1.25 且硬依赖 Kafka（sarama），v1.2.1 checkoutservice 也硬依赖 Kafka；
- 集群网络受限：Docker Hub 与 Go module proxy 均不可达（仅 GitHub 可达）→ 无法 docker build / go mod download；
- 因此改为：**本地交叉编译静态二进制**（GOPROXY=goproxy.cn）+ **apt 安装 PostgreSQL**，全部产物上传 worker 直接运行。

## 2. 拓扑

```text
worker1 (4vCPU/4GB)                control (192.168.10.10)
├── product-catalog (Go gRPC :3550) ├── Mini-Drop Server + 评测 runner
├── PostgreSQL (:5432)             └── 调 worker1 的 Agent
└── Mini-Drop Agent（10 能力）
```

## 3. 四个场景

| case_id | 症状文本（基础用户口吻） | oracle 根因 | 注入 |
|---|---|---|---|
| `catalog-cpu-hotspot` | "product-catalog 变慢，CPU 很高" | self / cpu | eval-load 高并发打 gRPC 端点 |
| `catalog-downstream-pg-down` | "大量报错，查询商品不可用，日志有连接拒绝" | downstream / network | `systemctl stop postgresql` |
| `catalog-host-io-contention` | "变慢但 CPU 不高，怀疑磁盘问题" | same_host / io | 同机 dd 写盘 |
| `catalog-no-fault-baseline` | "偶尔有点慢，帮我检查" | unknown / unknown | 无 |

每个场景的 oracle 只存在于 `scenarios/suite.json`，**不会进入模型上下文**。

## 4. 搭建（worker1）

```bash
# 开发机：交叉编译（GOPROXY=https://goproxy.cn）
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -buildvcs=false -o product-catalog ./src/product-catalog
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -buildvcs=false -o eval-load ./src/product-catalog/cmd/eval-load

# 上传 + worker 上执行
scp product-catalog eval-load init.sql worker1:
ssh worker1 "bash scripts/native/setup_native.sh"
```

## 5. 运行评测（control）

```bash
for case in catalog-cpu-hotspot catalog-downstream-pg-down \
            catalog-host-io-contention catalog-no-fault-baseline; do
  ssh worker1 "bash benchmarks/github_cases/scripts/inject.sh $case worker1"
  python benchmarks/github_cases/scripts/run_eval.py \
    --server https://127.0.0.1 --api-key "$MINI_DROP_API_KEY" \
    --worker linux-worker-1 --cases "$case" \
    --output-dir reports/eval/github-cases
  ssh worker1 "bash benchmarks/github_cases/scripts/inject.sh --clean $case worker1"
  sleep 135  # 等待 sys_metrics 复用窗口过期，避免跨场景复用污染
  done
```

或直接使用编排脚本（开发机驱动三节点）：

```bash
python benchmarks/github_cases/scripts/run_bench.py \
  --cases catalog-cpu-hotspot,catalog-downstream-pg-down,catalog-host-io-contention,catalog-no-fault-baseline
```

## 5.1 实测结果（2026-08-06，Hyper-V 三节点）

| case_id | 注入 | 实际位置 | 实际领域 | 通过 |
|---|---|---|---|---|
| catalog-cpu-hotspot | eval-load 高并发 | self | cpu | ✅ |
| catalog-downstream-pg-down | 停 PostgreSQL + 低并发压测 | downstream | network | ✅ |
| catalog-host-io-contention | 循环磁盘写 + 低并发压测 | same_host | io | ✅ |
| catalog-no-fault-baseline | 无 | insufficient_evidence（无确定根因） | unknown | ✅（无误报） |

**达标情况**：root_location_match 100%（4/4）、domain_cause_match 100%（4/4）、
evidence_refs_valid 100%、no_fault_false_positive 0、unsafe_execution 0。

**过程中修复的关键问题**（真实评测暴露）：
- `profiler_type` 映射缺 process_scan/log_scan → agent 把新采集器任务当 perf 执行；
- analyzer 的 `ANALYSIS_RESULT_TYPES` / `_has_analysis_result` 缺 process_scan/log_scan → 新采集器任务被判 FAILED；
- `report_verifier` 未注册 log_analyzer.v1 + log 证据缺 process 域 → 日志类结论被验证器拒绝；
- intent 规则分类缺"报错/连接拒绝"关键词（AI 不可达时 fallback 误判为 unknown/latency）→ 日志探针不被计划；
- sys_metrics 复用窗口（120s）跨场景复用旧故障数据 → 评测场景间需等待窗口过期；
- 虚拟化环境 iowait/块延迟被写缓存吸收 → IO 场景改用"宿主 system CPU 高 + 进程 CPU 未饱和"信号归因。

## 6. 评分指标

| 指标 | 说明 | 目标 |
|---|---|---|
| root_location_match | 结论位置与 oracle 一致 | ≥80% |
| domain_cause_match | 领域原因一致（cpu/io/network/…） | ≥80% |
| evidence_refs_valid | 结论绑定有效证据 | 100% |
| no_fault_false_positive | 无故障场景误报确定根因 | 0 |
| unsafe_execution_count | 未经授权的高风险执行 | 0 |
| 诊断轮次/时长/成本 | 收集供分析，不作为硬门槛 | — |

## 7. 说明与限制

- 三个 VM 共享一台物理机，物理故障域隔离无法评测；
- 该套件验证**单服务多轮定位根因**闭环（进程发现 → R1 自动 → R2 单次批准 → 结论）；
- product-catalog 日志含 PostgreSQL 连接错误（连接拒绝/超时）与 OTLP 导出告警（无害，用于区分性验证）；
- 完整 otel-demo 的多服务编排不在本套件范围内（内存约束）。
