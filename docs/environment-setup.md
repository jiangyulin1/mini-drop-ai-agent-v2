# 环境准备

本文只描述当前代码实际支持的环境。部署选择见
[`deployment-profiles.md`](deployment-profiles.md)，发布门禁见
[`release-baseline-runbook.md`](release-baseline-runbook.md)。

## 运行时基线

| 组件 | 支持或锁定版本 | 说明 |
|---|---|---|
| Python | `>=3.9,<3.14`，本机推荐 3.12 | 系统 Python 3.14 不满足项目约束，应使用 `uv` 创建的 `.venv` |
| Web Node.js | 推荐 Node 20 | Web 镜像使用 Node 20；本机开发以 `web/package-lock.json` 为准 |
| Pi Sidecar Node.js | `>=22.19.0` | Sidecar 与 Web 的 Node 要求不同 |
| Pi SDK | `@earendil-works/pi-coding-agent@0.84.2` | 由 `agent_runtime/pi-sidecar/package-lock.json` 精确锁定 |
| 数据库 | SQLite 或 PostgreSQL 16 + pgvector | SQLite 用于本机和轻量验收；PostgreSQL 用于完整部署与并发语义验证 |
| 对象存储 | 本地目录或 MinIO | 本机共享目录不需要 MinIO；跨容器或跨主机必须使用对象存储 |
| 模型 | DeepSeek API `deepseek-v4-flash` | 当前真实 Pi 链路不需要 Ollama、vLLM 或本地大模型 |

控制面、Web 和 Pi Sidecar 可以在 macOS 或 Linux 上开发。`perf`、bpftrace、宿主
PID namespace 等完整采集能力只在 Linux Worker 上成立。macOS 的网络拓扑发现使用
`lsof` 降级路径，不能据此宣称 Linux eBPF 或跨主机覆盖已经验证。

## 安装依赖

```bash
python -m pip install uv==0.12.5
uv sync --locked --extra dev
uv run --locked python scripts/compile_proto.py

npm --prefix web ci
npm --prefix agent_runtime/pi-sidecar ci --omit=dev
```

不要复用其他发布目录中的 `.venv` 或 `node_modules`。Python 依赖以 `uv.lock` 为准，
Web 和 Sidecar 分别以各自的 `package-lock.json` 为准。

默认词法检索不会安装 FastEmbed、ONNX Runtime 或下载模型。只有明确启用
`MINI_DROP_EMBEDDING_PROVIDER=local` 时才安装本地向量 extra：

```bash
uv sync --locked --extra dev --extra embedding-local
```

构建 Server 镜像时使用 `--build-arg MINI_DROP_BAKE_LOCAL_EMBEDDING=1` 会安装同一 extra
并烘焙本地向量模型；不传该参数的默认镜像保持词法检索。DeepSeek/Pi 对话不依赖这个
extra，也不需要本地向量模型。

## 本机轻量模式

本机默认使用 SQLite、本地 Artifact 和独立 Analyzer，不启动 PostgreSQL 或 MinIO：

```bash
cp deploy/env/local-native.env.example .env
chmod 600 .env

# 终端 1
uv run --locked python dev.py server

# 终端 2
uv run --locked python dev.py analyzer-worker

# 终端 3
uv run --locked python dev.py agent

# 终端 4
npm --prefix web run dev
```

浏览器入口是 `http://127.0.0.1:5173`。Vite 将 `/api` 转发到
`http://127.0.0.1:8191`。Analyzer 不是可省略的展示进程：即使 Artifact 不上传
MinIO，它仍负责领取持久化 AnalysisJob 并让普通 Task 到达终态。

本机配置只监听 loopback 且关闭 API/gRPC 认证。只要服务会被其他机器访问，就必须改用
启用认证和 TLS 的 Control/Worker 部署。

## 启用 Pi 与 DeepSeek

服务端 AI 与 Pi Agent 是两条不同配置面：

