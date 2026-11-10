"""Logins for portals, kept in the system keychain (macOS Keychain), never in code or config.

    uv run python -m sdfetch.credentials set portal.example.gov     # prompts for both
    uv run python -m sdfetch.credentials show portal.example.gov    # the username only
    uv run python -m sdfetch.credentials delete portal.example.gov

    from sdfetch import credentials
    username, password = credentials.get("portal.example.gov")

Needs the `keychain` extra (keyring). Entries are stored under the service name
"sdfetch:<name>".
"""

from __future__ import annotations

import argparse
import contextlib
import getpass

PREFIX = "sdfetch:"
USERNAME_KEY = "__username__"  # where each service's username is kept


class NoCredentials(LookupError):
    pass


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise NoCredentials("keyring isn't installed (sdfetch[keychain])") from exc
    return keyring


def set(name: str, username: str, password: str) -> None:  # noqa: A001
    kr = _keyring()
    kr.set_password(PREFIX + name, USERNAME_KEY, username)
    kr.set_password(PREFIX + name, username, password)


def get(name: str) -> tuple[str, str]:
    """(username, password) for name; raises NoCredentials if there aren't any."""
    kr = _keyring()
    username = kr.get_password(PREFIX + name, USERNAME_KEY)
    password = kr.get_password(PREFIX + name, username) if username else None
    if not username or password is None:
        raise NoCredentials(
            f"no login stored for {name!r}; run: python -m sdfetch.credentials set {name}"
        )
    return username, password


def delete(name: str) -> None:
    kr = _keyring()
    username = kr.get_password(PREFIX + name, USERNAME_KEY)
    for account in filter(None, [username, USERNAME_KEY]):
        with contextlib.suppress(Exception):  # already gone
            kr.delete_password(PREFIX + name, account)


def main() -> None:
    parser = argparse.ArgumentParser(description="Store portal logins in the system keychain")
    parser.add_argument("action", choices=["set", "show", "delete"])
    parser.add_argument("name", help="usually the portal's host name")
    args = parser.parse_args()
    if args.action == "set":
        username = input("username: ")
        set(args.name, username, getpass.getpass("password: "))
        print(f"stored {args.name}")
    elif args.action == "show":
        print(get(args.name)[0])
    else:
        delete(args.name)
        print(f"deleted {args.name}")


if __name__ == "__main__":
    main()
