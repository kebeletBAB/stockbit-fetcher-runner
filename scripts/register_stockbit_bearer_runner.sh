#!/usr/bin/env bash
set -euo pipefail

# Registrasi self-hosted runner GitHub untuk workflow refresh bearer.
# Anda perlu mengisi URL repo dan registration token dari UI GitHub.

GITHUB_REPO_URL="${GITHUB_REPO_URL:-}"
RUNNER_TOKEN="${RUNNER_TOKEN:-}"
RUNNER_NAME="${RUNNER_NAME:-stockbit-bearer-$(hostname)}"
RUNNER_LABELS="${RUNNER_LABELS:-stockbit-bearer,linux}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner-stockbit-bearer}"
RUNNER_VERSION="${RUNNER_VERSION:-2.328.0}"

if [[ -z "${GITHUB_REPO_URL}" ]]; then
  echo "Set GITHUB_REPO_URL, contoh: https://github.com/OWNER/REPO" >&2
  exit 1
fi

if [[ -z "${RUNNER_TOKEN}" ]]; then
  echo "Set RUNNER_TOKEN dari Settings > Actions > Runners > New self-hosted runner" >&2
  exit 1
fi

mkdir -p "${RUNNER_DIR}"
cd "${RUNNER_DIR}"

if [[ ! -f "./config.sh" ]]; then
  ARCHIVE="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${ARCHIVE}"
  curl -L -o "${ARCHIVE}" "${URL}"
  tar xzf "${ARCHIVE}"
fi

./config.sh \
  --url "${GITHUB_REPO_URL}" \
  --token "${RUNNER_TOKEN}" \
  --name "${RUNNER_NAME}" \
  --labels "${RUNNER_LABELS}" \
  --work "_work" \
  --replace \
  --unattended

echo
echo "Runner terkonfigurasi di ${RUNNER_DIR}"
echo "Untuk menjalankan sebagai service:"
echo "  sudo ./svc.sh install"
echo "  sudo ./svc.sh start"

