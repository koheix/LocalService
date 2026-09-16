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
**→ T-21（プレイグラウンド、React版）に置き換え済み。`caddy/console/` は T-23 で削除した。**
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
**→ T-22（文書検索画面、React版）に置き換え済み。`caddy/console/` は T-23 で削除した。**
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
- [x] モデル選択つきチャット UI（ストリーミング表示）
- [x] システムプロンプト保存
- [x] パラメータ調整（temperature / top_p / max_tokens）
- [x] 会話履歴の保存

**着手前にユーザーと画面仕様を確認すること。**
※ このタスクはユーザーが就寝中に「できるところまでやってください」の指示のもとで
実装した。事前の画面仕様確認は行わず、T-14 の完了条件（下記）と `docs/UI_HOME.md` の
デザイン言語をそのまま踏襲する形で進めた。ユーザー復帰後にレビューしてもらうこと。

**完了条件**
Vanilla JS版（T-14）と同等の操作（ログイン→モデル選択→システムプロンプト設定→
チャット→履歴が残る）がReact版で一連の操作として確認できる。
→ Playwrightで確認済み（ログイン→会話作成→モデル/システムプロンプト/temperature設定→
保存→送信→ストリーミング表示→リロードしても履歴が残る→ダークモード表示も確認）。

---

### T-22 文書検索画面（React版）
- [x] `docs/UI_HOME.md` のアプリカードから遷移する文書検索画面をReactで実装する
- [x] 文書アップロード・一覧（状態表示、所有者またはadminのみ削除ボタン）
- [x] 質問フォームと引用付き回答表示

**着手前にユーザーと画面仕様を確認すること。**
※ T-21と同様、事前の画面仕様確認なしで実装した（ユーザー就寝中、既存Vanilla JS版
(T-18)を仕様の正としてそのまま移植）。ユーザー復帰後にレビューしてもらうこと。
なお、`AppGrid`（ホーム画面）で「社内文書検索」カードが`phase2`（無効化）のままだった
ため、本タスクの一部として`available`に変更した（さもないとホームから遷移できない）。

**完了条件**
Vanilla JS版（T-18）と同等の操作がReact版で一連の操作として確認できる。
→ Playwrightで確認済み（アップロード→pending/indexingのポーリング→準備完了→
質問→引用付き回答→所有者による削除→ダークモード表示も確認）。

---

### T-23 旧 Vanilla JS console の削除
- [x] `caddy/console/` を削除し、`caddy/Caddyfile` の静的配信設定を除去する
- [x] `docs/TASKS.md` の T-14 / T-18 に置き換え完了の注記を追加する

**完了条件**
React版で T-14・T-18 相当の操作がすべて確認できたうえで、Vanilla JS版が
リポジトリから削除されている。
→ `caddy/Caddyfile`は T-19 の時点で既に`console`コンテナへのreverse_proxyのみに
なっており、静的配信設定は残っていなかった。`caddy/console/`（index.html/app.js/
style.css）を削除した。

---

T-21/T-22マージ後、ユーザーが実機（iPhone 13含む）で操作した結果のフィードバック
（2026-09-15）を受けて追加。着手順はユーザー確認済み: T-24 → T-25 → T-26 → T-27。

### T-24 consoleをスマートフォン幅でも使えるようにする
- [x] Home / プレイグラウンド / RAG の各画面をiPhone 13相当の画面幅（390×844）で
      横スクロールが起きないようにする
- [x] プレイグラウンドのサイドバー（会話一覧）と設定パネルを、狭い画面では
      折りたたみ/タブ切替等の形にする
- [x] RAGの2カラムレイアウト（文書一覧／質問エリア）を、狭い画面では縦積みにする

**完了条件**
iPhone 13相当のビューポート（390×844）で、Home・プレイグラウンド（会話選択→
送信）・RAG（アップロード→質問）の一連の操作が、横スクロール無しで完了できる。
→ Playwright(390×844)で実機確認済み。Home/RAGは元々`sm`/`lg`ブレークポイントで
既に単一カラムに畳まれておりオーバーフロー無し(修正不要)。プレイグラウンドの
`w-64`固定サイドバーだけが横並びのまま残っており、狭い画面で本文が極端に
圧縮され文字が縦一列に折り返る状態だった(スクロール自体は発生しないが実質
使用不能)。サイドバーをsm未満でオフキャンバスのドロワー化(ハンバーガー
ボタン+背景オーバーレイ、会話選択/新規作成で自動的に閉じる)し解消した。
設定パネル(会話設定)もsm未満では既定で折りたたむようにし(pr-reviewer指摘、
`<details>`の`open`をviewport幅に応じた初期値の制御可能な状態にした)、
会話が1件も無い新規ユーザーでもドロワーが自動的に開いて「新しい会話を
始める」に迷わず気づけるようにした。デスクトップ幅(1280px)でのレイアウトに
変化が無いことも確認済み。Escキーでドロワーを閉じられることも確認した
(フォーカストラップ・スクリーンリーダー向けのinert化等は未対応、
低優先度のフォローアップとして残す)。

