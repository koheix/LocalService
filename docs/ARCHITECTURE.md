# アーキテクチャ

## 階層

```
[ ブラウザ / 外部ツール ]
          │  HTTPS
     ┌────▼─────┐
     │  proxy   │  Caddy — TLS終端、/* → console、/api → gateway
     └──┬────┬──┘
        │    │
   ┌────▼──┐ │  console: React製SPA(ビルド成果物をCaddyで配信)
   │console│ │
   └───────┘ │
             │
   ┌─────────▼─────────────────────────────────┐
   │  gateway (FastAPI)                         │
   │  認証 / 権限 / 同時実行制限 / 利用ログ / 変換 │
   └──┬─────────────┬──────────────┬────────────┘
      │              │              │
 ┌────▼────┐  ┌──────▼─────┐  ┌────▼─────┐
 │ ollama  │  │ollama-embed│  │ postgres │
 │  GPU    │  │   CPU      │  │ pgvector │
 └─────────┘  └────────────┘  └──────────┘
```

内部ネットワーク `llmnet` は Docker の bridge。ホストにポートを公開するのは `proxy` のみ。

---

## 各サービス

### proxy (Caddy)

- `:8080`（開発）/ `:443`（本番）を公開
- `/api/*` → `gateway:8000`
- `/*` → `console` コンテナへ reverse_proxy（React SPA のビルド成果物を
  `console` コンテナ内の Caddy が配信。D-017。旧D-014のビルド不要静的
  配信方式から移行した）
- 社内CA or 自己署名証明書。Let's Encrypt は閉域のため使わない

### console (React SPA)

- React 18 + TypeScript + Vite + Tailwind CSS（D-017）
- `npm run build` の成果物を、`console` コンテナ内の Caddy が配信する
- ホストにポートは公開しない。`proxy` からの reverse_proxy 経由のみ
- gateway とは `/api/*` を叩くだけの関係で、`/api/v1` の OpenAI 互換契約
  や `/api/rag/*` 等の既存APIは無改修のまま利用する

### gateway (FastAPI)

本システムの唯一の入口。責務は5つ。

1. **認証** — セッションCookie（Web UI 用）と APIキー（外部ツール用）の両対応
2. **権限** — ユーザーのロールが対象モデルを使えるか判定
3. **制限** — ユーザー単位の同時実行数上限とレート制限
4. **プロキシ** — バックエンドへ転送。ストリーミングは SSE をそのまま中継
5. **記録** — `usage_logs` にトークン数・レイテンシ・ステータスを書く

### ollama (GPU)

- LLM推論専用。`--gpus all` で GPU を割り当て
- 環境変数で挙動を固定する:
  - `OLLAMA_KEEP_ALIVE=5m` — アイドル時にVRAMを解放
  - `OLLAMA_MAX_LOADED_MODELS=1` — 8GBで複数常駐は不可
  - `OLLAMA_NUM_PARALLEL=1` — 同時1名のため
- モデル本体は named volume `ollama-models` に保持（再ビルドで消さない）

### ollama-embed (CPU)

- 同じ Ollama イメージを **GPU割り当てなし**で起動するだけ。API が同一なので実装を共有できる
- `bge-m3` を使用。日本語RAGでの精度が安定している
- 1件あたり数百ms。検証用途では十分

### postgres

- PostgreSQL 16 + pgvector 拡張
- ユーザー、APIキー、モデル定義、利用ログ、RAGのチャンクとベクタを1つのDBに置く

---

## VRAM 配分（GTX 1080 / 8GB）

| 用途 | 概算 |
|---|---|
| LLM（4B クラス Q4_K_M） | 約 2.8 GB |
| KVキャッシュ（4k コンテキスト） | 約 0.8 GB |
| デスクトップ表示・ドライバ | 約 0.5 GB |
| **余裕** | **約 3.9 GB** |

- 既定モデルは 4B クラスの Q4_K_M から開始する
- 8B Q4（約5GB）も載るが、コンテキストを伸ばすと余裕がなくなる。切り替えは設定で可能にするが既定にはしない
- `num_ctx` は既定 4096。これを超える設定を管理画面から許可しない

---

## バックエンド抽象化

`app/backends/base.py` に以下の抽象基底を置き、Ollama と vLLM の差異をここだけに閉じ込める。

```python
class InferenceBackend(Protocol):
    async def list_models(self) -> list[BackendModel]: ...
    async def chat(self, req: ChatRequest) -> ChatResponse: ...
    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[bytes]: ...
    async def embed(self, texts: list[str], model: str) -> list[list[float]]: ...
    async def health(self) -> BackendHealth: ...
```

`config.py` の `LLM_BACKEND` (`ollama` | `vllm`) と `LLM_BACKEND_URL` で実装を選択する。
**ルーター層は `InferenceBackend` 型しか知らないこと。**

---

## 認証フロー

### Web UI（セッション）

```
POST /api/auth/login  {email, password}
  → Argon2 で検証
  → セッショントークンを生成、DB の sessions に保存
  → HttpOnly / SameSite=Lax / Secure Cookie で返却
```

### 外部ツール（APIキー）

```
Authorization: Bearer sk-xxxxxxxx
  → プレフィックス（先頭8文字）で api_keys を索引
  → 残りを Argon2 ハッシュ照合
  → last_used_at を更新
```

APIキーは**発行時にのみ平文を表示**し、DBにはハッシュのみ保存する。

### ロール

Phase 0 では `admin` と `user` の2つのみ。足りなくなるまで増やさない。

---

## 制限

| 制限 | 既定値 | 実装 |
|---|---|---|
| 同時実行数 | ユーザーあたり 2 | asyncio.Semaphore（Phase 0 はインメモリ） |
| レート制限 | 60 req/min | トークンバケット（Phase 0 はインメモリ） |
| 1日トークン上限 | 無制限 | `quotas` テーブルで将来有効化 |

**注意**: gateway 側で推論を直列化しないこと。Ollama / vLLM は内部でバッチ処理を行うため、
1件ずつ流すとスループットが大きく落ちる。gateway が持つのは上限のみで、順序制御はしない。

---

## 監視

Phase 0 では Prometheus / Grafana を入れない。代わりに以下を用意する。

- `GET /api/admin/health` — 各バックエンドの疎通、モデルのロード状態
- `GET /api/admin/gpu` — `nvidia-smi --query-gpu=...` の結果を JSON で返す
- `GET /api/admin/usage?from=&to=` — `usage_logs` の集計

`usage_logs` は月次で集計テーブルに畳んで明細をパージする。この仕組みは Phase 3 で入れるが、
**テーブル定義は最初から入れておく。**
