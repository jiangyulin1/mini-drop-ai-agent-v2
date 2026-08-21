# Mini-Drop Server Dockerfile
FROM python:3.11-slim

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true && \
    apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    gosu \
    linux-perf \
    perl \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户运行服务
RUN useradd --create-home --shell /bin/bash mini-drop

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY server/ ./server/
COPY agent/ ./agent/
COPY analyzer/ ./analyzer/
COPY mini_drop_contracts/ ./mini_drop_contracts/
COPY mini_drop_observability/ ./mini_drop_observability/
COPY migrations/ ./migrations/
COPY knowledge/ ./knowledge/

RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip install --no-cache-dir -e ".[mcp]" "grpcio-tools>=1.80,<1.81"

# Knowledge search is lexical and does not install FastEmbed by default. Image
# builders can explicitly install the extra and bake the optional local model with
# --build-arg MINI_DROP_BAKE_LOCAL_EMBEDDING=1.
ARG MINI_DROP_BAKE_LOCAL_EMBEDDING=0
ENV MINI_DROP_EMBEDDING_PROVIDER=lexical \
    MINI_DROP_EMBEDDING_CACHE_DIR=/opt/mini-drop/embedding-cache
RUN mkdir -p /opt/mini-drop/embedding-cache && \
    if [ "$MINI_DROP_BAKE_LOCAL_EMBEDDING" = "1" ]; then \
      pip install --no-cache-dir -e ".[embedding-local]" && \
      curl -4 -fsSL https://storage.googleapis.com/qdrant-fastembed/fast-bge-small-zh-v1.5.tar.gz \
        | tar -xz -C /opt/mini-drop/embedding-cache && \
      python -c "from fastembed import TextEmbedding; next(iter(TextEmbedding(model_name='BAAI/bge-small-zh-v1.5', specific_model_path='/opt/mini-drop/embedding-cache/fast-bge-small-zh-v1.5').embed(['Mini-Drop'])))"; \
    fi && \
    chown -R mini-drop:mini-drop /opt/mini-drop

COPY proto/ ./proto/
COPY scripts/compile_proto.py ./scripts/compile_proto.py
RUN python scripts/compile_proto.py

COPY deploy/scripts/server-entrypoint.sh /usr/local/bin/server-entrypoint
RUN chmod 0755 /usr/local/bin/server-entrypoint

EXPOSE 8191 8192 50051

ENTRYPOINT ["server-entrypoint"]
CMD ["python", "-m", "server.app.main"]
