from fastapi import APIRouter
from fastapi.responses import Response

from .. import metrics

router = APIRouter()


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    # No token: Prometheus scrapes it, and every label value is bounded and non-sensitive.
    return Response(metrics.render(), media_type=metrics.CONTENT_TYPE)
