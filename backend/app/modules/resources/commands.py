"""D 资源命令账本。只读取公共模型，不读取任何 M2 业务表。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, select
from sqlalchemy.dialects.mysql import BIGINT, DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.errors import DomainError, conflict
from app.core.time import to_utc, utcnow
from app.modules.resources.service import expire_m2_source, lock_room, occupy_room


class ResourceCommand(Base):
    __tablename__ = "d_resource_commands"
    __table_args__ = (
        UniqueConstraint("source_id", name="uq_resource_command_source"),
        CheckConstraint(
            "status IN ('PROCESSING','SUCCEEDED','REJECTED','CANCELLED')",
            name="ck_resource_command_status",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    source_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    revision: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    room_id: Mapped[int] = mapped_column(ForeignKey("d_rooms.id"))
    applicant_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    start_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    end_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    status: Mapped[str] = mapped_column(String(16))
    resource_hold_id: Mapped[int | None] = mapped_column(ForeignKey("d_resource_blocks.id"))
    reason_code: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


def execute_command(session, body, *, cancel=False):
    from app.modules.auth.permissions import load_principal

    room = lock_room(session, body.room_id)
    payload = {
        name: getattr(body, name)
        for name in (
            "source_id",
            "revision",
            "room_id",
            "applicant_id",
            "actor_id",
        )
    }
    payload.update(
        {name: to_utc(getattr(body, name)) for name in ("start_at", "end_at", "expires_at")}
    )
    row = session.scalar(
        select(ResourceCommand)
        .where(ResourceCommand.source_id == body.source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is not None and any(getattr(row, key) != value for key, value in payload.items()):
        raise conflict("资源命令来源已用于不同内容")
    if cancel:
        if utcnow() < payload["expires_at"]:
            raise conflict("未到截止时间，不能自动撤销资源命令")
        if row is None:
            # 墓碑与房间锁使延迟到达的创建请求不能复活已撤销申请。
            row = ResourceCommand(
                **payload, status="CANCELLED", reason_code="EXPIRED", reason="申请已超时撤销"
            )
            session.add(row)
        elif row.status == "SUCCEEDED":
            expire_m2_source(session, room_id=row.room_id, source_id=row.source_id)
            row.status, row.reason_code, row.reason = "CANCELLED", "EXPIRED", "申请已超时撤销"
        elif row.status != "CANCELLED":
            row.status, row.reason_code, row.reason = "CANCELLED", "EXPIRED", "申请已超时撤销"
        session.flush()
        return row
    if row is not None:
        return row
    row = ResourceCommand(**payload, status="PROCESSING")
    session.add(row)
    session.flush()
    # 只有写来源之前的确定业务拒绝会变为持久化失败；数据库异常整笔回滚。
    try:
        if utcnow() >= row.expires_at:
            raise DomainError(409, "EXPIRED", "申请审批期限或使用时间已到")
        if room.booking_mode != "WHOLE_ROOM":
            raise DomainError(422, "ROOM_MODE", "该教室不是仅整室预约教室")
        actor = load_principal(session, row.actor_id)
        from app.modules.auth.scopes import approval_requirement, require_scope

        require_scope(session, actor, approval_requirement(room, 2))
        hold = occupy_room(
            session,
            room_id=row.room_id,
            application_id=row.source_id,
            applicant_id=row.applicant_id,
            start_at=body.start_at,
            end_at=body.end_at,
            actor=actor,
        )
    except DomainError as error:
        row.status, row.reason_code, row.reason = "REJECTED", error.code, error.message
    else:
        row.status, row.resource_hold_id = "SUCCEEDED", hold.id
    session.flush()
    return row


def command_view(row):
    from app.core.time import as_utc

    return {
        "source_module": "M2",
        "source_id": str(row.source_id),
        "revision": row.revision,
        "room_id": str(row.room_id),
        "start_at": as_utc(row.start_at).isoformat().replace("+00:00", "Z"),
        "end_at": as_utc(row.end_at).isoformat().replace("+00:00", "Z"),
        "status": row.status,
        "resource_hold_id": str(row.resource_hold_id) if row.resource_hold_id else None,
        "reason_code": row.reason_code,
        "reason": row.reason,
    }