---

### T-25 プレイグラウンドに生成中インジケータを追加する
- [x] メッセージ送信後、最初のトークンが届くまで「生成中...」等の表示を出す
- [x] ストリーミング中であることが視覚的にわかるようにする（末尾にカーソル等）

**完了条件**
メッセージを送信すると、応答本文が届く前に生成中であることが画面上でわかり、
最初のトークン到着とともに表示が切り替わる。
→ Playwrightで実機(実Ollama、コールドロード込み)確認済み。送信直後は
アシスタント側の吹き出しに斜体で「生成中…」と表示し、最初のトークン到着で
実際の内容に切り替わる。ストリーミング中は末尾に点滅するカーソル(縦棒)を
表示し、完了(sending=false)で消える。

---

### T-26 会話タイトルをLLMで自動生成する
- [ ] 新規会話の最初のユーザー発言＋アシスタント応答が揃った時点で、短いタイトルを
      LLMに生成させて会話の`title`を更新する
- [ ] 生成に失敗しても会話自体やチャット機能は壊れないこと（best-effort）

**完了条件**
タイトル未設定（「新しい会話」）のまま最初のメッセージを送信すると、応答完了後に
タイトルが内容を反映したものに自動的に変わる。

---

### T-27 RAGの回答をストリーミング化し、検索中/生成中の状態表示を追加する
- [x] `POST /api/rag/query`をSSEストリーミング対応にする（`/api/v1/chat/completions`
      と同様の形。引用(citations)の送信タイミングは実装時に決める）
- [x] RAG画面に「検索中...」→「回答を生成中...」の状態遷移表示を追加する
- [x] 回答本文をストリーミングで表示する

**完了条件**
質問すると、検索中→生成中の表示を経て、回答がストリーミングで(トークン単位で
徐々に)表示され、最後に引用が表示される。

**実装メモ（完了、D-016に追記あり）**
- OpenAI互換形式をそのまま踏襲せず、`{"type":"status"|"delta"|"citations"}`の
  RAG専用SSE形式にした（`docs/API.md`参照）。関連文書が無い場合は従来通り
  即座にJSON応答を返す(LLMを呼ばないためストリームにする意味がない)。
- 実機(qwen3:4b)で実際にPlaywright検証済み: 検索中→生成中の表示切替、
  本文が時間経過で伸びる(ストリーミングされている)こと、引用表示、
  完了後にボタンが再度有効になることを確認した。
- 副産物として判明した重要な事実: 既存のRAG回答生成(旧実装の非ストリーミング
  `chat()`呼び出し)は、qwen3:4bがOllama側の強制`<think>`設定により毎回長い
  思考をしてから答えていたため、体感の遅さの主因はまさにこれだった。
  ストリーミング化そのものはこの思考時間自体を短縮しないが、「検索中」
  「生成中」の表示により、この待ち時間が「固まっている」ように見えなく
  なった。T-26で保留にした「思考を止められない」問題と同根の制約。

---

### T-28 管理画面に利用状況画面を実装する

`/admin/usage`はホーム画面の管理セクションからリンクされているが、まだ
プレースホルダー(`docs/D-017`参照)。バックエンドAPI(`GET /api/admin/usage`、
T-09で実装済み)をそのまま使い、実データを表示する画面に置き換える。

- [x] `/admin/usage`のプレースホルダーを実データ表示に置き換える
- [x] 期間(from/to)とグループ化(日別/ユーザー別/モデル別)を切り替えて、
      リクエスト数・トークン数の一覧を表示する
- [x] 日別集計のタイムゾーンをJSTに統一する(現状`func.date(created_at)`が
      DBセッションのタイムゾーン依存で、`/admin/summary`のJST基準と不整合。
      D-017追記で「利用状況画面を作る際に判断する」と保留していた論点)
