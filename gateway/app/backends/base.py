"""推論バックエンドの抽象。

Ollama と vLLM の差異はこのディレクトリ配下にのみ存在すること。
ルーター層はこの Protocol の型しか知らない。
"""

from collections.abc import AsyncIterator
from typing import Any, Protocol

from pydantic import BaseModel


class BackendModel(BaseModel):
    name: str
    size_bytes: int | None = None
    loaded: bool = False


class BackendHealth(BaseModel):
    ok: bool
    url: str
    loaded_models: list[str] = []
    detail: str | None = None


class ChatUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class InferenceBackend(Protocol):
    async def list_models(self) -> list[BackendModel]: ...

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """OpenAI 形式の payload を受け、OpenAI 形式の応答を返す。"""
        ...

    async def chat_stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """SSE のバイト列を yield する。末尾の `data: [DONE]` まで含める。"""
        ...

    async def embed(self, texts: list[str], model: str) -> list[list[float]]: ...

    async def health(self) -> BackendHealth: ...

    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        """モデルのダウンロード進捗を yield する。"""
        ...
