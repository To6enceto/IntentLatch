"""`intentlatch console-user`: console accounts, managed straight in the database."""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Mapping

from . import console_users, db
from .console_users import ROLES, check_password, clean_username
from .errors import GatewayError

DATABASE_URL_VAR = "INTENTLATCH_DATABASE_URL"
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


class CliError(Exception):
    pass


def prompt_password() -> str:
    password = getpass.getpass("Password: ")
    try:
        check_password(password)
    except ValueError as exc:
        raise CliError(f"Password {exc}.") from exc
    if getpass.getpass("Repeat password: ") != password:
        raise CliError("The passwords do not match.")
    return password


def username_argument(value: str) -> str:
    try:
        return clean_username(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"username {exc}") from exc


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    users = commands.add_parser("console-user", help="console accounts (there are no default accounts)")
    actions = users.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create", help="create an account; prompts for its password")
    create.add_argument("username", type=username_argument)
    create.add_argument("--role", choices=ROLES, required=True)
    actions.add_parser("list", help="list accounts")
    password = actions.add_parser("password", help="set a new password and end the account's sessions")
    password.add_argument("username")
    delete = actions.add_parser("delete", help="delete an account and end its sessions")
    delete.add_argument("username")


async def manage(args: argparse.Namespace, database_url: str, password: str | None) -> None:
    await db.migrate(database_url, db.MIGRATIONS_DIR)
    pool = await db.open_pool(database_url)
    try:
        if args.action == "create":
            user = await console_users.create_user(pool, args.username, password or "", args.role)
            print(f"Created {user.username} ({user.role}).")
        elif args.action == "list":
            for user, created_at in await console_users.list_users(pool):
                print(f"{user.username}\t{user.role}\t{created_at:%Y-%m-%d %H:%M}")
        elif args.action == "password":
            if not await console_users.set_password(pool, args.username, password or ""):
                raise CliError(f"No console user named {args.username}.")
            print(f"Password changed for {args.username}; their sessions have ended.")
        elif args.action == "delete":
            if not await console_users.delete_user(pool, args.username):
                raise CliError(f"No console user named {args.username}.")
            print(f"Deleted {args.username}.")
    finally:
        await pool.close()


def run(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    database_url = environ.get(DATABASE_URL_VAR)
    if not database_url:
        print(f"intentlatch: set {DATABASE_URL_VAR}.", file=sys.stderr)
        return EXIT_USAGE
    try:
        # Asked before connecting, so a typo in the setup never wastes a prompt.
        password = prompt_password() if args.action in ("create", "password") else None
        asyncio.run(manage(args, database_url, password))
    except (CliError, GatewayError, db.DatabaseUnavailable) as exc:
        print(f"intentlatch: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except (KeyboardInterrupt, EOFError):
        return 130
    return EXIT_OK
