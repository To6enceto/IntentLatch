import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .. import limits, pipeline, upstream
from ..auth import require_identity
from ..chat import ChatRequest, allowlisted, message_texts, prompt_texts

router = APIRouter(dependencies=[Depends(require_identity)])

FORWARDED_FIELDS = ("messages", "tools", "format")
FORWARDED_OPTIONS = (
    "temperature",
    "top_p",
    "top_k",
    "seed",
    "num_predict",
    "stop",
    "repeat_penalty",
    "presence_penalty",
    "frequency_penalty",
)


class OllamaChatRequest(ChatRequest):
    stream: bool = True


@router.get("/api/tags")
async def list_tags(request: Request) -> dict[str, Any]:
    listing = await upstream.request_json(request.app.state.ollama, "GET", "/api/tags")
    available = {
        entry.get("name"): entry for entry in listing.get("models") or [] if isinstance(entry, dict)
    }
    models = []
    for model_id, tag in request.app.state.llms.tags.items():
        entry = available.get(tag) or available.get(f"{tag}:latest")
        if entry is not None:
            models.append(entry | {"name": model_id, "model": model_id})
    return {"models": models}


@router.post("/api/chat")
async def chat(body: OllamaChatRequest, request: Request) -> Response:
    tag = request.app.state.llms.tag_for(body.model)
    data = body.model_dump(exclude_unset=True)
    snapshot = await pipeline.enforce_prompt_policies(
        request, body.model, prompt_texts(data["messages"])
    )
    payload = allowlisted(data, FORWARDED_FIELDS)
    options = data.get("options")
    if isinstance(options, dict):
        kept = allowlisted(options, FORWARDED_OPTIONS)
        if kept:
            payload["options"] = kept
    payload |= {"model": tag, "stream": False}
    reply = await upstream.request_json(request.app.state.ollama, "POST", "/api/chat", json=payload)
    # Counted before response policies run: a blocked answer still spent its tokens.
    await limits.record_usage(request.app.state.pool, request.state.identity.team_id, reply_usage(reply))
    pipeline.enforce_response_policies(request, snapshot, reply_texts(reply))
    reply["model"] = body.model
    if not body.stream:
        return JSONResponse(reply)
    return StreamingResponse(iter(replay_as_ndjson(reply)), media_type="application/x-ndjson")


def reply_texts(reply: dict[str, Any]) -> list[str]:
    return message_texts(reply.get("message"))


def reply_usage(reply: dict[str, Any]) -> int:
    return limits.reported(reply.get("prompt_eval_count")) + limits.reported(reply.get("eval_count"))


def replay_as_ndjson(reply: dict[str, Any]) -> list[str]:
    message = reply.get("message") or {}
    first = {
        "model": reply["model"],
        "created_at": reply.get("created_at"),
        "message": message,
        "done": False,
    }
    final = reply | {
        "message": {"role": message.get("role", "assistant"), "content": ""},
        "done": True,
    }
    return [json.dumps(first) + "\n", json.dumps(final) + "\n"]
