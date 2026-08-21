#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 CONTROL_IP OUTPUT_DIR" >&2
  exit 2
fi

control_ip="$1"
output_dir="$2"
if [[ ! "$control_ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
  echo "CONTROL_IP must be an IPv4 address" >&2
  exit 2
fi
if [[ -e "$output_dir" ]]; then
  echo "refusing to overwrite existing security material: $output_dir" >&2
  exit 1
fi

command -v openssl >/dev/null 2>&1 || {
  echo "openssl is required" >&2
  exit 1
}

umask 077
mkdir -p "$output_dir"
chmod 700 "$output_dir"

openssl genrsa -out "$output_dir/ca.key" 4096 >/dev/null 2>&1
openssl req -x509 -new -key "$output_dir/ca.key" -sha256 -days 3650 \
  -subj "/CN=Mini-Drop JYL Private CA" -out "$output_dir/ca.crt" >/dev/null 2>&1
openssl genrsa -out "$output_dir/server.key" 3072 >/dev/null 2>&1
openssl req -new -key "$output_dir/server.key" \
  -subj "/CN=$control_ip" \
  -addext "subjectAltName=IP:$control_ip,IP:127.0.0.1,DNS:localhost,DNS:mini-drop-jyl-control" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" \
  -out "$output_dir/server.csr" >/dev/null 2>&1
openssl x509 -req -in "$output_dir/server.csr" \
  -CA "$output_dir/ca.crt" -CAkey "$output_dir/ca.key" -CAcreateserial \
  -days 825 -sha256 -copy_extensions copy -out "$output_dir/server.crt" >/dev/null 2>&1

api_key="$(openssl rand -hex 32)"
grpc_token="$(openssl rand -hex 32)"
pi_token="$(openssl rand -hex 32)"
minio_access_key="$(openssl rand -hex 16)"
minio_secret_key="$(openssl rand -hex 32)"

{
  printf 'MINI_DROP_API_KEY=%s\n' "$api_key"
  printf 'MINI_DROP_GRPC_TOKEN=%s\n' "$grpc_token"
  printf 'MINI_DROP_PI_INTERNAL_TOKEN=%s\n' "$pi_token"
  printf 'MINIO_ACCESS_KEY=%s\n' "$minio_access_key"
  printf 'MINIO_SECRET_KEY=%s\n' "$minio_secret_key"
} > "$output_dir/runtime-secrets.env"
printf '%s\n' "$api_key" > "$output_dir/api-key.txt"

chmod 600 "$output_dir"/*.key "$output_dir"/*.csr \
  "$output_dir"/*.srl "$output_dir/runtime-secrets.env" "$output_dir/api-key.txt"
chmod 644 "$output_dir"/*.crt

openssl verify -CAfile "$output_dir/ca.crt" "$output_dir/server.crt" >/dev/null
openssl x509 -in "$output_dir/server.crt" -noout -checkip "$control_ip" >/dev/null

echo "security material generated in $output_dir"
echo "keep ca.key and runtime-secrets.env off the servers; distribute ca.crt only to Workers"
