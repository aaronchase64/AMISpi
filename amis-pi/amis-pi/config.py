# SPDX-License-Identifier: GPL-2.0-or-later
"""Configuration: defaults < TOML file < environment variables (AMIS_<SECTION>_<KEY>)."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import tomllib
from typing import Any

DEFAULTS: dict[str, dict[str, Any]] = {
    "meter": {
        "key": "",                       # AMIS/MBUS key from the NetzOÖ portal (32 hex characters)
        "port": "/dev/ttyAMA0",          # PL011 on GPIO14/15 (also on Pi 5)
        "simulate": False,               # use demo data instead of a meter (for testing)
        "stale_after": 15,               # seconds without valid telegram -> data is invalid
        "serialnumber": "",              # only shown in the MQTT message
    },
    "mqtt": {
        "enabled": False,
        "host": "localhost",
        "port": 1883,
        "username": "",
        "password": "",
        "tls": False,
        "tls_insecure": False,
        "client_id": "amis-pi",
        "topic": "amis/out",             # JSON with all values
        "status_topic": "amis/status",   # "online" / "offline" (last will), retained. Empty = off
        "interval": 30,                  # seconds between two messages
        "qos": 0,                        # 0 or 1
        "retain": False,
        "key_style": "dots",             # "dots" -> "1.8.0"   "underscore" -> "1_8_0"
        "saldo_offset": 0,               # W, is subtracted from the saldo
        "saldo_invert": False,           # invert the sign of the saldo
    },
    "modbus": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 502,
    },
    "web": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8080,
        "username": "admin",
        "password": "",                  # empty = no login (only in a trusted network!)
    },
    "log": {
        "level": "INFO",
    },
}

SECRETS = {("meter", "key"), ("mqtt", "password"), ("web", "password")}
MASK = "********"


def _cast(value: str, like: Any) -> Any:
    if isinstance(like, bool):
        val = value.strip().lower()
        if val not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
            raise ValueError("Boolean value must be true or false")
        return val in ("1", "true", "yes", "on")
    if isinstance(like, int):
        return int(value)
    return value


def load(path: str | None = None, environ: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    cfg = copy.deepcopy(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        for section, values in data.items():
            if section not in cfg:
                raise ValueError("Unknown section [%s] in %s" % (section, path))
            if not isinstance(values, dict):
                raise ValueError("[%s] must be a TOML table" % section)
            for key, value in values.items():
                if key not in cfg[section]:
                    raise ValueError("Unknown option '%s' in section [%s] of %s" % (key, section, path))
                cfg[section][key] = value
    env = os.environ if environ is None else environ
    for section, values in cfg.items():
        for key, default in values.items():
            name = "AMIS_%s_%s" % (section.upper(), key.upper())
            if name in env:
                values[key] = _cast(env[name], DEFAULTS[section][key])
    return cfg


def validate(cfg: dict[str, dict[str, Any]]) -> None:
    """Raises ValueError with a readable message."""
    from . import telegram
    for section, defaults in DEFAULTS.items():
        for key, default in defaults.items():
            value = cfg[section][key]
            if type(value) is not type(default):
                raise ValueError("%s.%s must be %s" % (section, key, type(default).__name__))
    m, mq, mb, w = cfg["meter"], cfg["mqtt"], cfg["modbus"], cfg["web"]
    if not m["simulate"] or m["key"]:
        telegram.parse_key(m["key"])
    for sec, name in (("modbus", "port"), ("mqtt", "port"), ("web", "port")):
        if not 1 <= int(cfg[sec][name]) <= 65535:
            raise ValueError("%s.%s must be 1..65535" % (sec, name))
    if mq["key_style"] not in ("dots", "underscore"):
        raise ValueError("mqtt.key_style must be 'dots' or 'underscore'")
    if int(mq["qos"]) not in (0, 1):
        raise ValueError("mqtt.qos must be 0 or 1")
    if int(mq["interval"]) < 1 or int(m["stale_after"]) < 2:
        raise ValueError("mqtt.interval >= 1 and meter.stale_after >= 2 required")
    if mq["enabled"] and not str(mq["host"]).strip():
        raise ValueError("mqtt.host is empty")
    if not str(m["port"]).strip() and not m["simulate"]:
        raise ValueError("meter.port is empty")
    if str(cfg["log"]["level"]).upper() not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ValueError("log.level invalid")
    for name in ("topic", "status_topic"):
        value = mq[name]
        if (name == "topic" and not value) or any(c in value for c in ("+", "#", "\x00")) or len(value.encode()) > 65535:
            raise ValueError("mqtt.%s must be a publish topic without wildcards" % name)
    if not mq["client_id"] or "\x00" in mq["client_id"]:
        raise ValueError("mqtt.client_id is empty or invalid")
    if mq["tls_insecure"] and not mq["tls"]:
        raise ValueError("mqtt.tls_insecure requires mqtt.tls")
    if w["enabled"] and mb["enabled"] and w["port"] == mb["port"]:
        raise ValueError("web.port and modbus.port must differ")


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return json.dumps(str(v), ensure_ascii=False)


def dumps(cfg: dict[str, dict[str, Any]]) -> str:
    out = []
    for section, values in cfg.items():
        out.append("[%s]" % section)
        for k, v in values.items():
            out.append("%s = %s" % (k, _toml_value(v)))
        out.append("")
    return "\n".join(out)


def save(path: str, cfg: dict[str, dict[str, Any]]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".amis-config-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(dumps(cfg))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
