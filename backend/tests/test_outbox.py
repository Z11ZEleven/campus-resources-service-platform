import threading
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.errors import DomainError
from app.core.time import to_utc, utcnow
from app.modules.audit.models import OperationLog
from app.modules.audit.service import record_operation
from app.modules.auth.models import ServicePrincipal
from app.modules.m1.integrations import audit_reservation, notify_reservation
from app.modules.m1.models import Reservation
from app.modules.notifications.models import Notification
from app.modules.outbox.handlers import deliver_notification, receive_audit
from app.modules.outbox.models import OutboxEvent
from app.modules.outbox.service import (
    LeaseLost,
    claim_events,
    complete_event,
    list_events,
    list_failures,
    mark_manual,
    register_event,
    retry_event,
)
from app.worker import run_once


def _key(prefix="event"):
    return f"{prefix}:{uuid4()}"


def _notification_payload(user_id, key, **overrides):
    payload = {
        "recipients": [user_id],
        "source_module": "M1",
        "business_id": 1,
        "event_key": key,
        "title": "预约提醒",
        "content": "您的自习座位预约即将开始。",
        "link": "/m1/reservations/1",
    }
    payload.update(overrides)
    return payload


def _audit_payload(key, **overrides):
    payload = {
        "module": "M1",
        "business_id": 7,
        "action": "NO_SHOW",
        "actor_type": "SYSTEM",
        "from_status": "RESERVED",
        "to_status": "NO_SHOW",
        "note": "未签到",
        "result": "SUCCESS",
        "event_key": key,
    }
    payload.update(overrides)
    return payload


def _register_notification(session, user_id, *, key=None, max_attempts=8, **payload_overrides):
    key = key or _key("notification")
    return register_event(
        session,
        source_module="M1",
        event_key=key,
        handler="notification",
        business_id=1,
        payload=_notification_payload(user_id, key, **payload_overrides),
        max_attempts=max_attempts,
    )


def _reservation(session, data):
    principal = data["users"]["student"]
    reservation = Reservation(
        user_id=principal.user_id,
        identity_id=principal.identity_id,
        room_id=data["room"],
        seat_id=data["seats"][0],
        start_at=to_utc(data["start"]),
        end_at=to_utc(data["end"]),
        status="RESERVED",
        settings_snapshot={"max_hours": 4},
        resource_snapshot={"seat_no": "1"},
        create_request_key=f"test-{uuid4()}",
        request_hash="a" * 64,
    )
    session.add(reservation)
    session.flush()
    return reservation


def test_registration_is_idempotent_and_rejects_content_changes(factory):
    key = _key()
    with factory() as session, session.begin():
        first = register_event(
            session,
            source_module="M1",
            event_key=key,
            handler="notification",
            business_id=1,
            payload={"value": 1},
        )
        second = register_event(
            session,
            source_module="M1",
            event_key=key,
            handler="notification",
            business_id=1,
            payload={"value": 1},
        )
        assert first.id == second.id
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1

    with factory() as session, session.begin():
        with pytest.raises(DomainError) as caught:
            register_event(
                session,
                source_module="M1",
                event_key=key,
                handler="notification",
                business_id=1,
                payload={"value": 2},
            )
        assert caught.value.status == 409
        assert caught.value.code == "CONFLICT"

    with factory() as session, session.begin():
        with pytest.raises(DomainError) as caught:
            register_event(
                session,
                source_module="M1",
                event_key=key,
                handler="notification",
                business_id=1,
                business_version=2,
                payload={"value": 1},
            )
        assert caught.value.status == 409


def test_registration_participates_in_calling_transaction(factory):
    key = _key()
    with factory() as session:
        transaction = session.begin()
        register_event(
            session,
            source_module="M1",
            event_key=key,
            handler="notification",
            business_id=1,
            payload={"value": 1},
        )
        transaction.rollback()

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0

    with factory() as session:
        with pytest.raises(RuntimeError):
            register_event(
                session,
                source_module="M1",
                event_key=key,
                handler="notification",
                business_id=1,
                payload={"value": 1},
            )


