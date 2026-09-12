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
- [ ] `config.py`（Pydantic Settings。環境変数の読み込みはここだけ）
- [ ] `db.py`、`models.py`、`errors.py` と exception handler
- [ ] `GET /api/health` が DB 疎通を含めて `200` を返す

**完了条件**
`make up` 後、`curl localhost:8080/api/health` が `{"status":"ok"}` を返す。

---

### T-05 認証
- [ ] Argon2id によるパスワードハッシュ
- [ ] セッション発行・検証・失効（HttpOnly / SameSite=Lax Cookie）
- [ ] APIキー発行・検証（平文は発行時のみ返す。DBはハッシュのみ）
- [ ] `deps.py` に `current_user` / `require_admin`
- [ ] `scripts/seed.py`（冪等）

**完了条件**
- シードした管理者でログインでき、`GET /api/auth/me` が正しいロールを返す
- 未認証で `/api/auth/me` が `401`
- 発行した APIキーで `Authorization: Bearer` 認証が通り、`last_used_at` が更新される

---

### T-06 バックエンド抽象化
- [ ] `backends/base.py` に `InferenceBackend` Protocol
- [ ] `backends/ollama.py` 実装（chat / chat_stream / embed / list_models / health）
- [ ] `config.LLM_BACKEND` による実装選択

**完了条件**
`grep -ri "ollama" gateway/app/routers/` が**何もヒットしないこと**。
バックエンド固有の語彙がルーター層に漏れていない証明になる。

---

### T-07 OpenAI 互換エンドポイント
- [ ] `GET /api/v1/models` — `served_name` を返す
- [ ] `POST /api/v1/chat/completions` — 非ストリーミング
- [ ] 同上 — ストリーミング（SSE 中継、末尾に `data: [DONE]`）
- [ ] `POST /api/v1/embeddings`
- [ ] `docs/API.md` の処理順序どおりに実装（認証→存在→権限→レート→スロット→転送→記録）

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
- [ ] ユーザー単位の同時実行数上限（既定2）
- [ ] レート制限（既定 60 req/min、`429` に `Retry-After`）
- [ ] `usage_logs` への記録（成功・失敗とも。ストリーミングは完了時に確定）
- [ ] `ttft_ms` の計測
- [ ] **ログ記録の例外が推論レスポンスを壊さないこと**

**完了条件**
- DB を停止した状態でも推論が成功し、警告ログだけが出る
- 61回連続リクエストで `429` が返る
- `usage_logs` に prompt 本文が入っていない

---

### T-09 管理 API
- [ ] `GET /api/admin/health` / `/gpu` / `/usage`
- [ ] ユーザー CRUD、モデル CRUD
- [ ] `POST /api/admin/models/{id}/pull`（SSE で進捗）

**完了条件**
`/api/admin/gpu` が実際の VRAM 使用量を返す。`nvidia-smi` が叩けない場合も
`available: false` を返して `500` にならない。

---

### T-10 疎通スクリプト
- [ ] `make smoke` — GPU認識 → モデル応答 → ログイン → 推論 → ログ記録 を一気に検証

**完了条件**
クリーンな状態から `make setup && make up && make pull-models && make migrate && make smoke`
が全て緑で通る。

---

## Phase 1 — プレイグラウンド（着手前にユーザーと仕様確認）

- [ ] モデル選択つきチャット UI
- [ ] システムプロンプト保存
- [ ] パラメータ調整（temperature / top_p / max_tokens）
- [ ] 会話履歴の保存

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
