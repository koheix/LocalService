---
name: dev
description: llm-console をローカルで起動する。依存関係を確認してから docker compose で gateway と各バックエンドを立ち上げる。「アプリを起動して」「動かして」「立ち上げて」と言われたときに使う。
---

# llm-console を起動する

## 1. 依存を確認する

```powershell
docker --version
docker compose version
```

`.env` が無ければ `cp .env.example .env` を提案する。**値を勝手に埋めない。**
`SECRET_KEY`・`POSTGRES_PASSWORD`・`ADMIN_INITIAL_PASSWORD` などの秘密値は
ユーザーに用意してもらうか、開発用のダミー値でよいかを確認する。

初回のみ（Docker / NVIDIA Container Toolkit が未セットアップの場合）:

```powershell
make setup
```

`scripts/setup-host.sh` はホストの設定を変更する（Docker のインストール、リポジトリ追加等）。
**実行前に何を変更するかをユーザーに伝えて確認する。**

## 2. 起動する

```powershell
make up          # docker compose up -d --build
make migrate      # Alembic マイグレーション適用
make seed        # 初回のみ。管理者ユーザーと既定モデルを投入（冪等）
make pull-models # 初回は時間がかかる旨を伝える
```

`make up` は `-d`（デタッチ）で起動するため、フォアグラウンドでブロックしない。

## 3. 開く

```powershell
Start-Process "http://localhost:8080/api/docs"
```

Console（Web UI）は Phase 2 以降。それまでは FastAPI の `/api/docs` で操作する。
ポートは `.env` の `PROXY_PORT`（既定 8080）で変わる。

## 4. 動作を確認する

起動しただけで「動いた」と報告しない。最低限これを確認する。

```powershell
make smoke
```

または個別に:
- `curl http://localhost:8080/api/health` が `200` を返す（DB・バックエンド疎通含む）
- `docker compose ps` で全サービスが起動している
- 実装が進んだ段階では、ログイン→推論→`usage_logs` への記録まで確認する

うまく動かないときは `make logs` でログを読んでから報告する。

## 5. 止め方を伝える

```powershell
make down
```

## 注意

- **`ollama` / `ollama-embed` のポートをホストに公開する設定を追加しない。**
  Ollama には利用者単位の認証機構が無く、外部公開するとアカウント管理が意味を失う
  （CLAUDE.md の絶対制約）。公開してよいのは `proxy` のみ
- `.env` に必要な値（`SECRET_KEY`、`POSTGRES_PASSWORD`、`ADMIN_INITIAL_PASSWORD` 等）が
  ダミーのままだと起動やシードが失敗することがある。無ければユーザーに用意を依頼する
- ポート 8080（`PROXY_PORT`）が使われていたら、`.env` で変えるか既存プロセスを
  止めるかを確認する
- モデルのダウンロードは named volume（`ollama-models`）に永続化される。
  `make down && make up` を繰り返しても再ダウンロードは起きない
- GPU が認識されているかは `/vram` スキルで確認できる
