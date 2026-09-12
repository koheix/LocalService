# タスク

上から順に実施する。**1タスク＝1コミット。** 完了したらチェックを入れる。
完了条件を満たさないまま次に進まないこと。

---

## Phase 0 — 基盤を立てる

### T-01 ホストセットアップの確立
- [x] `scripts/setup-host.sh` を完成させる（Docker / NVIDIA Container Toolkit）
- [x] Linux Mint 22.3 で `ID=linuxmint` を回避し `noble` / `ubuntu24.04` を明示していること

**完了条件**
`docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi` が
GTX 1080 を表示する。

---

### T-02 Compose で推論バックエンドを起動
- [x] `ollama`（GPU）と `ollama-embed`（CPU）が起動する
- [x] モデルは named volume に永続化され、`make down && make up` で再ダウンロードが起きない
- [x] **ホストにポートが公開されていないことを `docker compose ps` で確認**

**完了条件**
`make pull-models` 後、コンテナ内から `curl ollama:11434/api/tags` が既定モデルを返す。
`nvidia-smi` で VRAM 使用量が 4GB 未満であること。

---

### T-03 Postgres とマイグレーション
- [x] `postgres` サービス（pgvector 入り）
- [x] Alembic 初期化、`docs/SCHEMA.md` の全テーブルを1つのリビジョンで作成
- [x] `make migrate` が通る

**完了条件**
`\dt` で全テーブルが存在し、`CREATE EXTENSION vector` が成功している。

---

### T-04 gateway の骨格
- [x] `config.py`（Pydantic Settings。環境変数の読み込みはここだけ）
- [x] `db.py`、`models.py`、`errors.py` と exception handler
- [x] `GET /api/health` が DB 疎通を含めて `200` を返す

**完了条件**
`make up` 後、`curl localhost:8080/api/health` が `{"status":"ok"}` を返す。

---

### T-05 認証
- [x] Argon2id によるパスワードハッシュ
- [x] セッション発行・検証・失効（HttpOnly / SameSite=Lax Cookie）
- [x] APIキー発行・検証（平文は発行時のみ返す。DBはハッシュのみ）
- [x] `deps.py` に `current_user` / `require_admin`
- [x] `scripts/seed.py`（冪等）

**完了条件**
- シードした管理者でログインでき、`GET /api/auth/me` が正しいロールを返す
- 未認証で `/api/auth/me` が `401`
- 発行した APIキーで `Authorization: Bearer` 認証が通り、`last_used_at` が更新される

---

### T-06 バックエンド抽象化
- [x] `backends/base.py` に `InferenceBackend` Protocol
- [x] `backends/ollama.py` 実装（chat / chat_stream / embed / list_models / health）
- [x] `config.LLM_BACKEND` による実装選択

**完了条件**
`grep -ri "ollama" gateway/app/routers/` が**何もヒットしないこと**。
バックエンド固有の語彙がルーター層に漏れていない証明になる。

---

### T-07 OpenAI 互換エンドポイント
- [x] `GET /api/v1/models` — `served_name` を返す
- [x] `POST /api/v1/chat/completions` — 非ストリーミング
- [x] 同上 — ストリーミング（SSE 中継、末尾に `data: [DONE]`）
- [x] `POST /api/v1/embeddings`
- [x] `docs/API.md` の処理順序どおりに実装（認証→存在→権限→レート→スロット→転送→記録）
      ※ レート制限・同時実行スロット・usage_logs記録は T-08 で追加する
        (このタスクでは 認証→存在→権限→転送 を実装)

**完了条件**
以下が動くこと。

```python
from openai import OpenAI
c = OpenAI(base_url="http://localhost:8080/api/v1", api_key="sk-...")
c.chat.completions.create(model="chat-standard",
                          messages=[{"role":"user","content":"こんにちは"}])
```
ストリーミングでも同様に動くこと。

---

