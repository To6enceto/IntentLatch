from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .. import pipeline
from ..auth import require_identity
from ..chat import Piece

router = APIRouter(dependencies=[Depends(require_identity)])


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: StrictStr
    prompt: StrictStr = Field(min_length=1)


@router.post("/check")
async def check(body: CheckRequest, request: Request) -> dict[str, Any]:
    # Runs the pipeline as the token's employee and reports; nothing is forwarded.
    request.app.state.llms.tag_for(body.model)
    # The prompt counts as one user message, so its rewrite is the returned text.
    decision = await pipeline.decide_prompt(request, body.model, [Piece(body.prompt, True)])
    return decision.model_dump()
