"""Transactional public setting access for whitelisted callers."""

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert

from app.core.time import utcnow
from app.modules.settings.models import SystemSetting


def read_int(session, key, default, *, lock=False):
    if lock:
        # First-use insert is safe under concurrent transactions and the
        # subsequent locking read observes the committed current value.
        session.execute(
            insert(SystemSetting)
            .values(key=key, value=default, version=1, updated_at=utcnow())
            .on_duplicate_key_update(key=SystemSetting.key)
        )
    query = select(SystemSetting).where(SystemSetting.key == key)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(query)
    return (row.value, row.version) if row else (default, 1)
