# 外部 Holdout 严格执行手册 —— 联想单机版（用户本人执行）

> 2026-08-16 按当前环境重写：**联想 Windows 本体担任外部机器**，用户本人执行，
> Oracle 由你本人或你的另一 AI（不经过仓库侧 AI）编写。
> 全文命令均为 Windows PowerShell 语法。

## 0. 信任模型与边界（先读，再动手）

`VERIFIED` 的全部价值 = 「系统通过了一场建造者没看过答案的考试」。两个信任根：

1. **私钥** `holdout-evaluator-private.pem`：只存在于联想本机，只回传公钥。
2. **Oracle**（20 个 Case 的故障设计与期望结论）：由你或你的另一 AI 编写，
   仓库侧 AI 不参与、不审阅。

仓库侧 AI 的可触及范围（已实测）：本对话、这台 Mac 的文件系统、三台虚拟机
（有免密 SSH）。**联想 Windows 本体对我不可见**（2026-08-16 实测 SSH
`Permission denied`），因此联想满足「外部机器」条件。

维持边界的 4 条纪律（不要破坏）：

1. 不把仓库侧 AI 的 SSH 公钥装到 Windows 的 `administrators_authorized_keys`
2. 不把 Windows 登录密码发进对话
3. Oracle 与私钥的**内容**不贴进对话、不保存到这台 Mac
4. 联想上已有的端口转发/防火墙规则与本次无关，无需改动

## 1. 环境准备（联想 Windows 上，普通 PowerShell 即可）

```powershell
# 1.1 检查 Python（3.10+）。若提示找不到命令，先安装：
python --version
# 没有的话：winget install Python.Python.3.12  （装完重开终端）

# 1.2 建立工作目录
mkdir D:\holdout

# 1.3 获取仓库机械层（三选一）
#   A) 从 Mac 局域网下载（我已在本机开好 HTTP 服务，1.3MB）：
Invoke-WebRequest -Uri "http://192.168.2.153:8000/mini-drop-external-mechanics.zip" -OutFile "D:\holdout\mini-drop-external-mechanics.zip"
#   B) U盘/网盘拷贝 external-package\mini-drop-external-mechanics.zip
#   C) git clone https://github.com/jiangyulin1/mini-drop.git（远端可能不是最新，
#      完成后核对 scripts\external_evaluator_keygen.py 存在）

# 1.4 解压并装依赖
Expand-Archive -Path "D:\holdout\mini-drop-external-mechanics.zip" -DestinationPath "D:\holdout\mini-drop-external"
cd D:\holdout\mini-drop-external
python -m venv .venv
.venv\Scripts\python -m pip install cryptography jsonschema requests
```

> 压缩包只含机械层（脚本/schema/契约/API 参考/模板/golden_scenarios），
> **不含 Oracle**。Oracle 需要你在下一步自行产出。

## 2. 生成外部 Evaluator 密钥（联想本机）

```powershell
cd D:\holdout\mini-drop-external
.venv\Scripts\python scripts\external_evaluator_keygen.py --out-dir D:\holdout\keys
```

输出形如：

```text
private_key=D:\holdout\keys\holdout-evaluator-private.pem
public_key=D:\holdout\keys\holdout-evaluator-public.pem
key_fingerprint=<64位hex>
```

- 私钥留在联想本机，**不要上传、不要贴对话**
- 把 `key_fingerprint` 抄进安全的地方（最终回传项之一）

## 3. 取 SUT 访问令牌（联想上执行，内容只给 Evaluator）

```powershell
ssh control@192.168.10.10 "grep MINI_DROP_API_KEY ~/mini-drop-active/deploy/env/control-native.env"
ssh control@192.168.10.10 "grep MINI_DROP_PI_INTERNAL_TOKEN ~/mini-drop-active/deploy/env/control-native.env"
```

把两个值存到联想本机文件（不要贴进对话、不要放 Mac）：

```powershell
"D:\holdout\sut-token.txt"   # MINI_DROP_API_KEY 的值
"D:\holdout\internal-token.txt"  # MINI_DROP_PI_INTERNAL_TOKEN 的值
```

## 4. 编写 Oracle + Evaluator 程序（你或你的另一 AI 完成）

这一步是严格方案里唯一必须由外部完成的部分。若交给另一 AI，把以下
「交接包」文件发给它即可（全部无 Oracle 内容）：

```text
docs\external_holdout_api_reference.md      ← SUT 接口参考
benchmarks\agent_beta\schemas\holdout-score-v1.schema.json
benchmarks\agent_beta\external\score-unsigned-template.json  ← 成绩骨架，直接填
benchmarks\agent_beta\contracts\public-contract-v1.json      ← H01-H19 槽位来源
golden_scenarios\                             ← 现成故障场景（外部作者可读）
deploy\scripts\ 与 server\app\                ← 可读的实现参考
```

给外部作者的硬性要求：

- 20 个 Case 覆盖 H01–H19 槽，每个 Case 完整执行
  `baseline → inject → independent probe → observe → recover → cleanup`
- 故障未真正生效 → 该 Case 标 `HARNESS_INVALID`，不得计入通过
- `opaque_case_token` 用随机 UUID，不与 Case 内容可关联
- SUT 地址用 **`https://192.168.10.10`**（联想直连 control，自签名证书，
  调用加 `-k`）；无需经过 8443 端口转发