def test_claim_recovers_expired_lease_and_protects_version(factory):
    key = _key()
    with factory() as session, session.begin():
        event = register_event(
            session,
            source_module="M1",
            event_key=key,
            handler="notification",
            business_id=1,
            payload={"value": 1},
        )
        event_id = event.id

    base = utcnow()
    with factory() as session, session.begin():
        first = claim_events(
            session, worker_id="worker-a", limit=1, lease_seconds=30, now=base
        )
        assert [item.id for item in first] == [event_id]
        assert first[0].attempts == 1
        assert first[0].lease_token
        assert claim_events(
            session, worker_id="worker-b", limit=1, lease_seconds=30, now=base
        ) == []

    later = base + timedelta(seconds=31)
    with factory() as session, session.begin():
        second = claim_events(
            session, worker_id="worker-b", limit=1, lease_seconds=30, now=later
        )
        assert [item.id for item in second] == [event_id]
        assert second[0].lease_token != first[0].lease_token
        assert second[0].attempts == 2

    with factory() as session, session.begin():
        with pytest.raises(LeaseLost):
            complete_event(
                session,
                event_id=event_id,
                worker_id="worker-a",
                lease_token=first[0].lease_token,
                expected_version=first[0].version,
                now=later,
            )

    with factory() as session, session.begin():
        with pytest.raises(LeaseLost):
            complete_event(
                session,
                event_id=event_id,
                worker_id="worker-b",
                lease_token=second[0].lease_token,
                expected_version=second[0].version - 1,
                now=later,
            )

    with factory() as session, session.begin():
        complete_event(
            session,
            event_id=event_id,
            worker_id="worker-b",
            lease_token=second[0].lease_token,
            expected_version=second[0].version,
            now=later,
        )

    with factory() as session:
        row = session.get(OutboxEvent, event_id)
        assert row.status == "SUCCEEDED"
        assert row.attempts == 2
        assert row.lease_token is None
        assert row.completed_at is not None

    with factory() as session, session.begin():
        with pytest.raises(LeaseLost):
            complete_event(
                session,
                event_id=event_id,
                worker_id="worker-b",
                lease_token=second[0].lease_token,
                expected_version=second[0].version,
                now=later,
            )


def test_retry_delay_and_manual_failure_query(factory):
    key = _key()
    with factory() as session, session.begin():
        event = register_event(
            session,
            source_module="M1",
            event_key=key,
            handler="notification",
            business_id=1,
            payload={"value": 1},
            max_attempts=3,
        )
        event_id = event.id

    base = utcnow()
    with factory() as session, session.begin():
        first = claim_events(
            session, worker_id="worker-a", limit=1, lease_seconds=30, now=base
        )[0]

    retry_at = base + timedelta(seconds=10)
    with factory() as session, session.begin():
        retry_event(
            session,
            event_id=event_id,
            worker_id="worker-a",
            lease_token=first.lease_token,
            expected_version=first.version,
            error_code="TRANSIENT",
            error="temporary database timeout",
            available_at=retry_at,
            now=base,
        )

    with factory() as session, session.begin():
        assert (
            claim_events(
                session,
                worker_id="worker-b",
                limit=1,
                lease_seconds=30,
                now=retry_at - timedelta(seconds=1),
            )
            == []
        )

    with factory() as session, session.begin():
        second = claim_events(
            session, worker_id="worker-b", limit=1, lease_seconds=30, now=retry_at
        )[0]
        assert second.attempts == 2

    with factory() as session, session.begin():
        mark_manual(
            session,
            event_id=event_id,
            worker_id="worker-b",
            lease_token=second.lease_token,
            expected_version=second.version,
            error_code="PERMANENT",
            error="manual inspection required",
            now=retry_at,
        )

    with factory() as session:
        failures = list_failures(session)
        assert [row.id for row in failures] == [event_id]
        assert failures[0].status == "MANUAL"
        assert failures[0].error_code == "PERMANENT"
        assert [row.id for row in list_events(session, status="MANUAL")] == [event_id]


def test_notification_receiver_is_idempotent_when_response_is_lost(factory, data):
    key = _key("lost-response")
    user_id = data["users"]["student"].user_id
    with factory() as session, session.begin():
        _register_notification(session, user_id, key=key)

    # The receiver commits, but the worker never records completion.
    with factory() as session, session.begin():
        payload = session.scalar(
            select(OutboxEvent.payload).where(OutboxEvent.event_key == key)
        )
        deliver_notification(session, payload)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Notification)) == 1

    assert run_once(factory, worker_id="worker-a", batch_size=5, lease_seconds=30) == 1

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Notification)) == 1
        notification = session.scalar(select(Notification))
        assert notification.recipient_id == user_id
        assert notification.event_key == f"M1:{key}"
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.event_key == key))
        assert event.status == "SUCCEEDED"


