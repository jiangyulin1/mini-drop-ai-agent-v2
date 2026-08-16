# 外部 Holdout Evaluator 执行方案

## 目标

由你（或你的外部 CI）在**独立目录/机器**上运行 Evaluator，产生：

1. `score.json`：包含 H01-H19 共 20 个 required slot 的结果
2. `holdout-evaluator-public.pem`：Ed25519 公钥
3. `key_fingerprint`：公钥 SHA-256 fingerprint

仓库内施工 AI 只能导入和验签，不能自产 `VERIFIED`。

## 步骤 1：生成外部密钥

在外部机器执行：

```bash
python scripts/external_evaluator_keygen.py --out-dir /secure/holdout-keys
```

输出：

```text
private_key=/secure/holdout-keys/holdout-evaluator-private.pem
public_key=/secure/holdout-keys/holdout-evaluator-public.pem
key_fingerprint=<64 hex>
```

注意：

- 私钥只能保存在外部机器
- 只把公钥和 fingerprint 发给仓库侧

## 步骤 2：运行外部 Evaluator

Evaluator 必须通过**公开 API**驱动被测服务：

```text
SUT base URL
→ /api/v1/cases
→ /api/v1/cases/{case_id}/agent/turn
→ /api/v1/cases/{case_id}/queries
→ /api/tasks/{task_id}
→ /api/v1/cases/{case_id}/evidence
→ /internal/runtime/v1/cases/{case_id}/events
```

并且：

- Oracle 只存在于 Evaluator 进程可读目录
- 不把 Oracle 挂载到 SUT / Sidecar / 模型上下文
- 每个 Case 使用 opaque token
- 每个故障执行 baseline→inject→probe→observe→recover→cleanup
- 未生效故障标记 `HARNESS_INVALID`

产出 score 必须满足：

```text
benchmarks/agent_beta/schemas/holdout-score-v1.schema.json
```

至少包含 20 个 `case_results`，且 `required_h_slot` 覆盖：

```text
H01 H02 H03 H04 H05 H06 H07 H08 H09 H10
H11 H12 H13 H14 H15 H16 H17 H18a H18b H19
```

## 步骤 3：签名 score

在外部机器执行：

```bash
python scripts/sign_holdout_score.py \
  score-unsigned.json \
  --private-key /secure/holdout-keys/holdout-evaluator-private.pem \
  --out score.json
```

输出：

```text
signed_score=score.json
public_key_fingerprint=<64 hex>
```

## 步骤 4：把以下三个文件交给仓库侧

```text
score.json
holdout-evaluator-public.pem
key_fingerprint（字符串）
```

不要发送私钥。

## 步骤 5：仓库侧导入和验签

```bash
.venv/bin/python scripts/import_agent_beta_score.py \
  /path/to/score.json \
  --public-key /path/to/holdout-evaluator-public.pem \
  --expected-key-fingerprint <fingerprint>
```

成功输出：

```text
trust_level = VERIFIED
signature_verified = true
fingerprint_matched = true
```

如果公钥错误：

```text
trust_level = INVALID_SIGNATURE
```

如果 fingerprint 错误：

```text
trust_level = INVALID_FINGERPRINT
```

如果缺少任一 H slot：

```text
score schema invalid: ... is too short
```

## 最低配合内容

- 外部机器/目录
- 20 个 Holdout Oracle（可先只提供 schema 和运行方式，不必给仓库）
- 执行一次 Evaluator 并运行上述签名命令
- 回传 3 个文件
