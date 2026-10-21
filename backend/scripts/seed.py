"""Run from backend: python -m scripts.seed. No schema creation here."""

import os
import sys
from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session_factory
from app.modules.auth.models import User, UserIdentity, UserRole
from app.modules.auth.security import passwords
from app.modules.resources.models import Room, Seat

ACCOUNTS = (
    ("student", "演示学生", "STUDENT", "DEMO-S001", "CS", ("USER",)),
    ("student_ee", "演示其他院系学生", "STUDENT", "DEMO-S002", "EE", ("USER",)),
    ("teacher", "演示教师", "TEACHER", "DEMO-T001", "CS", ("USER",)),
    ("room_admin", "演示教室管理员", "STAFF", "DEMO-A001", "OPS", ("ROOM_ADMIN",)),
    ("property_admin", "演示物业管理员", "STAFF", "DEMO-A002", "OPS", ("PROPERTY_ADMIN",)),
    ("repairer", "演示维修人员", "STAFF", "DEMO-R001", "OPS", ("REPAIRER",)),
)


def seed(session: Session, password: str):
    if len(password) < 12:
        raise ValueError("SEED_USER_PASSWORD must contain at least 12 characters")
    for username, name, kind, number, department, roles in ACCOUNTS:
        user = session.scalar(select(User).where(User.username == username))
        if user is not None:
            # Do not reset passwords, enable users or restore removed roles.
            continue
        user = User(
            username=username,
            display_name=name,
            password_hash=passwords.hash(password),
            is_active=True,
        )
        session.add(user)
        session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                identity_type=kind,
                identity_no=number,
                department_code=department,
                is_active=True,
            )
        )
        session.add_all(UserRole(user_id=user.id, role_code=role) for role in roles)
    for code, name, department in (
        ("B1-101", "公共自习室", None),
        ("B1-102", "EE 院系自习室", "EE"),
    ):
        room = session.scalar(select(Room).where(Room.code == code).with_for_update())
        if room is None:
            room = Room(
                code=code,
                name=name,
                building_name="演示教学楼",
                capacity=3,
                department_code=department,
                open_time=time(8),
                close_time=time(22),
                is_active=True,
                resource_version=0,
            )
            session.add(room)
            session.flush()
        changed = False
        for number in range(1, 4):
            if (
                session.scalar(
                    select(Seat.id).where(Seat.room_id == room.id, Seat.seat_no == str(number))
                )
                is None
            ):
                session.add(
                    Seat(
                        room_id=room.id,
                        seat_no=str(number),
                        is_active=True,
                        has_fixed_socket=number == 1,
                        has_mobile_socket=number == 2,
                    )
                )
                changed = True
        if changed:
            room.resource_version += 1
    session.flush()


def main():
    password = os.environ.get("SEED_USER_PASSWORD", "")
    if not password:
        # Config already reads .env, read ONLY this seed setting as well.
        from dotenv import dotenv_values

        from app.core.config import ROOT

        values = {**dotenv_values(ROOT.parent / ".env"), **dotenv_values(ROOT / ".env")}
        password = values.get("SEED_USER_PASSWORD") or ""
    if len(password) < 12:
        sys.exit("请设置至少 12 位的 SEED_USER_PASSWORD，已有用户密码不会被覆盖")
    with get_session_factory()() as session, session.begin():
        seed(session, password)
    print("演示数据已初始化；账号：" + ", ".join(item[0] for item in ACCOUNTS))


if __name__ == "__main__":
    main()
