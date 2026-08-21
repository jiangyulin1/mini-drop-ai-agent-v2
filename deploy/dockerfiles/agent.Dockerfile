# Mini-Drop Agent Dockerfile
#
# 安全说明：Agent 需要运行 perf / bpftrace / py-spy 等内核级工具，
# 这些工具依赖 CAP_SYS_PTRACE 和 CAP_PERFMON capability。
# 因此 Agent 容器以 root 运行（Docker compose 中通过 cap_add 限制权限）。
# 生产环境应评估是否可使用 ambient capabilities 替代 root。
FROM python:3.11-slim

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true && \
    apt-get update && apt-get install -y --no-install-recommends \
    bash \
    bpftrace \
    curl \
    linux-perf \
    perl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY server/ ./server/
COPY agent/ ./agent/
COPY analyzer/ ./analyzer/
COPY mini_drop_contracts/ ./mini_drop_contracts/
COPY mini_drop_observability/ ./mini_drop_observability/
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip install --no-cache-dir -e . "grpcio-tools>=1.80,<1.81"

COPY proto/ ./proto/
COPY scripts/compile_proto.py ./scripts/compile_proto.py
RUN python scripts/compile_proto.py

CMD ["python", "-m", "agent.mini_drop_agent.main"]
