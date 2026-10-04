from typing import Any

import httpx

from .errors import GatewayError

PING_TIMEOUT_SECONDS = 2
MAX_UPSTREAM_MESSAGE = 300


def upstream_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:MAX_UPSTREAM_MESSAGE]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        error = error.get("message")
    return str(error if error is not None else body)[:MAX_UPSTREAM_MESSAGE]


async def request_json(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> dict:
    try:
        response = await client.request(method, path, **kwargs)
    except httpx.TimeoutException as exc:
        raise GatewayError(502, "upstream_timeout", "The corporate LLM did not answer in time.") from exc
    except httpx.TransportError as exc:
        raise GatewayError(502, "upstream_unavailable", "The corporate LLM is unreachable.") from exc
    if not response.is_success:
        raise GatewayError(
            502,
            "upstream_error",
            f"The corporate LLM returned {response.status_code}: {upstream_message(response)}",
        )
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise GatewayError(502, "upstream_error", "The corporate LLM returned an invalid response.")
    return body


def eval_seconds(reply: Any) -> float:
    """Ollama's own model time for a native chat reply, from its nanosecond durations."""
    if not isinstance(reply, dict):
        return 0.0
    total = 0
    for key in ("prompt_eval_duration", "eval_duration"):
        value = reply.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            total += value
    return total / 1e9


async def ping(client: httpx.AsyncClient) -> bool:
    try:
        response = await client.get("/api/version", timeout=PING_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return False
    return response.is_success
