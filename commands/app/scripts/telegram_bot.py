#!/usr/bin/env python3
"""Private Telegram commands for Svoi accounts and rooms.

Alert posts stay in telegram_notify.py. This process only long-polls.
Passwords are sent to a private chat. If that send fails, the new account
is deactivated and the password is not written down.
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote as urlquote

API_HOST = "api.telegram.org"
API_IP_DEFAULT = "149.154.167.220"
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 10818
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
_direct_until = 0.0

ROOT = Path(__file__).resolve().parents[1]
OFFSET = ROOT / "deploy" / "rendered" / "telegram_offset"
PENDING = ROOT / "deploy" / "rendered" / "telegram_pending_user"
CONSOLE = "svoibot"
PROTECTED = frozenset({CONSOLE, "_lk_jwt_service"})


def load_env() -> dict[str, str]:
    path = ROOT / ".env"
    data: dict[str, str] = {}
    if not path.is_file():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        data[key.strip()] = value
    return data


def api_ip(env: dict[str, str]) -> str:
    return os.environ.get("TELEGRAM_API_IP") or env.get("TELEGRAM_API_IP") or API_IP_DEFAULT


def read_secret(name: str) -> str:
    path = ROOT / "secrets" / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def homeserver_url() -> str:
    return os.environ.get("HOMESERVER_URL", "http://synapse:8008").rstrip("/")


def serve_health() -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            body = b"ok\n" if path == "/health" else b""
            self.send_response(200 if path == "/health" else 404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("0.0.0.0", HEALTH_PORT), Handler)
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
LOCALPART_RE = re.compile(r"[a-z0-9._=/-]+")
# Distinct when read aloud: no 0/O and no 1/l/I.
PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # gitleaks:allow
CYR = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

VERBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:help|start|помощь|команды)$", re.I), "help"),
    (re.compile(r"^(?:rooms|группы)$", re.I), "rooms"),
    (re.compile(r"^(?:room|группа)$", re.I), "need_room"),
    (re.compile(r"^(?:room|группа)\s+(.+)$", re.I), "room"),
    (re.compile(r"^(?:new|новый|создай|создать)$", re.I), "need_name"),
    (re.compile(r"^(?:new|новый|создай|создать)\s+(.+)$", re.I), "new"),
    (re.compile(r"^(?:reset|сбрось|сброс)\s+(\S+)$", re.I), "reset"),
    (re.compile(r"^(?:del|delete|удали|удалить)\s+совсем\s+(\S+)$", re.I), "erase"),
    (re.compile(r"^(?:del|delete|удали|удалить)\s+(\S+)$", re.I), "delete"),
    (re.compile(r"^(?:rename|назови)\s+(.+)$", re.I), "rename"),
    (re.compile(r"^(?:topic|тема)\s+(.+)$", re.I), "topic"),
    (re.compile(r"^(?:invite|пригласи|пригласить)\s+(\S+)\s+в\s+(.+)$", re.I), "invite"),
    (re.compile(r"^(?:kick|выгони|выгнать)\s+(\S+)\s+из\s+(.+)$", re.I), "kick"),
)


class Command:
    def __init__(self, name: str, args: tuple[str, ...] = ()) -> None:
        self.name = name
        self.args = args


class TelegramError(Exception):
    def __init__(self, status: int, description: str) -> None:
        super().__init__(f"telegram {status}")
        self.status = status
        self.description = description[:200]


class MatrixError(Exception):
    def __init__(self, status: int, errcode: str, error: str) -> None:
        super().__init__(f"matrix {status} {errcode} {error[:180]}")
        self.status = status
        self.errcode = errcode
        self.error = error[:300]


def log(message: str, hidden: list[str] | None = None) -> None:
    print(redact(message, hidden or ()), flush=True)


def redact(message: str, hidden: list[str] | tuple[str, ...]) -> str:
    text = message
    for item in hidden:
        if item and len(item) >= 8:
            text = text.replace(item, "•••")
    return text


def write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (value + "\n").encode())
    finally:
        os.close(fd)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def server_name(env: dict[str, str]) -> str:
    name = env.get("MATRIX_SERVER_NAME", "").strip().rstrip(".")
    if not name:
        raise RuntimeError("MATRIX_SERVER_NAME is missing")
    return name


def localpart_from_name(name: str) -> str:
    parts: list[str] = []
    for char in name.casefold():
        if char in CYR:
            parts.append(CYR[char])
        elif "a" <= char <= "z" or char.isdigit():
            parts.append(char)
        elif char in "._=/":
            parts.append(char)
        elif char in " -_'":
            parts.append("_")
    part = re.sub(r"_+", "_", "".join(parts)).strip("._")
    if not part:
        return ""
    if not part[0].isalpha():
        part = "u" + part
    return part[:64]


def strip_slash(text: str, username: str) -> str | None:
    text = " ".join(text.strip().split())
    if not text.startswith("/"):
        return text
    first, _, rest = text.partition(" ")
    body = first[1:]
    name, separator, mention = body.partition("@")
    if separator and username and mention.lower() != username.lower():
        return None
    return (name + (" " + rest if rest else "")).strip()


def parse_command(text: str, username: str = "") -> Command | None:
    flat = strip_slash(text, username)
    if not flat:
        return None
    for pattern, name in VERBS:
        match = pattern.fullmatch(flat)
        if not match:
            continue
        return Command(name, tuple(part.strip() for part in match.groups()))
    return None


def parse_new(args: str) -> tuple[str, str | None, str | None]:
    match = re.search(r"\s(?:логин|login)\s+(\S+)\s*$", args, re.I)
    if match:
        login = match.group(1).lower()
        display = args[: match.start()].strip()
        if not LOCALPART_RE.fullmatch(login):
            return "", None, "Логин — латиница, цифры и символы ._=-/"
        if not display:
            return "", None, "Напиши имя перед логином."
        return display, login, None
    display = args.strip()
    if not display:
        return "", None, "Напиши ник."
    return display, None, None


def pick_localpart(base: str, explicit: bool, lookup) -> tuple[str | None, str]:
    first_off = None
    for index in range(0, 21):
        candidate = base if index == 0 else f"{base}{index + 1}"
        info = lookup(candidate)
        if info is None:
            if first_off is not None and not explicit:
                return first_off, "reactivate"
            return candidate, "new"
        if info.get("admin"):
            if explicit:
                return None, "protected"
            continue
        if info.get("deactivated"):
            if explicit:
                return candidate, "reactivate"
            if first_off is None:
                first_off = candidate
            continue
        if explicit:
            return None, "taken"
    if first_off is not None:
        return first_off, "reactivate"
    return None, "taken"


def room_label(room: dict) -> str:
    name = (room.get("name") or "").strip()
    return name or "без имени"


def match_rooms(rooms: list[dict], query: str) -> list[dict]:
    query = query.strip()
    if not query:
        return []
    if query.startswith("!"):
        hits = []
        for room in rooms:
            room_id = room.get("room_id") or ""
            bare = room_id.split(":", 1)[0]
            if room_id == query or bare == query:
                hits.append(room)
        return hits
    folded = query.casefold()
    exact = [room for room in rooms if (room.get("name") or "").casefold() == folded]
    if exact:
        return exact
    return [room for room in rooms if folded in (room.get("name") or "").casefold()]


def split_room(rooms: list[dict], text: str) -> tuple[dict, str] | None:
    text = text.strip()
    best: tuple[int, dict, str] | None = None
    for room in rooms:
        room_id = room.get("room_id") or ""
        if room_id and (text == room_id or text.startswith(room_id + " ")):
            score = len(room_id)
            if best is None or score > best[0]:
                best = (score, room, text[len(room_id) :].strip())
        name = (room.get("name") or "").strip()
        if not name or len(text) < len(name):
            continue
        if text[: len(name)].casefold() != name.casefold():
            continue
        if len(text) != len(name) and not text[len(name)].isspace():
            continue
        score = len(name)
        if best is None or score > best[0]:
            best = (score, room, text[len(name) :].strip())
    if best is None:
        return None
    return best[1], best[2]


def clean_text(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit].strip()


def state_content(events: list[dict], event_type: str) -> dict:
    for event in events:
        if event.get("type") == event_type and event.get("state_key", "") == "":
            content = event.get("content")
            return content if isinstance(content, dict) else {}
    return {}


def help_text(server: str) -> str:
    return "\n".join(
        [
            "Пиши сюда, в личку. Пароль приходит только сюда.",
            "",
            "новый Ник",
            "создай Ник логин latin",
            "сбрось логин",
            "удали логин",
            "удали совсем логин",
            "",
            "группы",
            "группа Друзья",
            "назови Друзья Новое имя",
            "тема Друзья Текст темы",
            "пригласи логин в Друзья",
            "выгони логин из Друзья",
            "",
            "Логин собирается из ника латиницей. Если он занят, в конце будет цифра.",
            "В Element вставляй короткий логин с отдельной строки, без пробела в конце.",
            f"Сервер в Element: {server}",
            "Администраторов и svoibot я не удаляю и не сбрасываю.",
        ]
    )


def new_password() -> str:
    groups = ("".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(4)) for _ in range(4))
    return "-".join(groups)


def credentials(display: str, mxid: str, password: str, server: str, note: str) -> str:
    local = mxid.split(":", 1)[0].lstrip("@")
    lines = ["Готово."]
    if note:
        lines.append(note)
    lines.extend(
        [
            f"Имя: {display}",
            "В Element вставь только эту строку, без пробела в конце:",
            local,
            "Пароль, с дефисами, как написан:",
            password,
            f"Сервер: {server}",
        ]
    )
    return "\n".join(lines)


def human_matrix(exc: MatrixError) -> str:
    text = exc.error.lower()
    if exc.errcode in {"M_NOT_FOUND", "M_UNKNOWN"} and "user" in text:
        return "Нет такого логина."
    if "not found" in text or "unknown user" in text:
        return "Нет такого логина."
    if "already in the room" in text or "already joined" in text:
        return "Он уже в комнате."
    if "not in room" in text or "not in the room" in text:
        return "Его в комнате нет."
    if exc.errcode == "M_FORBIDDEN":
        return "Чат не дал это сделать."
    return "Чат отказал. Попробуй ещё раз."


def unchunk(data: bytes) -> bytes:
    out = bytearray()
    while data:
        line, separator, data = data.partition(b"\r\n")
        if not separator:
            break
        size_text = line.split(b";", 1)[0].strip()
        if not size_text:
            continue
        size = int(size_text, 16)
        if size == 0:
            break
        out += data[:size]
        data = data[size:]
        if data.startswith(b"\r\n"):
            data = data[2:]
    return bytes(out)


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _socks(sock: socket.socket, host: str, port: int) -> None:
    sock.sendall(b"\x05\x01\x00")
    if _read_exact(sock, 2) != b"\x05\x00":
        raise RuntimeError("proxy refused")
    host_bytes = host.encode()
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + port.to_bytes(2, "big"))
    head = _read_exact(sock, 4)
    if len(head) < 4 or head[1] != 0:
        raise RuntimeError("proxy did not open telegram")
    if head[3] == 1:
        _read_exact(sock, 6)
    elif head[3] == 4:
        _read_exact(sock, 18)
    elif head[3] == 3:
        length = _read_exact(sock, 1)
        if not length:
            raise RuntimeError("proxy did not open telegram")
        _read_exact(sock, length[0] + 2)
    else:
        raise RuntimeError("proxy did not open telegram")


def open_telegram(env: dict[str, str]) -> socket.socket:
    global _direct_until
    if time.time() >= _direct_until:
        try:
            return socket.create_connection((api_ip(env), 443), 5)
        except OSError:
            _direct_until = time.time() + 600
    sock = socket.create_connection((PROXY_HOST, PROXY_PORT), 5)
    try:
        _socks(sock, API_HOST, 443)
    except Exception:
        sock.close()
        raise
    return sock


def telegram_call(method: str, payload: dict, env: dict[str, str], timeout: int = 30) -> dict:
    token = read_secret("telegram_bot_token")
    if not token:
        raise SystemExit(0)
    body = json.dumps(payload).encode()
    path = f"/bot{token}/{method}"
    sock = open_telegram(env)
    ssock = None
    try:
        sock.settimeout(timeout)
        ssock = ssl.create_default_context().wrap_socket(sock, server_hostname=API_HOST)
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {API_HOST}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode() + body
        ssock.sendall(request)
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = ssock.recv(65536)
            if not chunk:
                break
            raw += chunk
            if len(raw) > 1024 * 1024:
                raise RuntimeError("telegram header too large")
        head, _, rest = raw.partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1", "replace").split("\r\n")
        status = int(lines[0].split(" ", 2)[1])
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        if "content-length" in headers:
            need = int(headers["content-length"])
            while len(rest) < need:
                chunk = ssock.recv(65536)
                if not chunk:
                    break
                rest += chunk
            data = rest[:need]
        elif "chunked" in headers.get("transfer-encoding", "").lower():
            while True:
                chunk = ssock.recv(65536)
                if not chunk:
                    break
                rest += chunk
            data = unchunk(rest)
        else:
            while True:
                chunk = ssock.recv(65536)
                if not chunk:
                    break
                rest += chunk
            data = rest
    finally:
        if ssock is not None:
            ssock.close()
        else:
            sock.close()
    try:
        decoded = json.loads(data.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"telegram {status} not json") from exc
    if status == 429 or not decoded.get("ok"):
        description = str(decoded.get("description") or "")
        if status == 429:
            retry = 1
            parameters = decoded.get("parameters") or {}
            try:
                retry = int(parameters.get("retry_after", 1))
            except (TypeError, ValueError):
                retry = 1
            time.sleep(min(max(retry, 1), 30))
        raise TelegramError(status, description)
    return decoded


def matrix_call(method: str, path: str, token: str | None, body: dict | None = None, timeout: int = 30) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = "Bearer " + token
    request = urllib.request.Request(homeserver_url() + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload.get("error"), str):
            payload["error"] = payload["error"][:300]
        return exc.code, payload


def registration_mac(secret: str, nonce: str, user: str, password: str, admin: bool) -> str:
    mac = hmac.new(secret.encode("utf-8"), digestmod=hashlib.sha1)
    mac.update(nonce.encode("utf-8"))
    mac.update(b"\x00")
    mac.update(user.encode("utf-8"))
    mac.update(b"\x00")
    mac.update(password.encode("utf-8"))
    mac.update(b"\x00")
    mac.update(b"admin" if admin else b"notadmin")
    return mac.hexdigest()


def login(local: str, password: str) -> str | None:
    # rc_login.address records the rejected attempt, so a tight retry loop
    # only pushes the window forward. One wait, then a single retry.
    for attempt in range(2):
        code, body = matrix_call(
            "POST",
            "/_matrix/client/v3/login",
            None,
            {
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": local},
                "password": password,
            },
        )
        token = body.get("access_token")
        if code < 300 and token:
            return str(token)
        if code == 429 and attempt == 0:
            try:
                wait = float(body.get("retry_after_ms", 1000)) / 1000
            except (TypeError, ValueError):
                wait = 5
            time.sleep(min(max(wait + 2, 2), 45))
            continue
        if code in {401, 403}:
            return None
        raise RuntimeError(f"login {code} {body.get('errcode', '')}")
    return None


def token_ok(token: str) -> bool:
    code, body = matrix_call("GET", "/_matrix/client/v3/account/whoami", token)
    return code < 300 and bool(body.get("user_id"))


def register_account(username: str, password: str, admin: bool, display: str) -> tuple[str, str]:
    shared = read_secret("registration_shared_secret")
    if not shared:
        raise RuntimeError("registration secret missing")
    code, body = matrix_call("GET", "/_synapse/admin/v1/register", None)
    nonce = body.get("nonce")
    if code >= 300 or not nonce:
        raise RuntimeError(f"nonce {code}")
    code, body = matrix_call(
        "POST",
        "/_synapse/admin/v1/register",
        None,
        {
            "nonce": nonce,
            "username": username,
            "displayname": display,
            "password": password,
            "admin": admin,
            "mac": registration_mac(shared, str(nonce), username, password, admin),
        },
    )
    if code < 300 and body.get("access_token"):
        return "created", str(body["access_token"])
    errcode = str(body.get("errcode") or "")
    if errcode in {"M_USER_IN_USE", "M_USER_ALREADY_EXISTS"}:
        return "in_use", ""
    raise RuntimeError(f"register {code} {errcode}")


def ensure_console() -> tuple[str, str]:
    path = ROOT / "secrets" / "svoibot_password"
    token_path = ROOT / "secrets" / "svoibot_access_token"
    wrote_now = False
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        password = path.read_text(encoding="utf-8").strip()
    else:
        password = new_password()
        write_secret(path, password)
        wrote_now = True
    if token_path.is_file():
        cached = token_path.read_text(encoding="utf-8").strip()
        if cached and token_ok(cached):
            return cached, password
    token = login(CONSOLE, password)
    if token:
        write_secret(token_path, token)
        return token, password
    status, registered = register_account(CONSOLE, password, True, "Свои")
    if status == "created" and registered:
        write_secret(token_path, registered)
        return registered, password
    if wrote_now:
        path.unlink(missing_ok=True)
    raise RuntimeError("svoibot exists but the saved password was rejected")


class Homeserver:
    def __init__(self, server: str, token: str) -> None:
        self.server = server
        self.token = token
        self.user_id = f"@{CONSOLE}:{server}"
        self.hidden: list[str] = [token]

    def veil(self, value: str) -> None:
        if value and value not in self.hidden:
            self.hidden.append(value)

    def mxid(self, local: str) -> str:
        text = local.strip()
        if text.startswith("@"):
            text = text[1:]
        if ":" in text:
            return "@" + text
        return f"@{text.lower()}:{self.server}"

    def localpart(self, text: str) -> str:
        raw = text.strip()
        if raw.startswith("@"):
            raw = raw[1:]
        return raw.split(":", 1)[0].lower()

    def request(self, method: str, path: str, body: dict | None = None, token: str | None = None, timeout: int = 30) -> tuple[int, dict]:
        return matrix_call(method, path, self.token if token is None else token, body, timeout)

    def need(self, code: int, body: dict) -> dict:
        if code >= 300:
            raise MatrixError(code, str(body.get("errcode") or ""), str(body.get("error") or ""))
        return body

    def get_user(self, local: str) -> dict | None:
        code, body = self.request("GET", "/_synapse/admin/v2/users/" + urlquote(self.mxid(local), safe=""))
        if code == 404:
            return None
        return self.need(code, body)

    def mutable(self, local: str) -> dict | None:
        if local in PROTECTED:
            raise RuntimeError(f"protected {local}")
        info = self.get_user(local)
        if info and info.get("admin"):
            raise RuntimeError(f"protected admin {local}")
        return info

    def put_member(self, local: str, password: str, display: str, deactivated: bool = False) -> None:
        self.mutable(local)
        self.need(
            *self.request(
                "PUT",
                "/_synapse/admin/v2/users/" + urlquote(self.mxid(local), safe=""),
                {
                    "password": password,
                    "displayname": display,
                    "admin": False,
                    "deactivated": deactivated,
                    "logout_devices": True,
                },
            )
        )

    def reset_password(self, local: str, password: str) -> None:
        self.mutable(local)
        self.need(
            *self.request(
                "POST",
                "/_synapse/admin/v1/reset_password/" + urlquote(self.mxid(local), safe=""),
                {"new_password": password, "logout_devices": True},
            )
        )

    def deactivate(self, local: str, erase: bool) -> None:
        self.mutable(local)
        self.need(
            *self.request(
                "POST",
                "/_synapse/admin/v1/deactivate/" + urlquote(self.mxid(local), safe=""),
                {"erase": erase},
                timeout=120,
            )
        )

    def list_rooms(self) -> list[dict]:
        rooms: list[dict] = []
        offset = 0
        while True:
            code, body = self.request("GET", f"/_synapse/admin/v1/rooms?from={offset}&limit=100")
            self.need(code, body)
            batch = body.get("rooms") or []
            rooms.extend(batch)
            nxt = body.get("next_batch")
            total = body.get("total_rooms", len(rooms))
            if not batch or nxt is None or len(rooms) >= total:
                break
            try:
                nxt_offset = int(nxt)
            except (TypeError, ValueError):
                break
            if nxt_offset <= offset:
                break
            offset = nxt_offset
        return rooms

    def state(self, room_id: str) -> list[dict]:
        code, body = self.request("GET", "/_synapse/admin/v1/rooms/" + urlquote(room_id, safe="") + "/state")
        return list(self.need(code, body).get("state") or [])

    def joined(self) -> set[str]:
        code, body = self.request(
            "GET",
            "/_synapse/admin/v1/users/" + urlquote(self.user_id, safe="") + "/joined_rooms",
        )
        return set(self.need(code, body).get("joined_rooms") or [])

    def ensure_power(self, room_id: str) -> None:
        content = state_content(self.state(room_id), "m.room.power_levels")
        users = content.get("users") or {}
        try:
            level = int(users.get(self.user_id, content.get("users_default", 0)) or 0)
        except (TypeError, ValueError):
            level = 0
        if level >= 100:
            return
        code, body = self.request(
            "POST",
            "/_synapse/admin/v1/rooms/" + urlquote(room_id, safe="") + "/make_room_admin",
            {"user_id": self.user_id},
        )
        self.need(code, body)

    def enter(self, room_id: str) -> None:
        # Room v12 keeps the creator's power out of content.users. Joining from
        # outside needs an existing local member to invite us and grant power.
        if room_id not in self.joined():
            self.need(
                *self.request(
                    "POST",
                    "/_synapse/admin/v1/rooms/" + urlquote(room_id, safe="") + "/make_room_admin",
                    {"user_id": self.user_id},
                )
            )
            path = "/_matrix/client/v3/rooms/" + urlquote(room_id, safe="") + "/join"
            code, body = self.request("POST", path, {})
            if code >= 300:
                time.sleep(0.5)
                code, body = self.request("POST", path, {})
            self.need(code, body)
        self.ensure_power(room_id)

    def leave(self, room_id: str) -> None:
        code, body = self.request("POST", "/_matrix/client/v3/rooms/" + urlquote(room_id, safe="") + "/leave", {})
        self.need(code, body)

    def try_leave(self, room_id: str) -> bool:
        try:
            if room_id in self.joined():
                self.leave(room_id)
            return room_id not in self.joined()
        except (MatrixError, RuntimeError, urllib.error.URLError, TimeoutError, OSError) as exc:
            log(f"leave failed {exc}", self.hidden)
            return False

    def with_control(self, room_id: str, action):
        self.enter(room_id)
        error = None
        result = None
        try:
            result = action()
        except Exception as exc:
            error = exc
        left = self.try_leave(room_id)
        if error:
            raise error
        return result, left

    def set_state(self, room_id: str, event_type: str, content: dict, token: str | None = None) -> None:
        path = "/_matrix/client/v3/rooms/" + urlquote(room_id, safe="") + "/state/" + event_type
        self.need(*self.request("PUT", path, content, token=token))

    def delete_room(self, room_id: str) -> None:
        code, body = self.request(
            "DELETE",
            "/_synapse/admin/v2/rooms/" + urlquote(room_id, safe=""),
            {"block": True, "purge": True},
            timeout=60,
        )
        delete_id = self.need(code, body).get("delete_id")
        if not delete_id:
            return
        for _ in range(60):
            code, status = self.request(
                "GET",
                "/_synapse/admin/v2/rooms/delete_status/" + urlquote(str(delete_id), safe=""),
            )
            self.need(code, status)
            if status.get("status") == "complete":
                return
            if status.get("status") == "failed":
                raise MatrixError(500, "M_UNKNOWN", "room delete failed")
            time.sleep(0.5)
        raise MatrixError(500, "M_UNKNOWN", "room delete timed out")


def save_offset(value: int) -> None:
    OFFSET.parent.mkdir(parents=True, exist_ok=True)
    temporary = OFFSET.with_suffix(".tmp")
    temporary.write_text(str(value) + "\n", encoding="utf-8")
    os.replace(temporary, OFFSET)


def discard_backlog(env: dict[str, str]) -> int:
    offset = 0
    for _ in range(20):
        payload = telegram_call(
            "getUpdates",
            {"timeout": 0, "offset": offset, "allowed_updates": ["message", "channel_post"]},
            env,
            30,
        )
        batch = payload.get("result") or []
        if not batch:
            save_offset(offset)
            return offset
        offset = int(batch[-1]["update_id"]) + 1
    save_offset(offset)
    return offset


def load_offset(env: dict[str, str]) -> int:
    if OFFSET.is_file():
        raw = OFFSET.read_text(encoding="utf-8").strip()
        if raw.isdigit():
            return int(raw)
    return discard_backlog(env)


def file_admin_ids() -> set[int]:
    path = ROOT / "secrets" / "telegram_admin_ids"
    if not path.is_file():
        return set()
    ids: set[int] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.lstrip("-").isdigit():
            ids.add(int(line))
    return ids


def admin_ids(env: dict[str, str]) -> set[int] | None:
    ids = file_admin_ids()
    chat = read_secret("telegram_chat_id")
    if not chat:
        return ids or None
    try:
        payload = telegram_call("getChatAdministrators", {"chat_id": chat}, env, 30)
    except (TelegramError, RuntimeError, TimeoutError, OSError) as exc:
        log(f"admins unavailable: {exc}")
        return ids or None
    for item in payload.get("result") or []:
        user = item.get("user") or {}
        if user.get("is_bot"):
            continue
        if item.get("status") in {"creator", "administrator"} and user.get("id") is not None:
            ids.add(int(user["id"]))
    return ids


def clear_pending(hs: Homeserver) -> bool:
    if not PENDING.is_file():
        return True
    local = PENDING.read_text(encoding="utf-8").strip().lower()
    if not local or local in PROTECTED:
        PENDING.unlink(missing_ok=True)
        return True
    info = hs.get_user(local)
    if info and not info.get("deactivated") and not info.get("admin"):
        log(f"deactivating undelivered @{local}")
        hs.deactivate(local, erase=False)
        info = hs.get_user(local)
        if info and not info.get("deactivated"):
            return False
    PENDING.unlink(missing_ok=True)
    return True


def members_of(events: list[dict]) -> list[str]:
    people = []
    for event in events:
        if event.get("type") != "m.room.member":
            continue
        content = event.get("content") or {}
        if content.get("membership") != "join":
            continue
        user_id = event.get("state_key") or ""
        display = content.get("displayname") or user_id
        people.append(f"{display} ({user_id})")
    people.sort()
    return people


def stayed_note(left: bool) -> str:
    if left:
        return "Свои на секунду заходил в комнату и вышел."
    return "Готово, но Свои остался в комнате. Напиши ещё раз ту же команду."


def perform(cmd: Command, hs: Homeserver, reply) -> None:
    if cmd.name == "help":
        reply(help_text(hs.server))
        return
    if cmd.name == "need_name":
        reply("Напиши ник: новый Ник")
        return
    if cmd.name == "need_room":
        reply("Напиши имя комнаты или: группы")
        return
    if cmd.name == "rooms":
        rooms = sorted(hs.list_rooms(), key=lambda room: room_label(room).casefold())
        if not rooms:
            reply("Комнат нет.")
            return
        lines = ["Комнаты:"]
        for room in rooms:
            count = room.get("joined_members")
            suffix = f" · {count} чел." if isinstance(count, int) else ""
            lines.append(f"{room_label(room)}{suffix}")
            lines.append(str(room.get("room_id") or ""))
        reply("\n".join(lines))
        return
    if cmd.name == "room":
        rooms = match_rooms(hs.list_rooms(), cmd.args[0])
        if not rooms:
            reply("Нет такой комнаты. Напиши: группы")
            return
        if len(rooms) > 1:
            reply(ambiguous(rooms))
            return
        events = hs.state(rooms[0]["room_id"])
        name = state_content(events, "m.room.name").get("name") or room_label(rooms[0])
        topic = state_content(events, "m.room.topic").get("topic") or "—"
        people = members_of(events)
        reply("\n".join([str(name), rooms[0]["room_id"], f"Тема: {topic}", "Внутри: " + (", ".join(people) if people else "никого")]))
        return
    if cmd.name == "new":
        handle_new(hs, cmd.args[0], reply)
        return
    if cmd.name in {"reset", "delete", "erase"}:
        handle_user_change(hs, cmd, reply)
        return
    if cmd.name in {"rename", "topic"}:
        handle_room_text(hs, cmd, reply)
        return
    if cmd.name in {"invite", "kick"}:
        handle_membership(hs, cmd, reply)
        return
    reply(help_text(hs.server))


def ambiguous(rooms: list[dict]) -> str:
    lines = ["Нашёл несколько. Напиши имя точнее или id:"]
    for room in rooms[:15]:
        lines.append(f"{room_label(room)} · {room.get('room_id')}")
    return "\n".join(lines)


def lookup_for(hs: Homeserver):
    def lookup(name: str) -> dict | None:
        if name in PROTECTED:
            return {"deactivated": False, "admin": True}
        info = hs.get_user(name)
        if info and info.get("admin"):
            return {"deactivated": False, "admin": True}
        return info

    return lookup


def handle_new(hs: Homeserver, args: str, reply) -> None:
    display, explicit, error = parse_new(args)
    if error:
        reply(error)
        return
    display = clean_text(display, 80)
    if not display:
        reply("Напиши ник.")
        return
    if explicit:
        base = explicit
    else:
        base = localpart_from_name(display)
        if not base:
            reply("Из этого ника не выходит логин. Напиши: создай Ник логин latin")
            return
    if explicit and (explicit in PROTECTED):
        reply("Этот логин служебный, я его не трогаю.")
        return
    if not clear_pending(hs):
        reply("Прошлый логин не удалось выключить, новый не создал.")
        return
    local, mode = pick_localpart(base, explicit is not None, lookup_for(hs))
    if local is None and mode == "protected":
        reply("Этот логин служебный, я его не трогаю.")
        return
    if local is None and explicit:
        reply(f"Логин {base} уже есть. Пароль не меняю. Если нужен новый: сбрось {base}")
        return
    if local is None:
        reply(f"Свободного логина рядом с {base} нет. Напиши: создай {display} логин другой")
        return
    if mode == "reactivate" and local == base:
        note = "Этот логин был выключен, включил заново."
    elif mode == "reactivate":
        note = f"Логин {base} занят, снова включил {local}."
    elif local != base:
        note = f"Логин {base} занят, поставил {local}."
    else:
        note = ""
    password = new_password()
    hs.veil(password)
    PENDING.write_text(local + "\n", encoding="utf-8")
    try:
        hs.put_member(local, password, display, deactivated=False)
        reply(credentials(display, hs.mxid(local), password, hs.server, note))
    except Exception:
        try:
            hs.deactivate(local, erase=False)
        except Exception as cleanup:
            log(f"cleanup @{local} failed {cleanup}", hs.hidden)
        else:
            PENDING.unlink(missing_ok=True)
        raise
    PENDING.unlink(missing_ok=True)
    log(f"created @{local}")


def handle_user_change(hs: Homeserver, cmd: Command, reply) -> None:
    local = hs.localpart(cmd.args[0])
    if not LOCALPART_RE.fullmatch(local):
        reply("Нужен логин латиницей, не имя.")
        return
    if local in PROTECTED:
        reply("Этот логин я не трогаю.")
        return
    info = hs.get_user(local)
    if info is None:
        reply("Нет такого логина.")
        return
    if info.get("admin"):
        reply("Этот логин я не трогаю.")
        return
    if cmd.name == "delete" and info.get("deactivated"):
        reply(f"Уже выключен. Переписка на месте. Стереть: удали совсем {local}")
        return
    if cmd.name == "reset":
        password = new_password()
        hs.veil(password)
        if info.get("deactivated"):
            hs.put_member(local, password, info.get("displayname") or local, deactivated=False)
            note = "Включил заново. Старый пароль больше не работает."
        else:
            hs.reset_password(local, password)
            note = "Старые входы закрыты."
        reply(credentials(info.get("displayname") or local, hs.mxid(local), password, hs.server, note))
        log(f"reset @{local}")
        return
    hs.deactivate(local, erase=cmd.name == "erase")
    if cmd.name == "erase":
        reply(f"Выключил {hs.mxid(local)} и стираю переписку.")
    else:
        reply(f"Выключил {hs.mxid(local)}. Переписка на месте. Вход закрыт.")
    log(f"deactivated @{local} erase={cmd.name == 'erase'}")


def one_room(hs: Homeserver, query: str) -> dict | str:
    rooms = match_rooms(hs.list_rooms(), query)
    if not rooms:
        return "Нет такой комнаты. Напиши: группы"
    if len(rooms) > 1:
        return ambiguous(rooms)
    return rooms[0]


def handle_room_text(hs: Homeserver, cmd: Command, reply) -> None:
    found = split_room(hs.list_rooms(), cmd.args[0])
    if found is None:
        reply("Не понял комнату. Напиши: группы")
        return
    room, rest = found
    if cmd.name == "rename":
        text = clean_text(rest, 80)
        if not text:
            reply("Напиши новое имя после названия комнаты.")
            return
        event_type, content, done = "m.room.name", {"name": text}, f"Назвал комнату «{text}»."
    else:
        text = clean_text(rest, 300)
        if not text:
            reply("Напиши тему после названия комнаты.")
            return
        event_type, content, done = "m.room.topic", {"topic": text}, "Тема обновлена."

    def action() -> None:
        hs.set_state(room["room_id"], event_type, content)

    _, left = hs.with_control(room["room_id"], action)
    reply(done + "\n" + stayed_note(left))
    log(f"{cmd.name} {room['room_id']}")


def handle_membership(hs: Homeserver, cmd: Command, reply) -> None:
    local = hs.localpart(cmd.args[0])
    if not LOCALPART_RE.fullmatch(local):
        reply("Нужен логин латиницей.")
        return
    if local == CONSOLE:
        reply("Служебную учётку из комнаты так не убрать.")
        return
    found = one_room(hs, cmd.args[1])
    if isinstance(found, str):
        reply(found)
        return
    target = hs.mxid(local)
    room_id = found["room_id"]

    def action() -> None:
        path = "/invite" if cmd.name == "invite" else "/kick"
        body = {"user_id": target}
        if cmd.name == "kick":
            body["reason"] = ""
        hs.need(*hs.request("POST", "/_matrix/client/v3/rooms/" + urlquote(room_id, safe="") + path, body))

    _, left = hs.with_control(room_id, action)
    if cmd.name == "invite":
        done = f"Пригласил {target} в {room_label(found)}."
    else:
        done = f"Убрал {target} из {room_label(found)}."
    reply(done + "\n" + stayed_note(left))
    log(f"{cmd.name} {local} {room_id}")


def chunks(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.splitlines():
        if size + len(line) + 1 > limit and buf:
            parts.append("\n".join(buf))
            buf = []
            size = 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        parts.append("\n".join(buf))
    return parts or [text[:limit]]


def send_text(chat_id: int, text: str, env: dict[str, str]) -> None:
    for part in chunks(text):
        telegram_call("sendMessage", {"chat_id": str(chat_id), "text": part}, env, 30)


def set_menu(env: dict[str, str]) -> None:
    telegram_call(
        "setMyCommands",
        {
            "commands": [
                {"command": "new", "description": "Новый человек: новый Ник"},
                {"command": "del", "description": "Выключить вход: удали логин"},
                {"command": "rooms", "description": "Список комнат"},
                {"command": "help", "description": "Все команды"},
            ]
        },
        env,
        30,
    )


def connect(env: dict[str, str]) -> Homeserver:
    token, password = ensure_console()
    hs = Homeserver(server_name(env), token)
    hs.veil(password)
    hs.veil(read_secret("telegram_bot_token"))
    hs.veil(read_secret("registration_shared_secret"))
    return hs


def actor(update: dict) -> tuple[dict, dict, dict] | None:
    message = update.get("message") or update.get("channel_post")
    if not isinstance(message, dict):
        return None
    user = message.get("from") if isinstance(message.get("from"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    return message, user, chat


def handle_update(update: dict, hs: Homeserver, env: dict[str, str], bot_id: int, username: str) -> None:
    found = actor(update)
    if found is None:
        return
    message, user, chat = found
    if user.get("is_bot") or user.get("id") == bot_id:
        return
    text = message.get("text") or ""
    if not str(text).strip():
        return
    private = chat.get("type") == "private"
    command = parse_command(str(text), username)
    allowed = admin_ids(env)
    user_id = user.get("id")
    if not private:
        if command is None or allowed is None or user_id not in allowed:
            return
        send_text(
            int(chat["id"]),
            "Пароль и команды только в личке. Открой @" + username + " и напиши: новый Ник",
            env,
        )
        return
    if not isinstance(user_id, int):
        return

    def reply(body: str) -> None:
        send_text(int(user_id), body, env)

    if allowed is None:
        reply("Команды сейчас выключены.")
        return
    if user_id not in allowed:
        log(f"refused telegram id {user_id}")
        reply("Нет доступа.")
        return
    if command is None:
        reply("Напиши: помощь")
        return
    log(f"cmd {command.name} from {user_id}")
    try:
        perform(command, hs, reply)
    except MatrixError as exc:
        log(f"matrix {exc.status} {exc.errcode} {exc.error}", hs.hidden)
        reply(human_matrix(exc))


def serve() -> None:
    env = load_env()
    if not read_secret("telegram_bot_token") or not read_secret("telegram_chat_id"):
        log("telegram secrets missing, commands off")
        raise SystemExit(0)
    hs = connect(env)
    telegram_call("deleteWebhook", {"drop_pending_updates": False}, env, 30)
    me = telegram_call("getMe", {}, env, 30)["result"]
    username = str(me.get("username") or "")
    bot_id = int(me["id"])
    try:
        set_menu(env)
    except (TelegramError, RuntimeError, TimeoutError, OSError) as exc:
        log(f"commands menu: {exc}", hs.hidden)
    offset = load_offset(env)
    allowed = admin_ids(env)
    log(f"listening as @{username}, admins {0 if allowed is None else len(allowed)}")
    failures: dict[int, int] = {}
    while True:
        try:
            payload = telegram_call(
                "getUpdates",
                {"offset": offset, "timeout": 50, "allowed_updates": ["message", "channel_post"]},
                env,
                70,
            )
        except (TelegramError, RuntimeError, TimeoutError, OSError) as exc:
            log(f"poll: {exc}", hs.hidden)
            time.sleep(5)
            continue
        for update in payload.get("result") or []:
            update_id = int(update["update_id"])
            try:
                handle_update(update, hs, env, bot_id, username)
            except Exception as exc:
                failures[update_id] = failures.get(update_id, 0) + 1
                log(f"update {update_id} failed {failures[update_id]}: {exc}", hs.hidden)
                if failures[update_id] < 3:
                    return
                offset = update_id + 1
                save_offset(offset)
                found = actor(update)
                if found is not None and (found[2].get("type") == "private") and isinstance(found[1].get("id"), int):
                    try:
                        send_text(int(found[1]["id"]), "Не вышло, я пропустил команду. Напиши ещё раз.", env)
                    except (TelegramError, RuntimeError, TimeoutError, OSError):
                        pass
                continue
            failures.pop(update_id, None)
            offset = update_id + 1
            save_offset(offset)


def drill(hs: Homeserver) -> None:
    local = "zdrill" + secrets.token_hex(2)
    password = new_password()
    hs.veil(password)
    room_id = ""
    try:
        status, user_token = register_account(local, password, False, "Проверка")
        if status != "created" or not user_token:
            raise RuntimeError(f"drill register {status}")
        hs.veil(user_token)
        created = hs.need(
            *hs.request(
                "POST",
                "/_matrix/client/v3/createRoom",
                {"name": "z-drill", "preset": "private_chat", "visibility": "private"},
                token=user_token,
            )
        )
        room_id = str(created.get("room_id") or "")
        if not room_id:
            raise RuntimeError("drill room has no id")
        if room_id in hs.joined():
            raise RuntimeError("svoibot was created inside the drill room")

        def action() -> None:
            hs.set_state(room_id, "m.room.name", {"name": "z-drill-2"})
            hs.set_state(room_id, "m.room.topic", {"topic": "проверка"})

        _, left = hs.with_control(room_id, action)
        if not left or room_id in hs.joined():
            raise RuntimeError("svoibot stayed in the drill room")
        events = hs.state(room_id)
        if state_content(events, "m.room.name").get("name") != "z-drill-2":
            raise RuntimeError("room rename did not stick")
        if state_content(events, "m.room.topic").get("topic") != "проверка":
            raise RuntimeError("room topic did not stick")
        log("room drill ok")
    finally:
        if room_id:
            hs.try_leave(room_id)
        if room_id:
            try:
                hs.delete_room(room_id)
            except Exception as exc:
                log(f"drill room cleanup: {exc}", hs.hidden)
        try:
            if hs.get_user(local) and not (hs.get_user(local) or {}).get("deactivated"):
                hs.deactivate(local, erase=True)
        except Exception as exc:
            log(f"drill user cleanup: {exc}", hs.hidden)
        log("user drill cleaned")


def check() -> None:
    env = load_env()
    if not read_secret("telegram_bot_token") or not read_secret("telegram_chat_id"):
        raise SystemExit("telegram secrets missing")
    hs = connect(env)
    before = hs.joined()
    me = telegram_call("getMe", {}, env, 30)["result"]
    admins = telegram_call("getChatAdministrators", {"chat_id": read_secret("telegram_chat_id")}, env, 30)
    print(f"bot @{me.get('username')}")
    shown = []
    for item in admins.get("result") or []:
        user = item.get("user") or {}
        if user.get("is_bot"):
            continue
        shown.append(f"{item.get('status')} {user.get('id')}")
    print("admins " + (", ".join(shown) if shown else "none"))
    print("rooms " + ", ".join(sorted(room_label(room) for room in hs.list_rooms())))
    drill(hs)
    extra = hs.joined() - before
    if extra:
        raise SystemExit("svoibot stayed in " + ", ".join(sorted(extra)))
    code, body = hs.request("GET", "/_synapse/admin/v2/users?from=0&limit=50&guests=false&name=" + urlquote("zdrill"))
    hs.need(code, body)
    active = [
        user.get("name")
        for user in body.get("users") or []
        if "zdrill" in str(user.get("name") or "") and not user.get("deactivated")
    ]
    if active:
        raise SystemExit("drill user still active")
    print("rooms after " + ", ".join(sorted(room_label(room) for room in hs.list_rooms())))
    print("check ok")


def self_test() -> None:
    assert localpart_from_name("Пример") == "primer"
    assert localpart_from_name("Элина") == "elina"
    assert localpart_from_name("Сергей") == "sergey"
    assert localpart_from_name("Дима") == "dima"
    assert localpart_from_name("Ёлка") == "yolka"
    assert localpart_from_name("Андрей") == "andrey"
    assert parse_command("новый Ирина").name == "new"
    assert parse_command("новый Пример").args == ("Пример",)
    display, login, error = parse_new("Дима логин dima")
    assert (display, login, error) == ("Дима", "dima", None)
    assert parse_command("удали совсем sergon").name == "erase"
    assert parse_command("удали sergon").name == "delete"
    assert parse_command("группы").name == "rooms"
    invite = parse_command("пригласи elina в Друзья дома")
    assert invite is not None and invite.args == ("elina", "Друзья дома")
    assert parse_command("/new@Other Ник", "ExampleBot") is None
    renamed = parse_command("/new@ExampleBot Пример", "examplebot")
    assert renamed is not None and renamed.args == ("Пример",)
    users = {"operator": {"deactivated": False, "admin": True}}

    def lookup(name: str):
        return users.get(name)

    assert pick_localpart("operator", False, lookup) == ("operator2", "new")
    users["operator2"] = {"deactivated": True}
    assert pick_localpart("operator", False, lookup) == ("operator2", "reactivate")
    assert pick_localpart("operator", True, lookup) == (None, "protected")
    assert pick_localpart("elina", True, lookup) == ("elina", "new")
    rooms = [
        {"room_id": "!a:s", "name": "Друзья"},
        {"room_id": "!b:s", "name": "Друзья 2"},
    ]
    room, rest = split_room(rooms, "Друзья Новое имя")
    assert room["room_id"] == "!a:s" and rest == "Новое имя"
    room, rest = split_room(rooms, "Друзья 2 Дома")
    assert room["room_id"] == "!b:s" and rest == "Дома"
    assert redact("hello SECRET-value", ["SECRET-value"]) == "hello •••"
    sample = new_password()
    groups = sample.split("-")
    assert len(groups) == 4
    assert all(len(group) == 4 and set(group) <= set(PASSWORD_ALPHABET) for group in groups)
    issued = credentials("Серж", "@serzh:example.test", sample, "example.test", "")
    issued_lines = issued.splitlines()
    assert "serzh" in issued_lines
    assert sample in issued_lines
    assert "@serzh" not in issued
    assert not any(line.endswith(" ") for line in issued_lines)
    assert "Пароль: secret" not in help_text("example.test")
    print("self-test ok")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "--self-test":
        self_test()
        return
    if command == "--check":
        check()
        return
    if command:
        raise SystemExit("usage: telegram_bot.py [--self-test|--check]")
    serve_health()
    while True:
        try:
            serve()
        except SystemExit:
            raise
        except Exception as exc:
            log(f"restarting: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
