"""独立 D 资源网络应用：uvicorn app.resource_api:app --port 8001。"""

import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.schema import DecimalId, Input
from app.modules.auth.dependencies import Db
from app.modules.resources.commands import ResourceCommand, command_view, execute_command

app = FastAPI(title="D 资源命令服务")
bearer = HTTPBearer(auto_error=False)


class ResourceRequest(Input):
    source_id: DecimalId
    revision: int = Field(strict=True, ge=1, le=1)
    room_id: DecimalId
    applicant_id: DecimalId
    actor_id: DecimalId
    start_at: AwareDatetime
    end_at: AwareDatetime
    expires_at: AwareDatetime


def service_auth(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    key = get_settings().resource_service_key
    if key is None:
        raise DomainError(503, "SERVICE_NOT_CONFIGURED", "资源服务密钥未配置")
    if credentials is None or not secrets.compare_digest(
        credentials.credentials, key.get_secret_value()
    ):
        raise DomainError(401, "SERVICE_AUTH_REQUIRED", "资源命令需要服务身份")


@app.exception_handler(DomainError)
def error_handler(_request: Request, error: DomainError):
    return JSONResponse(
        status_code=error.status, content={"detail": {"code": error.code, "message": error.message}}
    )


@app.get("/health")
def health():
    return {"status": "ok", "component": "D-resource-service"}


@app.post("/api/v1/resource-commands/m2", dependencies=[Depends(service_auth)])
def acquire(body: ResourceRequest, session: Db):
    return command_view(execute_command(session, body))


@app.post("/api/v1/resource-commands/m2/cancel", dependencies=[Depends(service_auth)])
def cancel(body: ResourceRequest, session: Db):
    return command_view(execute_command(session, body, cancel=True))


@app.get("/api/v1/resource-commands/m2/{source_id}", dependencies=[Depends(service_auth)])
def result(source_id: int, session: Db):
    row = session.scalar(select(ResourceCommand).where(ResourceCommand.source_id == source_id))
    if row is None:
        raise DomainError(404, "NOT_FOUND", "资源命令尚无确定结果")
    return command_view(row)
