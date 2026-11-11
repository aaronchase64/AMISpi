# SPDX-License-Identifier: GPL-2.0-or-later
"""Modbus/TCP server which emulates a SunSpec "Smart Meter" for a Fronius inverter (e.g. Gen24).

Register layout and values are taken over from the (working) ESP firmware
https://github.com/mgerhard74/amis_smartmeter_reader (ModbusSmartmeterEmulation.cpp).
Only the registers the Fronius really polls are filled - the rest is fix or zero.

Addressing: the Fronius asks for the "register index" 40000 ... 40196, which is the
register number 40001 ... 40197 of the documentation. We use the register *index* (= wire address).
"""

from __future__ import annotations

import logging
import socket
import socketserver
import struct
import threading
import time

log = logging.getLogger("amis.modbus")

FIRST_IDX = 40000
LAST_IDX = 40196
NUM_REGS = LAST_IDX - FIRST_IDX + 1   # 197

# Fix part (register index 40000 ... 40070) as in the firmware (BE_holdregs_40000).
# The firmware stores the values byte swapped (little endian CPU) - here they are in "wire" order.
_STATIC_FW = [
    0x7553, 0x536e, 0x0100, 0x4100, 0x4100, 0x6d00, 0x6900, 0x7300, 0x2000, 0x5200,  # "SunS" DID=1 L=65 "Amis R"
    0x6500, 0x6100, 0x6400, 0x6500, 0x7200, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,  # "eader"
    0x5300, 0x6d00, 0x6100, 0x7200, 0x7400, 0x2000, 0x4d00, 0x6500, 0x7400, 0x6500,  # "Smart Mete"
    0x7200, 0x2000, 0x3600, 0x3300, 0x4100, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,  # "r 63A"
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x3500, 0x3400, 0x3400, 0x3300, 0x3300, 0x3200, 0x3200, 0x3100,  # serial number
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0xf000, 0xd500,  # 240, 213
    0x7c00,                                                                           # 124
]
STATIC_REGS = [((v & 0xFF) << 8) | (v >> 8) for v in _STATIC_FW]
assert len(STATIC_REGS) == 71


def _set_float(regs: list[int], regno: int, value: float) -> None:
    """Write a float32 (big endian, high word first) to the register *number* 'regno' (= index + 1)."""
    hi, lo = struct.unpack(">HH", struct.pack(">f", value))
    idx = regno - 1 - FIRST_IDX
    regs[idx] = hi
    regs[idx + 1] = lo


def build_registers(import_w: float, export_w: float, import_wh: float, export_wh: float) -> list[int]:
    """Register image 40000 ... 40196 for the given meter values."""
    regs = [0] * NUM_REGS
    regs[:len(STATIC_REGS)] = STATIC_REGS

    saldo = float(import_w) - float(export_w)       # 1.7.0 - 2.7.0 (>0 = from grid)

    for reg in (40074, 40076, 40078):               # Phase A/B/C current
        _set_float(regs, reg, saldo / 690)
    for reg in (40080, 40082, 40084, 40086):        # L-N voltage and phase voltages
        _set_float(regs, reg, 230.0)
    for reg in (40088, 40090, 40092, 40094):        # L-L voltages
        _set_float(regs, reg, 400.0)
    _set_float(regs, 40096, 50.0)                   # frequency
    _set_float(regs, 40098, saldo)                  # total power
    for reg in (40100, 40102, 40104):               # power per phase
        _set_float(regs, reg, saldo / 3)
    _set_float(regs, 40130, float(export_wh))       # total real energy exported (2.8.0)
    _set_float(regs, 40138, float(import_wh))       # total real energy imported (1.8.0)
    for reg in (40122, 40124, 40126, 40128):        # power factor
        _set_float(regs, reg, 1.0)
    # Model 213 starts at 40069 and has 124 data registers.
    # Its successor is the two-register end model at 40195: ID=FFFF, L=0.
    regs[40195 - FIRST_IDX] = 0xFFFF
    regs[40196 - FIRST_IDX] = 0
    return regs


