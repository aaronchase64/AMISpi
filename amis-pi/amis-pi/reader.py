# SPDX-License-Identifier: GPL-2.0-or-later
"""Reading the telegrams from the optical head connected to a serial port (UART or USB-TTL adapter).

The meter works as M-Bus "slave":
  * once per minute it sends a SND_NKE (search mode) which has to be answered with an ACK (0xE5)
  * then it sends every second a 101 byte SND_UD telegram which also has to be acknowledged
Serial parameters: 9600 baud, 8 data bits, even parity, 1 stop bit.
Only the standard library (termios) is used.
"""

from __future__ import annotations

import logging
import os
import select
import threading
import time
from typing import Callable

from . import telegram

try:  # Linux / macOS only
    import termios
    import fcntl
except ImportError:  # pragma: no cover
    termios = None  # type: ignore

log = logging.getLogger("amis.reader")

PARTIAL_TIMEOUT = 1.0       # seconds - a started telegram must be completed within this time


class FrameParser:
    """Finds SND_NKE and SND_UD telegrams in a byte stream (and resynchronizes on garbage)."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self._last_rx = 0.0

    def feed(self, data: bytes, now: float | None = None) -> list[tuple[str, bytes]]:
        now = time.monotonic() if now is None else now
        if self.buf and now - self._last_rx > PARTIAL_TIMEOUT:
            self.buf.clear()                 # stale half telegram
        self._last_rx = now
        self.buf += data

        events: list[tuple[str, bytes]] = []
        buf = self.buf
        while buf:
            if buf[0] == 0x10:
                if len(buf) < 5:
                    break
                if bytes(buf[:5]) == telegram.NKE:
                    del buf[:5]
                    events.append(("nke", b""))
                    continue
            elif buf[0] == 0x68:
                if len(buf) < 4:
                    break
                if buf[3] == 0x68 and buf[1] == buf[2] == 0x5F:
                    if len(buf) < telegram.FRAME_LEN:
                        break
                    frame = bytes(buf[:telegram.FRAME_LEN])
                    del buf[:telegram.FRAME_LEN]
                    events.append(("frame", frame))
                    continue
            del buf[0]                       # garbage - resynchronize
        return events


class _BaseReader(threading.Thread):
    def __init__(self, key: bytes, on_values: Callable[[telegram.MeterValues], None],
                 on_error: Callable[[telegram.TelegramError], None] | None = None) -> None:
        super().__init__(name="reader", daemon=True)
        self.key = key
        self.on_values = on_values
        self.on_error = on_error
        self._stop_event = threading.Event()
        self.parser = FrameParser()
        self.last_error = ""
        self.connected = False

    def stop(self) -> None:
        self._stop_event.set()

    def _handle_frame(self, frame: bytes) -> None:
        try:
            values = telegram.decode_frame(frame, self.key)
        except telegram.TelegramError as exc:
            log.debug("telegram rejected: %s", exc)
            if self.on_error:
                self.on_error(exc)
            return
        self.on_values(values)


class SerialReader(_BaseReader):
    def __init__(self, port: str, key: bytes, on_values, on_error=None, baud: int = 9600) -> None:
        super().__init__(key, on_values, on_error)
        if termios is None:
            raise RuntimeError("termios is not available on this platform")
        self.port = port
        self.baud = baud
        self._fd = -1

    def _open(self) -> None:
        fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            self._configure(fd)
        except BaseException as exc:
            os.close(fd)
            if isinstance(exc, termios.error):
                raise OSError("UART configuration failed: %s" % exc) from exc
            raise
        self._fd = fd
        self.connected = True
        self.last_error = ""
        self.parser = FrameParser()
        log.info("Serial port %s opened (%d baud, 8E1)", self.port, self.baud)

    def _configure(self, fd: int) -> None:
        # Prevent two AMIS processes from acknowledging the same meter.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        attrs = termios.tcgetattr(fd)
        speed = getattr(termios, "B%d" % self.baud)
        iflag = 0                                    # no parity checking, no translations
        oflag = 0
        cflag = termios.CS8 | termios.CREAD | termios.CLOCAL | termios.PARENB   # 8E1 (PARODD not set)
        lflag = 0                                    # raw
        cc = list(attrs[6])
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, speed, speed, cc])
        termios.tcflush(fd, termios.TCIOFLUSH)
        actual = termios.tcgetattr(fd)
        # PTYs discard parity bits; real hardware must actually support 8E1.
        if not os.ttyname(fd).startswith("/dev/pts/"):
            mask = termios.CSIZE | termios.PARENB | termios.PARODD | termios.CSTOPB
            if actual[2] & mask != cflag & mask:
                raise OSError("UART does not support 8E1; use a PL011 UART (ttyAMA), not ttyS0")

    def _close(self) -> None:
        self.connected = False
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1

    def _ack(self) -> None:
        try:
            os.write(self._fd, telegram.ACK)
        except OSError as exc:
            log.debug("could not send ACK: %s", exc)

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._open()
            except (OSError, termios.error) as exc:
                self.last_error = "Cannot open %s: %s" % (self.port, exc)
                log.error("Can not open serial port %s: %s (retry in 10s)", self.port, exc)
                if self._stop_event.wait(10):
                    return
                continue
            try:
                self._loop()
            except (OSError, termios.error) as exc:
                self.last_error = "Serial port error: %s" % exc
                log.error("Serial port error: %s", exc)
            finally:
                self._close()
            if self._stop_event.wait(2):
                return

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            ready, _, _ = select.select([self._fd], [], [], 0.5)
            if not ready:
                continue
            data = os.read(self._fd, 256)
            if not data:
                raise OSError("Serial device disconnected (end of stream)")
            for kind, payload in self.parser.feed(data):
                if kind == "nke":
                    log.debug("SND_NKE received - sending ACK")
                    self._ack()
                else:
                    self._ack()                      # received data has to be confirmed
                    self._handle_frame(payload)


class SimulatedReader(_BaseReader):
    """Feeds the demo telegram once per second (key: 1234567890abcdef1234567890abcdef)."""

    def run(self) -> None:
        log.warning("SIMULATION mode: using fixed demo data - NOT reading from a meter!")
        self.connected = True
        try:
            while not self._stop_event.wait(1.0):
                self._handle_frame(telegram.DEMO_FRAME)
        finally:
            self.connected = False
