"""Ollama 実装。Ollama 固有の語彙（APIパス、モデル一覧の形式など）はこのファイルにのみ書く。

Ollama は OpenAI 互換の /v1/chat/completions を提供しているため、
chat/chat_stream はペイロードをほぼそのまま中継する。
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.backends.base import BackendHealth, BackendModel


class OllamaBackend:
    def __init__(self, base_url: str, timeout_s: float = 300.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)

    async def list_models(self) -> list[BackendModel]:
        async with self._client() as client:
            tags_resp = await client.get("/api/tags")
            tags_resp.raise_for_status()
            ps_resp = await client.get("/api/ps")
            ps_resp.raise_for_status()

        loaded_names = {m["name"] for m in ps_resp.json().get("models", [])}
        return [
            BackendModel(
                name=m["name"],
                size_bytes=m.get("size"),
                loaded=m["name"] in loaded_names,
            )
            for m in tags_resp.json().get("models", [])
        ]

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as client:
            resp = await client.post("/v1/chat/completions", json={**payload, "stream": False})
            resp.raise_for_status()
            return resp.json()

    async def chat_stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        async with (
            self._client() as client,
            client.stream("POST", "/v1/chat/completions", json={**payload, "stream": True}) as resp,
        ):
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                yield chunk

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        async with self._client() as client:
            resp = await client.post("/api/embed", json={"model": model, "input": texts})
            resp.raise_for_status()
            return resp.json()["embeddings"]

    async def health(self) -> BackendHealth:
        try:
            async with self._client() as client:
                resp = await client.get("/api/tags")
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            return BackendHealth(ok=False, url=self._base_url, detail=str(exc))

        loaded = [m["name"] for m in data.get("models", [])]
        return BackendHealth(ok=True, url=self._base_url, loaded_models=loaded)

    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        async with (
            self._client() as client,
            client.stream("POST", "/api/pull", json={"model": model}) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    yield json.loads(line)
