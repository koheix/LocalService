"""環境変数の読み込みはこのファイルだけで行う。

他のモジュールから os.environ を直接参照しないこと。
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # データベース
    database_url: str

    # 推論バックエンド
    llm_backend: str = "ollama"  # 'ollama' | 'vllm'
    llm_backend_url: str = "http://ollama:11434"
    embed_backend_url: str = "http://ollama-embed:11434"
    backend_timeout_s: float = 300.0

    # 認証
    secret_key: str
    session_ttl_hours: int = 12
    cookie_secure: bool = False

    # 制限
    default_rpm_limit: int = 60
    default_max_concurrent: int = 2

    # ログ
    log_level: str = "INFO"
    enable_prompt_logging: bool = False

    # 初期管理者（seed 用）
    admin_email: str = "admin@example.local"
    admin_initial_password: str = "change-me-on-first-login"

    # 既定モデル（seed 用）
    default_chat_model: str = "qwen3:4b"
    default_embed_model: str = "bge-m3"
    default_num_ctx: int = 4096

    # RAG（Phase 2）
    uploads_dir: str = "/app/uploads"
    max_upload_size_bytes: int = 20 * 1024 * 1024
    chunk_size_chars: int = 1000
    chunk_overlap_chars: int = 200
    rag_top_k: int = 4
    # コサイン距離(0=完全一致〜2=正反対)。これより遠ければ「関係ない質問」とみなしLLMを呼ばない。
    rag_distance_threshold: float = 0.6


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
