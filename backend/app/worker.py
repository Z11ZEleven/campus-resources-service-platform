import argparse
import logging
import os
import signal
import socket
import threading
from collections.abc import Mapping
from datetime import timedelta

from app import models  # noqa: F401 - register every table before using sessions
from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.time import utcnow
from app.modules.outbox.handlers import Handler, PermanentOutboxError, get_handler
from app.modules.outbox.service import (
    LeaseLost,
    claim_events,
    complete_event,
    mark_manual,
    retry_event,
)

logger = logging.getLogger("app.worker")


def _backoff(attempt: int, *, base_seconds: int, max_seconds: int) -> int:
    exponent = min(max(attempt, 1), 10)
    return min(max_seconds, base_seconds * 2 ** (exponent - 1))


def _record_failure(
    factory,
    event,
    *,
    worker_id: str,
    error_code: str,
    error: str,
    manual: bool,
    retry_at,
    now,
) -> None:
    try:
        with factory() as session, session.begin():
            if manual:
                mark_manual(
                    session,
                    event_id=event.id,
                    worker_id=worker_id,
                    lease_token=event.lease_token,
                    expected_version=event.version,
                    error_code=error_code,
                    error=error,
                    now=now,
                )
            else:
                retry_event(
                    session,
                    event_id=event.id,
                    worker_id=worker_id,
                    lease_token=event.lease_token,
                    expected_version=event.version,
                    error_code=error_code,
                    error=error,
                    available_at=retry_at,
                    now=now,
                )
    except LeaseLost:
        logger.warning(
            "outbox event %s lease was lost before failure could be recorded", event.id
        )


def _process_event(
    factory,
    event,
    *,
    worker_id: str,
    handlers: Mapping[str, Handler] | None,
    retry_base_seconds: int,
    retry_max_seconds: int,
    now,
) -> None:
    try:
        handler = get_handler(event.handler, handlers)
        # Receiver effects and the completion marker commit atomically. If the
        # process dies before commit, the lease expires and the event is retried;
        # receiver-side unique keys make that retry harmless.
        with factory() as session, session.begin():
            handler(session, event.payload)
            complete_event(
                session,
                event_id=event.id,
                worker_id=worker_id,
                lease_token=event.lease_token,
                expected_version=event.version,
                now=now,
            )
    except LeaseLost:
        logger.warning("outbox event %s lease was lost during processing", event.id)
    except PermanentOutboxError as exc:
        logger.error("outbox event %s rejected permanently: %s", event.id, exc)
        _record_failure(
            factory,
            event,
            worker_id=worker_id,
            error_code="PERMANENT",
            error=str(exc),
            manual=True,
            retry_at=None,
            now=now,
        )
    except Exception as exc:
        logger.exception("outbox event %s failed transiently", event.id)
        base = now or utcnow()
        retry_at = base + timedelta(
            seconds=_backoff(
                event.attempts,
                base_seconds=retry_base_seconds,
                max_seconds=retry_max_seconds,
            )
        )
        exhausted = (
            event.max_attempts is not None and event.attempts >= event.max_attempts
        )
        _record_failure(
            factory,
            event,
            worker_id=worker_id,
            error_code="RETRY_EXHAUSTED" if exhausted else "TRANSIENT",
            error=str(exc) or exc.__class__.__name__,
            manual=exhausted,
            retry_at=retry_at,
            now=now,
        )


def run_once(
    factory,
    *,
    worker_id: str,
    batch_size: int = 10,
    lease_seconds: int = 30,
    retry_base_seconds: int = 5,
    retry_max_seconds: int = 300,
    handlers: Mapping[str, Handler] | None = None,
    now=None,
) -> int:
    """Claim and process one batch. Returns the number of claimed events."""

    with factory() as session, session.begin():
        claimed = claim_events(
            session,
            worker_id=worker_id,
            limit=batch_size,
            lease_seconds=lease_seconds,
            now=now,
        )
    for event in claimed:
        _process_event(
            factory,
            event,
            worker_id=worker_id,
            handlers=handlers,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
            now=now,
        )
    return len(claimed)


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:64]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Campus resources D outbox worker")
    parser.add_argument("--once", action="store_true", help="process one batch and exit")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--poll-interval", type=float, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--lease-seconds", type=int, default=None)
    parser.add_argument("--retry-base-seconds", type=int, default=None)
    parser.add_argument("--retry-max-seconds", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    worker_id = (args.worker_id or _default_worker_id())[:64]
    poll_interval = (
        args.poll_interval
        if args.poll_interval is not None
        else settings.outbox_poll_interval_seconds
    )
    batch_size = (
        args.batch_size if args.batch_size is not None else settings.outbox_batch_size
    )
    lease_seconds = (
        args.lease_seconds
        if args.lease_seconds is not None
        else settings.outbox_lease_seconds
    )
    retry_base_seconds = (
        args.retry_base_seconds
        if args.retry_base_seconds is not None
        else settings.outbox_retry_base_seconds
    )
    retry_max_seconds = (
        args.retry_max_seconds
        if args.retry_max_seconds is not None
        else settings.outbox_retry_max_seconds
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    factory = get_session_factory()
    stop = threading.Event()

    def request_stop(signum, _frame):
        logger.info("outbox worker received signal %s, stopping", signum)
        stop.set()

    if not args.once:
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)

    logger.info("outbox worker %s starting", worker_id)
    while not stop.is_set():
        try:
            processed = run_once(
                factory,
                worker_id=worker_id,
                batch_size=batch_size,
                lease_seconds=lease_seconds,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            )
            logger.info("outbox worker processed %s event(s)", processed)
        except Exception:
            logger.exception("outbox worker batch failed")
            if args.once:
                return 1
            stop.wait(poll_interval)
            continue
        if args.once:
            break
        if not processed:
            stop.wait(poll_interval)
    logger.info("outbox worker %s stopped", worker_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
