import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import AsyncConnectionPool

from .errors import GatewayError

log = logging.getLogger("intentlatch")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# Fixed key so every replica contends for the same advisory lock.
MIGRATION_LOCK_KEY = 7311204118
CONNECT_TIMEOUT_SECONDS = 10
REQUEST_CONNECTION_TIMEOUT_SECONDS = 5
PING_TIMEOUT_SECONDS = 2


class DatabaseUnavailable(RuntimeError):
    pass


def describe_target(conninfo: str) -> str:
    try:
        parts = conninfo_to_dict(conninfo)
    except psycopg.ProgrammingError:
        return "the configured database"
    return f"{parts.get('host', 'localhost')}:{parts.get('port', 5432)}/{parts.get('dbname', '')}"


async def migrate(conninfo: str, directory: Path) -> list[str]:
    files = sorted(directory.glob("*.sql")) if directory.is_dir() else []
    try:
        conn = await psycopg.AsyncConnection.connect(
            conninfo, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS
        )
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailable(f"PostgreSQL is unreachable at {describe_target(conninfo)}") from exc

    async with conn:
        await conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cursor = await conn.execute("SELECT version FROM schema_migrations")
            done = {row[0] for row in await cursor.fetchall()}
            applied = []
            for path in files:
                if path.stem in done:
                    continue
                async with conn.transaction():
                    await conn.execute(path.read_text())
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,)
                    )
                applied.append(path.stem)
            return applied
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


async def open_pool(conninfo: str) -> AsyncConnectionPool:
    # The check discards connections the server closed (for example after a
    # PostgreSQL restart) instead of handing them to a request.
    pool = AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=10,
        open=False,
        check=AsyncConnectionPool.check_connection,
    )
    try:
        await pool.open(wait=True, timeout=CONNECT_TIMEOUT_SECONDS)
    except psycopg.OperationalError as exc:
        await pool.close()
        raise DatabaseUnavailable(f"PostgreSQL is unreachable at {describe_target(conninfo)}") from exc
    return pool


@asynccontextmanager
async def connection(pool: AsyncConnectionPool) -> AsyncIterator[psycopg.AsyncConnection]:
    try:
        async with pool.connection(timeout=REQUEST_CONNECTION_TIMEOUT_SECONDS) as conn:
            yield conn
    except psycopg.OperationalError as exc:
        raise GatewayError(503, "database_unavailable", "The database is unavailable.") from exc


async def ping(pool: AsyncConnectionPool) -> bool:
    try:
        async with asyncio.timeout(PING_TIMEOUT_SECONDS):
            async with pool.connection(timeout=PING_TIMEOUT_SECONDS) as conn:
                await conn.execute("SELECT 1")
    except (psycopg.Error, TimeoutError):
        return False
    return True
