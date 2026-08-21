# Low-I/O deployment overlay for a validated Mini-Drop Server image.
# Use this when a route-only hotfix has passed local regression tests and a
# full dependency/model rebuild would put unnecessary pressure on a small VM.
ARG BASE_IMAGE=mini-drop-jyl-control-server:latest
FROM ${BASE_IMAGE}

ARG RELEASE_ID=plans-control-local
COPY server/app/routes/plans_control.py /app/server/app/routes/plans_control.py
LABEL mini-drop.release="${RELEASE_ID}" \
      mini-drop.server-build="plans-control-overlay"
