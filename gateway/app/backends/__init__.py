"""バックエンド選択。config.LLM_BACKEND に応じて実装を切り替える。

Embedding は VRAM 配分のハード制約により、LLM_BACKEND の値に関わらず
常に Ollama（CPU側インスタンス）を使う。
"""

from functools import lru_cache

from app.backends.base import InferenceBackend
from app.backends.ollama import OllamaBackend
from app.config import get_settings


@lru_cache
def get_chat_backend() -> InferenceBackend:
    settings = get_settings()
    if settings.llm_backend == "ollama":
        return OllamaBackend(settings.llm_backend_url, timeout_s=settings.backend_timeout_s)
    raise NotImplementedError(f"未対応の LLM_BACKEND: {settings.llm_backend}")


@lru_cache
def get_embed_backend() -> InferenceBackend:
    settings = get_settings()
    return OllamaBackend(settings.embed_backend_url, timeout_s=settings.backend_timeout_s)
