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
{ "id": 1, "email": "...", "display_name": "...", "role": "user", "created_at": "..." }
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
  { "id": "chat-standard", "object": "model", "owned_by": "local", "kind": "chat" }
]}
```
返すのは **`models` テーブルの `served_name`** であって、Ollama の内部モデル名ではない。
これによりモデル実体を差し替えてもクライアント側は無改修で済む。`kind`
（`chat` | `embedding`）は OpenAI 互換の標準フィールドではない加筆で、
console がチャット用途とembedding用途のモデルを区別するために使う
（追加フィールドなので既存のOpenAI互換クライアントの動作は変えない）。

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

## プレイグラウンド（会話履歴）

自分が所有する会話のみ操作できる。他ユーザーの会話IDを指定した場合は
存在の有無を漏らさないため `403` ではなく `404` を返す。

### `GET /api/conversations`
自分の会話一覧を `updated_at` 降順で返す。

### `POST /api/conversations`
```json
{ "title": "雑談", "model": "chat-standard",
  "system_prompt": "簡潔に答えて", "temperature": 0.7,
  "top_p": 1.0, "max_tokens": null }
```
`model` は `served_name`（`models.model_id` ではない。D-003）。すべて省略可。

### `GET /api/conversations/{id}` / `PATCH /api/conversations/{id}` / `DELETE /api/conversations/{id}`
`PATCH` は指定したフィールドのみ更新する（部分更新）。

### `GET /api/conversations/{id}/messages`
会話のメッセージ履歴を古い順で返す（`system` ロールは含まない設計だが、
保存自体は `role` に `system/user/assistant` を許容する）。

### `POST /api/conversations/{id}/messages`
```json
{ "content": "こんにちは" }
```
- ユーザーメッセージを先に保存してから推論する（推論が失敗してもユーザーの
  入力は残る）
- 会話に `model` が設定されていなければ `400`（`invalid_request_error`）
- 処理順序・エラー・レート制限は `/api/v1/chat/completions` と同じ
- 応答は常に `stream: true` 相当の SSE（`/api/v1` と同形式）。完了後に
  アシスタント応答を保存し、`usage_logs` に `app: "playground"` で記録する

---

## 社内文書検索（RAG）

アップロードした文書は全ユーザー共有（社内ナレッジベース、D-016）。
`owner_id` は記録用のみで、閲覧・検索には影響しない。

### `GET /api/documents`
全ユーザーの文書一覧を新しい順で返す。

### `POST /api/documents`
`multipart/form-data`。`file` フィールドに PDF / Word(`.docx`) /
テキストのみ許可（それ以外は `400`）。アップロード後、`FastAPI
BackgroundTasks` でテキスト抽出→チャンク分割→埋め込み生成を非同期
実行する（D-016）。応答は即座に返り、`status: "pending"`。

### `GET /api/documents/{id}`
`status` は `pending → indexing → ready`、失敗時は `failed`。

### `DELETE /api/documents/{id}`
アップロードした本人または `admin` のみ（それ以外は `403`）。
ファイル本体と `chunks` も削除する。

### `POST /api/rag/query`
```json
{ "question": "検証機のGPUは何ですか？" }
```
質問を埋め込み化し `chunks` をコサイン距離で検索する（この検索自体は
同期処理で、通常は一瞬で終わる）。

十分近いチャンクが無い場合は LLM を呼ばず、従来通り即座にJSON応答を返す。
```json
{ "answer": "関連する社内文書が見つかりませんでした。", "citations": [] }
```

関連チャンクがあれば、チャット推論の結果を **SSEストリーミング
(`Content-Type: text/event-stream`)** で返す（T-27）。フロントエンドは
リクエスト送信からこのイベントが届くまでを「検索中」として表示し、
最初のイベントを受けて「生成中」に切り替える想定。
```
data: {"type": "status", "phase": "generating"}

data: {"type": "delta", "content": "GeForce"}

