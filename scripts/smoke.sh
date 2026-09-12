#!/usr/bin/env bash
# 疎通確認: GPU認識 → モデル応答 → 認証(ログイン) → 推論 → ログ記録
set -euo pipefail

cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
[ -f .env ] && set -a && source .env && set +a

DC="docker compose"
PROXY_URL="http://localhost:${PROXY_PORT:-8080}"
CHAT_MODEL="${DEFAULT_CHAT_MODEL:-qwen3:4b}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.local}"
ADMIN_PASSWORD="${ADMIN_INITIAL_PASSWORD:-change-me-on-first-login}"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

log() { echo "[smoke] $*"; }
fail() { echo "[smoke] FAIL: $*" >&2; exit 1; }

log "1/5 GPU認識を確認します"
nvidia-smi >/dev/null 2>&1 || fail "nvidia-smi が実行できません(ホスト側)"
log "  OK: $(nvidia-smi -L | head -1)"

log "2/5 モデル応答を確認します(${CHAT_MODEL})"
RESP=$(${DC} exec -T ollama ollama run "${CHAT_MODEL}" "1+1は？数字だけ答えて" 2>/dev/null || true)
[ -n "$(echo "${RESP}" | tr -d '[:space:]')" ] || fail "モデルから応答がありませんでした"
log "  OK: ${RESP}"

log "  seed データ(管理者ユーザー・既定モデル)を投入します(冪等)"
${DC} exec -T gateway python -m scripts.seed

log "3/5 認証(ログイン)を確認します(${ADMIN_EMAIL})"
COOKIEJAR="${TMPDIR}/cookies.txt"
LOGIN_CODE=$(curl -s -o "${TMPDIR}/login.json" -w "%{http_code}" -c "${COOKIEJAR}" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}" \
  "${PROXY_URL}/api/auth/login")
[ "${LOGIN_CODE}" = "200" ] || fail "ログインに失敗しました (HTTP ${LOGIN_CODE})"
log "  OK"

log "4/5 推論(OpenAI互換エンドポイント)を確認します"
INFER_CODE=$(curl -s -o "${TMPDIR}/infer.json" -w "%{http_code}" -b "${COOKIEJAR}" \
  -H "Content-Type: application/json" \
  -d '{"model":"chat-standard","messages":[{"role":"user","content":"1+1は？数字だけ答えて"}]}' \
  "${PROXY_URL}/api/v1/chat/completions")
[ "${INFER_CODE}" = "200" ] || fail "推論に失敗しました (HTTP ${INFER_CODE})"
grep -q '"content"' "${TMPDIR}/infer.json" || fail "推論の応答に content がありません"
log "  OK"

log "5/5 usage_logs への記録を確認します"
sleep 1
LOG_STATUS=$(${DC} exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc \
  "SELECT status FROM usage_logs ORDER BY id DESC LIMIT 1;" | tr -d '[:space:]')
[ "${LOG_STATUS}" = "ok" ] || fail "usage_logs に成功記録が見つかりません(status=${LOG_STATUS})"
log "  OK"

log "全ての疎通確認が成功しました"