### T-08 制限と利用ログ
- [x] ユーザー単位の同時実行数上限（既定2）
- [x] レート制限（既定 60 req/min、`429` に `Retry-After`）
- [x] `usage_logs` への記録（成功・失敗とも。ストリーミングは完了時に確定）
- [x] `ttft_ms` の計測
- [x] **ログ記録の例外が推論レスポンスを壊さないこと**

**完了条件**
- DB を停止した状態でも推論が成功し、警告ログだけが出る
- 61回連続リクエストで `429` が返る
- `usage_logs` に prompt 本文が入っていない

---

### T-09 管理 API
- [x] `GET /api/admin/health` / `/gpu` / `/usage`
- [x] ユーザー CRUD、モデル CRUD
- [x] `POST /api/admin/models/{id}/pull`（SSE で進捗）

**完了条件**
`/api/admin/gpu` が実際の VRAM 使用量を返す。`nvidia-smi` が叩けない場合も
`available: false` を返して `500` にならない。

---

### T-10 疎通スクリプト
- [x] `make smoke` — GPU認識 → モデル応答 → ログイン → 推論 → ログ記録 を一気に検証

**完了条件**
クリーンな状態から `make setup && make up && make pull-models && make migrate && make smoke`
が全て緑で通る。

---

## Phase 1 — プレイグラウンド

方針は D-014 で決定済み（Web UI をPhase 1で作る、ビルド不要の静的サイト、
専用コンテナは立てず Caddy が配信）。以下のタスクに分解して進める。

---

### T-11 会話履歴スキーマ
- [x] `conversations` テーブル（user_id, title, model_id, system_prompt,
      temperature, top_p, max_tokens, created_at, updated_at）
- [x] `messages` テーブル（conversation_id, role, content, created_at）
- [x] `docs/SCHEMA.md` に追記
- [x] Alembic リビジョン追加

**完了条件**
`make migrate` 後、`\d conversations` `\d messages` で列が確認できる。

---

### T-12 会話CRUD API
- [ ] `GET/POST /api/conversations`
- [ ] `GET/PATCH/DELETE /api/conversations/{id}`
- [ ] `GET /api/conversations/{id}/messages`
- [ ] 他ユーザーの会話は `404`（存在の有無を漏らさない）

**完了条件**
自分の会話のみ一覧・取得・更新・削除でき、他ユーザーの会話IDを指定すると
`404` になる。`system_prompt` / `temperature` / `top_p` / `max_tokens` /
`model_id` を作成・更新できる。

---

### T-13 チャット送信API（ストリーミング）
- [ ] `POST /api/conversations/{id}/messages`:
      ユーザーメッセージ保存 → 推論 → アシスタント応答保存
- [ ] SSE ストリーミング対応
- [ ] `usage_logs` に `app='playground'` で記録

**完了条件**
ブラウザ以外（curl等）から会話を継続でき、リロード相当（再度
`GET .../messages`）しても履歴が残る。`usage_logs.app` が `playground`
になっている。

---

### T-14 console 静的UI
- [ ] `caddy/console/` に HTML/CSS/Vanilla JS を配置
- [ ] `caddy/Caddyfile` に静的配信を追加（`/*` → `file_server`）
- [ ] ログイン画面
- [ ] 会話一覧・新規作成・切り替え
- [ ] チャット画面（モデル選択、システムプロンプト編集、
      temperature/top_p/max_tokens調整、ストリーミング表示）

**完了条件**
ブラウザで `http://localhost:8080/` を開き、ログイン→モデル選択→
システムプロンプト設定→チャット→履歴が残ることを一連の操作で確認できる。

## Phase 2 — 社内文書検索（RAG）

- [ ] ファイルアップロードと抽出（PDF / Word / テキスト）
- [ ] チャンク分割と埋め込み生成（バックグラウンドジョブ）
- [ ] pgvector による検索と引用つき回答

## Phase 3 — 本番機対応

- [ ] `backends/vllm.py` の実装と切り替え確認
- [ ] Prometheus / Grafana / dcgm-exporter
- [ ] `usage_logs` の月次集計とパージ
- [ ] systemd unit（`Restart=always`、GPUドライバ起動後の依存）
- [ ] Active Directory / OIDC 連携
