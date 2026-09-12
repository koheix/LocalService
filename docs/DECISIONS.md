# 設計判断の記録

新しい判断をしたら追記する。**既存の判断を覆す場合は削除せず、下に「撤回」として追記する。**

---

## D-001 検証機では vLLM を使わない

**背景** — 当初 vLLM を前提に設計していたが、検証機の GPU は GTX 1080（Pascal、
compute capability 6.1）だった。

**判断** — 検証機の推論バックエンドを Ollama（llama.cpp / GGUF）にする。

**理由** — vLLM は compute capability 7.0 以上のみをサポートしており、6.1 では
実行時に「no kernel image is available for execution on the device」で落ちる。
FlashAttention への依存が主因。ソースビルドによる回避は現行版では現実的でない。

**影響** — なし。gateway が OpenAI 互換 API を公開する設計のため、上位層は無改修。
本番機では `LLM_BACKEND=vllm` に切り替えるだけで済む。この切り替え可能性を
壊さないことが本プロジェクトの最重要制約になった。

---

## D-002 Embedding を CPU で動かす

**判断** — Embedding 用に GPU 割り当てのない2つ目の Ollama インスタンスを立てる。

**理由** — VRAM が 8GB しかなく、LLM と KVキャッシュで大半を使う。Embedding モデルを
GPU に載せると LLM のコンテキスト長を削ることになる。Embedding は 1件あたり数百ms かかるが、
検証フェーズでは許容範囲。

**代案と却下理由** — 独自の sentence-transformers サービスを書く案もあったが、
Ollama をもう1つ立てれば API が完全に同一になり、実装を共有できるため不要と判断。

---

## D-003 モデルの公開名と実体を分離する

**判断** — `models` テーブルに `served_name`（公開名）と `backend_name`（実体）を持たせ、
クライアントには `served_name` のみを見せる。

**理由** — モデルを差し替えても、繋ぎ込んだ外部ツールやアプリのコードを変更せずに済む。
`chat-standard` という名前のまま、中身を `qwen3:4b` から別モデルに変えられる。

**適用範囲の確認(T-14で発覚)** — `/api/conversations` を最初に実装した際、
誤って内部主キー `model_id` をリクエスト/レスポンスに直接使っていた。
`/api/v1/models` は `served_name` しか返さないため、UIがモデルを指定
できなくなる不整合だった。T-14着手時に気づき、`conversations` API も
`model`(served_name文字列)でやり取りするよう修正し、内部でのみ
`model_id` に変換するようにした。

---

## D-004 gateway で推論を直列化しない

**判断** — gateway が持つのは「ユーザーあたりの同時実行数上限」と「レート制限」のみ。
リクエストを1件ずつバックエンドに流すキューは作らない。

**理由** — vLLM は continuous batching で同時リクエストを束ねて処理するため、
gateway 側で直列化するとスループットが大幅に落ちる。並列制御は推論エンジンに任せる。
検証機の Ollama は `OLLAMA_NUM_PARALLEL=1` で同時1件に固定するが、
これはバックエンド側の設定であり、gateway の実装には現れない。

---

## D-005 認証を Keycloak 等に外出ししない

**判断** — 認証は gateway 内に最小実装する。OIDC / LDAP は連携フックのみ用意する。

**理由** — お客様先に設置して運用する製品であり、Keycloak や Authentik は
運用負荷とトラブル時の切り分けコストが高い。ロールも `admin` / `user` の2つで始め、
足りなくなってから増やす。Active Directory 連携は要望が出る前提で接続口だけ先に用意する。

---

## D-006 検証フェーズでも認証と利用ログは省略しない

**判断** — 同時利用者が1名でも、`users` / `api_keys` / `usage_logs` と
認証ミドルウェアは Phase 0 で実装する。

**理由** — 後から差し込むと全アプリのリクエスト経路を書き直すことになる。
一方 Prometheus / Grafana / Redis は後付けが容易なので Phase 0 では入れない。

---

