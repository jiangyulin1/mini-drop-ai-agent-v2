"""InitAgent gRPC 服务：Agent 注册与配置拉取。"""

import os
from typing import Any

from server.app.generated import init_pb2, init_pb2_grpc


class InitAgentService(init_pb2_grpc.InitAgentServicer):
    """Agent 启动时调用的初始化服务。"""

    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def RegisterAgent(self, request: init_pb2.RegisterAgentRequest, context) -> init_pb2.RegisterAgentResponse:
        agent = self._repo.register_agent(
            agent_id=request.agent_id,
            hostname=request.hostname,
            ip_addr=request.ip_addr,
            version=request.version,
            os_info=request.os_info,
            capabilities=list(request.capabilities),
        )
        _ = agent  # register_agent 副作用已完成（包括 AGENT_ONLINE 审计日志）
        return init_pb2.RegisterAgentResponse(heartbeat_interval_sec=5)

    def FetchConfig(self, request: init_pb2.FetchConfigRequest, context) -> init_pb2.FetchConfigResponse:
        # 当前阶段 MinIO 凭证通过环境变量注入，暂不从 gRPC 下发。
        # 返回空 CosConfig 表示 Agent 应使用环境变量中的凭证。
        # NOTE: 生产环境必须启用 gRPC TLS (MINI_DROP_GRPC_SECURE=1)，否则凭证明文传输。
        return init_pb2.FetchConfigResponse(
            cos_config=init_pb2.common__pb2.CosConfig(
                endpoint=os.getenv("MINIO_ENDPOINT", "minio:9000"),
                access_key=os.getenv("MINIO_ACCESS_KEY", ""),
                secret_key=os.getenv("MINIO_SECRET_KEY", ""),
                bucket=os.getenv("MINIO_BUCKET", "mini-drop"),
                region=os.getenv("MINIO_REGION", ""),
            )
        )
