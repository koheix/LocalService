"""OllamaBackend のエラー変換(502/503)を検証する。ネットワーク・実Ollama不要。"""

import httpx
import pytest

from app.backends.ollama import OllamaBackend
from app.errors import BackendError, BackendUnavailableError


def _backend(handler) -> OllamaBackend:
    backend = OllamaBackend("http://fake-ollama:11434")
    transport = httpx.MockTransport(handler)
    backend._client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        base_url=backend._base_url, transport=transport
    )
    return backend


async def test_chat_success_passthrough() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    backend = _backend(handler)
    result = await backend.chat({"model": "m", "messages": []})
    assert result["choices"][0]["message"]["content"] == "hi"


async def test_chat_http_error_becomes_backend_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    backend = _backend(handler)
    with pytest.raises(BackendError) as exc_info:
        await backend.chat({"model": "m", "messages": []})
    assert exc_info.value.status_code == 502


async def test_chat_connection_error_becomes_backend_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    backend = _backend(handler)
    with pytest.raises(BackendUnavailableError) as exc_info:
        await backend.chat({"model": "m", "messages": []})
    assert exc_info.value.status_code == 503


async def test_chat_stream_connection_error_becomes_backend_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    backend = _backend(handler)
    with pytest.raises(BackendUnavailableError):
        async for _ in backend.chat_stream({"model": "m", "messages": []}):
            pass


async def test_chat_stream_http_error_becomes_backend_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="model not loaded")

    backend = _backend(handler)
    with pytest.raises(BackendError):
        async for _ in backend.chat_stream({"model": "m", "messages": []}):
            pass


async def test_embed_connection_error_becomes_backend_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    backend = _backend(handler)
    with pytest.raises(BackendUnavailableError):
        await backend.embed(["hi"], "m")


async def test_health_reports_not_ok_on_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    backend = _backend(handler)
    health = await backend.health()
    assert health.ok is False
    assert "接続できません" in (health.detail or "")


async def test_health_reports_not_ok_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    backend = _backend(handler)
    health = await backend.health()
    assert health.ok is False
