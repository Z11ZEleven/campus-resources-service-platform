import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.config import get_settings
from app.core.database import get_engine
from app.core.errors import DomainError
from app.modules.auth.router import router as auth_router
from app.modules.m2.router import router as m2_router
from app.modules.m3.router import router as m3_router
from app.modules.m3.worker import timeout_worker
from app.modules.notifications.router import router as notification_router
from app.modules.resources.router import router as resource_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app):
    settings = get_settings()  # fail startup on missing/unsafe configuration
    stop = asyncio.Event()
    worker = asyncio.create_task(timeout_worker(stop)) if settings.m3_timeout_scan_enabled else None
    try:
        yield
    finally:
        stop.set()
        if worker is not None:
            await worker
        get_engine().dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="校园资源服务平台", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, exc: DomainError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(
            status_code=exc.status,
            headers=headers,
            content={"detail": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(_request: Request, _exc: IntegrityError):
        return JSONResponse(
            status_code=409,
            content={"detail": {"code": "DATA_CONFLICT", "message": "数据约束冲突，请刷新后重试"}},
        )

    @app.get("/health", tags=["运行状态"])
    def health():
        return {"status": "ok", "component": "public-layer", "version": "0.1.0"}

    @app.get("/ready", tags=["运行状态"])
    def ready():
        try:
            with get_engine().connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            logger.warning("Database readiness check failed")
            return JSONResponse(status_code=503, content={"status": "database_unavailable"})
        return {"status": "ready"}

    app.include_router(auth_router)
    app.include_router(resource_router)
    app.include_router(m2_router)
    app.include_router(m3_router)
    app.include_router(notification_router)
    return app


app = create_app()
