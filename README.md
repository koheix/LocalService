# llm-console

社内設置型 GPU サーバー上で動く、ローカルLLM利用プラットフォーム。
アカウント単位でチャット・文書検索などの機能を GUI 提供する。

## セットアップ

```bash
cp .env.example .env
$EDITOR .env          # SECRET_KEY と各パスワードを必ず変更する
make setup            # Docker / NVIDIA Container Toolkit（初回のみ）
make up
make migrate
make seed
make pull-models      # 初回は時間がかかる
make smoke
```

ブラウザで `http://localhost:8080/api/docs` を開く。

## ドキュメント

| ファイル | 内容 |
|---|---|
| `CLAUDE.md` | Claude Code 向けの規約と制約。**最初に読む** |
| `docs/ARCHITECTURE.md` | 構成、VRAM配分、認証フロー |
| `docs/API.md` | エンドポイント仕様 |
| `docs/SCHEMA.md` | DB スキーマ |
| `docs/TASKS.md` | タスク分解と完了条件 |
| `docs/DECISIONS.md` | 設計判断の記録 |

## 現在のフェーズ

Phase 0（基盤構築）。検証機は GTX 1080 / VRAM 8GB のため推論バックエンドは Ollama。
本番機では vLLM に切り替える。詳細は `docs/DECISIONS.md` の D-001 を参照。
