from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .identity import Identity
    from .pipeline import Decision
    from .policies import PolicySnapshot


@dataclass
class Stage:
    """One direction's checks: what was decided, on which text, and how long it took."""

    decision: "Decision"
    texts: list[str]
    non_ai_ms: float
    agent_ms: float = 0.0
    agent_seconds: float = 0.0


@dataclass
class Trace:
    """What one chat request did, filled in while it runs and logged when it ends."""

    request_id: str
    identity: "Identity"
    model: str
    auth_ms: float = 0.0
    snapshot: "PolicySnapshot | None" = None
    prompt: Stage | None = None
    response: Stage | None = None
    upstream_ms: float = 0.0
    upstream_seconds: float = 0.0
    upstream_error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
