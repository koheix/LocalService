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
専用コンテナは立てず Caddy が配信）。**この方針は D-017 で React ベースに
更新された。** 以下の T-11〜T-14 は初期実装として完了済みだが、UI部分
（T-14）は Phase 3（React 移行）で置き換える。バックエンドAPI
（T-11〜T-13）はそのまま新UIからも利用する。

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
- [x] `GET/POST /api/conversations`
- [x] `GET/PATCH/DELETE /api/conversations/{id}`
- [x] `GET /api/conversations/{id}/messages`
- [x] 他ユーザーの会話は `404`（存在の有無を漏らさない）

**完了条件**
自分の会話のみ一覧・取得・更新・削除でき、他ユーザーの会話IDを指定すると
`404` になる。`system_prompt` / `temperature` / `top_p` / `max_tokens` /
`model_id` を作成・更新できる。

---

### T-13 チャット送信API（ストリーミング）
- [x] `POST /api/conversations/{id}/messages`:
      ユーザーメッセージ保存 → 推論 → アシスタント応答保存
- [x] SSE ストリーミング対応
- [x] `usage_logs` に `app='playground'` で記録

**完了条件**
ブラウザ以外（curl等）から会話を継続でき、リロード相当（再度
`GET .../messages`）しても履歴が残る。`usage_logs.app` が `playground`
になっている。

---

### T-14 console 静的UI（初期実装、Phase 3 で React 版に置き換え予定）
- [x] `caddy/console/` に HTML/CSS/Vanilla JS を配置
- [x] `caddy/Caddyfile` に静的配信を追加（`/*` → `file_server`）
- [x] ログイン画面
- [x] 会話一覧・新規作成・切り替え
- [x] チャット画面（モデル選択、システムプロンプト編集、
      temperature/top_p/max_tokens調整、ストリーミング表示）

**完了条件**
ブラウザで `http://localhost:8080/` を開き、ログイン→モデル選択→
システムプロンプト設定→チャット→履歴が残ることを一連の操作で確認できる。

## Phase 2 — 社内文書検索（RAG）

方針（ユーザー確認済み、D-016）:
- バックグラウンドジョブは Redis/Celery を使わず `FastAPI BackgroundTasks` で実装する
- アップロードした文書は全ユーザー共有（社内ナレッジベース。`owner_id` はアップロード者の記録用）
- 回答は新規専用エンドポイント `POST /api/rag/query` で提供する（`/api/v1` のOpenAI互換契約は変更しない）

---

### T-15 文書アップロードAPI
- [x] `POST /api/documents`（multipart、PDF/Word(.docx)/テキストのみ許可）
- [x] ファイル本体は named volume に保存し、`documents.storage_path` に記録
- [x] `GET /api/documents`（全ユーザー共有、一覧）
- [x] `DELETE /api/documents/{id}`（ファイル本体・chunksも削除）

**完了条件**
PDF・Word・テキストファイルをそれぞれアップロードでき、`documents` テーブルに
`status='pending'` で記録される。別ユーザーでログインしても一覧に表示される。

---

### T-16 チャンク分割・埋め込み生成（バックグラウンドジョブ）
- [x] アップロード後、`BackgroundTasks` でテキスト抽出→チャンク分割→埋め込み生成を実行
- [x] 抽出: PDF(pypdf) / Word(python-docx) / テキスト(そのまま)
- [x] チャンク分割: 固定長+オーバーラップ
- [x] 埋め込み: `ollama-embed`(bge-m3, 1024次元)で `chunks` に保存
- [x] `documents.status` を `pending→indexing→ready` / 失敗時 `failed` に更新

**完了条件**
アップロード後しばらく待つと `GET /api/documents/{id}` の `status` が `ready` になり、
`chunks` に埋め込み付きの行が複数作成される。壊れたファイルは `failed` になり、
アプリ全体は落ちない。

---

### T-17 RAG検索API
- [x] `POST /api/rag/query` `{"question": "..."}`
- [x] 質問を埋め込み化 → pgvectorのコサイン類似度で `chunks` を検索 → 上位N件を
      コンテキストにチャット推論 → 引用（`document_id`・ファイル名・該当チャンク）
      付きで回答を返す

