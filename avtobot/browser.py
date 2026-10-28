"""One ordinary Chrome window. Headless is rejected by the site."""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "chrome-profile"
PORT = 9227
DISPLAY = ":98"
IDLE_SECONDS = 10 * 60

_xvfb: subprocess.Popen[bytes] | None = None
_chrome: subprocess.Popen[bytes] | None = None
_used = 0.0
_last_page = ""


def _alive(proc: subprocess.Popen[bytes] | None) -> bool:
    return proc is not None and proc.poll() is None


def _port_open() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1).read()
    except (OSError, TimeoutError):
        return False
    return True


def _stop() -> None:
    global _xvfb, _chrome
    for proc in (_chrome, _xvfb):
        if _alive(proc):
            proc.terminate()
    for proc in (_chrome, _xvfb):
        if _alive(proc):
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    _chrome = None
    _xvfb = None


def _ensure() -> None:
    global _xvfb, _chrome, _used
    if _port_open() and _alive(_chrome) and _alive(_xvfb):
        _used = time.time()
        return
    _stop()
    PROFILE.mkdir(parents=True, exist_ok=True)
    _xvfb = subprocess.Popen(
        ["Xvfb", DISPLAY, "-screen", "0", "800x600x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    _chrome = subprocess.Popen(
        [
            "google-chrome",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            f"--remote-debugging-port={PORT}",
            "--remote-allow-origins=*",
            f"--user-data-dir={PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-sync",
            "--disable-background-networking",
            "--mute-audio",
            "--renderer-process-limit=1",
            "--window-size=800,600",
            "about:blank",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 8
    while time.time() < deadline:
        if _port_open():
            _used = time.time()
            return
        if not _alive(_chrome):
            break
        time.sleep(0.1)
    _stop()
    raise RuntimeError("chrome did not start")


def retire_if_idle() -> None:
    if _used and time.time() - _used > IDLE_SECONDS:
        _stop()


def _send(sock: socket.socket, opcode: int, payload: bytes) -> None:
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += length.to_bytes(2, "big")
    else:
        header.append(0x80 | 127)
        header += length.to_bytes(8, "big")
    sock.sendall(bytes(header) + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)))


def _connect(url: str) -> socket.socket:
    hostport, path = url.removeprefix("ws://").split("/", 1)
    host, port = hostport.split(":")
    key = base64.b64encode(os.urandom(16)).decode()
    sock = socket.create_connection((host, int(port)), 5)
    sock.settimeout(8)
    sock.sendall(
        (
            f"GET /{path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
    )
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise RuntimeError("chrome closed the debugger")
        buf += chunk
    if b" 101 " not in buf.split(b"\r\n", 1)[0]:
        sock.close()
        raise RuntimeError("chrome refused the debugger")
    return sock


def _command(sock: socket.socket, message_id: int, expression: str) -> str:
    payload = json.dumps(
        {
            "id": message_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }
    ).encode()
    _send(sock, 1, payload)
    deadline = time.time() + 8
    while time.time() < deadline:
        opcode, data = _frame(sock)
        if opcode == 8:
            raise RuntimeError("chrome closed the page")
        if opcode == 9:
            _send(sock, 10, data)
            continue
        if opcode != 1 or not data:
            continue
        body = json.loads(data.decode("utf-8", "replace"))
        if body.get("id") != message_id:
            continue
        result = body.get("result") or {}
        details = result.get("exceptionDetails")
        if details:
            raise RuntimeError(json.dumps(details, ensure_ascii=False)[:240])
        value = result.get("result", {}).get("value")
        return "" if value is None else str(value)
    raise RuntimeError("chrome did not answer")


def _frame(sock: socket.socket) -> tuple[int, bytes]:
    header = _read(sock, 2)
    if len(header) < 2:
        return 8, b""
    length = header[1] & 127
    if length == 126:
        raw = _read(sock, 2)
        if len(raw) < 2:
            return 8, b""
        length = int.from_bytes(raw, "big")
    elif length == 127:
        raw = _read(sock, 8)
        if len(raw) < 8:
            return 8, b""
        length = int.from_bytes(raw, "big")
    if header[1] & 128:
        mask = _read(sock, 4)
        data = _read(sock, length)
        if len(mask) < 4 or len(data) < length:
            return 8, b""
        data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
    else:
        data = _read(sock, length)
        if len(data) < length:
            return 8, b""
    return header[0] & 15, data


def _read(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _tabs() -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=5) as response:
        payload = json.load(response)
    return payload if isinstance(payload, list) else []


def _open(page: str) -> dict:
    target = f"http://127.0.0.1:{PORT}/json/new?{page}"
    request = urllib.request.Request(target, method="PUT")
    with urllib.request.urlopen(request, timeout=10) as response:
        opened = json.load(response)
    if isinstance(opened, dict) and opened.get("id"):
        return opened
    for tab in _tabs():
        if tab.get("type") == "page" and "avtocod.ru" in str(tab.get("url") or ""):
            return tab
    raise RuntimeError("tab was not created")


def _close(tab_id: str) -> None:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/close/{tab_id}", timeout=3).read()
    except (OSError, TimeoutError):
        return


_SNAPSHOT = r"""
(() => {
  const title = document.title || "";
  const text = (document.body && document.body.innerText) || "";
  const head = text.split("Зачем проверять")[0].split("Юридические")[0].slice(0, 1800);
  return title + "\n---\n" + head;
})()
"""


def _watch(url: str) -> str:
    """Short reads on one socket. A long script dies when the page navigates."""
    global _last_page
    sock = _connect(url)
    try:
        deadline = time.time() + 20
        last = ""
        message_id = 0
        while time.time() < deadline:
            message_id += 1
            try:
                last = _command(sock, message_id, _SNAPSHOT)
            except RuntimeError as exc:
                broken = str(exc).casefold()
                if "context" in broken or "destroy" in broken or "closed" in broken:
                    time.sleep(0.15)
                    continue
                raise
            _last_page = last
            if _filled(last):
                return last
            title = last.split("\n", 1)[0]
            if "Forbidden" in last and not _card_ready(title):
                return last
            time.sleep(0.15)
        return last
    finally:
        sock.close()


def read_plate(plate: str) -> str:
    error: Exception | None = None
    for attempt in (1, 2):
        try:
            return _read_once(plate)
        except (RuntimeError, OSError, TimeoutError, socket.timeout) as exc:
            error = exc
            if attempt == 2:
                break
            if _port_open() and "did not fill" in str(exc):
                continue
            _stop()
    raise RuntimeError(str(error or "site did not open the card"))


def _read_once(plate: str) -> str:
    page = "https://avtocod.ru/proverkaavto/" + urllib.parse.quote(plate) + "?rd=GRZ"
    _ensure()
    tab = _open(page)
    tab_id = str(tab.get("id") or "")
    socket_url = str(tab.get("webSocketDebuggerUrl") or "")
    try:
        if not socket_url:
            deadline = time.time() + 3
            while time.time() < deadline and not socket_url:
                for item in _tabs():
                    if item.get("id") == tab_id:
                        socket_url = str(item.get("webSocketDebuggerUrl") or "")
                time.sleep(0.05)
        if not socket_url:
            raise RuntimeError("site did not open the card")
        last = _watch(socket_url)
        title = last.split("\n", 1)[0]
        if "Forbidden" in last and not _card_ready(title):
            raise RuntimeError("site forbidden")
        if not _card_ready(title):
            raise RuntimeError("site did not open the card")
        if not _filled(last):
            raise RuntimeError("site did not fill the card")
        return last
    finally:
        if tab_id:
            _close(tab_id)


def _card_ready(title: str) -> bool:
    if "по госномеру" not in title or "Проверить авто" in title:
        return False
    return title.count(" ") >= 2


def _filled(raw: str) -> bool:
    if not _card_ready(raw.split("\n", 1)[0]):
        return False
    return re.search(r"Год производства[:：]?\s*\d{4}", raw) is not None
