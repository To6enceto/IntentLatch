import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import upstream
from ..chat import ChatRequest, allowlisted

router = APIRouter()

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
async def chat(body: OllamaChatRequest, request: Request):
    tag = request.app.state.llms.tag_for(body.model)
    data = body.model_dump(exclude_unset=True)
    payload = allowlisted(data, FORWARDED_FIELDS)
    options = data.get("options")
    if isinstance(options, dict):
        kept = allowlisted(options, FORWARDED_OPTIONS)
        if kept:
            payload["options"] = kept
    payload |= {"model": tag, "stream": False}
    reply = await upstream.request_json(request.app.state.ollama, "POST", "/api/chat", json=payload)
    reply["model"] = body.model
    if not body.stream:
        return JSONResponse(reply)
    return StreamingResponse(iter(replay_as_ndjson(reply)), media_type="application/x-ndjson")


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
