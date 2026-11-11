# SPDX-License-Identifier: GPL-2.0-or-later
"""Application: runs the Service, applies configuration changes from the web UI without exiting."""

from __future__ import annotations

import logging
import copy
import threading

from . import config
from .service import Service
from .webui import LogBuffer, WebUI

log = logging.getLogger("amis")


class App:
    def __init__(self, cfg: dict, path: str | None) -> None:
        self.cfg = cfg
        self.path = path
        self.service: Service | None = None
        self.error = ""
        self._quit = threading.Event()
        self._wake = threading.Event()
        self._cfg_lock = threading.RLock()
        self._revision = 0
        self.logbuf = LogBuffer()
        logging.getLogger().addHandler(self.logbuf)
        self.web: WebUI | None = None

    # --- callbacks for the web UI ---
    def get_cfg(self) -> dict:
        with self._cfg_lock:
            return copy.deepcopy(self.cfg)

    def get_status(self) -> dict:
        svc = self.service
        if svc is None:
            return {"online": False, "simulate": False, "age": None, "mqtt": None, "modbus": None,
                    "values": None, "error": self.error}
        return svc.status()

    def apply_cfg(self, new: dict) -> None:
        with self._cfg_lock:
            config.validate(new)
            if self.path:
                config.save(self.path, new)
            self.cfg = copy.deepcopy(new)
            self._revision += 1
        logging.getLogger().setLevel(getattr(logging, str(new["log"]["level"]).upper(), logging.INFO))
        log.info("Configuration saved - restarting service")
        self.restart()

    def restart(self) -> None:
        self._wake.set()
        svc = self.service
        if svc:
            svc.stop()

    def stop(self) -> None:
        self._quit.set()
        self.restart()

    # --- main ---
    def run(self) -> None:
        web = self.cfg["web"]
        if web["enabled"]:
            self.web = WebUI(self.get_cfg, self.get_status, self.apply_cfg, self.logbuf, self.restart)
            try:
                self.web.start(web["host"], int(web["port"]))
            except OSError as exc:
                log.error("Web UI could not start on %s:%s: %s", web["host"], web["port"], exc)
                raise
        try:
            while not self._quit.is_set():
                with self._cfg_lock:
                    self._wake.clear()
                    current, revision = copy.deepcopy(self.cfg), self._revision
                try:
                    config.validate(current)
                    service = Service(current)
                    with self._cfg_lock:
                        self.service = service
                        self.error = ""
                        if revision != self._revision or self._wake.is_set() or self._quit.is_set():
                            service.stop()
                except (ValueError, OSError, RuntimeError) as exc:
                    self.service, self.error = None, str(exc)
                    log.error("Configuration problem: %s - waiting for new settings (web UI)", exc)
                    self._wake.wait()
                    continue
                try:
                    self.service.run()
                except (ValueError, OSError, RuntimeError) as exc:  # e.g. port 502 already in use
                    self.error = str(exc)
                    log.error("Service failed: %s - waiting for new settings (web UI)", exc)
                    self.service = None
                    self._wake.wait()
                    continue
                if not self._wake.is_set():  # service ended on its own
                    break
        finally:
            self.service = None
            if self.web:
                self.web.stop()
            logging.getLogger().removeHandler(self.logbuf)
