"""Cached health/metric state for a future status container, not a daemon.

A caller schedules one bounded probe per path every 15s with jitter and calls
tick from its supervisor. Only effective required paths gate readiness; pinned
primary/backup diagnostics may be optional so failover does not mask a warning
or incorrectly make a working effective path unready. No raw product metrics
or exception strings are accepted.
"""

from dataclasses import dataclass
import math
import threading

from .probes import PUBLIC_ID, REASONS, Result


@dataclass
class PathState:
    ready: bool = False
    successes: int = 0
    failures: int = 0
    last_success: float | None = None
    reason: str = "startup"
    duration: float = 0
    count: int = 0


def _time(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


class Status:
    def __init__(self, policy_version, keys, required=None):
        try:
            valid = PUBLIC_ID.fullmatch(policy_version) and 0 < len(keys) <= 256
            valid = valid and len(set(keys)) == len(keys)
            valid = valid and all(
                len(key) == 3
                and PUBLIC_ID.fullmatch(key[0])
                and PUBLIC_ID.fullmatch(key[1])
                and key[2] in {"http", "socks5"}
                for key in keys
            )
            mandatory = set(keys if required is None else required)
            valid = valid and mandatory and mandatory.issubset(keys)
        except (TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("status_config")
        self.policy_version = policy_version
        self.paths = {key: PathState() for key in keys}
        self.required = mandatory
        self.heartbeat = None
        self.lock = threading.Lock()

    def tick(self, now):
        if not _time(now):
            raise ValueError("status_time")
        with self.lock:
            self.heartbeat = now

    def record(self, key, result: Result, now):
        if key not in self.paths:
            raise ValueError("status_key")
        if result.reason not in REASONS or not _time(result.duration) or not _time(now):
            raise ValueError("status_result")
        with self.lock:
            state = self.paths[key]
            # Stale successes cannot count toward recovery, even if no caller
            # requested a snapshot during the outage.
            if state.last_success is None or now - state.last_success > 60:
                state.ready = False
                state.successes = 0
            state.reason, state.duration = result.reason, result.duration
            state.count += 1
            if result.reason == "ok":
                state.failures = 0
                state.successes += 1
                state.last_success = now
                if state.successes >= 2:
                    state.ready = True
            else:
                state.successes = 0
                state.failures += 1
                if state.failures >= 3:
                    state.ready = False

    def snapshot(self, now):
        if not _time(now):
            raise ValueError("status_time")
        with self.lock:
            paths = []
            for key, state in self.paths.items():
                age = (
                    None
                    if state.last_success is None
                    else max(0, now - state.last_success)
                )
                fresh = age is not None and age <= 60
                paths.append(
                    {
                        "profile": key[0],
                        "upstream": key[1],
                        "protocol": key[2],
                        "required": key in self.required,
                        "ready": state.ready and fresh,
                        "reason": state.reason if fresh else "stale",
                        "age_seconds": age,
                        "duration_seconds": state.duration,
                        "count": state.count,
                    }
                )
            return {
                "live": self.heartbeat is not None and 0 <= now - self.heartbeat <= 60,
                "ready": all(path["ready"] for path in paths if path["required"]),
                "policy_version": self.policy_version,
                "paths": paths,
            }

    def metrics(self, now):
        snapshot = self.snapshot(now)
        lines = [
            f"egress_ready {int(snapshot['ready'])}",
            f"egress_status_live {int(snapshot['live'])}",
            f'egress_policy_info{{version="{self.policy_version}"}} 1',
        ]
        for path in snapshot["paths"]:
            labels = ",".join(
                f'{field}="{path[field]}"'
                for field in ("profile", "upstream", "protocol")
            )
            for metric, value in (
                ("ready", int(path["ready"])),
                (
                    "age_seconds",
                    path["age_seconds"] if path["age_seconds"] is not None else "+Inf",
                ),
                ("duration_seconds", path["duration_seconds"]),
                ("total", path["count"]),
            ):
                lines.append(f"egress_probe_{metric}{{{labels}}} {value}")
        return "\n".join(lines) + "\n"