def test_audit_receiver_is_idempotent_and_uses_system_actor(factory):
    key = _key("audit")
    with factory() as session, session.begin():
        register_event(
            session,
            source_module="M1",
            event_key=key,
            handler="audit",
            business_id=7,
            payload=_audit_payload(key),
        )

    # Receiver commits, completion is lost, and the worker delivers again.
    with factory() as session, session.begin():
        payload = session.scalar(
            select(OutboxEvent.payload).where(OutboxEvent.event_key == key)
        )
        receive_audit(session, payload)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(OperationLog)) == 1

    assert run_once(factory, worker_id="worker-a", batch_size=5, lease_seconds=30) == 1

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(OperationLog)) == 1
        log = session.scalar(select(OperationLog))
        assert log.actor_type == "SYSTEM"
        assert log.actor_id is None
        assert log.service_principal_id is None
        assert log.result == "SUCCESS"
        assert log.event_key == key
        assert log.created_at.tzinfo is None
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.event_key == key))
        assert event.status == "SUCCEEDED"
        assert event.error_code is None


def test_audit_supports_service_principal_without_reusing_user_identity(factory):
    with factory() as session, session.begin():
        principal = ServicePrincipal(
            name=f"service-{uuid4()}",
            token_hash=uuid4().hex + uuid4().hex,
            module="M1",
            is_active=True,
        )
        session.add(principal)
        session.flush()
        row = record_operation(
            session,
            module="M1",
            business_id=11,
            action="REPAIR_SYNC",
            actor_type="SERVICE",
            service_principal_id=principal.id,
        )
        assert row.actor_id is None
        assert row.service_principal_id == principal.id
        assert row.actor_type == "SERVICE"

        with pytest.raises(DomainError) as caught:
            record_operation(
                session,
                module="M1",
                business_id=11,
                action="BROKEN",
                actor_type="USER",
            )
        assert caught.value.status == 422


def test_worker_recovers_crashed_lease_and_delivers_real_notification(factory, data):
    key = _key("crash")
    user_id = data["users"]["student"].user_id
    with factory() as session, session.begin():
        event = _register_notification(session, user_id, key=key)
        event_id = event.id

    with factory() as session, session.begin():
        claim_events(session, worker_id="crashed-worker", limit=1, lease_seconds=30)

    with factory() as session, session.begin():
        event = session.get(OutboxEvent, event_id)
        event.lease_expires_at = utcnow() - timedelta(seconds=1)

    assert run_once(factory, worker_id="recovery-worker", batch_size=5, lease_seconds=30) == 1

    with factory() as session:
        event = session.get(OutboxEvent, event_id)
        assert event.status == "SUCCEEDED"
        assert event.attempts == 2
        assert session.scalar(select(func.count()).select_from(Notification)) == 1


def test_worker_marks_unknown_handler_and_permanent_rejection_for_manual_handling(factory, data):
    unknown_key = _key("unknown")
    permanent_key = _key("permanent")
    user_id = data["users"]["student"].user_id
    with factory() as session, session.begin():
        register_event(
            session,
            source_module="M1",
            event_key=unknown_key,
            handler="missing-handler",
            business_id=1,
            payload={"value": 1},
        )
        _register_notification(
            session, user_id, key=permanent_key, link="//unsafe"
        )

    assert run_once(factory, worker_id="worker-a", batch_size=5, lease_seconds=30) == 2

    with factory() as session:
        unknown = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_key == unknown_key)
        )
        permanent = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_key == permanent_key)
        )
        assert unknown.status == "MANUAL"
        assert unknown.error_code == "PERMANENT"
        assert "handler" in unknown.last_error
        assert permanent.status == "MANUAL"
        assert permanent.error_code == "PERMANENT"
        assert session.scalar(select(func.count()).select_from(Notification)) == 0
        assert {row.event_key for row in list_failures(session)} == {
            unknown_key,
            permanent_key,
        }


def test_worker_stops_retrying_when_event_attempt_limit_is_reached(factory):
    key = _key("limited")

    def always_fails(session, payload):
        raise RuntimeError("database is temporarily unavailable")

    with factory() as session, session.begin():
        register_event(
            session,
            source_module="M1",
            event_key=key,
            handler="failing",
            business_id=1,
            payload={},
            max_attempts=1,
        )

    assert (
        run_once(
            factory,
            worker_id="worker-a",
            batch_size=5,
            lease_seconds=30,
            handlers={"failing": always_fails},
        )
        == 1
    )

    with factory() as session:
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.event_key == key))
        assert event.status == "MANUAL"
        assert event.attempts == 1
        assert event.error_code == "RETRY_EXHAUSTED"


