import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import db, upstream

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    database_ok, upstream_ok = await asyncio.gather(
        db.ping(request.app.state.pool), upstream.ping(request.app.state.ollama)
    )
    body = {
        "status": "ok" if database_ok else "unavailable",
        "database": "ok" if database_ok else "unavailable",
        "upstream": "ok" if upstream_ok else "unreachable",
    }
    return JSONResponse(body, status_code=200 if database_ok else 503)
