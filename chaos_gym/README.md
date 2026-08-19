# Mini-Drop Autonomous Chaos & Benchmark Gym

公共混沌演练靶场：为 Mini-Drop 诊断引擎提供动态故障注入与自动评分环境。

## 目录结构

```text
chaos_gym/
  manifests/   Ground Truth 故障清单
  results/     离线评测样例结果（也可由 run_chaos_gym.py 生成）
  README.md
```

## 快速开始（离线评测）

```bash
python scripts/run_chaos_gym.py \
  --mode offline \
  --manifest chaos_gym/manifests/cpu-hotspot.json \
  --result chaos_gym/results/cpu-hotspot.json \
  --output-dir reports/chaos-gym
```

## 快速开始（真实三节点）

```bash
python scripts/run_chaos_gym.py \
  --mode live \
  --control-url http://47.112.10.137 \
  --worker-ssh "ssh root@120.24.187.205" \
  --manifest chaos_gym/manifests/cpu-hotspot.json
```

## 故障类型

当前支持：

- cpu-hotspot
- cpu-contention
- io-write
- io-hang
- memory-leak
- lock-contend
- network-jitter
- fd-leak
- thread-spawn
- python-cpu
- python-multi

## 评分维度

- Strict RCA Hit
- Forbidden Ratio
- Evidence Citation Validity
- Required Probe Recall
- Token / 耗时 Pareto 评分
