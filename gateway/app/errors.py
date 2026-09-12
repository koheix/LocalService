"""独自例外と FastAPI ハンドラ。

エラー応答の形式は docs/API.md に従う。OpenAI 互換エンドポイントも同形式。
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code: int = 500
    error_type: str = "internal_error"
    code: str = "INTERNAL"

    def __init__(self, message: str, *, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.headers = headers or {}


class AuthenticationError(AppError):
    status_code = 401
    error_type = "authentication_error"
    code = "UNAUTHENTICATED"


class PermissionError_(AppError):
    status_code = 403
    error_type = "permission_error"
    code = "FORBIDDEN"


class NotFoundError(AppError):
    status_code = 404
    error_type = "not_found"
    code = "NOT_FOUND"


class RateLimitError(AppError):
    status_code = 429
    error_type = "rate_limit_exceeded"
    code = "RATE_LIMIT"


class BackendError(AppError):
    status_code = 502
    error_type = "backend_error"
    code = "BACKEND_ERROR"


class BackendUnavailableError(AppError):
    status_code = 503
    error_type = "backend_unavailable"
    code = "BACKEND_UNAVAILABLE"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "error": {
                    "type": exc.error_type,
                    "message": exc.message,
                    "code": exc.code,
                }
            },
        )