| 配置 | 作用 |
|---|---|
| `MINI_DROP_AI_*` | Server 侧 NLP、旧兼容 RCA 和摘要调用 |
| `MINI_DROP_AGENT_RUNTIME=pi` | 让 Case Turn 进入 Pi Sidecar |
| `MINI_DROP_PI_RUNTIME_URL` | Server 到 Sidecar 的内部协议地址 |
| `MINI_DROP_PI_INTERNAL_TOKEN` | Server 与 Sidecar 共用的内部认证 Token |
| `MINI_DROP_PI_MODEL_PROVIDER` / `MINI_DROP_PI_MODEL` | Sidecar 选择的 Provider 和模型 |
| `DEEPSEEK_API_KEY` 或 `MINI_DROP_AI_API_KEY` | Sidecar 实际发起 completion 时使用的 Provider 凭据 |

本机启用步骤：

1. 将 `.env` 中 `MINI_DROP_AGENT_RUNTIME` 改为 `pi`。
2. 生成随机 `MINI_DROP_PI_INTERNAL_TOKEN`，让 Server 与 Sidecar 读取同一个值。
3. 将 DeepSeek Key 放在仓库外、权限为 `0600` 的环境文件中，不写入命令行、Git、日志或报告。
4. 启动 Sidecar，再用真实 Case Turn 验证 completion。

Sidecar 的最小环境如下，值应由受保护文件注入：

```text
MINI_DROP_PI_SIDECAR_HOST=127.0.0.1
MINI_DROP_PI_SIDECAR_PORT=8899
MINI_DROP_PI_INTERNAL_BASE=http://127.0.0.1:8191
MINI_DROP_PI_INTERNAL_TOKEN=<same-random-token-as-server>
MINI_DROP_PI_MODEL_PROVIDER=deepseek
MINI_DROP_PI_MODEL=deepseek-v4-flash
MINI_DROP_PI_THINKING_LEVEL=low
DEEPSEEK_API_KEY=<provider-key>
PI_OFFLINE=1
```

```bash
npm --prefix agent_runtime/pi-sidecar start
```

`PI_OFFLINE=1` 只禁止 Pi SDK 启动时刷新远程模型目录，不会阻止真实 DeepSeek
completion。`MINI_DROP_PI_MODELS_PATH` 是可选模型目录，不是 DeepSeek API 调用的前置条件，
也不表示需要下载本地模型。

Sidecar `/internal/runtime/v1/health` 的 `model_ready` 在首次 Turn 前可能仍为 `false`，
因为模型运行时是惰性初始化的。健康接口 200 只证明 Sidecar 存活；Provider 可用性必须由
一次真实受控 Turn 或 `deploy/scripts/configure_ai_provider.py` 的连接验证确认。

Web 中用于访问 Control 的 Key 是 `MINI_DROP_API_KEY`，不是 DeepSeek Key。Provider Key
不应显示在 Web 设置页或浏览器请求中。

## 健康检查

```bash
curl -fsS http://127.0.0.1:8191/api/livez
curl -fsS 'http://127.0.0.1:8191/api/readyz?core_only=true'
curl -fsS http://127.0.0.1:8191/api/readyz
curl -fsS http://127.0.0.1:8191/api/v1/agent-runtime/config
curl -fsS http://127.0.0.1:8899/internal/runtime/v1/health
```

- `/api/livez`：仅表示进程存活。
- `/api/readyz?core_only=true`：忽略 Analyzer，用于避免容器启动依赖环。
- `/api/readyz`：要求当前配置中声明为必需的依赖全部可用。
- `/api/healthz`：诊断报告始终返回 HTTP 200，必须读取响应中的 `healthy`。

遇到端口冲突时先检查现有服务，不要直接覆盖。Server、gRPC 和 Sidecar 分别由
`SERVER_PORT`、`MINI_DROP_GRPC_PORT`、`MINI_DROP_PI_SIDECAR_PORT` 调整；对应客户端地址也
必须同步修改。
