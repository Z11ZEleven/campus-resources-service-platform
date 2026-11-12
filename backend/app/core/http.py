"""Server-owned correlation IDs and redacted failures; never log request bodies."""

import logging
from uuid import uuid4

from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def error_body(request_id, code, message, **extra):
    return {"detail": {"code": code, "message": message, "request_id": request_id, **extra}}


class RequestCorrelation:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = False

        async def correlated_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                message["headers"] = [*headers, (b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, correlated_send)
        except Exception as exc:
            # Exception text, tracebacks, SQL parameters and submitted values can
            # contain credentials. Record category and correlation only.
            logger.error("request_id=%s unhandled_category=%s", request_id, type(exc).__name__)
            if started:
                raise
            response = JSONResponse(
                status_code=500,
                content=error_body(
                    request_id, "INTERNAL_ERROR", "服务暂时不可用，请凭请求编号联系管理员"
                ),
            )
            await response(scope, receive, correlated_send)
