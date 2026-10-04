import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from .. import metrics, prometheus
from ..auth import require_admin
from ..prometheus import Range

router = APIRouter()
# The console's Metrics page: fixed queries against Prometheus, never PromQL from the browser.
dashboard_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    # No token: Prometheus scrapes it, and every label value is bounded and non-sensitive.
    return Response(metrics.render(), media_type=metrics.CONTENT_TYPE)


@dashboard_router.get("/metrics")
async def dashboard(request: Request, range: Range = Query("1h")) -> dict[str, Any]:
    client = request.app.state.prometheus
    if client is None:
        raise prometheus.unavailable("Prometheus is not configured. Set INTENTLATCH_PROMETHEUS_URL on the gateway.")
    return await prometheus.dashboard(client, range, time.time())
