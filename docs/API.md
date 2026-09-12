# API 仕様

ベースパス: `/api`

認証は以下のいずれか。両方無い場合は `401`。
- Cookie: `session=<token>`
- Header: `Authorization: Bearer sk-...`

---

## 認証

### `POST /api/auth/login`
```json
{ "email": "user@example.com", "password": "..." }
```
→ `200` + `Set-Cookie: session=...` / `401` 認証失敗

### `POST /api/auth/logout`
セッションを失効させる。→ `204`

### `GET /api/auth/me`
```json
{ "id": 1, "email": "...", "role": "user", "created_at": "..." }
```

### `POST /api/auth/api-keys`
```json
{ "name": "Dify連携用" }
```
→ `201`
```json
{ "id": 3, "name": "Dify連携用", "key": "sk-abc12345...", "prefix": "sk-abc12" }
```
`key` はこの応答でのみ返る。以降取得不可。

### `GET /api/auth/api-keys` / `DELETE /api/auth/api-keys/{id}`

---

## 推論（OpenAI 互換）

**このセクションのパスとスキーマは OpenAI の仕様に厳密に合わせること。**
外部ツール（Dify、LangChain、OpenAI SDK）が `base_url` を差し替えるだけで繋がることが要件。

### `GET /api/v1/models`
```json
{ "object": "list", "data": [
  { "id": "chat-standard", "object": "model", "owned_by": "local" }
]}
```
返すのは **`models` テーブルの `served_name`** であって、Ollama の内部モデル名ではない。
これによりモデル実体を差し替えてもクライアント側は無改修で済む。

### `POST /api/v1/chat/completions`

OpenAI 準拠。`stream: true` の場合は `text/event-stream` で SSE 中継し、
最後に `data: [DONE]` を送る。

処理順序（**この順で実装すること**）:
1. 認証 → `401`
2. `served_name` の存在確認 → `404`
3. モデル権限チェック → `403`
4. レート制限 → `429`（`Retry-After` ヘッダ付き）
5. 同時実行スロット取得 → 取れなければ `429`
6. バックエンドへ転送
7. `usage_logs` へ記録（**失敗しても応答は返す**）

### `POST /api/v1/embeddings`
OpenAI 準拠。バックエンドは `ollama-embed`（CPU）に固定。

---

## 管理（`admin` ロールのみ）

### `GET /api/admin/health`
```json
{
  "llm": { "ok": true, "url": "...", "loaded_models": ["qwen3:4b"] },
  "embed": { "ok": true, "url": "..." },
  "db": { "ok": true }
}
```

### `GET /api/admin/gpu`
```json
{ "name": "NVIDIA GeForce GTX 1080",
  "memory_total_mb": 8192, "memory_used_mb": 3421,
  "utilization_pct": 62, "temperature_c": 58 }
```
`nvidia-smi` を叩く。コンテナから叩けない場合は `available: false` を返して落とさない。

### `GET /api/admin/usage`
クエリ: `from`, `to`, `user_id?`, `group_by` (`user` | `model` | `day`)

### ユーザー管理
`GET|POST /api/admin/users`, `PATCH|DELETE /api/admin/users/{id}`

### モデル管理
`GET|POST /api/admin/models`, `PATCH|DELETE /api/admin/models/{id}`

`POST /api/admin/models/{id}/pull` — バックエンドにモデルをダウンロードさせる。
進捗は SSE で返す。

---

## エラー形式

全エンドポイント共通。OpenAI 互換エンドポイントも同形式にする。

```json
{ "error": { "type": "rate_limit_exceeded",
             "message": "リクエスト数の上限に達しました",
             "code": "RATE_LIMIT" } }
```

| HTTP | type |
|---|---|
| 401 | `authentication_error` |
| 403 | `permission_error` |
| 404 | `not_found` |
| 429 | `rate_limit_exceeded` |
| 502 | `backend_error` |
| 503 | `backend_unavailable` |
