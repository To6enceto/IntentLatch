import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response

from . import db, decision_log, metrics, policies, testcases
from .errors import GatewayError, error_response, install_error_handlers
from .llms import CorporateLlms
from .routes import admin, authority, check, console, health, ollama, openai
from .routes import metrics as metrics_route
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


async def record_request(request: Request, response: Response, started: float) -> Response:
    # The trace is complete here: the endpoint has built its response, buffered streams included.
    trace = getattr(request.state, "trace", None)
    if trace is None or trace.prompt is None:
        return response
    total_ms = round((time.perf_counter() - started) * 1000, 3)
    metrics.observe(trace, total_ms)
    try:
        await decision_log.write(request.app.state.pool, trace, total_ms)
    except GatewayError as exc:
        # A decision that cannot be recorded does not go out.
        log.error("decision log write failed request_id=%s code=%s", trace.request_id, exc.code)
        return error_response(exc.status_code, exc.code, exc.message)
    except Exception:
        log.exception("decision log write failed request_id=%s", trace.request_id)
        return error_response(500, "internal_error", "Internal error.")
    return response


def create_app(
    ollama_transport: httpx.AsyncBaseTransport | None = None,
    control_agent_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = load_settings()
        seeds = policies.load_seeds()
        case_seeds = testcases.load_seeds()
        applied = await db.migrate(settings.database_url, db.MIGRATIONS_DIR)
        log.info("database migrations applied: %d", len(applied))
        pool = await db.open_pool(settings.database_url)
        prometheus = (
            httpx.AsyncClient(base_url=settings.prometheus_url) if settings.prometheus_url else None
        )
        try:
            seeded = await policies.seed_policies(pool, seeds)
            log.info("policy seeds inserted: %d", seeded)
            seeded = await testcases.seed_cases(pool, case_seeds)
            log.info("test case seeds inserted: %d", seeded)
            timeout = httpx.Timeout(settings.upstream_timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS)
            async with (
                httpx.AsyncClient(
                    base_url=settings.ollama_url, timeout=timeout, transport=ollama_transport
                ) as ollama,
                httpx.AsyncClient(
                    base_url=settings.control_agent_url, timeout=timeout, transport=control_agent_transport
                ) as control_agent,
            ):
                app.state.settings = settings
                app.state.pool = pool
                app.state.ollama = ollama
                app.state.control_agent = control_agent
                app.state.llms = CorporateLlms.from_settings(settings)
                app.state.started_at = int(time.time())
                app.state.prometheus = prometheus
                # Sets intentlatch_policies_active before the first request.
                metrics.set_active_policies(await policies.load_snapshot(pool))
                yield
        finally:
            if prometheus is not None:
                await prometheus.aclose()
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
        response = await record_request(request, response, started)
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
    app.include_router(admin.router)
    app.include_router(console.router)
    app.include_router(authority.router)
    app.include_router(check.router)
    app.include_router(metrics_route.router)
    app.include_router(metrics_route.dashboard_router)
    return app


app = create_app()
