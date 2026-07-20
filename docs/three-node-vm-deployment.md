# Control + 两个 Linux Worker 三机部署与联调

本文面向当前的虚拟机调试阶段，不包含 SSH 自动安装或远程命令编排。

## 1. 拓扑和端口

| 来源 | Control 端口 | 用途 |
|---|---:|---|
| Windows | 80 / 443 | 页面与 REST API；80 自动跳转 443 |
| Worker 1、2 | 50051 | Agent gRPC，Token + TLS |
| Worker 1、2 | 9000 | MinIO 产物上传 |

PostgreSQL `5432`、MinIO Console `9001` 和 Server HTTP `8191` 仅在 Docker 网络中
`expose`，没有宿主机端口映射。浏览器的产物下载经
`/api/tasks/{task_id}/artifacts/{artifact_type}/download` 由 Server 流式转发，因此
Windows 不需要访问 MinIO 9000。

Compose 只能控制监听地址，不能按来源 IP 做防火墙过滤。实际 VM 上仍应将 50051、9000
限制为两个 Worker IP；SSH 规则待后续联调时配置。

## 2. Control VM

准备配置并替换所有 `CHANGE_ME`：

```bash
cp deploy/env/control.env.example deploy/env/control.env
vi deploy/env/control.env
```

生成仅用于虚拟机联调的私有 CA 和服务证书。参数必须是 Worker 连接 Control 时使用的
IP 或 DNS 名，否则 gRPC 主机名校验会失败：

```bash
bash deploy/scripts/generate-dev-certs.sh 10.0.0.10
openssl x509 -in deploy/certs/server.crt -noout -subject -issuer -ext subjectAltName
```

校验并启动：

```bash
docker compose --env-file deploy/env/control.env -f docker-compose.control.yml config --quiet
docker compose --env-file deploy/env/control.env -f docker-compose.control.yml up -d --build
docker compose --env-file deploy/env/control.env -f docker-compose.control.yml ps
docker compose --env-file deploy/env/control.env -f docker-compose.control.yml logs -f server web
```

只把 `deploy/certs/ca.crt` 复制给 Worker 和需要信任开发证书的 Windows。`ca.key` 只留在
Control 且不可分发。

## 3. Worker 1 / Worker 2（Docker）

每台 Worker 都需要仓库代码、各自的环境文件，以及来自 Control 的 `ca.crt`：

```bash
cp deploy/env/worker.env.example deploy/env/worker.env
mkdir -p deploy/certs
# 将 Control 的 ca.crt 放到 deploy/certs/ca.crt
vi deploy/env/worker.env
docker compose --env-file deploy/env/worker.env -f docker-compose.worker.yml config --quiet
docker compose --env-file deploy/env/worker.env -f docker-compose.worker.yml up -d --build
docker compose --env-file deploy/env/worker.env -f docker-compose.worker.yml logs -f agent
```

Worker 1 示例：

```dotenv
AGENT_ID=linux-worker-1
AGENT_IP_ADDR=10.0.0.21
AGENT_GRPC_ADDR=10.0.0.10:50051
MINIO_ENDPOINT=http://10.0.0.10:9000
```

Worker 2 将 ID 和 IP 改为 `linux-worker-2`、`10.0.0.22`。两台 Worker 的
`MINI_DROP_GRPC_TOKEN`、MinIO Access Key 和 Secret 必须与 Control 一致。

容器模式适合快速模拟。`perf` 工具版本可能需要与 Worker 宿主机内核匹配；遇到该问题时
使用 systemd 裸机模式更稳定。

## 4. Worker systemd 模式

安装脚本创建项目虚拟环境、编译 gRPC stub、安装 systemd unit，但不会自动启动服务：

```bash
sudo bash deploy/scripts/install-worker.sh "$PWD"
sudo vi /etc/mini-drop/worker.env
sudo systemctl enable --now mini-drop-agent
sudo systemctl status mini-drop-agent
sudo journalctl -u mini-drop-agent -f
```

证书默认读取仓库中的 `deploy/certs/ca.crt`。Agent 以 root 运行是因为 perf、bpftrace 和
跨进程采样需要内核权限；不要在 Worker 上运行 Web、Server 或数据库容器。

## 5. 功能验收

1. Windows 导入 `ca.crt` 到受信任根证书颁发机构，访问 `https://<control-IP>`。
2. 在设置页填写 `MINI_DROP_API_KEY`，确认 Agent 列表出现两个 `ONLINE` Worker。
3. 在“AI 集群诊断”中添加两个或更多实例，为每个实例选择 Worker、填写 PID。
4. 添加 `CALLS` 等依赖边，选择入口服务并创建诊断。
5. 检查会话的探针目标覆盖对应 Worker；R2 探针必须单次审批。
6. 在任务结果页下载产物，确认浏览器请求目标仍为 Control 的 443，而不是 MinIO 9000。

Control 上可做基础检查：

```bash
curl --cacert deploy/certs/ca.crt https://10.0.0.10/api/healthz
docker compose --env-file deploy/env/control.env -f docker-compose.control.yml logs server | grep -E 'agent|grpc|task'
```

若 Agent 报 `UNAUTHENTICATED`，核对共享 Token；若报证书主机名不匹配，重新按实际连接地址
生成证书，或将 `AGENT_GRPC_TLS_SERVER_NAME` 设置为证书 SAN 中的 DNS 名。不要通过关闭 TLS
规避证书问题。

## 6. DeepSeek V4 Flash（隐藏输入 Key）

API Key 不要写入命令行、聊天或仓库。在 Control VM 上运行以下命令，程序会通过
TTY 隐藏输入读取 Key，先调用 DeepSeek 官方 `/v1/models` 验证，再以 `0600` 权限
原子更新 Server 的环境文件：

```bash
cd /home/control/mini-drop
.venv/bin/python deploy/scripts/configure_ai_provider.py \
  --target-env /home/control/mini-drop/deploy/env/control-native.env \
  --prompt-key
sudo systemctl restart mini-drop-server
```

默认配置为 `https://api.deepseek.com` 和 `deepseek-v4-flash`。程序只输出
`key=present`，不会打印 Key。
