#!/usr/bin/env python3
"""Small admin helper: login, create a room, invite, reset a password."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def env_value(key: str) -> str:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            return value
    return ""


def request(method: str, url: str, body: dict | None = None, token: str | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        sys.exit(f"{method} {url} failed: HTTP {exc.code} {detail}")
    return json.loads(raw) if raw else {}


def login(base: str, user: str, password: str) -> str:
    result = request(
        "POST",
        f"{base}/_matrix/client/v3/login",
        {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": user},
            "password": password,
        },
    )
    token = result.get("access_token")
    if not token:
        sys.exit("login returned no access token")
    return token


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    room = sub.add_parser("room")
    room.add_argument("--admin", required=True)
    room.add_argument("--password", required=True)
    room.add_argument("--name", default="Друзья")

    invite = sub.add_parser("invite")
    invite.add_argument("--admin", required=True)
    invite.add_argument("--password", required=True)
    invite.add_argument("--room", required=True)
    invite.add_argument("--user", required=True)

    reset = sub.add_parser("reset")
    reset.add_argument("--admin", required=True)
    reset.add_argument("--password", required=True)
    reset.add_argument("--user", required=True)
    reset.add_argument("--new-password", required=True)

    args = parser.parse_args()
    server = env_value("MATRIX_SERVER_NAME")
    if not server:
        sys.exit("MATRIX_SERVER_NAME is missing")
    base = "http://127.0.0.1:8008"
    token = login(base, args.admin, args.password)

    if args.cmd == "room":
        created = request(
            "POST",
            f"{base}/_matrix/client/v3/createRoom",
            {"name": args.name, "preset": "private_chat", "visibility": "private"},
            token,
        )
        print(created.get("room_id", ""))
        return

    if args.cmd == "invite":
        user_id = args.user if args.user.startswith("@") else f"@{args.user}:{server}"
        room_id = urllib.parse.quote(args.room, safe="")
        request(
            "POST",
            f"{base}/_matrix/client/v3/rooms/{room_id}/invite",
            {"user_id": user_id},
            token,
        )
        print(f"invited {user_id} to {args.room}")
        return

    user_id = args.user if args.user.startswith("@") else f"@{args.user}:{server}"
    quoted = urllib.parse.quote(user_id, safe="")
    request(
        "POST",
        f"{base}/_synapse/admin/v1/reset_password/{quoted}",
        {"new_password": args.new_password, "logout_devices": True},
        token,
    )
    print(f"password reset for {user_id}; devices logged out")


if __name__ == "__main__":
    import urllib.parse

    main()
