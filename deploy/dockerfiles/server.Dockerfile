# Mini-Drop Server Dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    perl \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户运行服务
RUN useradd --create-home --shell /bin/bash mini-drop

WORKDIR /app

COPY pyproject.toml README.md ./
COPY server/ ./server/
COPY agent/ ./agent/
COPY analyzer/ ./analyzer/

RUN pip install --no-cache-dir -e ".[chatops]" "grpcio-tools>=1.80,<1.81"

COPY proto/ ./proto/
RUN cd proto && bash compile.sh

EXPOSE 8191 50051

# 非 root 运行
USER mini-drop

CMD ["python", "-m", "server.app.main"]