## D-007 NVIDIA Container Toolkit のリポジトリは distro 非依存の stable/deb を使う

**背景** — `scripts/setup-host.sh` で当初 `libnvidia-container/ubuntu24.04/libnvidia-container.list`
という distro 別パスを指定していたが、`curl` が 404 を返してインストールに失敗した。

**判断** — distro 別パスではなく、NVIDIA が現在案内している distro 非依存の
`https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list`
を使う。

**理由** — NVIDIA は distro 別のリポジトリ一覧（`ubuntu24.04` 等）を廃止し、
`stable/deb` 配下の共通リポジトリに一本化している。Docker 側のリポジトリ指定
（`noble` 固定）とは別物であり、こちらは codename/distro 指定が不要になった。

**影響** — `scripts/setup-host.sh` の NVIDIA Container Toolkit 導入部分のみ修正。
Docker Engine 側の `noble` 固定は変更なし。

---

## D-008 Alembic はコンソールスクリプトではなく `python -m alembic` で呼ぶ

**背景** — `docker compose exec gateway alembic upgrade head` を実行すると
`ModuleNotFoundError: No module named 'app'` で失敗した。

**理由** — pip がインストールする `alembic` コンソールスクリプトは
`sys.path[0]` にスクリプト自身の置き場所（`/usr/local/bin`）を積む。
WORKDIR の `/app` はカレントディレクトリではあっても `sys.path` には
含まれないため、`gateway/alembic/env.py` の `from app.config import ...` が
解決できない。`python -m alembic ...` で起動すると `sys.path[0]` が
カレントディレクトリ（`''` → `/app`）になり解決できる。

**判断** — `Makefile` の `migrate` / `revision` ターゲットを
`docker compose exec gateway python -m alembic ...` に統一する。
今後 gateway コンテナ内で alembic を直接呼ぶ場合も同様にすること。

---

## D-009 セッションは失効時に物理削除する

**判断** — ログアウト時・期限切れ検出時ともに `sessions` 行を `DELETE` する。
論理削除用の `revoked_at` カラムは追加しない。

**理由** — `docs/SCHEMA.md` の `sessions` テーブル定義に `revoked_at` が
存在せず、追加するとスキーマ変更が必要になる。同時利用1名の検証機では
セッション監査証跡の必要性が薄く、実装を単純に保つ方を優先した。
ユーザーに確認済み。

## D-010 APIキーは `sk-` 全体を Argon2id でハッシュする

**判断** — API キーは `sk-<32byteランダム>` を発行し、`key_hash` には
キー全体を Argon2id でハッシュしたものを保存する。検証時は
`key_prefix`（先頭8文字）で候補行を絞り込んでから Argon2 verify する。

**理由** — `docs/SCHEMA.md` の `api_keys.key_hash` に `-- Argon2id` と
明記されている。SHA-256 等の高速ハッシュは DB 漏洩時により安全側だが、
仕様に反するためユーザーに確認のうえ Argon2id を採用した。
リクエスト毎に Argon2 検証のコストがかかる点は Phase 0（同時利用1名）
では許容範囲と判断。将来ボトルネックになる場合は候補を減らす
（key_prefix の桁数を増やす等）対応を検討する。

---

## D-011 usage_logs のベストエフォート範囲は「記録処理」に限る

**背景** — T-08 の完了条件に「DB を停止した状態でも推論が成功し、警告ログ
だけが出る」とあるが、認証（セッション/APIキー検証）自体が `users` /
`sessions` / `api_keys` テーブルへの問い合わせに依存しているため、
DB を完全に停止すると認証の時点で `500` になり、推論まで到達できない。

**判断** — 「ログ記録の失敗が推論レスポンスを壊してはならない」という
非交渉事項は、**usage_logs への書き込み処理そのもの**に限定して適用する。
認証・モデル存在確認・権限チェックが DB に依存すること自体は許容する
（D-006「認証は最初から入れる」と両立しない代替手段がないため）。
`_record_usage()` は例外を握って warn ログのみ出し、呼び出し元に伝播
させない設計とし、これを直接 DB 停止状態で呼び出して例外が出ないことを
確認した。

