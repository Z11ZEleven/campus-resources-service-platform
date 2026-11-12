import pytest

from app.core.errors import DomainError
from app.modules.auth.permissions import Principal


def test_m1_ownership_uses_unified_permission_and_hides_other_users_records():
    from app.modules.m1.permissions import require_own

    student = Principal(1, "student", "student", "STUDENT", "CS", frozenset({"USER"}))
    require_own(student, 1, "m1:reservation:read_own")
    with pytest.raises(DomainError) as error:
        require_own(student, 2, "m1:reservation:read_own")
    assert error.value.status == 404
    admin = Principal(3, "admin", "admin", "STAFF", "OPS", frozenset({"ROOM_ADMIN"}))
    with pytest.raises(DomainError) as error:
        require_own(admin, 3, "m1:reservation:read_own")
    assert error.value.status == 403


def test_m1_student_creation_checks_selected_identity_and_department():
    from types import SimpleNamespace

    from app.modules.m1.permissions import require_student_for_room

    room = SimpleNamespace(id=1, building_id=1, department_code="CS", is_active=True)
    student = Principal(1, "student", "student", "STUDENT", "CS", frozenset({"USER"}))
    require_student_for_room(student, room)
    for kind, dept in [("TEACHER", "CS"), ("STUDENT", "EE")]:
        actor = Principal(2, "user", "user", kind, dept, frozenset({"USER"}))
        with pytest.raises(DomainError) as error:
            require_student_for_room(actor, room)
        assert error.value.status == 403
