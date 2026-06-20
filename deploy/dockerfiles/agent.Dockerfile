# Mini-Drop Agent Dockerfile
#
# 安全说明：Agent 需要运行 perf / bpftrace / py-spy 等内核级工具，
# 这些工具依赖 CAP_SYS_PTRACE 和 CAP_PERFMON capability。
# 因此 Agent 容器以 root 运行（Docker compose 中通过 cap_add 限制权限）。
# 生产环境应评估是否可使用 ambient capabilities 替代 root。
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
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
RUN pip install --no-cache-dir -e .

COPY proto/ ./proto/
RUN cd proto && bash compile.sh

CMD ["python", "-m", "agent.mini_drop_agent.main"]
