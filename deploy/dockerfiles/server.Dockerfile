# Mini-Drop Server Dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    gosu \
    perl \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户运行服务
RUN useradd --create-home --shell /bin/bash mini-drop

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY server/ ./server/
COPY agent/ ./agent/
COPY analyzer/ ./analyzer/
COPY mini_drop_observability/ ./mini_drop_observability/
COPY migrations/ ./migrations/

RUN pip install --no-cache-dir -e ".[mcp]" "grpcio-tools>=1.80,<1.81"

COPY proto/ ./proto/
RUN cd proto && bash compile.sh

COPY deploy/scripts/server-entrypoint.sh /usr/local/bin/server-entrypoint
RUN chmod 0755 /usr/local/bin/server-entrypoint

EXPOSE 8191 8192 50051

ENTRYPOINT ["server-entrypoint"]
CMD ["python", "-m", "server.app.main"]
