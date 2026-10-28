"""Telegram plate bot. A repeat of a known plate does not open the site."""

from __future__ import annotations

import http.server
import json
import os
import socket
import sqlite3
import ssl
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import browser
import card
import plate
import store

ROOT = Path(__file__).resolve().parent
TOKEN_PATH = ROOT / "secrets" / "telegram_bot_token"
OFFSET_PATH = ROOT / "offset"
DB_PATH = ROOT / "history.sqlite"
API_HOST = "api.telegram.org"
API_IP = "149.154.167.220"
PROXY_PORT = 10818
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
MOSCOW = ZoneInfo("Europe/Moscow")
_direct_until = 0.0
_xray: subprocess.Popen[bytes] | None = None
HELP = (
    "Пришли госномер.\n"
    "Подойдут Н401ВР70, н 401 вр 70 и H401BP70.\n"
    "История покажет прошлые запросы.\n"
    "Обнови Н401ВР70 спросит сайт ещё раз."
)


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


def token() -> str:
    if not TOKEN_PATH.is_file():
        raise SystemExit(0)
    value = TOKEN_PATH.read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit(0)
    return value


def _unchunk(data: bytes) -> bytes:
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


def _proxy_up() -> bool:
    try:
        probe = socket.create_connection(("127.0.0.1", PROXY_PORT), 0.3)
    except OSError:
        return False
    probe.close()
    return True


