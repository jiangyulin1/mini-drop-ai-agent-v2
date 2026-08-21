# Low-I/O deployment overlay for the secure Web image.
# Build web/dist on a workstation, then copy only the static bundle into the
# already-tested Nginx/TLS image on the target host. This avoids running npm
# and a large source build on a small control VM.
ARG BASE_IMAGE=mini-drop-jyl-control-web:latest
FROM ${BASE_IMAGE}

ARG RELEASE_ID=static-local-dist
RUN find /usr/share/nginx/html -mindepth 1 -maxdepth 1 -exec rm -rf {} +
COPY web/dist/ /usr/share/nginx/html/
LABEL mini-drop.release="${RELEASE_ID}" \
      mini-drop.web-build="prebuilt-local-dist"
