"""Ollama 実装。Ollama 固有の語彙（APIパス、モデル一覧の形式など）はこのファイルにのみ書く。

Ollama は OpenAI 互換の /v1/chat/completions を提供しているため、
chat/chat_stream はペイロードをほぼそのまま中継する。

Ollama 自体のエラー(HTTPステータスエラー・接続不可)は、ルーター層に
httpx の例外を漏らさず、docs/API.md の契約どおり BackendError(502) /
BackendUnavailableError(503) に変換する。ただしストリーミングは
最初のチャンクを送出した後に発生した接続断までは変換できない
(クライアントへの応答が既に開始しているため)。
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.backends.base import BackendHealth, BackendModel
from app.errors import BackendError, BackendUnavailableError


def _raise_for_backend_status(resp: httpx.Response) -> None:
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise BackendError(
            f"バックエンドがエラーを返しました: HTTP {exc.response.status_code}"
        ) from exc


class OllamaBackend:
    def __init__(self, base_url: str, timeout_s: float = 300.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)

    async def _request(
        self, client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        try:
            resp = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise BackendUnavailableError(f"バックエンドに接続できません: {exc}") from exc
        _raise_for_backend_status(resp)
        return resp

    async def list_models(self) -> list[BackendModel]:
        async with self._client() as client:
            tags_resp = await self._request(client, "GET", "/api/tags")
            ps_resp = await self._request(client, "GET", "/api/ps")

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
            resp = await self._request(
                client, "POST", "/v1/chat/completions", json={**payload, "stream": False}
            )
            return resp.json()

    async def chat_stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        async with self._client() as client:
            try:
                async with client.stream(
                    "POST", "/v1/chat/completions", json={**payload, "stream": True}
                ) as resp:
                    _raise_for_backend_status(resp)
                    async for chunk in resp.aiter_bytes():
                        yield chunk
            except httpx.TransportError as exc:
                raise BackendUnavailableError(f"バックエンドに接続できません: {exc}") from exc

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        async with self._client() as client:
            resp = await self._request(
                client, "POST", "/api/embed", json={"model": model, "input": texts}
            )
            return resp.json()["embeddings"]

    async def health(self) -> BackendHealth:
        try:
            async with self._client() as client:
                resp = await self._request(client, "GET", "/api/tags")
                data = resp.json()
        except (BackendError, BackendUnavailableError) as exc:
            return BackendHealth(ok=False, url=self._base_url, detail=str(exc))

        loaded = [m["name"] for m in data.get("models", [])]
        return BackendHealth(ok=True, url=self._base_url, loaded_models=loaded)

    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        async with self._client() as client:
            try:
                async with client.stream("POST", "/api/pull", json={"model": model}) as resp:
                    _raise_for_backend_status(resp)
                    async for line in resp.aiter_lines():
                        if line:
                            yield json.loads(line)
            except httpx.TransportError as exc:
                raise BackendUnavailableError(f"バックエンドに接続できません: {exc}") from exc
