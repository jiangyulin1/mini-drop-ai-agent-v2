#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 RELEASE_NAME [OUTPUT_TAR_GZ]" >&2
  exit 2
fi

release_name="$1"
if [[ ! "${release_name}" =~ ^mini-drop-release-[A-Za-z0-9._-]+$ ]]; then
  echo "release name must match mini-drop-release-[A-Za-z0-9._-]+" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/../.." && pwd)"
output="${2:-/tmp/${release_name}.tar.gz}"

if ! git -C "${project_root}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "native release packaging must run from a Git worktree" >&2
  exit 2
fi
if [[ -n "$(git -C "${project_root}" status --porcelain --untracked-files=normal)" ]]; then
  echo "refusing to package a dirty worktree; commit or remove local files first" >&2
  exit 2
fi
if [[ -e "${output}" ]]; then
  echo "refusing to overwrite existing output: ${output}" >&2
  exit 2
fi
if [[ ! -f "${project_root}/web/dist/index.html" ]]; then
  echo "web/dist/index.html is missing; run the Web production build first" >&2
  exit 2
fi

stage="$(mktemp -d "${TMPDIR:-/tmp}/mini-drop-release.XXXXXX")"
cleanup() {
  rm -rf "${stage}"
}
trap cleanup EXIT

mkdir -p "${stage}/${release_name}"
# Only committed files may enter a public release. This avoids accidentally
# packaging local credentials, private topology notes, caches or test data.
git -C "${project_root}" archive --format=tar HEAD \
  | tar -xf - -C "${stage}/${release_name}"
mkdir -p "${stage}/${release_name}/web/dist"
rsync -a --exclude='._*' \
  "${project_root}/web/dist/" "${stage}/${release_name}/web/dist/"

# COPYFILE_DISABLE prevents macOS tar from synthesizing AppleDouble resource
# fork members (._*), which Linux otherwise extracts as invalid Python files.
COPYFILE_DISABLE=1 tar -C "${stage}" -czf "${output}" "${release_name}"

archive_members="$(tar -tzf "${output}")"
unsafe_members="$(
  printf '%s\n' "${archive_members}" \
    | grep -E '(^|/)\._|(^|/)\.env($|\.)|control-native\.env$|(^|/)id_rsa($|\.)|\.pem$' \
    | grep -Ev '(^|/)\.env\.example$' \
    || true
)"
if [[ -n "${unsafe_members}" ]]; then
  rm -f "${output}"
  echo "release archive failed credential/metadata member scan" >&2
  printf '%s\n' "${unsafe_members}" >&2
  exit 1
fi

echo "created ${output}"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "${output}"
else
  shasum -a 256 "${output}"
fi
