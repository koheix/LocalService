# DB スキーマ

PostgreSQL 16 + pgvector。マイグレーションは Alembic。

**Phase 0 で全テーブルを作る。** 使わないカラムがあっても後で足すより安い。

```sql
CREATE TABLE users (
  id            BIGSERIAL PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE,
  display_name  TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,               -- Argon2id
  role          TEXT NOT NULL DEFAULT 'user' -- 'admin' | 'user'
                CHECK (role IN ('admin','user')),
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
  token_hash  TEXT PRIMARY KEY,
  user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at  TIMESTAMPTZ NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON sessions (user_id);
CREATE INDEX ON sessions (expires_at);

CREATE TABLE api_keys (
  id           BIGSERIAL PRIMARY KEY,
  user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  key_prefix   TEXT NOT NULL,      -- 表示用。先頭8文字
  key_hash     TEXT NOT NULL,      -- Argon2id
  last_used_at TIMESTAMPTZ,
  revoked_at   TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON api_keys (key_prefix) WHERE revoked_at IS NULL;

-- 公開名と実体の分離。これがバックエンド差し替えの要
CREATE TABLE models (
  id           BIGSERIAL PRIMARY KEY,
  served_name  TEXT NOT NULL UNIQUE,   -- クライアントに見せる名前 'chat-standard'
  backend      TEXT NOT NULL,          -- 'ollama' | 'ollama-embed' | 'vllm'
  backend_name TEXT NOT NULL,          -- 実体 'qwen3:4b'
  kind         TEXT NOT NULL           -- 'chat' | 'embedding'
               CHECK (kind IN ('chat','embedding')),
  num_ctx      INTEGER NOT NULL DEFAULT 4096,
  is_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
  description  TEXT NOT NULL DEFAULT '',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE model_permissions (
  model_id BIGINT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
  role     TEXT NOT NULL,
  PRIMARY KEY (model_id, role)
);

CREATE TABLE usage_logs (
  id                BIGSERIAL PRIMARY KEY,
  user_id           BIGINT REFERENCES users(id) ON DELETE SET NULL,
  api_key_id        BIGINT REFERENCES api_keys(id) ON DELETE SET NULL,
  model_id          BIGINT REFERENCES models(id) ON DELETE SET NULL,
  app               TEXT NOT NULL DEFAULT 'api',  -- 'playground' | 'rag' | 'api' ...
  prompt_tokens     INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  latency_ms        INTEGER NOT NULL DEFAULT 0,
  ttft_ms           INTEGER,                      -- 最初のトークンまで
  status            TEXT NOT NULL,                -- 'ok' | 'error' | 'rate_limited'
  error_code        TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON usage_logs (created_at);
CREATE INDEX ON usage_logs (user_id, created_at);

-- Phase 3 で集計に使う。定義だけ先に置く
CREATE TABLE usage_daily (
  day               DATE NOT NULL,
  user_id           BIGINT NOT NULL,
  model_id          BIGINT NOT NULL,
  request_count     INTEGER NOT NULL DEFAULT 0,
  prompt_tokens     BIGINT NOT NULL DEFAULT 0,
  completion_tokens BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (day, user_id, model_id)
);

CREATE TABLE quotas (
  user_id            BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  daily_token_limit  BIGINT,   -- NULL = 無制限
  rpm_limit          INTEGER NOT NULL DEFAULT 60,
  max_concurrent     INTEGER NOT NULL DEFAULT 2
);

-- RAG（Phase 2 で使用、定義は先に作る）
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
  id          BIGSERIAL PRIMARY KEY,
  owner_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  filename    TEXT NOT NULL,
  mime_type   TEXT NOT NULL,
  size_bytes  BIGINT NOT NULL,
  storage_path TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',  -- pending|indexing|ready|failed
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chunks (
  id          BIGSERIAL PRIMARY KEY,
  document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ordinal     INTEGER NOT NULL,
  content     TEXT NOT NULL,
  embedding   vector(1024),    -- bge-m3 は 1024 次元
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
```

## 初期データ（シード）

`scripts/seed.py` で投入する。

- 管理者ユーザー1件。パスワードは `.env` の `ADMIN_INITIAL_PASSWORD` から読む
- モデル2件:
  - `chat-standard` → `ollama` / `qwen3:4b` / `chat` / num_ctx 4096
  - `embed-standard` → `ollama-embed` / `bge-m3` / `embedding`
- 両モデルに `admin` と `user` の権限を付与

**シードは冪等にすること。** 既存レコードがあればスキップする。
