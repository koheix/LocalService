# CLAUDE.md

このファイルは Claude Code がセッション開始時に必ず読むプロジェクト規約です。
作業前に `docs/TASKS.md` を確認し、着手するタスクを1つ宣言してから始めてください。

---

## プロジェクト概要

社内に1台設置する小型GPUサーバー上で動く、**ローカルLLM利用のための社内プラットフォーム**。
AWSマネジメントコンソールのように、複数の便利アプリをアカウント単位でGUI提供する。

現在は **検証フェーズ（Phase 0）**。同時利用1名、実機は GTX 1080 (8GB)。
本番機は別スペックになるため、**GPUバックエンドは差し替え可能な設計を絶対に崩さないこと。**

---

## ハード制約（絶対に守る）

| 項目 | 検証機の実際の値 |
|---|---|
| GPU | GeForce GTX 1080 / VRAM 8GB / Pascal (compute capability 6.1) |
| OS | Linux Mint 22.3（Ubuntu 24.04 "noble" ベース） |
| 同時利用者 | 1名 |

### やってはいけないこと

- **vLLM を使わない。** compute capability 6.1 は vLLM 非対応で起動時に落ちる。
  検証機の推論バックエンドは Ollama（llama.cpp / GGUF）。
- **bfloat16 を前提にしたコードを書かない。** Pascal は bf16 非対応、fp16 も低速。
- **Embedding と Whisper を GPU に載せない。** 8GB を LLM 専有にする。CPU で動かす。
- **`ollama` コンテナのポートをホストに公開しない。** Ollama には利用者単位の認証機構がないため、
  外部公開すると本システムのアカウント管理が意味を失う。公開するのは `proxy` のみ。
- **Linux Mint 用のリポジトリ設定をしない。** Docker / NVIDIA Container Toolkit の
  公式インストールスクリプトは `ID=linuxmint` を解釈できず失敗する。
  必ず `noble` / `ubuntu24.04` を明示指定する（`scripts/setup-host.sh` 参照）。

---

## 設計上の非交渉事項

1. **すべてのアプリは `gateway` を経由して推論する。** アプリから Ollama を直接叩かない。
2. **`gateway` は OpenAI 互換 API を公開する。** 後で vLLM に差し替えても上位層が無改修で動くこと。
3. **推論リクエストは必ず `usage_logs` に記録する。** 成功・失敗を問わず記録する。
   ログ記録の失敗が推論レスポンスを壊してはならない（記録は best-effort、例外は握って warn ログ）。
4. **認証は最初から入れる。** 利用者が1名でも省略しない。後付けは全アプリの書き直しになる。
5. **シークレットをコミットしない。** 設定はすべて環境変数、`.env` は `.gitignore` 済み。

---

## 技術スタック

- **gateway**: Python 3.12 / FastAPI / SQLAlchemy 2.x (async) / Alembic / httpx
- **DB**: PostgreSQL 16 + pgvector
- **推論**: Ollama（GPU 1台、LLM用）＋ Ollama（CPU、Embedding用）の2インスタンス
- **リバースプロキシ**: Caddy
- **実行基盤**: Docker Compose / NVIDIA Container Toolkit
- **console (Web UI)**: Phase 1（プレイグラウンド）から着手。ビルド不要の
  静的 HTML/CSS/Vanilla JS とし、`proxy`（Caddy）が `caddy/console/` を
  直接配信する（専用コンテナは立てない）。詳細は D-014 を参照。

---

## コーディング規約

- フォーマッタは `ruff format`、リンタは `ruff check`。コミット前に `make fmt` を通す。
- 型ヒントは必須。`mypy` は現状導入しないが、Pydantic モデルで境界を固める。
- DB アクセスは必ず async。同期ドライバを混ぜない。
- 例外は `app/errors.py` の独自例外に集約し、FastAPI の exception handler で JSON 化する。
- **バックエンド固有の処理は `app/backends/` にのみ書く。** ルーター層に Ollama の語彙を漏らさない。
- ログは構造化（`structlog`）。推論ログには prompt 本文を含めない（`ENABLE_PROMPT_LOGGING=false` が既定）。

---

## ディレクトリ

```
gateway/app/
  main.py          FastAPI アプリ生成、ルーター登録
  config.py        Pydantic Settings（環境変数はここだけで読む）
  db.py            エンジン・セッション
  models.py        SQLAlchemy モデル
  schemas.py       Pydantic スキーマ
  errors.py        独自例外と handler
  auth.py          パスワード検証・セッション・APIキー検証
  deps.py          FastAPI Depends（現在ユーザー取得、権限チェック）
  limits.py        同時実行数・レート制限
  backends/
    base.py        InferenceBackend 抽象基底
    ollama.py      Ollama 実装
    vllm.py        vLLM 実装（本番用、Phase 3 で追加）
  routers/
    auth.py        ログイン・ログアウト・APIキー発行
    v1.py          OpenAI 互換エンドポイント
    admin.py       ユーザー管理・モデル管理・利用状況
```

---

## コマンド

```bash
make setup       # ホスト初期セットアップ（Docker / NVIDIA Toolkit）
make up          # 起動
make down        # 停止
make logs        # 全サービスのログ追従
make pull-models # 既定モデルのダウンロード
make migrate     # Alembic マイグレーション適用
make revision m="..."  # マイグレーション生成
make fmt         # ruff format + check --fix
make test        # pytest
make smoke       # 疎通確認（GPU認識 → モデル応答 → 認証 → 推論 → ログ記録）
```

---

## 作業の進め方

1. `docs/TASKS.md` を開き、未完了の最上位タスクを1つ選ぶ。
2. 着手前に、そのタスクの「完了条件」をユーザーに読み上げて合意を取る。
3. 実装する。**1タスク＝1コミット**。関係ないリファクタを混ぜない。
4. 完了条件を満たしたか自分で検証する（`make test` と `make smoke`）。
5. `docs/TASKS.md` のチェックボックスを更新し、詰まった点があれば `docs/DECISIONS.md` に追記する。

**推測で仕様を埋めない。** 仕様が `docs/` に書かれていない判断が必要になったら、
実装を止めてユーザーに質問すること。特に認証・権限・VRAM配分に関わる判断は勝手に決めない。
