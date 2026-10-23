import asyncio
import logging

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.time import utcnow
from app.modules.m2.dispatch import dispatch_one
from app.modules.m2.models import ACTIVE, Application, ResourceJob
from app.modules.m2.service import scan_local

logger = logging.getLogger(__name__)


def run_scan():
    counts = {"checked": 0, "expired": 0, "reminded": 0, "resources_confirmed": 0}
    # 分批扫描，但一个周期最终遍历所有活动申请；每条独立事务便于失败后重启补扫。
    last_id = 0
    while True:
        with get_session_factory()() as session:
            ids = list(
                session.scalars(
                    select(Application.id)
                    .where(
                        Application.id > last_id,
                        Application.status.in_(ACTIVE),
                    )
                    .order_by(Application.id)
                    .limit(100)
                )
            )
        if not ids:
            break
        for app_id in ids:
            with get_session_factory()() as session, session.begin():
                result = scan_local(session, app_id)
                counts["checked"] += 1
                if result in {"expired", "reminded"}:
                    counts[result] += 1
        last_id = ids[-1]
    with get_session_factory()() as session:
        jobs = list(
            session.scalars(
                select(ResourceJob.application_id)
                .where(
                    ResourceJob.status == "PENDING",
                    ResourceJob.next_attempt_at <= utcnow(),
                )
                .order_by(ResourceJob.next_attempt_at, ResourceJob.id)
                .limit(100)
            )
        )
    for app_id in jobs:
        if dispatch_one(app_id):
            counts["resources_confirmed"] += 1
    return counts


async def m2_worker(stop: asyncio.Event):
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_scan)
        except Exception:
            logger.error("M2 扫描未完成，下个周期重试")
        try:
            await asyncio.wait_for(stop.wait(), timeout=get_settings().m2_scan_interval_seconds)
        except TimeoutError:
            pass
