# 部署模式

Mini-Drop 的数据库、Artifact 和 Agent 权限随部署模式变化。不要混用不同模式的 env
文件，也不要把某次实验机器的 IP、密码或目录写回通用文档。

## 模式总览

| 模式 | 入口 | 数据与 Artifact | Pi | 适用范围 |
|---|---|---|---|---|
| Native 轻量 | `deploy/env/local-native.env.example` | SQLite + 本地共享目录 | 可选，DeepSeek API | macOS/Linux 开发、轻量真实链路验收 |
| Local Compose | `docker-compose.yml` + `docker-compose.local.yml` | SQLite + Compose 共享卷 | 默认无 Sidecar | 本机容器化 UI/Task 验证 |
| Linux 全栈 | `docker-compose.yml` | PostgreSQL + MinIO | 默认无 Sidecar | 单台 Linux 完整 Collector 演示 |
| Control/Worker | `docker-compose.control.yml` + `docker-compose.worker.yml` | PostgreSQL + MinIO | 可外接 Sidecar | 通用多机部署 |
| Pi Control Demo | `deploy/compose/jyl-secure.control.yml` | PostgreSQL + MinIO | 内置 Sidecar + DeepSeek | 当前受控 AI 演示拓扑 |
| 低带宽评测 | 上一模式叠加 `eval.lowbandwidth.*.yml` | 复用 Evidence ID，关闭大 Artifact 上传 | 内置 Sidecar | 单轮或显式多轮 PR/Case 评测 |

## Native 轻量模式

完整命令见 [`environment-setup.md`](environment-setup.md)。这一模式不需要 Docker、
PostgreSQL、MinIO 或本地模型，但仍需要 Server、Analyzer、Agent、Web 四个进程。macOS
可验证控制面、Pi/DeepSeek 和受控网络发现，Linux 专属采集器不在此模式的能力声明内。

## Local Compose

先构建 Web，再启动包含 Analyzer 的服务集合：

```bash
cp .env.example .env
npm --prefix web ci
npm --prefix web run build
docker compose -f docker-compose.yml -f docker-compose.local.yml \
  up -d --build server analyzer agent web
```

此 override 使用 Compose 的 `!reset` 和 `!override` 标签。部署前必须用当前 Compose
版本执行 `config`；仅写“Compose v2”不足以保证旧版本支持这些标签。

```bash
docker compose --env-file .env \
  -f docker-compose.yml -f docker-compose.local.yml config --quiet
```

“Local”表示不启动 PostgreSQL/MinIO、Web 使用预构建产物，并不保证机器完全离线；如果
本机没有 Python 或 Nginx 基础镜像，构建仍会拉取镜像。

## Linux 全栈

```bash
cp .env.example .env
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d --build
```

该模式启动 PostgreSQL、MinIO、Server、Analyzer、特权 Agent 和 Web。它必须运行在
Linux 主机上，Docker Desktop 中的 Agent 看不到 macOS/Windows 宿主机的真实 PID 和
内核探针。

根 Compose 当前没有 Pi Sidecar。即使 `.env` 写入 DeepSeek Key，也只会配置 Server
侧 AI 功能，不会让 Evidence Case Turn 自动进入 Pi。需要 Pi 时使用 Pi Control Demo，
或按环境文档单独部署 Sidecar并设置 `MINI_DROP_AGENT_RUNTIME=pi`。

完整 Compose 中 Agent 和 Analyzer 不共享本地文件系统，因此
`AGENT_UPLOAD_ARTIFACTS` 必须为 `1`。MinIO 是跨进程 Artifact 的权威存储。

## 通用 Control/Worker

Control 运行 Server、PostgreSQL、MinIO、Analyzer 和 Web；每台 Linux Worker 运行一个
特权 Agent：

```bash
cp deploy/env/control.env.example deploy/env/control.env
bash deploy/scripts/generate-dev-certs.sh <control-ip-or-dns>
docker compose --env-file deploy/env/control.env \
  -f docker-compose.control.yml config --quiet
docker compose --env-file deploy/env/control.env \
  -f docker-compose.control.yml up -d --build
```

```bash
cp deploy/env/worker.env.example deploy/env/worker.env
docker compose --env-file deploy/env/worker.env \
  -f docker-compose.worker.yml config --quiet
docker compose --env-file deploy/env/worker.env \
  -f docker-compose.worker.yml up -d --build
```

必须分别确认：Worker 能访问 gRPC 地址、Worker 能访问 MinIO 地址、证书 SAN 匹配、
API/gRPC Token 已随机生成、防火墙只开放必要端口。`MINIO_PUBLIC_ENDPOINT` 是 Worker
可达地址；浏览器下载经 Server 转发，不要求浏览器直连 MinIO。

## Pi Control Demo

`deploy/compose/jyl-secure.control.yml` 将 Pi Sidecar 与 Control 放在同一 Compose 网络。
该文件默认开启 HTTP API 认证，并要求外部提供 `MINI_DROP_API_KEY`、gRPC Token、Pi
内部 Token、数据库/MinIO Secret 和 DeepSeek Key。不得把这些值写进 Compose 文件。

```bash
docker compose --env-file <protected-env-file> \
  -f deploy/compose/jyl-secure.control.yml config --quiet
docker compose --env-file <protected-env-file> \
  -f deploy/compose/jyl-secure.control.yml up -d --build
```

Sidecar 端口只在 Compose 网络内暴露；Provider Key 由 Sidecar 环境读取。公网演示前还
需核对证书、绑定地址和反向代理，不应因文件名含 `secure` 就跳过这些检查。

## 低带宽评测

配置模板是 `deploy/env/eval.lowbandwidth.env.example`。它采用以下约束：

- `MINI_DROP_EVAL_ROUNDS=1`，只有明确要求稳定性时才用三轮。
- `AGENT_UPLOAD_ARTIFACTS=0`、`MINI_DROP_ANALYZER_UPLOAD=0`。
- 关闭 MCP、Tracing、集群 fan-out 和额外 Source 访问。
- `MINI_DROP_PI_CONTEXT_MAX_CHARS=8000`，思考级别为 `low`。
- 首次导入 bounded Evidence pack，后续只发送 Evidence ID 和投影引用。
- Runner 使用 5 至 10 秒轮询；完成后再抓完整事件，避免反复下载 payload。

```bash
docker compose --env-file <protected-lowbandwidth-env> \
  -f deploy/compose/jyl-secure.control.yml \
  -f deploy/compose/eval.lowbandwidth.control.yml config --quiet
```

PR pack 本身已经包含受控 Evidence 时不需要 Worker 或 Analyzer。只有评分项明确要求新
采集证据时才启动 Worker，并单独记录实际传输量。

## 端口与就绪语义

| 服务 | 默认端口 | 暴露原则 |
|---|---:|---|
| Web | 80/443 或 Vite 5173 | 唯一面向用户的入口 |
| Server HTTP | 8191 | 容器网络或 loopback；公网经 Web 代理 |
| Server gRPC | 50051 | 仅 Worker 可达，启用 Token + TLS |
| Pi Sidecar | 8899 | 仅 Server 可达，不暴露原始 Pi RPC |
| PostgreSQL | 5432 | 仅 Control 内部网络 |
| MinIO | 9000 | 仅 Server、Analyzer 和需要上传的 Worker 可达 |

发布激活以 `/api/readyz` 为准，不以进程存在、`/api/livez` 或 Sidecar 健康接口单独判定
完整链路可用。