data: {"type": "delta", "content": " GTX 1080"}

data: {"type": "citations", "citations": [
  {"document_id": 6, "filename": "spec.txt", "chunk_id": 2,
   "content": "検証機にはGeForce GTX 1080を使用しており..."}
]}

data: [DONE]

```
`citations` は回答本文がすべて届いた後、最後にまとめて1回だけ送る。
ストリーミング中にバックエンド側で例外が起きた場合は、200応答を返した
後で接続が異常終了する（`/api/v1/chat/completions` や
`/api/conversations/{id}/messages` のストリーミングと同じ扱い。
専用のSSEエラーイベントは無い）。

`/api/v1` と同様にレート制限・同時実行スロット・`usage_logs`（`app: "rag"`）の
対象になる。関連文書が無く埋め込みのみ行った場合は埋め込みモデルを、
チャット推論まで進んだ場合はチャット応答モデルを対象に1リクエスト1行で
記録する（バックグラウンドの文書インデックス時の埋め込みは対象外。D-016）。
ストリーミング時は同時実行スロットも回答生成が完了するまで保持される。

---

## 管理

`GET /api/admin/health` と `GET /api/admin/gpu` の2つだけは例外で、
**認証済みなら誰でも**呼べる（ホーム画面のシステム状態パネルを一般ユーザーにも
表示するため。D-017/docs/UI_HOME.md、ユーザー確認済み）。それ以外は
`admin` ロールのみ。

### `GET /api/admin/health`
`admin` ロールには以下の完全な形を返す。
```json
{
  "llm": { "ok": true, "url": "...", "loaded_models": ["qwen3:4b"] },
  "embed": { "ok": true, "url": "..." },
  "db": { "ok": true }
}
```
`admin` 以外（認証済みなら誰でも）には `url` / `detail`（運用情報）を除いた
`{"llm": {"ok": ..., "loaded_models": [...]}, "embed": {"ok": ..., "loaded_models": [...]}, "db": {"ok": ...}}`
のみ返す。

### `GET /api/admin/gpu`
```json
{ "name": "NVIDIA GeForce GTX 1080",
  "memory_total_mb": 8192, "memory_used_mb": 3421,
  "utilization_pct": 62, "temperature_c": 58 }
```
`nvidia-smi` を叩く。コンテナから叩けない場合は `available: false` を返して落とさない。

### `GET /api/admin/summary`（`admin` ロールのみ）
```json
{ "active_user_count": 5, "active_model_count": 2, "today_total_tokens": 12345 }
```
ホーム画面の管理セクション（ユーザー管理・モデル管理・利用状況の各行に出す件数）を
1回のリクエストでまとめて返す。

### `GET /api/admin/usage`（`admin` ロールのみ）
クエリ: `from`, `to`, `user_id?`, `group_by` (`user` | `model` | `day`)
```json
{ "group_by": "day",
  "data": [
    { "day": "2026-09-16", "request_count": 42,
      "prompt_tokens": 1234, "completion_tokens": 567 }
  ] }
```
`group_by=user`/`model`では`day`の代わりに`user`/`model`キーでID(数値)を返す
（表示名への解決はフロントエンド側で`/api/admin/users`・`/api/admin/models`を
使って行う）。該当ユーザー/モデルが削除済みの場合、`usage_logs.user_id`/
`model_id`は`NULL`になり得るため、`user`/`model`キーの値も`null`になり得る。
`group_by=day`の日付区切りはJST(Asia/Tokyo)基準
（`/api/admin/summary`の「当日」と同じ基準。T-28、ユーザー確認済み）。

`from`/`to`は半開区間（`from <= created_at < to`）。`to`当日を含めたい場合は
呼び出し側で`to`に「含めたい最終日の翌日」を渡す必要がある
（コンソールの利用状況画面はJSTの暦日として扱い、`to`には終了日の翌日の
JST 00:00を渡している）。

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
