#!/usr/bin/env bash
set -euo pipefail

CONTROL_ADDRESS="${1:-}"
CERT_DIR="${2:-deploy/certs}"

if [[ -z "$CONTROL_ADDRESS" ]]; then
  echo "usage: $0 <control-IP-or-DNS> [cert-dir]" >&2
  exit 2
fi
if [[ -e "$CERT_DIR/server.key" || -e "$CERT_DIR/server.crt" || -e "$CERT_DIR/ca.crt" ]]; then
  echo "certificate files already exist in $CERT_DIR; remove them explicitly before regenerating" >&2
  exit 1
fi

command -v openssl >/dev/null 2>&1 || { echo "openssl is required" >&2; exit 1; }
mkdir -p "$CERT_DIR"
umask 077

# Git for Windows 会把 /CN=... 误转换成 Windows 路径；Linux 下该变量无副作用。
if [[ -n "${MSYSTEM:-}" ]]; then
  export MSYS2_ARG_CONV_EXCL="/CN="
fi

if [[ "$CONTROL_ADDRESS" =~ ^[0-9a-fA-F:.]+$ ]]; then
  CONTROL_SAN="IP:$CONTROL_ADDRESS"
else
  CONTROL_SAN="DNS:$CONTROL_ADDRESS"
fi

openssl genrsa -out "$CERT_DIR/ca.key" 4096
openssl req -x509 -new -nodes -key "$CERT_DIR/ca.key" -sha256 -days 3650 \
  -subj "/CN=Mini-Drop Development CA" -out "$CERT_DIR/ca.crt"
openssl genrsa -out "$CERT_DIR/server.key" 2048
openssl req -new -key "$CERT_DIR/server.key" -subj "/CN=$CONTROL_ADDRESS" \
  -out "$CERT_DIR/server.csr"

printf '%s\n' \
  'authorityKeyIdentifier=keyid,issuer' \
  'basicConstraints=CA:FALSE' \
  'keyUsage=digitalSignature,keyEncipherment' \
  'extendedKeyUsage=serverAuth' \
  "subjectAltName=$CONTROL_SAN,DNS:localhost,IP:127.0.0.1" > "$CERT_DIR/server.ext"

openssl x509 -req -in "$CERT_DIR/server.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
  -CAcreateserial -out "$CERT_DIR/server.crt" -days 825 -sha256 -extfile "$CERT_DIR/server.ext"
chmod 600 "$CERT_DIR"/*.key
chmod 644 "$CERT_DIR"/*.crt

echo "generated development CA and server certificate in $CERT_DIR"
echo "copy ca.crt to each Worker deploy/certs directory; never copy ca.key"
