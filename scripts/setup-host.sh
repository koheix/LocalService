#!/usr/bin/env bash
# ホスト初期セットアップ: Docker Engine + NVIDIA Container Toolkit
#
# 対象: Linux Mint 22.3 (Ubuntu 24.04 "noble" ベース)
# 注意: Docker / NVIDIA の公式インストールスクリプトは /etc/os-release の
#       ID=linuxmint を解釈できず失敗するため、本スクリプトでは
#       リポジトリのコードネームを "noble" / "ubuntu24.04" に固定する。
#       (実際の /etc/os-release の値は判定に使わない)
set -euo pipefail

UBUNTU_CODENAME="noble"
ARCH="$(dpkg --print-architecture)"

log() { echo "[setup-host] $*"; }
die() { echo "[setup-host] ERROR: $*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "コマンドが見つかりません: $1"
}

require_cmd curl
require_cmd gpg
require_cmd dpkg

if [ "$(id -u)" -eq 0 ]; then
  die "root では実行しないでください（内部で sudo を使います）"
fi
require_cmd sudo

# ---------------------------------------------------------------------------
# 前提: NVIDIA ドライバがホストにインストール済みであること
# ---------------------------------------------------------------------------
if ! command -v nvidia-smi >/dev/null 2>&1; then
  die "nvidia-smi が見つかりません。先にホストへ NVIDIA ドライバを導入してください。"
fi
log "ホスト側 nvidia-smi を確認しました:"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

# ---------------------------------------------------------------------------
# Docker Engine
# ---------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  log "Docker は既にインストール済みです。スキップします。"
else
  log "Docker Engine をインストールします (codename=${UBUNTU_CODENAME})"

  sudo apt-get update
  sudo apt-get install -y ca-certificates curl gnupg

  sudo install -m 0755 -d /etc/apt/keyrings
  if [ ! -f /etc/apt/keyrings/docker.asc ]; then
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
  fi

  echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

  sudo apt-get update
  sudo apt-get install -y \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

if ! getent group docker >/dev/null; then
  sudo groupadd docker
fi
if ! id -nG "${USER}" | grep -qw docker; then
  sudo usermod -aG docker "${USER}"
  log "ユーザー ${USER} を docker グループに追加しました。反映には再ログインが必要です。"
fi

sudo systemctl enable --now docker

# ---------------------------------------------------------------------------
# NVIDIA Container Toolkit
# ---------------------------------------------------------------------------
if dpkg -l nvidia-container-toolkit >/dev/null 2>&1; then
  log "NVIDIA Container Toolkit は既にインストール済みです。スキップします。"
else
  log "NVIDIA Container Toolkit をインストールします (distro非依存の stable/deb リポジトリ)"

  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

  # NVIDIA は distro 別パス (例: ubuntu24.04/libnvidia-container.list) を廃止し、
  # distro 非依存の stable/deb リポジトリに統一している。
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

  sudo apt-get update
  sudo apt-get install -y nvidia-container-toolkit
fi

sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

log "セットアップ完了。動作確認:"
log "  docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi"
