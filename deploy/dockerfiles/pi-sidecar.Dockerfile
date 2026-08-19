FROM node:22.19.0-alpine

WORKDIR /app

COPY agent_runtime/pi-sidecar/package.json agent_runtime/pi-sidecar/package-lock.json ./
RUN npm config set registry https://registry.npmmirror.com && npm ci --omit=dev

COPY agent_runtime/pi-sidecar/ ./

ENV MINI_DROP_PI_SIDECAR_PORT=8899
EXPOSE 8899

CMD ["node", "src/server.mjs"]
