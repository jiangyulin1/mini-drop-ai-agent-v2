# Online Boutique VM 部署说明

> 状态：**Linux VM 静态验证完成，运行态验证被镜像源阻塞**。Compose 使用固定的官方 v0.10.3 镜像和完整依赖链；只有实跑后才可把案例置为 `verified_vm`。

## 2026-08-10 VM 验证记录

- worker1 上 `docker compose ... config --quiet` 通过；
- 所有故障、回滚、preflight 和负载脚本的 `bash -n` 通过；
- Docker daemon 正常，但 VM 和 Mac 访问 `us-central1-docker.pkg.dev` 均超时；
- worker 本地无 v0.10.3 镜像，因此未执行 `compose up`、故障注入或
  `verified_vm` 晋级。恢复镜像访问或导入固定的 amd64 离线镜像包后，从本页
  的健康检查继续。

## 环境假设

- 2 台 Linux Worker：`worker1`（192.168.10.11）、`worker2`（192.168.10.12），4 vCPU / 4GB；
- Docker + docker-compose（或 podman-compose）；
- mini-drop Agent 已部署在两台 worker 上（能对容器内进程采样）；
- 系统来源：Online Boutique = [GoogleCloudPlatform/microservices-demo](https://github.com/GoogleCloudPlatform/microservices-demo)。

## 两种部署选择

| 方案 | 说明 | 适用 |
|---|---|---|
| **A. 仓库 Compose** | `docker-compose.trimmed.yaml`，固定 v0.10.3，业务依赖完整 | **首轮 smoke test 推荐**；当前是单 Docker 网络 |
| **B. 官方 Kubernetes manifest** | 官方 v0.10.3 `release/kubernetes-manifests.yaml` | 多节点正式回归；需现成 Kubernetes |

> Compose 文件名为历史遗留；当前已补齐 frontend/checkout 所需依赖，不再是会制造依赖错误的五服务骨架。

## 逻辑拓扑

```text
frontend → productcatalog / recommendation / cart / checkout / shipping / ad
checkout → productcatalog / currency / cart / shipping / payment / email
cart → redis-cart
```

**VM 验证项**：镜像可拉取、frontend 健康、下单链路正常；多节点部署映射；显式测试网卡；Agent 对容器 PID 的采样权限。

```bash
docker compose -f deploy/docker-compose.trimmed.yaml config --quiet
docker compose -f deploy/docker-compose.trimmed.yaml up -d
curl --fail http://127.0.0.1:8080/_healthz
```

## 采集要点

- 每个 worker 上 mini-drop Agent 需常驻 `continuous_perf`（低频率），供瞬态场景（`ob-cpu-burst-001`）回补窗口；
- `run-fault.sh` 会自动执行 preflight、基线、注入、回滚和恢复观察；mini-drop 捕获需覆盖整个 runner 窗口。
