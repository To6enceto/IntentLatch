import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal

import psycopg
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from . import db
from .errors import GatewayError

Role = Literal["viewer", "analyst", "admin"]
ROLES: tuple[Role, ...] = ("viewer", "analyst", "admin")
ROLE_RANK = {role: rank for rank, role in enumerate(ROLES)}

USERNAME_MAX = 64
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]*")
PASSWORD_MIN = 12
PASSWORD_MAX = 256

SESSION_COOKIE = "intentlatch_session"
SESSION_SECONDS = 8 * 60 * 60

# scrypt with 16 MiB of memory per hash: about 50 ms on one core.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LENGTH = 32


@dataclass(frozen=True)
class ConsoleUser:
    id: uuid.UUID
    username: str
    role: Role


def user_json(user: ConsoleUser) -> dict[str, Any]:
    return {"id": str(user.id), "username": user.username, "role": user.role}


def clean_username(value: str) -> str:
    name = value.strip()
    if not 1 <= len(name) <= USERNAME_MAX:
        raise ValueError(f"must be 1 to {USERNAME_MAX} characters after trimming")
    if not USERNAME_PATTERN.fullmatch(name):
        raise ValueError("may only contain letters, digits, '.', '_', '@' and '-', starting with a letter or digit")
    return name


def check_password(value: str) -> str:
    if not PASSWORD_MIN <= len(value) <= PASSWORD_MAX:
        raise ValueError(f"must be {PASSWORD_MIN} to {PASSWORD_MAX} characters")
    return value


def allows(role: Role, required: Role) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[required]


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LENGTH
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        expected_bytes = base64.b64decode(expected, validate=True)
        computed = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt, validate=True),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected_bytes),
        )
    except ValueError:
        return False
    return hmac.compare_digest(computed, expected_bytes)


@cache
def dummy_hash() -> str:
    # Unknown usernames are checked against this so they take as long as known ones.
    return hash_password(secrets.token_urlsafe(16))


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=PASSWORD_MAX)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


async def authenticate(pool: AsyncConnectionPool, username: str, password: str) -> ConsoleUser | None:
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            "SELECT id, username, role, password_hash FROM console_users WHERE lower(username) = lower(%s)",
            (username.strip(),),
        )
        row = await cursor.fetchone()
    stored = row[3] if row is not None else dummy_hash()
    valid = await asyncio.to_thread(verify_password, password, stored)
    if row is None or not valid:
        return None
    return ConsoleUser(row[0], row[1], row[2])


async def create_session(pool: AsyncConnectionPool, user_id: uuid.UUID) -> str:
    token = secrets.token_urlsafe(32)
    async with db.connection(pool) as conn:
        await conn.execute("DELETE FROM console_sessions WHERE expires_at <= now()")
        await conn.execute(
            "INSERT INTO console_sessions (token_hash, user_id, expires_at)"
            " VALUES (%s, %s, now() + make_interval(secs => %s))",
            (token_hash(token), user_id, SESSION_SECONDS),
        )
    return token


async def load_session(pool: AsyncConnectionPool, token: str) -> ConsoleUser | None:
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            "SELECT u.id, u.username, u.role FROM console_sessions s"
            " JOIN console_users u ON u.id = s.user_id"
            " WHERE s.token_hash = %s AND s.expires_at > now()",
            (token_hash(token),),
        )
        row = await cursor.fetchone()
    return ConsoleUser(*row) if row is not None else None


async def delete_session(pool: AsyncConnectionPool, token: str) -> None:
    async with db.connection(pool) as conn:
        await conn.execute("DELETE FROM console_sessions WHERE token_hash = %s", (token_hash(token),))


# Account management, used by the command line.


async def create_user(pool: AsyncConnectionPool, username: str, password: str, role: Role) -> ConsoleUser:
    password_hash = await asyncio.to_thread(hash_password, password)
    try:
        async with db.connection(pool) as conn:
            cursor = await conn.execute(
                "INSERT INTO console_users (username, password_hash, role) VALUES (%s, %s, %s)"
                " RETURNING id, username, role",
                (username, password_hash, role),
            )
            row = await cursor.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise GatewayError(409, "console_user_exists", "A console user with this username already exists.") from exc
    return ConsoleUser(*row)


async def list_users(pool: AsyncConnectionPool) -> list[tuple[ConsoleUser, Any]]:
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            "SELECT id, username, role, created_at FROM console_users ORDER BY lower(username)"
        )
        rows = await cursor.fetchall()
    return [(ConsoleUser(row[0], row[1], row[2]), row[3]) for row in rows]


async def set_password(pool: AsyncConnectionPool, username: str, password: str) -> bool:
    """Changes the password and ends the user's sessions. False if there is no such user."""
    password_hash = await asyncio.to_thread(hash_password, password)
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            "UPDATE console_users SET password_hash = %s WHERE lower(username) = lower(%s) RETURNING id",
            (password_hash, username),
        )
        row = await cursor.fetchone()
        if row is not None:
            await conn.execute("DELETE FROM console_sessions WHERE user_id = %s", (row[0],))
    return row is not None


async def delete_user(pool: AsyncConnectionPool, username: str) -> bool:
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            "DELETE FROM console_users WHERE lower(username) = lower(%s) RETURNING id", (username,)
        )
        return await cursor.fetchone() is not None
