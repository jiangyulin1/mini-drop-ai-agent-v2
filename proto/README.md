# Mini-Drop Protobuf 协议

## 设计原则

参考 DeepFlow 的 `message/` 目录组织方式，协议文件作为 Server 和 Agent 之间的
**唯一契约来源**（single source of truth）。双方各自编译同一份 `.proto` 文件生成 stub，
编译期即可发现字段不匹配。

## 文件说明

| 文件 | 服务 | 调用方 → 被调用方 | 职责 |
|------|------|--------------------|------|
| `common.proto` | — | 被 import | PidStats / CosConfig / File 通用结构 |
| `init.proto` | InitAgent | Agent → Server | 注册 + 获取对象存储凭证 |
| `healthcheck.proto` | HealthCheck | Agent → Server | 1 Hz 心跳 + 拉取待执行任务 |
| `hotmethod.proto` | Hotmethod | Agent → Server | 采集结果上报 |
| `control.proto` | Control | Web(Server) → Server(gRPC) | 创建任务 / 查询 Agent 状态 |

## 编译

```bash
pip install grpcio grpcio-tools protobuf
python scripts/compile_proto.py
```

编译产物输出到 `server/app/generated/`，包含 `*_pb2.py` 和 `*_pb2_grpc.py`。
该 Python 入口是 Linux、macOS 和 Windows 的唯一编译方式，`make proto` 与
`python dev.py proto` 均调用它。

## 兼容性约定

- 字段编号不可重用。删除字段后将其编号标记为 `reserved`。
- 新增字段使用下一个可用编号。
- 不修改已有字段的类型（protobuf 没有 alter 语义）。
- RPC 方法签名一旦发布不可变更，新功能走新 RPC。
