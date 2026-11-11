# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from . import config, telegram
from .reader import SerialReader, SimulatedReader
from .app import App
from .service import build_mqtt_payload


def _once(cfg: dict, timeout: float) -> int:
    """Wait for one valid telegram, print it and exit. Good to check wiring and key."""
    key = telegram.parse_key(telegram.DEMO_KEY if cfg["meter"]["simulate"]
                             else cfg["meter"]["key"])
    got: list[telegram.MeterValues] = []
    event = threading.Event()
    errors: list[telegram.TelegramError] = []

    def on_values(v):
        got.append(v)
        event.set()

    if cfg["meter"]["simulate"]:
        reader = SimulatedReader(key, on_values, errors.append)
    else:
        reader = SerialReader(cfg["meter"]["port"], key, on_values, errors.append)
    reader.start()
    print("Waiting up to %d s for a telegram (the meter needs up to 1-2 minutes to start sending) ..." % timeout,
          file=sys.stderr)
    ok = event.wait(timeout)
    reader.stop()
    reader.join(timeout=2)
    if not ok:
        if any(e.wrong_key for e in errors):
            print("Telegrams were received but could not be decrypted: wrong AMIS key?", file=sys.stderr)
        else:
            print("Nothing received. Check wiring (RX/TX), serial port and that the serial console is off.",
                  file=sys.stderr)
        return 1
    print(build_mqtt_payload(got[0], cfg))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="amis-pi", description="AMIS smart meter reader -> MQTT + Fronius Modbus/TCP")
    ap.add_argument("-c", "--config", help="config file (TOML)")
    ap.add_argument("--once", action="store_true", help="read one telegram, print it as JSON and exit")
    ap.add_argument("--simulate", action="store_true", help="use demo data instead of a meter")
    ap.add_argument("--log-level", help="DEBUG, INFO, WARNING, ERROR")
    ap.add_argument("--timeout", type=float, default=150, help="timeout in seconds for --once")
    ap.add_argument("--check-uart", action="store_true", help="open UART and verify 9600 8E1, then exit (no key needed)")
    args = ap.parse_args(argv)

    try:
        cfg = config.load(args.config)
    except (ValueError, OSError) as exc:
        print("Cannot load configuration: %s" % exc, file=sys.stderr)
        return 2
    if not 0 < args.timeout <= 3600:
        ap.error("--timeout must be > 0 and <= 3600")
    if args.simulate:
        cfg["meter"]["simulate"] = True
    if args.log_level:
        cfg["log"]["level"] = args.log_level
    logging.basicConfig(level=getattr(logging, str(cfg["log"]["level"]).upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    if args.check_uart:
        try:
            reader = SerialReader(cfg["meter"]["port"], bytes(16), lambda v: None)
            try:
                reader._open()
                print("UART OK: %s, 9600 baud, 8E1" % cfg["meter"]["port"])
            finally:
                reader._close()
            return 0
        except (OSError, RuntimeError) as exc:
            print("UART check failed: %s" % exc, file=sys.stderr)
            return 2

    if args.once:
        try:
            config.validate(cfg)
            return _once(cfg, args.timeout)
        except (ValueError, OSError, RuntimeError) as exc:
            print("Configuration error: %s" % exc, file=sys.stderr)
            return 2

    app = App(cfg, args.config)

    def _terminate(signum, frame):
        app.stop()

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    logging.getLogger("amis").info("amis-pi started (%s)",
                                   "SIMULATION" if cfg["meter"]["simulate"] else cfg["meter"]["port"])
    try:
        app.run()
    except (ValueError, OSError, RuntimeError) as exc:
        logging.getLogger("amis").error("Startup failed: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