## D-012 ストリーミング応答のログ書き込みは `asyncio.shield` で保護する

**背景** — ストリーミング応答が完了しクライアントが切断すると、
その応答を生成している ASGI タスクがキャンセルされる。`finally` 節で
`await _record_usage(...)` を呼んでいても、`asyncio.CancelledError` は
`Exception` を継承していない（`BaseException` 直下）ため、
`except Exception` では捕まらず、DB 書き込みが完了する前に中断されて
usage_logs に記録が残らなかった。

**判断** — ストリーミング完了時・非ストリーミング完了時の両方で、
`_record_usage(...)` の呼び出しを `asyncio.shield(...)` で包み、
親タスクがキャンセルされてもログ書き込みタスク自体は最後まで実行させる。

**理由** — ログ記録はクライアントの生死に関わらず必ず残したい
（利用実績の集計・不正利用検知の基礎データになるため）。shield は
無制限に待ち続けるリスクはあるが、DB 書き込み自体は数十ms程度で
完了する想定であり Phase 0 では許容する。

---

## D-013 gateway コンテナに NVIDIA の utility capability のみ付与する

**背景** — `GET /api/admin/gpu` で実際の VRAM 使用量を返すには、
gateway コンテナから `nvidia-smi` を実行できる必要がある。しかし
`ollama` サービスのように `capabilities: [gpu]` を付与すると、
CUDA 計算能力まで要求してしまい「VRAM は LLM 専有」という
ハード制約（D-001/D-002）と衝突しかねない。

**判断** — `docker-compose.yml` の `gateway` サービスに
`NVIDIA_DRIVER_CAPABILITIES: utility` と
`deploy.resources.reservations.devices[].capabilities: [utility]` を
追加した。`gpu`(compute) ではなく `utility` のみを要求することで、
ドライバライブラリと `nvidia-smi` バイナリだけがコンテナに注入され、
VRAM 確保は発生しない。

**確認** — gateway コンテナ内で `nvidia-smi` が実行でき、実際の
使用量(MiB)を返すことを確認済み。バイナリが無い/失敗するケースの
フォールバック(`available: false`)も、存在しないコマンド名を使って
`FileNotFoundError`(`OSError` 派生)が捕捉されることを確認した。

---

## D-014 console(Web UI)をPhase 1に前倒しし、ビルド不要の静的サイトにする

**背景** — `CLAUDE.md`/`docs/ARCHITECTURE.md` は当初「console(Web UI)は
Phase 2以降、それまでは `/api/docs` で操作する」としていたが、
`docs/TASKS.md` の Phase 1 タスク一覧には「モデル選択つきチャットUI」が
既に含まれており矛盾していた。ユーザーに確認し、Phase 1 の時点で
実際に Web UI（プレイグラウンド）を作ることに決定した。

**判断**
- console は Phase 1 から着手する（`CLAUDE.md`/`ARCHITECTURE.md` を修正）。
- フロントエンドはビルド不要の静的 HTML/CSS/Vanilla JS とする
  （React/Vue 等のビルドチェーンは導入しない）。
- 専用コンテナは立てず、`proxy`(Caddy) が `caddy/console/` 配下の
  静的ファイルを直接配信する。`/api/*` は従来どおり `gateway` へ。

**理由** — 検証機は小型GPUサーバー1台で完結させる方針であり、
Node.js ビルド環境やSPAフレームワークの複雑さを持ち込むメリットが
Phase 1 の時点では薄い。fetch API + テンプレート関数で十分な規模。
Caddy は既に `file_server` を持つため、専用コンテナを増やさずに済む。
ユーザーに確認済み。

**影響** — Phase 1 のタスクを T-11〜T-14 として `docs/TASKS.md` に
分解した。会話履歴・システムプロンプト保存用に `conversations` /
`messages` テーブルを新設する（T-11、`docs/SCHEMA.md` に追記）。