def test_worker_retries_with_backoff_then_succeeds(factory):
    key = _key("backoff")
    base = utcnow()
    calls = []

    def flaky(session, payload):
        calls.append(payload)
        if len(calls) == 1:
            raise RuntimeError("temporary")
        return None

    with factory() as session, session.begin():
        register_event(
            session,
            source_module="M1",
            event_key=key,
            handler="flaky",
            business_id=1,
            payload={"value": 1},
            available_at=base,
            max_attempts=3,
        )

    assert (
        run_once(
            factory,
            worker_id="worker-a",
            batch_size=5,
            lease_seconds=30,
            retry_base_seconds=5,
            handlers={"flaky": flaky},
            now=base,
        )
        == 1
    )
    with factory() as session:
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.event_key == key))
        assert event.status == "PENDING"
        assert event.error_code == "TRANSIENT"
        assert event.available_at == base + timedelta(seconds=5)

    assert (
        run_once(
            factory,
            worker_id="worker-a",
            batch_size=5,
            lease_seconds=30,
            handlers={"flaky": flaky},
            now=base + timedelta(seconds=4),
        )
        == 0
    )
    assert (
        run_once(
            factory,
            worker_id="worker-a",
            batch_size=5,
            lease_seconds=30,
            handlers={"flaky": flaky},
            now=base + timedelta(seconds=5),
        )
        == 1
    )
    with factory() as session:
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.event_key == key))
        assert event.status == "SUCCEEDED"
        assert event.attempts == 2
    assert calls == [{"value": 1}, {"value": 1}]


def test_concurrent_workers_claim_distinct_events(factory):
    keys = [_key("concurrent-a"), _key("concurrent-b")]
    event_ids = []
    with factory() as session, session.begin():
        for key in keys:
            event = register_event(
                session,
                source_module="M1",
                event_key=key,
                handler="missing-handler",
                business_id=1,
                payload={"value": key},
            )
            event_ids.append(event.id)

    barrier = threading.Barrier(2)
    claimed = {}
    errors = []

    def claim(worker_id):
        try:
            with factory() as session, session.begin():
                barrier.wait(timeout=10)
                items = claim_events(
                    session, worker_id=worker_id, limit=1, lease_seconds=30
                )
                barrier.wait(timeout=10)
                claimed[worker_id] = [item.id for item in items]
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [
        threading.Thread(target=claim, args=("worker-a",)),
        threading.Thread(target=claim, args=("worker-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert errors == []
    assert sorted(claimed["worker-a"] + claimed["worker-b"]) == sorted(event_ids)
    with factory() as session:
        rows = list(session.scalars(select(OutboxEvent)))
        assert {row.status for row in rows} == {"PROCESSING"}
        assert len({row.lease_token for row in rows}) == 2


def test_m1_integration_registers_business_events_in_the_same_transaction(factory, data):
    with factory() as session, session.begin():
        reservation = _reservation(session, data)
        reservation_id = reservation.id
        principal = data["users"]["student"]
        notify_reservation(
            session,
            reservation,
            event_suffix="confirmed",
            recipients=[principal.user_id],
            title="预约成功",
            content="您的预约已确认。",
            link=f"/m1/reservations/{reservation_id}",
        )
        audit_reservation(
            session,
            reservation,
            event_suffix="confirmed",
            action="CREATE",
            actor_type="SYSTEM",
            to_status="RESERVED",
        )

    with factory() as session:
        events = {
            row.handler: row
            for row in session.scalars(
                select(OutboxEvent).order_by(OutboxEvent.id)
            )
        }
        assert events["notification"].business_id == reservation_id
        assert events["notification"].max_attempts == 6
        assert events["audit"].business_id == reservation_id
        assert events["audit"].max_attempts == 6

    assert run_once(factory, worker_id="worker-a", batch_size=10, lease_seconds=30) == 2

    with factory() as session:
        notification = session.scalar(select(Notification))
        assert notification.recipient_id == principal.user_id
        assert notification.business_id == reservation_id
        log = session.scalar(select(OperationLog))
        assert log.module == "M1"
        assert log.business_id == reservation_id
        assert log.action == "CREATE"
        assert log.actor_type == "SYSTEM"


def test_m1_integration_events_roll_back_with_the_business_transaction(factory, data):
    with factory() as session:
        transaction = session.begin()
        reservation = _reservation(session, data)
        principal = data["users"]["student"]
        notify_reservation(
            session,
            reservation,
            event_suffix="confirmed",
            recipients=[principal.user_id],
            title="预约成功",
            content="您的预约已确认。",
            link=f"/m1/reservations/{reservation.id}",
        )
        audit_reservation(
            session,
            reservation,
            event_suffix="confirmed",
            action="CREATE",
            actor_type="SYSTEM",
            to_status="RESERVED",
        )
        transaction.rollback()

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert session.scalar(select(func.count()).select_from(Reservation)) == 0
