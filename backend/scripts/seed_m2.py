"""幂等 M2 演示数据；不覆盖已有用户、角色、范围或教室配置。"""

from datetime import time

from sqlalchemy import select

from app.core.database import get_session_factory
from app.modules.auth.models import RoleScope, User, UserIdentity, UserRole
from app.modules.auth.security import passwords
from app.modules.resources.models import Room, Seat
from scripts.seed import seed


def seed_m2(session, password):
    seed(session, password)
    rooms = []
    for code, name, department in (
        ("M2-CAMPUS", "校级整室活动教室", None),
        ("M2-CS", "计算机院系整室教室", "CS"),
        ("M2-EE", "电子院系整室教室", "EE"),
    ):
        room = session.scalar(select(Room).where(Room.code == code))
        if room is None:
            room = Room(
                code=code,
                name=name,
                building_name="整室申请演示楼",
                capacity=30,
                department_code=department,
                campus_code="MAIN",
                booking_mode="WHOLE_ROOM",
                open_time=time(8),
                close_time=time(22),
                is_active=True,
                resource_version=1,
            )
            session.add(room)
            session.flush()
            for number in range(1, 4):
                session.add(
                    Seat(
                        room_id=room.id,
                        seat_no=str(number),
                        is_active=True,
                        has_fixed_socket=number == 1,
                        has_mobile_socket=number == 2,
                    )
                )
        rooms.append(room)
    for username, department, role, scopes in (
        ("dept_admin_cs", "CS", "DEPT_ADMIN", [("DEPARTMENT", "CS")]),
        ("dept_admin_ee", "EE", "DEPT_ADMIN", [("DEPARTMENT", "EE")]),
        ("campus_admin", "OPS", "CAMPUS_ADMIN", [("CAMPUS", "MAIN")]),
        ("m2_room_admin", "OPS", "ROOM_ADMIN", [("ROOM", str(room.id)) for room in rooms]),
        ("staff", "OPS", "USER", []),
    ):
        if session.scalar(select(User.id).where(User.username == username)) is not None:
            continue
        user = User(
            username=username,
            display_name={
                "dept_admin_cs": "计算机院系审批员",
                "dept_admin_ee": "电子院系审批员",
                "campus_admin": "校级审批员",
                "m2_room_admin": "M2教室审批员",
                "staff": "演示工作人员",
            }[username],
            password_hash=passwords.hash(password),
            is_active=True,
        )
        session.add(user)
        session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                identity_type="STAFF",
                identity_no="M2-" + username,
                department_code=department,
                is_active=True,
            )
        )
        session.add(UserRole(user_id=user.id, role_code=role))
        for kind, key in scopes:
            session.add(
                RoleScope(
                    user_id=user.id, role_code=role, scope_type=kind, scope_key=key, is_active=True
                )
            )
    session.flush()


def main():
    import os

    from dotenv import dotenv_values

    from app.core.config import ROOT

    values = {**dotenv_values(ROOT.parent / ".env"), **dotenv_values(ROOT / ".env"), **os.environ}
    password = values.get("SEED_USER_PASSWORD") or ""
    if len(password) < 12:
        raise SystemExit("请配置至少12位的 SEED_USER_PASSWORD")
    with get_session_factory()() as session, session.begin():
        seed_m2(session, password)
    print(
        "M2演示数据初始化完成；审批账号：dept_admin_cs、dept_admin_ee、campus_admin、m2_room_admin"
    )


if __name__ == "__main__":
    main()
