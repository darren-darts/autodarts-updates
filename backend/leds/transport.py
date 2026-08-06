"""Transports for talking to the ESP32 LED controller.

Both speak the same JSON schema (the firmware shares one state parser):
- SerialTransport: JSON lines over USB at 115200 baud
- HttpTransport:   REST calls over WiFi (POST /api/state etc.)
"""
from __future__ import annotations

import json
import logging

import httpx
import serial
from serial.tools import list_ports

log = logging.getLogger(__name__)

# USB-UART bridge chips commonly found on ESP32 dev boards, for auto-detection
ESP32_USB_VIDS = {0x10C4, 0x1A86, 0x0403, 0x303A}  # CP210x, CH340, FTDI, Espressif


class TransportError(Exception):
    pass


def list_serial_ports() -> list[dict]:
    ports = []
    for p in list_ports.comports():
        ports.append(
            {
                "device": p.device,
                "description": p.description,
                "vid": p.vid,
                "pid": p.pid,
                "likely_esp32": p.vid in ESP32_USB_VIDS if p.vid else False,
            }
        )
    return ports


def guess_esp32_port() -> str | None:
    candidates = [p for p in list_serial_ports() if p["likely_esp32"]]
    return candidates[0]["device"] if candidates else None


class SerialTransport:
    """Persistent serial connection.

    The port is kept open: opening it toggles DTR, which resets classic
    ESP32 dev boards, so we open once and hold. DTR/RTS are forced low
    before opening to minimise reset-on-connect.
    """

    kind = "serial"

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        try:
            self._ser = serial.Serial()
            self._ser.port = port
            self._ser.baudrate = baud
            self._ser.timeout = 2.0
            self._ser.write_timeout = 2.0
            self._ser.dtr = False
            self._ser.rts = False
            self._ser.open()
            self._ser.reset_input_buffer()
        except (serial.SerialException, OSError) as e:
            raise TransportError(f"cannot open {port}: {e}") from e

    @property
    def target(self) -> str:
        return self.port

    def send(self, payload: dict) -> dict:
        try:
            self._ser.write((json.dumps(payload) + "\n").encode())
            # Replies are single JSON lines; the firmware prefixes log
            # output with "# ", so skip anything that isn't JSON.
            for _ in range(20):
                line = self._ser.readline().decode(errors="replace").strip()
                if not line:
                    raise TransportError(f"no reply from {self.port}")
                if line.startswith("{"):
                    return json.loads(line)
            raise TransportError(f"no JSON reply from {self.port}")
        except (serial.SerialException, OSError, json.JSONDecodeError) as e:
            raise TransportError(f"serial error on {self.port}: {e}") from e

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass


class HttpTransport:
    kind = "http"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=3.0)

    @property
    def target(self) -> str:
        return self.base_url

    def send(self, payload: dict) -> dict:
        cmd = payload.get("cmd")
        try:
            if cmd in ("state", "effects", "info"):
                res = self._client.get(f"{self.base_url}/api/{cmd}")
            elif cmd == "ping":
                res = self._client.get(f"{self.base_url}/api/info")
            else:
                res = self._client.post(f"{self.base_url}/api/state", json=payload)
            res.raise_for_status()
            return res.json()
        except httpx.HTTPError as e:
            raise TransportError(f"http error on {self.base_url}: {e}") from e

    def close(self) -> None:
        self._client.close()
