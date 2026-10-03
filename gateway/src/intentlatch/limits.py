import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg_pool import AsyncConnectionPool

from . import db
from .policies import INT_MAX


@dataclass(frozen=True)
class WindowUsage:
    """A team's tokens in the current window of one size, and when that window ends."""

    used: int
    ends: datetime


def window_start(now: float, window_seconds: int) -> datetime:
    # Fixed windows aligned to multiples of their size since the epoch, so 3600 is the clock hour.
    return datetime.fromtimestamp(now // window_seconds * window_seconds, UTC)


def window_end(now: float, window_seconds: int) -> datetime:
    return window_start(now, window_seconds) + timedelta(seconds=window_seconds)


def reported(value: Any) -> int:
    # Counts arrive as upstream JSON, so anything but a plain in-range int counts 0.
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= INT_MAX:
        return value
    return 0


async def record_usage(pool: AsyncConnectionPool, team_id: uuid.UUID, tokens: int) -> None:
    async with db.connection(pool) as conn:
        await conn.execute(
            "INSERT INTO token_usage (team_id, tokens) VALUES (%s, %s)", (team_id, tokens)
        )


async def load_usage(
    pool: AsyncConnectionPool, team_id: uuid.UUID, window_sizes: set[int]
) -> dict[int, WindowUsage]:
    if not window_sizes:
        return {}
    usage = {}
    async with db.connection(pool) as conn:
        # The database clock also stamps created_at, so every replica sees the same windows.
        cursor = await conn.execute("SELECT extract(epoch FROM now())::float8")
        now = (await cursor.fetchone())[0]
        for size in sorted(window_sizes):
            cursor = await conn.execute(
                "SELECT coalesce(sum(tokens), 0)::bigint FROM token_usage"
                " WHERE team_id = %s AND created_at >= %s",
                (team_id, window_start(now, size)),
            )
            used = (await cursor.fetchone())[0]
            usage[size] = WindowUsage(used=used, ends=window_end(now, size))
    return usage
