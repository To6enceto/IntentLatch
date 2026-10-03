import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response

from . import db
from .errors import error_response, install_error_handlers
from .llms import CorporateLlms
from .routes import health, ollama, openai
from .settings import load_settings

CONNECT_TIMEOUT_SECONDS = 5

log = logging.getLogger("intentlatch")


def configure_logging() -> None:
    if log.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def create_app(ollama_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = load_settings()
        applied = await db.migrate(settings.database_url, db.MIGRATIONS_DIR)
        log.info("database migrations applied: %d", len(applied))
        pool = await db.open_pool(settings.database_url)
        try:
            async with httpx.AsyncClient(
                base_url=settings.ollama_url,
                timeout=httpx.Timeout(settings.upstream_timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS),
                transport=ollama_transport,
            ) as ollama:
                app.state.settings = settings
                app.state.pool = pool
                app.state.ollama = ollama
                app.state.llms = CorporateLlms.from_settings(settings)
                app.state.started_at = int(time.time())
                yield
        finally:
            await pool.close()

    app = FastAPI(title="IntentLatch gateway", lifespan=lifespan)
    install_error_handlers(app)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error request_id=%s", request_id)
            response = error_response(500, "internal_error", "Internal error.")
        response.headers["X-Request-ID"] = request_id
        log.info(
            "%s %s %d %.1fms request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
            request_id,
        )
        return response

    app.include_router(health.router)
    app.include_router(openai.router)
    app.include_router(ollama.router)
    return app


app = create_app()
