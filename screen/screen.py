#!/usr/bin/env python3
"""Keep the SuiPlay panel off.

Brightness 0 is not enough on this eDP. Writing bl_power=4 again does
nothing when it is already 4 and the panel is lit. Unblank, then power
the panel down so the driver drops DPMS.
"""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SYS = "/host/sys"
BACKLIGHT = SYS + "/class/backlight/amdgpu_bl1"
DPMS = SYS + "/class/drm/card1-eDP-1/dpms"
BLANK = SYS + "/class/graphics/fb0/blank"
LEDS = SYS + "/class/leds"

off = False


def write(path: str, value: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(value)


def panel_off() -> bool:
    try:
        dpms = open(DPMS, encoding="utf-8").read().strip()
        actual = open(BACKLIGHT + "/actual_brightness", encoding="utf-8").read().strip()
    except OSError:
        return False
    return dpms == "Off" and actual == "0"


def power_down() -> None:
    if os.path.isdir(LEDS):
        for name in os.listdir(LEDS):
            base = os.path.join(LEDS, name)
            for leaf, value in (("trigger", "none"), ("brightness", "0")):
                path = os.path.join(base, leaf)
                if os.path.exists(path):
                    try:
                        write(path, value)
                    except OSError:
                        pass
    write(BACKLIGHT + "/bl_power", "0")
    write(BACKLIGHT + "/brightness", "0")
    time.sleep(0.3)
    write(BACKLIGHT + "/bl_power", "4")
    write(BLANK, "4")


def hold() -> None:
    global off
    while True:
        if panel_off():
            off = True
        else:
            try:
                power_down()
            except OSError:
                off = False
            else:
                off = panel_off()
        time.sleep(2)


class Health(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = b"ok\n" if off else b"on\n"
        self.send_response(200 if off else 503)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def main() -> None:
    threading.Thread(target=hold, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Health).serve_forever()


if __name__ == "__main__":
    main()