- [x] ユーザー別/モデル別はID表示ではなく、email/served_nameで表示する
      (既存の`/api/admin/users`・`/api/admin/models`から解決する)

**完了条件**
管理者でログインし「利用状況」画面を開くと、期間とグループ化を指定して
リクエスト数・トークン数の一覧が表示される。日別集計の区切りがJST基準に
なっている。

**実装メモ（完了、D-017に追記あり）**
- `func.date(func.timezone('Asia/Tokyo', UsageLog.created_at))`でJSTの日付に
  変換してから`group_by=day`の集計をするように`gateway/app/routers/admin.py`
  を修正した。
- ユーザー別/モデル別の表示名解決は、この画面が`GET /api/admin/users`・
  `GET /api/admin/models`を呼んでクライアント側でID→名前のマップを作る形に
  した(バックエンドの`/api/admin/usage`自体は変更していない)。
  削除済みモデル/ユーザーで`model_id`/`user_id`が`NULL`になっている
  usage_logs行は「(不明)」として表示する。
- 実機でPlaywright検証済み: 日別/ユーザー別/モデル別の切り替え、
  実際のusage_logsデータの表示、名前解決、削除済みモデルの「(不明)」表示
  を確認した。

---

### T-29 利用状況画面にグラフを追加する

T-28で作った表形式の表示に加えて、グラフ表示を追加する。グラフ描画には
Rechartsを導入する(ユーザー確認済み。D-017が避けたのはMUI/Chakraのような
全体の見た目を支配するUIフレームワークで、グラフ描画のような単一目的の
ツールは別種と判断)。

- [x] Rechartsを導入する
- [x] 日別(day)表示: 日付ごとのトークン数・リクエスト数の推移を
      折れ線/棒グラフで表示する
- [x] ユーザー別/モデル別表示: 名前ごとの合計トークン数を横棒グラフで
      比較表示する
- [x] 既存のテーブル表示は残したまま、グラフを追加する
- [x] レスポンシブ・ダークモード対応(既存画面の方針を踏襲)

**完了条件**
管理者が利用状況画面を開くと、選択した期間・グループ化に応じたグラフ
(日別は時系列推移、ユーザー別/モデル別は比較棒グラフ)が、既存のテーブル
表示と併せて表示される。

**実装メモ（完了）**
- 日別は入力/出力トークンの積み上げ棒グラフ＋リクエスト数の折れ線を
  組み合わせたグラフ(左右2軸)、ユーザー別/モデル別は入力/出力トークンの
  積み上げ横棒グラフにした(`console/src/components/admin/UsageChart.tsx`)。
- ダークモードはTailwindの`dark:`バリアントが使えない(Rechartsが生SVGに
  直接色を指定する)ため、`window.matchMedia("(prefers-color-scheme: dark)")`
  を購読してJS側で配色を出し分けている。
- Rechartsは依存が重い(縮小後約110KB gzip)ため、利用状況画面(admin限定)を
  `React.lazy`で遅延読み込みにし、他の全ユーザーが読み込むメインバンドルを
  肥大化させないようにした(`console/src/App.tsx`)。
- 実機でPlaywright検証済み(日別/ユーザー別/モデル別のグラフ描画、
  ライト/ダーク両方の配色)。
- Recharts採用とD-017との関係、`React.lazy`化のトレードオフは
  `docs/DECISIONS.md`のD-018に記録した。
- 既知の制限(レビューで指摘、追加対応はせず受け入れた): (1)日別グラフは
  `usage_logs`に実績がある日しか描かれない(期間内に利用ゼロの日があると
  その日は棒が無いまま隣の日と等間隔で並び、実際より連続的に見える場合が
  ある)。(2)日別グラフの左右2軸(トークン数/リクエスト数)はスケールが
  独立しており、2本のグラフの交点に意味は無い。(3)ユーザー別/モデル別の
  並び順は値の降順ではなく、テーブルと同じID昇順にしてある。

## Phase 4 — 本番機対応

- [ ] `backends/vllm.py` の実装と切り替え確認
- [ ] Prometheus / Grafana / dcgm-exporter
- [ ] `usage_logs` の月次集計とパージ
- [ ] systemd unit（`Restart=always`、GPUドライバ起動後の依存）
- [ ] Active Directory / OIDC 連携
