"""Background timeout scans without blocking FastAPI's event loop."""

import asyncio
import logging

from app.core.config import get_settings
from app.modules.m3.timeouts import run_timeout_scan

logger = logging.getLogger(__name__)


async def timeout_worker(stop: asyncio.Event):
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_timeout_scan)
        except Exception:
            logger.error("M3 timeout scan failed; retry next interval")
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=get_settings().m3_timeout_scan_interval_seconds,
            )
        except TimeoutError:
            pass