def ensure_proxy() -> None:
    global _xray
    if _proxy_up():
        return
    binary = ROOT / "bin" / "xray"
    config = ROOT / "secrets" / "xray.json"
    if not binary.is_file() or not config.is_file():
        return
    _xray = subprocess.Popen(
        [str(binary), "run", "-c", str(config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 4
    while time.time() < deadline:
        if _proxy_up():
            return
        if _xray.poll() is not None:
            return
        time.sleep(0.1)


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


def _open_api() -> socket.socket:
    global _direct_until
    if time.time() >= _direct_until:
        try:
            return socket.create_connection((API_IP, 443), 3)
        except OSError:
            _direct_until = time.time() + 600
    ensure_proxy()
    sock = socket.create_connection(("127.0.0.1", PROXY_PORT), 5)
    try:
        _socks(sock, API_HOST, 443)
    except Exception:
        sock.close()
        raise
    return sock


def telegram(method: str, payload: dict, timeout: int = 30) -> dict:
    secret = token()
    body = json.dumps(payload, ensure_ascii=False).encode()
    path = f"/bot{secret}/{method}"
    sock = _open_api()
    ssock = None
    try:
        sock.settimeout(timeout)
        ssock = ssl.create_default_context().wrap_socket(sock, server_hostname=API_HOST)
        ssock.sendall(
            (
                f"POST {path} HTTP/1.1\r\n"
                f"Host: {API_HOST}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            + body
        )
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = ssock.recv(65536)
            if not chunk:
                break
            raw += chunk
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
            data = _unchunk(rest)
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
        raise RuntimeError(f"telegram {status}") from exc
    if not decoded.get("ok"):
        description = str(decoded.get("description") or status)
        raise RuntimeError(description.replace(secret, "…"))
    return decoded


def send(chat_id: int, text: str) -> None:
    telegram("sendMessage", {"chat_id": chat_id, "text": text[:4000]})


def typing(chat_id: int) -> None:
    try:
        telegram("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
    except RuntimeError:
        return


def load_offset() -> int | None:
    if not OFFSET_PATH.is_file():
        return None
    raw = OFFSET_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return int(raw)


def save_offset(value: int) -> None:
    OFFSET_PATH.write_text(str(value), encoding="utf-8")


def stamp(moment: int) -> str:
    return datetime.fromtimestamp(moment, MOSCOW).strftime("%d.%m %H:%M")


def _head(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped:
        return "", ""
    first, _, rest = stripped.partition(" ")
    token = first.casefold()
    if token.startswith("/") and "@" in token:
        token = token.split("@", 1)[0]
    return token, rest.strip()


def format_history(rows: list[tuple[str, str, int]]) -> str:
    if not rows:
        return "Пока пусто. Пришли госномер."
    lines = ["Последние запросы:"]
    for index, (number, title, asked) in enumerate(rows, start=1):
        lines.append(f"{index}. {plate.show(number)} — {title} — {stamp(asked)}")
    return "\n".join(lines)


_fetches: dict[int, list[float]] = {}


def _may_fetch(user_id: int) -> bool:
    now = time.time()
    recent = [item for item in _fetches.get(user_id, []) if now - item < 3600]
    if len(recent) >= 30:
        _fetches[user_id] = recent
        return False
    recent.append(now)
    _fetches[user_id] = recent
    return True


def reply_for(db: sqlite3.Connection, user_id: int, text: str, chat_id: int | None = None) -> str:
    token, rest = _head(text)
    if token in {"/start", "/help", "помощь"}:
        return HELP
    if token in {"история", "/history", "history"}:
        return format_history(store.history(db, user_id))
    refresh = token in {"обнови", "обновить", "заново", "/refresh", "/update"}
    if refresh and not rest:
        rest = store.last_plate(db, user_id) or ""
        if not rest:
            return "Сначала пришли номер."
    found = plate.choose(rest if refresh else text)
    if not found:
        return "Не вижу госномер. Пример: Н401ВР70 или H401BP70."
    if len(found) > 1:
        shown = "\n".join(plate.show(item) for item, _kind in found)
        return "Вижу несколько номеров, пришли один:\n" + shown
    number = found[0][0]
    cached = None if refresh else store.fresh(db, number)
    if cached is not None:
        title, body, fetched = cached
        if card.useful(body):
            store.remember(db, user_id, number, title, body, fetched)
            return f"{plate.show(number)}\n{body}\nПроверка {stamp(fetched)}, из истории"
    if not _may_fetch(user_id):
        return "Слишком много новых номеров за час. Повтор уже смотренного номера по-прежнему сразу."
    typing(chat_id or user_id)
    try:
        raw = browser.read_plate(number)
    except RuntimeError as exc:
        print("lookup failed", number, str(exc)[:160], flush=True)
        return f"{plate.show(number)}\nСайт не открыл карточку. Напиши номер ещё раз через минуту."
    title, body = card.parse(raw)
    if not card.useful(body):
        print("lookup empty", number, flush=True)
        return f"{plate.show(number)}\nСайт не успел открыть характеристики. Напиши номер ещё раз."
    fetched = int(time.time())
    store.remember(db, user_id, number, title, body, fetched)
    return f"{plate.show(number)}\n{body}\nПроверка {stamp(fetched)}"


def handle(db: sqlite3.Connection, update: dict) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    if chat.get("type") != "private":
        return
    user = message.get("from") or {}
    if user.get("is_bot"):
        return
    moment = int(message.get("date") or 0)
    if moment and moment < time.time() - 15 * 60:
        return
    text = str(message.get("text") or "").strip()
    if not text:
        return
    chat_id = int(chat["id"])
    user_id = int(user.get("id") or chat_id)
    send(chat_id, reply_for(db, user_id, text, chat_id))


def main() -> None:
    token()
    serve_health()
    global _direct_until
    try:
        probe = socket.create_connection((API_IP, 443), 3)
        probe.close()
    except OSError:
        _direct_until = time.time() + 600
        ensure_proxy()
    db = store.connect(DB_PATH)
    offset = load_offset()
    if offset is None:
        offset = 0
    print("avtobot listening", flush=True)
    while True:
        browser.retire_if_idle()
        try:
            payload = telegram(
                "getUpdates",
                {"timeout": 50, "offset": offset, "allowed_updates": ["message"]},
                timeout=70,
            )
        except (RuntimeError, OSError, TimeoutError, socket.timeout) as exc:
            print("telegram", str(exc)[:200], flush=True)
            time.sleep(2)
            continue
        for update in payload.get("result") or []:
            update_id = int(update["update_id"])
            try:
                handle(db, update)
            except Exception as exc:
                print("update failed", type(exc).__name__, str(exc)[:200], flush=True)
            offset = update_id + 1
            save_offset(offset)


if __name__ == "__main__":
    main()
