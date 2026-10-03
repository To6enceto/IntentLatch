from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .. import pipeline
from ..auth import require_identity

router = APIRouter(dependencies=[Depends(require_identity)])


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: StrictStr
    prompt: StrictStr = Field(min_length=1)


@router.post("/check")
async def check(body: CheckRequest, request: Request) -> dict[str, Any]:
    # Runs the pipeline as the token's employee and reports; nothing is forwarded.
    request.app.state.llms.tag_for(body.model)
    decision = await pipeline.decide_prompt(request, body.model, [body.prompt])
    return decision.model_dump()