class _Handler(socketserver.BaseRequestHandler):
    server: "FroniusModbusServer"

    def _recv_exact(self, n: int) -> bytes | None:
        data = b""
        while len(data) < n:
            chunk = self.request.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    @staticmethod
    def _exception(tid: int, unit: int, fc: int, code: int) -> bytes:
        return struct.pack(">HHHBBB", tid, 0, 3, unit, fc | 0x80, code)

    def handle(self) -> None:
        peer = "%s:%s" % self.client_address[:2]
        log.debug("client %s connected", peer)
        self.request.settimeout(60)
        try:
            while True:
                head = self._recv_exact(7)              # MBAP: tid, pid, length, unit
                if head is None:
                    break
                tid, pid, length, unit = struct.unpack(">HHHB", head)
                if pid != 0 or length < 2 or length > 254:
                    break                               # no modbus
                pdu = self._recv_exact(length - 1)
                if pdu is None:
                    break

                regs = self.server.snapshot()
                if regs is None:
                    log.debug("no valid meter data - closing connection to %s", peer)
                    break                               # like the firmware: do not answer without data

                fc = pdu[0]
                if fc != 3:
                    self.request.sendall(self._exception(tid, unit, fc, 1))
                    continue
                if len(pdu) != 5:
                    self.request.sendall(self._exception(tid, unit, fc, 3))
                    continue
                first, count = struct.unpack(">HH", pdu[1:5])
                if count < 1 or count > 125:
                    self.request.sendall(self._exception(tid, unit, fc, 3))
                    continue
                if first < FIRST_IDX or first > LAST_IDX or first + count - 1 > LAST_IDX:
                    self.request.sendall(self._exception(tid, unit, fc, 2))
                    continue
                if count < 1 or count > 125:
                    self.request.sendall(self._exception(tid, unit, fc, 3))
                    continue

                part = regs[first - FIRST_IDX:first - FIRST_IDX + count]
                payload = struct.pack(">%dH" % count, *part)
                resp = struct.pack(">HHHBBB", tid, 0, count * 2 + 3, unit, 3, count * 2) + payload
                self.request.sendall(resp)
                log.debug("%s read %d registers from %d", peer, count, first)
        except (OSError, socket.timeout):
            pass
        finally:
            log.debug("client %s disconnected", peer)

    def setup(self) -> None:
        with self.server._clients_lock:
            self.server._clients.add(self.request)

    def finish(self) -> None:
        with self.server._clients_lock:
            self.server._clients.discard(self.request)


class FroniusModbusServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str = "0.0.0.0", port: int = 502, stale_after: float = 15.0):
        super().__init__((host, port), _Handler, bind_and_activate=False)
        self._clients_lock = threading.Lock()
        self._clients: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._regs: list[int] | None = None
        self._updated = 0.0
        self._stale_after = stale_after
        self._thread: threading.Thread | None = None

    # --- data side ---
    def set_values(self, import_w: float, export_w: float, import_wh: float, export_wh: float) -> None:
        regs = build_registers(import_w, export_w, import_wh, export_wh)
        with self._lock:
            self._regs = regs
            self._updated = time.monotonic()

    def invalidate(self) -> None:
        with self._lock:
            self._regs = None

    def valid(self) -> bool:
        with self._lock:
            return self._regs is not None and time.monotonic() - self._updated <= self._stale_after

    def registers(self) -> list[int]:
        with self._lock:
            return list(self._regs) if self._regs else [0] * NUM_REGS

    def snapshot(self) -> list[int] | None:
        """Check freshness and copy atomically: never return zeros after invalidation."""
        with self._lock:
            if self._regs is None or time.monotonic() - self._updated > self._stale_after:
                return None
            return list(self._regs)

    # --- server side ---
    def start(self) -> None:
        self.server_bind()
        self.server_activate()
        self._thread = threading.Thread(target=self.serve_forever, name="modbus", daemon=True)
        self._thread.start()
        log.info("Modbus/TCP (Fronius smart meter emulation) listening on port %d", self.server_address[1])

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self.shutdown()
            self._thread.join(timeout=2)
        with self._clients_lock:
            for client in self._clients:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                client.close()
            self._clients.clear()
        self.server_close()
