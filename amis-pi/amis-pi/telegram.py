# SPDX-License-Identifier: GPL-2.0-or-later
"""Decoding of the encrypted M-Bus telegram of the NetzOÖ Siemens TD-3511/TD-3512 (AMIS).

Based on the analysis done in https://github.com/mgerhard74/amis_smartmeter_reader
(AmisReader.cpp, decodeBuffer()).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import aes

FRAME_LEN = 101           # 0x5F + 6
NKE = b"\x10\x40\xF0\x30\x16"   # SND_NKE to primary address 240 (search mode of the meter)
ACK = b"\xE5"

# Names (OBIS) of the 8 unsigned values, in the order they are in the telegram
OBIS_KEYS = ("1.8.0", "2.8.0", "3.8.1", "4.8.1", "1.7.0", "2.7.0", "3.7.0", "4.7.0")
INKASSO_KEY = "1.128.0"

# Layout of the 80 decrypted bytes (little endian, packed)
_DECRYPTED = struct.Struct("<2s 2s 6s 2s I 3s I 5s I 6s I 2s I 3s I 3s I 4s I 4s i 2s")
assert _DECRYPTED.size == 80

# Expected DIF/VIF markers - they have to match if the key was right
_EXPECTED_MARKERS = (
    (1, b"\x06\x6d", -10),                 # Date/Time
    (3, b"\x04\x03", -11),                 # 1.8.0  A+ [Wh]
    (5, b"\x04\x83\x3c", -12),             # 2.8.0  A- [Wh]
    (7, b"\x84\x10\xfb\x82\x73", -13),     # 3.8.1  R+
    (9, b"\x84\x10\xfb\x82\xf3\x3c", -14), # 4.8.1  R-
    (11, b"\x04\x2b", -15),                # 1.7.0  P+ [W]
    (13, b"\x04\xab\x3c", -16),            # 2.7.0  P-
    (15, b"\x04\xfb\x14", -17),            # 3.7.0  Q+
    (17, b"\x04\xfb\x94\x3c", -18),        # 4.7.0  Q-
    (19, b"\x04\x83\xff\x04", -19),        # 1.128.0 Inkasso
)


class TelegramError(Exception):
    """Telegram can not be used. 'code' is the same as returned by decodeBuffer() of the firmware."""

    def __init__(self, code: int, message: str):
        super().__init__(f"{message} (error {code})")
        self.code = code

    @property
    def wrong_key(self) -> bool:
        # Telegram itself looked fine (header, checksum), but decrypted data is wrong
        return self.code < -7


@dataclass(frozen=True)
class MeterValues:
    ts: int                          # Meter time as unix timestamp (UTC)
    values: tuple[int, ...]          # in the order of OBIS_KEYS
    inkasso: int                     # 1.128.0 (signed)

    def get(self, obis: str) -> int:
        if obis == INKASSO_KEY:
            return self.inkasso
        return self.values[OBIS_KEYS.index(obis)]

    @property
    def import_wh(self) -> int:      # 1.8.0
        return self.values[0]

    @property
    def export_wh(self) -> int:      # 2.8.0
        return self.values[1]

    @property
    def import_w(self) -> int:       # 1.7.0
        return self.values[4]

    @property
    def export_w(self) -> int:       # 2.7.0
        return self.values[5]

    @property
    def saldo_w(self) -> int:        # >0 import, <0 export
        return self.values[4] - self.values[5]


def parse_key(key: str) -> bytes:
    key = key.strip().replace(" ", "")
    if key.lower().startswith("0x"):
        key = key[2:]
    if len(key) != 32:
        raise ValueError("The AMIS key needs 32 hex characters")
    try:
        return bytes.fromhex(key)
    except ValueError as exc:
        raise ValueError("The AMIS key contains invalid characters") from exc


def decode_cp48(data: bytes) -> tuple[datetime, bool]:
    """M-Bus CP48 (type I) date/time -> (naive local time, is_dst)."""
    if data[1] & 0x80:
        raise TelegramError(-20, "time invalid flag set")
    sec = data[0] & 0x3F
    is_dst = bool(data[0] & 0x40)
    minute = data[1] & 0x3F
    hour = data[2] & 0x1F
    wday = data[2] >> 5
    if wday == 0:
        raise TelegramError(-20, "weekday not set")
    mday = data[3] & 0x1F
    month = data[4] & 0x0F
    year = 2000 + (((data[3] & 0xE0) >> 5) | ((data[4] & 0xF0) >> 1))
    try:
        return datetime(year, month, mday, hour, minute, sec), is_dst
    except ValueError as exc:
        raise TelegramError(-20, f"invalid date/time: {exc}") from exc


def cp48_to_unix(data: bytes) -> int:
    """The meter reports CET/CEST: the DST bit tells us which one."""
    local, is_dst = decode_cp48(data)
    offset = timedelta(hours=2 if is_dst else 1)
    return int(local.replace(tzinfo=timezone(offset)).timestamp())


def build_iv(frame: bytes) -> bytes:
    sec = frame[7:16]  # identification(4) vendor(2) version(1) medium(1) accessNumber(1)
    return bytes([sec[4], sec[5], sec[0], sec[1], sec[2], sec[3], sec[6], sec[7]]) + bytes([sec[8]]) * 8


def decode_frame(frame: bytes, key: bytes) -> MeterValues:
    """Check, decrypt and decode one complete 101 byte telegram. Raises TelegramError."""
    if len(frame) < FRAME_LEN:
        raise TelegramError(-1, "telegram too short")
    frame = bytes(frame[:FRAME_LEN])
    if frame[0] != 0x68 or frame[3] != 0x68:
        raise TelegramError(-2, "invalid start sign")
    if frame[1] != frame[2]:
        raise TelegramError(-3, "length fields differ")
    if frame[1] != 0x5F:
        raise TelegramError(-4, "unexpected length")
    if frame[4] not in (0x53, 0x73):
        raise TelegramError(-5, "unexpected C field")
    if frame[100] != 0x16:
        raise TelegramError(-6, "invalid stop sign")
    if sum(frame[4:99]) & 0xFF != frame[99]:
        raise TelegramError(-7, "checksum mismatch")

    plain = aes.cbc_decrypt(frame[19:99], key, build_iv(frame))
    f = _DECRYPTED.unpack(plain)

    if f[0] != b"\x2f\x2f":
        raise TelegramError(-8, "filler at start missing (wrong key?)")
    if f[-1] != b"\x2f\x2f":
        raise TelegramError(-9, "filler at end missing (wrong key?)")
    for idx, marker, code in _EXPECTED_MARKERS:
        if f[idx] != marker:
            raise TelegramError(code, "unexpected DIF/VIF (wrong key?)")

    ts = cp48_to_unix(f[2])
    values = (f[4], f[6], f[8], f[10], f[12], f[14], f[16], f[18])
    return MeterValues(ts=ts, values=tuple(values), inkasso=f[20])


# Demo telegram (incl. a leading SND_NKE) for the key 1234567890abcdef1234567890abcdef
# Taken from AmisReader.cpp ("Demodaten", 2025/12/18 18:34:41)
DEMO_KEY = "1234567890abcdef1234567890abcdef"
DEMO_DATA = bytes([
    0x10, 0x40, 0xF0, 0x30, 0x16,
    0x68, 0x5f, 0x5f, 0x68, 0x53, 0xf0, 0x5b, 0x00, 0x00, 0x00, 0x00, 0x2d, 0x4c, 0x01, 0x0e, 0x00,
    0x50, 0x05, 0x00, 0xf3, 0xd2, 0xd5, 0xaf, 0x0d, 0xf9, 0xe4, 0x3e, 0xcd, 0x57, 0xc5, 0x1e, 0xf1,
    0x57, 0x93, 0x62, 0x5c, 0x98, 0x7f, 0x71, 0x63, 0xc9, 0x5a, 0x4a, 0xec, 0xf4, 0xfc, 0x1d, 0xf1,
    0x93, 0x68, 0x27, 0xd8, 0xf0, 0x01, 0x42, 0x84, 0x15, 0xa2, 0x98, 0xda, 0x6d, 0xe5, 0x27, 0x9f,
    0xa8, 0x2b, 0x7c, 0x84, 0xa6, 0x05, 0xd2, 0x5d, 0x92, 0x6e, 0xf3, 0x93, 0x29, 0x9a, 0x67, 0x4b,
    0x16, 0xd2, 0x6f, 0x68, 0x8d, 0x0b, 0x33, 0xbe, 0x5f, 0xc4, 0xc5, 0x1c, 0x79, 0x2a, 0x6a, 0x7b,
    0x5a, 0x29, 0x1e, 0xdd, 0x16,
])
DEMO_FRAME = DEMO_DATA[5:5 + FRAME_LEN]
