#!/usr/bin/env bash
set -euo pipefail

# Bootstrap host Linux untuk self-hosted runner bearer Stockbit.
# Script ini hanya menyiapkan dependency OS, direktori kerja, dan
# browser profile root. Registrasi runner ke GitHub dilakukan terpisah.

RUNNER_USER="${RUNNER_USER:-$USER}"
RUNNER_HOME="${RUNNER_HOME:-$HOME}"
PROFILE_ROOT="${PROFILE_ROOT:-$RUNNER_HOME/.stockbit-bearer/profiles}"
REPO_WORKDIR="${REPO_WORKDIR:-$RUNNER_HOME/stockbit-runner}"

echo "== Bootstrap Stockbit Bearer Runner =="
echo "RUNNER_USER   : ${RUNNER_USER}"
echo "RUNNER_HOME   : ${RUNNER_HOME}"
echo "PROFILE_ROOT  : ${PROFILE_ROOT}"
echo "REPO_WORKDIR  : ${REPO_WORKDIR}"

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y \
    git \
    curl \
    unzip \
    jq \
    ca-certificates \
    python3 \
    python3-pip \
    python3-venv
else
  echo "Package manager apt-get tidak ditemukan. Install dependency OS manual." >&2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "Install GitHub CLI..."
  if command -v apt-get >/dev/null 2>&1; then
    type -p curl >/dev/null || sudo apt-get install curl -y
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | \
      sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
    sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | \
      sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
    sudo apt-get update
    sudo apt-get install gh -y
  else
    echo "gh belum ada dan installer otomatis hanya support apt-get. Install manual." >&2
  fi
fi

mkdir -p "${PROFILE_ROOT}/primary"
mkdir -p "${PROFILE_ROOT}/secondary"
mkdir -p "${REPO_WORKDIR}"

echo
echo "Bootstrap selesai."
echo "Langkah berikutnya:"
echo "1. clone/pull repo ke ${REPO_WORKDIR}"
echo "2. jalankan scripts/register_stockbit_bearer_runner.sh"
echo "3. isi GitHub Actions secrets di repo"