**完了条件**
アップロード済み文書の内容について質問すると、その文書を引用した回答が返る。
関係ない質問には、その旨（該当文書なし）を返す。

---

### T-18 console にRAG画面を追加（初期実装、Phase 3 で React 版に置き換え予定）
- [x] タブ切替（チャット／文書検索）
- [x] 文書アップロード・一覧（状態表示、所有者またはadminのみ削除ボタン）
- [x] 質問フォームと引用付き回答表示

**完了条件**
ブラウザで文書検索タブに切替え、アップロード→インデックス完了(準備完了表示)
待ち→質問→引用付き回答の表示までを一連の操作で確認できる。

## Phase 3 — Web UI を React へ移行

方針（ユーザー確認済み、D-017。D-014を上書きする）:
- console を Vanilla JS 静的サイトから React 18 + TypeScript + Vite + Tailwind の
  SPA に全面移行する。ビルド成果物は `console` コンテナ（nginx または Caddy）
  から配信し、`proxy` の Caddyfile はそこへリバースプロキシする
- バックエンドAPI（T-11〜T-13、T-15〜T-17）は無改修で新UIから利用する。
  gateway 側の変更は T-20 の `GET /api/admin/summary` 追加のみ
- UIライブラリ（MUI / Chakra 等）は導入しない。状態管理ライブラリも入れない

---

### T-19 フロントエンドの土台
- [x] `console/` に React 18 + TypeScript + Vite + Tailwind をセットアップ
- [x] Dockerfile（ビルド成果物を nginx または Caddy で配信）
- [x] `caddy/Caddyfile` の `handle {}` を console へのリバースプロキシに変更
- [x] ログイン画面、認証状態の保持、`401` でのリダイレクト

**完了条件**
`http://localhost:8080/` でログイン画面が出て、シードした管理者でログインでき、
リロードしてもセッションが維持される。

---

### T-20 ホーム画面
- [x] `docs/UI_HOME.md` の仕様どおりに実装する
- [x] `GET /api/admin/summary` を gateway に追加（件数を1回で返す）
- [x] システム状態パネルの30秒ポーリング
- [x] `user` ロールで管理セクションが表示されないことを確認

**完了条件**
- `docs/assets/home-mockup.png` と見比べて構成が一致している
- gateway を停止した状態でも画面が描画され、状態パネルだけが赤いドットと `—` になる
- ブラウザを dark mode にしても全テキストが読める
- `user` ロールでログインすると管理セクションが DOM に存在しない

---

### T-21 プレイグラウンド（React版）
- [ ] モデル選択つきチャット UI（ストリーミング表示）
- [ ] システムプロンプト保存
- [ ] パラメータ調整（temperature / top_p / max_tokens）
- [ ] 会話履歴の保存

**着手前にユーザーと画面仕様を確認すること。**

**完了条件**
Vanilla JS版（T-14）と同等の操作（ログイン→モデル選択→システムプロンプト設定→
チャット→履歴が残る）がReact版で一連の操作として確認できる。

---

### T-22 文書検索画面（React版）
- [ ] `docs/UI_HOME.md` のアプリカードから遷移する文書検索画面をReactで実装する
- [ ] 文書アップロード・一覧（状態表示、所有者またはadminのみ削除ボタン）
- [ ] 質問フォームと引用付き回答表示

**着手前にユーザーと画面仕様を確認すること。**

**完了条件**
Vanilla JS版（T-18）と同等の操作がReact版で一連の操作として確認できる。

---

### T-23 旧 Vanilla JS console の削除
- [ ] `caddy/console/` を削除し、`caddy/Caddyfile` の静的配信設定を除去する
- [ ] `docs/TASKS.md` の T-14 / T-18 に置き換え完了の注記を追加する

**完了条件**
React版で T-14・T-18 相当の操作がすべて確認できたうえで、Vanilla JS版が
リポジトリから削除されている。

## Phase 4 — 本番機対応

- [ ] `backends/vllm.py` の実装と切り替え確認
- [ ] Prometheus / Grafana / dcgm-exporter
- [ ] `usage_logs` の月次集計とパージ
- [ ] systemd unit（`Restart=always`、GPUドライバ起動後の依存）
- [ ] Active Directory / OIDC 連携
