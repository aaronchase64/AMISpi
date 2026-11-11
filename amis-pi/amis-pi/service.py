# SPDX-License-Identifier: GPL-2.0-or-later
"""Glue: meter reader -> MQTT + Modbus (Fronius smart meter emulation)."""

from __future__ import annotations

import json
import logging
import threading
import time

from . import telegram
from .modbus_fronius import FroniusModbusServer
from .mqtt import MqttPublisher
from .reader import SerialReader, SimulatedReader

log = logging.getLogger("amis")

KEY_STYLES = {"dots": lambda k: k, "underscore": lambda k: k.replace(".", "_")}


def build_mqtt_payload(values: telegram.MeterValues, cfg: dict, serialnumber: str = "") -> str:
    """Same JSON as the ESP firmware publishes."""
    mq = cfg["mqtt"]
    style = KEY_STYLES.get(mq["key_style"])
    if style is None:
        raise ValueError("mqtt.key_style must be 'dots' or 'underscore'")
    saldo = values.import_w - values.export_w - int(mq["saldo_offset"])
    if mq["saldo_invert"]:
        saldo = -saldo
    doc: dict[str, object] = {}
    for obis, value in zip(telegram.OBIS_KEYS, values.values):
        doc[style(obis)] = value
    doc[style(telegram.INKASSO_KEY)] = values.inkasso
    doc["saldo"] = saldo
    doc["time"] = values.ts
    doc["serialnumber"] = serialnumber
    return json.dumps(doc, separators=(",", ":"))


class Service:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        m = cfg["meter"]
        key_text = telegram.DEMO_KEY if m["simulate"] else m["key"]
        self.key = telegram.parse_key(key_text)       # raises ValueError for a bad key
        self._lock = threading.Lock()
        self._latest: telegram.MeterValues | None = None
        self._latest_at = 0.0
        self._online = False
        self._last_published = 0.0
        self._last_keyerr_log = 0.0
        self._stop = threading.Event()

        if m["simulate"]:
            self.reader = SimulatedReader(self.key, self._on_values, self._on_error)
        else:
            self.reader = SerialReader(m["port"], self.key, self._on_values, self._on_error)

        self.mqtt: MqttPublisher | None = None
        mq = cfg["mqtt"]
        if mq["enabled"]:
            self.mqtt = MqttPublisher(mq["host"], int(mq["port"]), mq["client_id"], mq["username"],
                                      mq["password"], mq["tls"], mq["tls_insecure"],
                                      status_topic=mq["status_topic"])

        self.modbus: FroniusModbusServer | None = None
        if cfg["modbus"]["enabled"]:
            self.modbus = FroniusModbusServer(cfg["modbus"]["host"], int(cfg["modbus"]["port"]),
                                              stale_after=float(m["stale_after"]))

    # --- callbacks from the reader thread ---
    def _on_values(self, values: telegram.MeterValues) -> None:
        with self._lock:
            first = not self._online
            self._latest, self._latest_at, self._online = values, time.monotonic(), True
            if self.modbus:
                self.modbus.set_values(values.import_w, values.export_w, values.import_wh, values.export_wh)
        if first:
            log.info("Data synced with meter: 1.8.0=%d Wh 2.8.0=%d Wh saldo=%d W",
                     values.import_wh, values.export_wh, values.saldo_w)

    def _on_error(self, exc: telegram.TelegramError) -> None:
        now = time.monotonic()
        if exc.wrong_key and now - self._last_keyerr_log > 60:
            self._last_keyerr_log = now
            log.error("Telegram received but decryption failed - is the AMIS key correct? (%s)", exc)

    def status(self) -> dict:
        with self._lock:
            latest, at, online = self._latest, self._latest_at, self._online
        doc: dict = {"online": online, "simulate": bool(self.cfg["meter"]["simulate"]),
                     "age": round(time.monotonic() - at, 1) if latest else None,
                     "mqtt": ({"connected": self.mqtt.connected,
                               "last_publish_age": round(time.monotonic() - self._last_published, 1)
                               if self._last_published else None} if self.mqtt else None),
                     "modbus": {"valid": self.modbus.valid()} if self.modbus else None,
                     "values": None}
        doc["online"] = online = online and time.monotonic() - at <= float(self.cfg["meter"]["stale_after"])
        doc["error"] = self.reader.last_error
        doc["reader"] = {"alive": self.reader.is_alive(), "port": self.cfg["meter"]["port"],
                         "connected": self.reader.connected}
        if latest and online:
            mq = self.cfg["mqtt"]
            saldo = latest.import_w - latest.export_w - int(mq["saldo_offset"])
            doc["values"] = {"obis": dict(zip(telegram.OBIS_KEYS, latest.values)),
                             "inkasso": latest.inkasso,
                             "saldo": -saldo if mq["saldo_invert"] else saldo, "time": latest.ts}
        return doc

    # --- main loop ---
    def start(self) -> None:
        if self.modbus:
            self.modbus.start()
        if self.mqtt:
            self.mqtt.start()
        self.reader.start()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            if self._stop.is_set():
                return
            self.start()
            while not self._stop.wait(0.5):
                self._tick()
        finally:
            self.reader.stop()
            if self.reader.is_alive():
                self.reader.join(timeout=2)
            if self.mqtt:
                self.mqtt.stop()
            if self.modbus:
                self.modbus.stop()

    def _tick(self) -> None:
        stale_after = float(self.cfg["meter"]["stale_after"])
        now = time.monotonic()
        expired = False
        with self._lock:
            latest, at, online = self._latest, self._latest_at, self._online
            if online and now - at > stale_after:
                self._online = False
                expired = True
                if self.modbus:
                    self.modbus.invalidate()
        if expired:
            log.warning("No valid telegram for %d s - meter data invalid", stale_after)
            return
        if not online or latest is None or not self.mqtt:
            return
        mq = self.cfg["mqtt"]
        if now - self._last_published >= float(mq["interval"]) and self.mqtt.connected:
            payload = build_mqtt_payload(latest, self.cfg, self.cfg["meter"]["serialnumber"])
            if self.mqtt.publish(mq["topic"], payload, int(mq["qos"]), bool(mq["retain"])):
                self._last_published = now
                log.debug("published to %s: %s", mq["topic"], payload)
