#!/usr/bin/env bash
# 既定モデルを ollama（LLM/GPU）と ollama-embed（Embedding/CPU）にダウンロードする。
# 冪等: 既に取得済みのモデルは Ollama 側でスキップされる。
set -euo pipefail

cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
[ -f .env ] && set -a && source .env && set +a

CHAT_MODEL="${DEFAULT_CHAT_MODEL:-qwen3:4b}"
EMBED_MODEL="${DEFAULT_EMBED_MODEL:-bge-m3}"

DC="docker compose"

log() { echo "[pull-models] $*"; }

log "ollama（GPU）と ollama-embed（CPU）を起動します"
${DC} up -d ollama ollama-embed

log "chat モデルを pull します: ${CHAT_MODEL} (ollama)"
${DC} exec -T ollama ollama pull "${CHAT_MODEL}"

log "embedding モデルを pull します: ${EMBED_MODEL} (ollama-embed)"
${DC} exec -T ollama-embed ollama pull "${EMBED_MODEL}"

log "完了。取得済みモデル一覧:"
${DC} exec -T ollama ollama list
${DC} exec -T ollama-embed ollama list