- 故障注入途径：SSH 到 worker（`ssh worker1@192.168.10.11` /
  `ssh worker2@192.168.10.12`）用 docker/tc/压力工具，或适配
  `golden_scenarios\` 现有场景；注入方式属于 Oracle 设计，仓库侧 AI 不参与

## 5. 运行 Evaluator 生成未签名成绩

```powershell
cd D:\holdout\mini-drop-external
.venv\Scripts\python evaluator.py `
  --sut https://192.168.10.10 `
  --token-file D:\holdout\sut-token.txt `
  --cases D:\holdout\cases.json `
  --out D:\holdout\score-unsigned.json
```

（`evaluator.py` 与 `cases.json` 是第 4 步外部作者产出的程序与 Case 定义，
文件名按实际调整。）

schema 自检：

```powershell
.venv\Scripts\python -c "import json,jsonschema; s=json.load(open(r'benchmarks/agent_beta/schemas/holdout-score-v1.schema.json')); d=json.load(open(r'D:\holdout\score-unsigned.json')); jsonschema.validate(d,s); print('schema OK, cases =', len(d['case_results']))"
```

## 6. 签名

```powershell
.venv\Scripts\python scripts\sign_holdout_score.py `
  D:\holdout\score-unsigned.json `
  --private-key D:\holdout\keys\holdout-evaluator-private.pem `
  --out D:\holdout\score.json
```

输出 `public_key_fingerprint=<64位hex>`，应与第 2 步一致。

## 7. 回传（只回传这 3 个文件）

```text
1. D:\holdout\score.json
2. D:\holdout\keys\holdout-evaluator-public.pem
3. key_fingerprint（一行字符串）
```

- 传递方式任意（云盘 / U 盘 / 放到 Mac 上均可）——这 3 个文件本来就是要给
  仓库侧的，**只有私钥永远不离开联想**
- 放到 Mac 后告诉我路径（例如 `~/Documents/mini_drop/holdout-results/`）

## 8. 仓库侧导入验签（我执行）

收到 3 个文件后我执行：

```bash
.venv/bin/python scripts/import_agent_beta_score.py \
  /path/to/score.json \
  --public-key /path/to/holdout-evaluator-public.pem \
  --expected-key-fingerprint <fingerprint>
```

| trust_level | 含义 |
|---|---|
| `VERIFIED` | 签名 + 指纹全部通过 |
| `INVALID_SIGNATURE` | 公钥与签名不匹配 |
| `INVALID_FINGERPRINT` | 公钥指纹与外部固定值不符 |
| `missing required holdout slots` | H 槽缺失，拒绝导入 |

## 附录 A：score.json 各 digest 字段计算命令（联想 PowerShell 上执行）

机械层已带跨平台哈希工具 `scripts\tree_digest.py`（纯标准库），单文件与
目录树都能算。统一在 `D:\holdout\mini-drop-external` 目录下执行。

| 字段 | 命令 |
|---|---|
| `public_contract_digest` | `.venv\Scripts\python scripts\tree_digest.py benchmarks\agent_beta\contracts\public-contract-v1.json` |
| `source_lock_digest` | `.venv\Scripts\python scripts\tree_digest.py benchmarks\agent_beta\sources.lock.json` |
| `prompt_manifest_digest` | `.venv\Scripts\python scripts\tree_digest.py docs\ai_agent_feature_complete_demo_prompt.md` |
| `model_manifest_digest` | `.venv\Scripts\python scripts\tree_digest.py deploy\env\control.env.example` |
| `skill_manifest_digest` | `.venv\Scripts\python scripts\tree_digest.py agent` |
| `knowledge_manifest_digest` | `.venv\Scripts\python scripts\tree_digest.py knowledge` |
| `candidate_archive_digest` | `.venv\Scripts\python scripts\tree_digest.py D:\holdout\mini-drop-external-mechanics.zip` |
| `deployed_release_manifest_digest` | `ssh control@192.168.10.10 "find ~/mini-drop-active -maxdepth 3 -type f \| sort \| xargs sha256sum \| sha256sum"`（取输出第一段） |
| `evaluator_build_digest` | Evaluator 程序目录树：`.venv\Scripts\python scripts\tree_digest.py D:\holdout\evaluator-src`（目录名按实际） |
| `provider_ledger_root_hash` | Evaluator 程序内计算（对每 Case 的 provider 调用账本做根哈希） |
| `evidence_pack_root_hash` | Evaluator 程序内计算（对每 Case 的 `evidence_pack_subtree_hash` 做 Merkle 根） |
| `started_at` / `finished_at` | 运行起止时间，ISO8601 UTC（如 `2026-08-16T05:30:00Z`） |

> `public_contract_digest` 必须是 64 位小写 hex（schema 强校验）；其余字段
> schema 只要求非空，但**严格做法是全部填真实哈希**。

## 附录 B：验收清单

- [ ] 联想 Windows 上我没有登录能力（未装我的公钥、密码未进对话）
- [ ] 私钥在联想生成，从未离开联想
- [ ] 20 个 Case 真实执行六阶段；未生效故障标 `HARNESS_INVALID`
- [ ] H01–H19 槽全覆盖，`opaque_case_token` 不可关联
- [ ] jsonschema 自检通过；digest 字段为真实哈希
- [ ] 签名完成，fingerprint 与 keygen 输出一致
- [ ] 只回传 score.json + 公钥 + fingerprint
