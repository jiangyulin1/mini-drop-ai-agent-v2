# Mini-Drop 测试集目录

测试集为诊断 AI Agent 的评测提供**真实 + 合成**案例，设计详见
[`docs/ai_testset_design.md`](../docs/ai_testset_design.md)。

## 目录

```text
testsets/
├── manifest.schema.json       manifest 字段规范（所有 case 的 JSON Schema）
├── real/online-boutique/      Online Boutique 真实部署 + 故障注入（主）
│   ├── cases/                 8 个案例 manifest（已通过 schema 校验）
│   ├── scripts/               只读 preflight + HTTP workload
│   ├── faults/                cpu/memory/delay/connection/code_hotspot 适配器
│   │   └── run-fault.sh       preflight→基线→注入→回滚→恢复→校验 GT
│   ├── deploy/                裁剪部署配置 + 说明（待 VM 验证）
│   └── ground_truth/          注入时自记录（运行时生成）
├── synthetic/                 离线合成（边界/负例/安全/瞬态）——待建
└── designer-scoring/          设计者评分台（深标签，开发用）——待建
```

## 案例清单（8 个，已通过 manifest schema 校验）

| case_id | 故障 | 期望根因 | 对应场景 |
|---|---|---|---|
| `ob-cpu-hog-001` | CPU 占核 (productcatalog) | self/cpu | S1/S3 |
| `ob-memory-leak-001` | 内存泄漏 fixture | self/memory | S2 |
| `ob-network-delay-001` | 网络延迟 (checkout 路径) | downstream/network | S4 |
| `ob-conn-exhaust-001` | 连接/fd 压力 fixture | self/network | S4/S6 |
| `ob-downstream-cpu-001` | 下游 CPU (currencyservice) | downstream/cpu | S4 |
| `ob-code-hotspot-001` | 代码热点 fixture | self/cpu | S3/S6 |
| `ob-cpu-burst-001` | 60s 瞬态突发 | self/cpu | S1 |
| `ob-same-host-noise-001` | 同宿主噪声邻居 | same_host/cpu | S5 |

## 状态

- **静态阶段（已完成）**：manifest schema + 8 案例 + 生命周期脚本 + 采集器注册校验 + Oracle 隔离；
- **VM 阶段（待有 Linux 环境）**：部署 → 注入 → 捕获 → 诊断 → 对比 GT，逐案例置 `verified_vm`；
- 仍需在 VM 确认跨节点执行、容器 PID 解析和隔离网卡；业务依赖已在固定 v0.10.3 Compose 中补齐。

## 使用（VM 阶段）

```bash
# 1. 部署（首轮建议全量官方 compose，见 deploy/README）
docker-compose -f deploy/docker-compose.trimmed.yaml up -d   # 或官方全量

# 2. 只读预检（所有案例需 TARGET_URL；网络案例还需 FAULT_IFACE）
export TARGET_URL=http://worker1:8080/
./faults/run-fault.sh ob-cpu-hog-001 --preflight

# 3. 正式运行：脚本自行建立基线、注入、回滚和恢复窗口
./faults/run-fault.sh ob-cpu-hog-001
# → 校验 ground_truth/ob-cpu-hog-001.json

# 4. 将每次结果保存为 runs/<case_id>/run-<run_id>.json 后评分
python ../../../scripts/score_testset_runs.py \
  --manifest cases/ob-cpu-hog-001.json \
  --ground-truth ground_truth/ob-cpu-hog-001.json \
  --runs-dir runs/ob-cpu-hog-001 \
  --promote
# 只有全部门禁通过才会生成 score.json 并置 verified_vm
```

## manifest 规范

每个 case 一个 `manifest.json`，字段定义见 `manifest.schema.json`。关键约定：
`user_query` 是可给 AI 的公开输入；`oracle_visibility` 固定为 `private`；
`fault.type` ∈ cpu/memory/delay/loss/disk/connection/code_hotspot/code_leak；
`expected.root_location` ∈ self/same_host/downstream/shared_resource/unknown；
`expected.domain_cause` ∈ cpu/io/memory/network/database/runtime/unknown；
`ground_truth_source` ∈ rcaeval/self-injected/upstream-fix/synthetic；
`status` ∈ designed/verified_offline/verified_vm/regression。
