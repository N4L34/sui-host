#!/usr/bin/env python3
"""Telegram notes for Svoi.

Pages go out through Alertmanager. This script writes the public-path
metrics, the morning note, and the Sunday note. It talks to api.telegram.org
by a pinned address: on this link the address from DNS does not answer.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_HOST = "api.telegram.org"
API_IP_DEFAULT = "149.154.167.220"
TEXTFILE = ROOT / "deploy" / "rendered" / "textfile" / "edge.prom"

PAGE_TITLES = {
    "HomeserverDown": "чат",
    "DiskSpaceLow": "диск",
    "BackupStale": "локальная копия",
    "OffsiteBackupStale": "копия наружу",
    "OomKill": "память",
    "MatrixPrefixDown": "адрес чата",
    "CallsNotReady": "звонки",
    "EdgeProbeStale": "проверка адреса",
}


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


def secret(name: str) -> str:
    path = ROOT / "secrets" / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def telegram_post(method: str, fields: dict[str, str], env: dict[str, str]) -> dict:
    token = secret("telegram_bot_token")
    if not token:
        raise SystemExit(0)
    body = urllib.parse.urlencode(fields).encode()
    path = f"/bot{token}/{method}"
    sock = socket.create_connection((api_ip(env), 443), 20)
    try:
        ssock = ssl.create_default_context().wrap_socket(sock, server_hostname=API_HOST)
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {API_HOST}\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode() + body
        ssock.sendall(request)
        data = b""
        while True:
            chunk = ssock.recv(8192)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()
    raw = data.split(b"\r\n\r\n", 1)[-1].decode("utf-8", "replace")
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise SystemExit("telegram answer was not json")
    payload = json.loads(raw[start : end + 1])
    if not payload.get("ok"):
        raise SystemExit(payload.get("description") or "telegram rejected the message")
    return payload


def send(text: str, env: dict[str, str]) -> None:
    chat = secret("telegram_chat_id")
    if not secret("telegram_bot_token") or not chat:
        raise SystemExit(0)
    telegram_post("sendMessage", {"chat_id": chat, "text": text}, env)
    print("sent")


def prom_query(query: str) -> float | None:
    url = "http://127.0.0.1:9091/api/v1/query?" + urllib.parse.urlencode({"query": query})
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    if payload.get("status") != "success":
        return None
    result = payload.get("data", {}).get("result") or []
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (KeyError, TypeError, ValueError):
        return None


def firing_names() -> list[str] | None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:9091/api/v1/alerts", timeout=5) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    names: list[str] = []
    for alert in payload.get("data", {}).get("alerts", []):
        if alert.get("state") == "firing":
            name = alert.get("labels", {}).get("alertname", "")
            if name and name not in names:
                names.append(name)
    return names


def gauge(path: Path, name: str) -> float | None:
    if not path.is_file():
        return None
    prefix = name + " "
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            try:
                return float(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def age_text(seconds: float | None) -> str:
    if seconds is None:
        return "нет данных"
    if seconds < 0:
        seconds = 0
    minutes = int(seconds // 60)
    if minutes < 90:
        return f"{minutes} мин назад"
    hours = int(round(seconds / 3600))
    return f"{hours} ч назад"


def container_running(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def server_name(env: dict[str, str]) -> str:
    return env.get("MATRIX_SERVER_NAME", "").strip().rstrip(".")


def matrix_ok(env: dict[str, str]) -> bool:
    name = server_name(env)
    if not name:
        return False
    url = f"https://{name}/_matrix/client/versions"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            body = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False
    return isinstance(body.get("versions"), list) and bool(body["versions"])


def calls_enabled(env: dict[str, str]) -> bool:
    return env.get("ENABLE_CALLS", "false").lower() == "true"


def tailscale_ipv4() -> str:
    result = subprocess.run(
        ["tailscale", "ip", "-4"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[0] if lines else ""


def rtc_status() -> int | None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:7880/rtc/v1/validate", timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def livekit_ports_ok(ip: str) -> bool:
    if not ip:
        return False
    result = None
    for name in ("chat-livekit-1", "svoi-livekit-1"):
        result = subprocess.run(
            ["docker", "inspect", name],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            break
    if result.returncode != 0:
        return False
    try:
        inspected = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError, TypeError):
        return False
    networks = (inspected.get("NetworkSettings") or {}).get("Networks") or {}
    if not any("edge" in name for name in networks):
        return False
    ports = (inspected.get("NetworkSettings") or {}).get("Ports") or {}

    def bound(key: str) -> bool:
        for entry in ports.get(key) or []:
            if entry.get("HostIp") == ip:
                return True
        return False

    udp_span = False
    for key, entries in ports.items():
        number, _, proto = key.partition("/")
        if proto != "udp" or not number.isdigit():
            continue
        port = int(number)
        if 50000 <= port <= 50100 and any(entry.get("HostIp") == ip for entry in entries or []):
            udp_span = True
            break
    return bound("7881/tcp") and udp_span


def calls_ready(env: dict[str, str]) -> bool:
    # /rtc/v1 itself is 404 even on 1.13.10. /rtc/v1/validate is 401 without a token
    # and 404 on the old image that dropped calls after 15 seconds.
    return rtc_status() == 401 and livekit_ports_ok(tailscale_ipv4())


def write_probe(env: dict[str, str]) -> None:
    lines = [
        "# HELP messenger_matrix_prefix_ok 1 when the public Matrix path returns version JSON.",
        "# TYPE messenger_matrix_prefix_ok gauge",
        f"messenger_matrix_prefix_ok {1 if matrix_ok(env) else 0}",
        "# HELP messenger_edge_probe_timestamp Unix time of the last public-path check.",
        "# TYPE messenger_edge_probe_timestamp gauge",
        f"messenger_edge_probe_timestamp {int(time.time())}",
    ]
    if calls_enabled(env):
        lines.extend(
            [
                "# HELP messenger_calls_ready 1 when LiveKit answers /rtc/v1/validate and owns its tailnet ports.",
                "# TYPE messenger_calls_ready gauge",
                f"messenger_calls_ready {1 if calls_ready(env) else 0}",
            ]
        )
    TEXTFILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = TEXTFILE.with_suffix(".prom.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o644)
    os.replace(temporary, TEXTFILE)


def core_line() -> str:
    postgres = container_running("svoi-postgres-1")
    synapse = container_running("svoi-synapse-1")
    element = container_running("svoi-element-web-1")
    if postgres and synapse and element:
        return "Чат: контейнеры запущены"
    missing = []
    if not synapse:
        missing.append("чат")
    if not postgres:
        missing.append("база")
    if not element:
        missing.append("веб")
    return "Чат: не запущены " + ", ".join(missing)


def backup_lines() -> list[str]:
    prom = ROOT / "deploy" / "rendered" / "textfile" / "backup.prom"
    local_age = gauge(prom, "messenger_last_backup_success_timestamp")
    offsite = gauge(prom, "messenger_last_offsite_backup_success_timestamp")
    lines = [
        "Локальная копия: " + (age_text(None if local_age is None else time.time() - local_age)),
    ]
    if offsite is None:
        lines.append("Копия наружу: не настроена")
    else:
        lines.append("Копия наружу: " + age_text(time.time() - offsite))
    return lines


def daily(env: dict[str, str]) -> None:
    observe = prom_query("up") is not None
    if not observe:
        text = "\n".join(
            [
                "Свои · утро",
                "Наблюдение выключено.",
                core_line(),
                *backup_lines(),
            ]
        )
        send(text, env)
        return

    matrix = matrix_ok(env)
    disk = prom_query(
        'min(node_filesystem_avail_bytes{fstype=~"ext4|xfs|btrfs|zfs",mountpoint!~"/boot.*|/run.*"}'
        ' / node_filesystem_size_bytes{fstype=~"ext4|xfs|btrfs|zfs",mountpoint!~"/boot.*|/run.*"})'
    )
    memory = prom_query("min_over_time(node_memory_MemAvailable_bytes[12h])")
    load = prom_query("max_over_time(node_load15[12h])")
    restarts = prom_query(
        'sum(changes(container_start_time_seconds{name=~"svoi-(synapse|postgres)-1"}[12h]))'
    )
    errors = prom_query('sum(increase(synapse_http_server_responses_total{code=~"5.."}[12h]))')
    deadlocks = prom_query('sum(increase(pg_stat_database_deadlocks{datname="synapse"}[12h]))')
    oom = prom_query("increase(node_vmstat_oom_kill[12h])")
    synapse_up = prom_query('up{job="synapse"}')
    postgres_up = prom_query("pg_up")
    users = prom_query("synapse_admin_daily_active_users")
    local_stamp = gauge(ROOT / "deploy/rendered/textfile/backup.prom", "messenger_last_backup_success_timestamp")
    local_age = None if local_stamp is None else time.time() - local_stamp
    fired = firing_names() or []
    calls_line = None
    calls_bad = False
    if calls_enabled(env):
        ready = calls_ready(env)
        calls_bad = not ready
        calls_line = "Звонки: доступны" if ready else "Звонки: к ним не подключиться"

    quiet = (
        matrix
        and disk is not None
        and disk >= 0.30
        and local_age is not None
        and local_age < 2 * 3600
        and not fired
        and not calls_bad
        and container_running("svoi-postgres-1")
        and container_running("svoi-synapse-1")
        and container_running("svoi-element-web-1")
        and (memory is None or memory >= 1024**3)
        and (restarts is None or restarts == 0)
        and (errors is None or errors == 0)
        and (deadlocks is None or deadlocks == 0)
        and (oom is None or oom == 0)
        and synapse_up == 1
        and postgres_up == 1
    )
    if quiet:
        send("Свои: всё тихо. Чат отвечает, копия свежая.", env)
        return

    lines = ["Свои · утро", "Чат: отвечает" if matrix else "Чат: с телефона открывается не туда"]
    if synapse_up == 0 and matrix:
        lines.append("Метрики чата молчали, сам чат отвечал")
    lines.append("База: работает" if postgres_up == 1 else "База: метрики молчат")
    if disk is None:
        lines.append("Диск: нет данных")
    else:
        lines.append(f"Диск: свободно {round(disk * 100)}%")
    lines.extend(backup_lines())
    if memory is not None:
        lines.append(f"Память в минимуме за ночь: {memory / 1024**3:.1f} ГБ")
    if load is not None and load > 24:
        lines.append(f"Нагрузка за 15 минут доходила до {load:.0f}")
    if restarts:
        lines.append(f"Рестарты чата и базы за ночь: {int(restarts)}")
    if errors:
        lines.append(f"Ответы с ошибкой за ночь: {int(errors)}")
    if deadlocks:
        lines.append(f"Дедлоки в базе: {int(deadlocks)}")
    if oom:
        lines.append("Ночью ядро останавливало процесс по памяти")
    if users is not None:
        lines.append(f"Заходили за сутки: {int(users)}")
    if calls_line:
        lines.append(calls_line)
    if fired:
        titles = [PAGE_TITLES.get(name, name) for name in fired]
        lines.append("Уже писали: " + ", ".join(titles))
    send("\n".join(lines), env)


def weekly(env: dict[str, str]) -> None:
    uptime = prom_query('avg(avg_over_time(probe_success{job="blackbox-http"}[7d]))')
    disk = prom_query(
        "min_over_time((node_filesystem_avail_bytes"
        '{fstype=~"ext4|xfs|btrfs|zfs",mountpoint="/"}'
        " / node_filesystem_size_bytes"
        '{fstype=~"ext4|xfs|btrfs|zfs",mountpoint="/"})[7d:10m])'
    )
    copies = prom_query("changes(messenger_last_backup_success_timestamp[7d])")
    pages = prom_query('max(max_over_time(ALERTS{alertstate="firing"}[7d]))')
    if uptime is None:
        send("Свои · неделя\nНаблюдение было выключено, долю проб посчитать не из чего.", env)
        return
    percent = f"{uptime * 100:.1f}".replace(".0", "")
    disk_text = "нет данных" if disk is None else f"{round(disk * 100)}%"
    copy_text = "нет данных" if copies is None else str(int(copies))
    page_text = "были" if pages else "не было"
    send(
        "\n".join(
            [
                "Свои · неделя",
                f"Пока наблюдение было включено, чат отвечал на {percent}% проб (цель 99.5%).",
                f"Страницы: {page_text}.",
                f"Диск в минимуме: {disk_text}.",
                f"Локальных копий: {copy_text}.",
            ]
        ),
        env,
    )


def ping(env: dict[str, str]) -> None:
    send(
        "Свои: бот на связи.\n"
        "Сюда приходят только поломки, утренний отчёт в 8:00 и сводка в воскресенье в 11:00.",
        env,
    )


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    env = load_env()
    if command == "probe":
        write_probe(env)
        return
    if command == "daily":
        daily(env)
        return
    if command == "weekly":
        weekly(env)
        return
    if command == "ping":
        ping(env)
        return
    raise SystemExit("usage: telegram_notify.py probe|daily|weekly|ping")


if __name__ == "__main__":
    main()
