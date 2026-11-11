"""Authenticated, idempotent service-facing resource interface."""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import DomainError, conflict, forbidden, not_found
from app.core.schema import Id, Input
from app.core.time import as_utc, to_utc
from app.modules.auth.dependencies import Db, authenticate_service
from app.modules.auth.models import ServicePrincipal
from app.modules.m1.resource_impact import apply_maintenance
from app.modules.resources.models import ResourceBlock
from app.modules.resources.operation_models import ResourceOperation
from app.modules.resources.service import apply_service_source, lock_room

router = APIRouter(prefix="/api/v1/internal", tags=["服务资源接口"])


class SourceInput(Input):
    room_id: Id
    active: bool
    revision: int = Field(ge=0, le=2**64 - 1)
    start_at: datetime | None = None
    end_at: datetime | None = None
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_times(self):
        for value in (self.start_at, self.end_at):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("时间必须包含时区")
        return self


def credentials(request):
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise DomainError(401, "INVALID_SERVICE", "服务凭据无效")
    return token


def authenticated_principal(session, request):
    from app.modules.auth.sessions import credential_hash

    principal = session.scalar(
        select(ServicePrincipal).where(
            ServicePrincipal.token_hash == credential_hash(credentials(request)),
            ServicePrincipal.is_active.is_(True),
        )
    )
    if principal is None:
        raise DomainError(401, "INVALID_SERVICE", "服务凭据无效")
    return principal


def source_view(row, room, affected=0):
    return {
        "source_type": row.source_module,
        "source_id": str(row.source_id),
        "room_id": str(row.room_id),
        "status": row.status,
        "revision": str(row.revision),
        "resource_version": str(room.resource_version),
        "affected_count": affected,
        "start_at": as_utc(row.start_at).isoformat() if row.start_at else None,
        "end_at": as_utc(row.end_at).isoformat() if row.end_at else None,
    }


@router.put("/resource-holds/{source_type}/{source_id}")
def update_source(
    source_type: Literal["M2", "M3"],
    source_id: Id,
    body: SourceInput,
    request: Request,
    session: Db,
    operation_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
):
    principal = authenticate_service(session, credentials(request), source_type, body.room_id)
    if source_type == "M2":
        if (
            body.start_at is None
            or body.end_at is None
            or to_utc(body.start_at) >= to_utc(body.end_at)
        ):
            raise DomainError(422, "INVALID_PERIOD", "M2 必须提供有效的有限时段")
    elif body.start_at is not None or body.end_at is not None:
        raise DomainError(422, "INVALID_PERIOD", "M3 不接受时段")
    room = lock_room(session, body.room_id)
    # Serializes keys for the same service even when different rooms are used.
    session.scalar(
        select(ServicePrincipal.id).where(ServicePrincipal.id == principal.id).with_for_update()
    )
    session.expire_all()
    principal = authenticate_service(session, credentials(request), source_type, body.room_id)
    payload = body.model_dump(mode="json")
    payload["source_id"] = str(source_id)
    payload["source_type"] = source_type
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    prior = session.scalar(
        select(ResourceOperation)
        .where(
            ResourceOperation.service_id == principal.id,
            ResourceOperation.operation_key == operation_key,
        )
        .with_for_update()
    )
    if prior:
        if prior.request_hash != digest:
            raise conflict("操作键已用于不同请求")
        return JSONResponse(status_code=prior.status_code, content=prior.result)
    same_revision = session.scalar(
        select(ResourceOperation)
        .where(
            ResourceOperation.source_module == source_type,
            ResourceOperation.source_id == source_id,
            ResourceOperation.status_code == 200,
        )
        .order_by(ResourceOperation.id.desc())
    )
    try:
        with session.begin_nested():
            if (
                same_revision
                and same_revision.request_payload["revision"] == body.revision
                and same_revision.request_payload != payload
            ):
                raise conflict("相同版本载荷不一致")
            source, created = apply_service_source(
                session,
                room=room,
                module=source_type,
                source_id=source_id,
                active=body.active,
                revision=body.revision,
                start=to_utc(body.start_at) if body.start_at else None,
                end=to_utc(body.end_at) if body.end_at else None,
                actor=principal,
            )
            affected = (
                apply_maintenance(
                    session, room=room, effective_at=source.start_at, source_id=source_id
                )
                if source_type == "M3" and created
                else 0
            )
            result = source_view(source, room, affected)
            status = 200
    except DomainError as exc:
        status = exc.status
        result = {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "request_id": request.state.request_id,
            }
        }
    operation = ResourceOperation(
        source_module=source_type,
        source_id=source_id,
        room_id=body.room_id,
        service_id=principal.id,
        operation_key=operation_key,
        request_hash=digest,
        request_payload=payload,
        status_code=status,
        result=result,
    )
    session.add(operation)
    try:
        session.flush()
    except IntegrityError as exc:
        raise conflict("操作键并发冲突，请重试") from exc
    return JSONResponse(status_code=status, content=result)


@router.get("/resource-holds/{source_type}/{source_id}")
def get_source(source_type: Literal["M2", "M3"], source_id: Id, request: Request, session: Db):
    principal = authenticated_principal(session, request)
    if principal.module != source_type:
        raise forbidden()
    row = session.scalar(
        select(ResourceBlock).where(
            ResourceBlock.source_module == source_type, ResourceBlock.source_id == source_id
        )
    )
    if row is None:
        raise not_found()
    authenticate_service(session, credentials(request), source_type, row.room_id)
    from app.modules.resources.models import Room

    return source_view(row, session.get(Room, row.room_id))


@router.get("/resource-operations/{operation_key}")
def get_operation(operation_key: str, request: Request, session: Db):
    principal = authenticated_principal(session, request)
    operation = session.scalar(
        select(ResourceOperation).where(
            ResourceOperation.service_id == principal.id,
            ResourceOperation.operation_key == operation_key,
        )
    )
    if operation is None:
        raise not_found()
    authenticate_service(session, credentials(request), principal.module, operation.room_id)
    return JSONResponse(status_code=operation.status_code, content=operation.result)
