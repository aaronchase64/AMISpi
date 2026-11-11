# SPDX-License-Identifier: GPL-2.0-or-later
"""Minimal AES-128 (decrypt only) with CBC mode - pure Python, no dependencies.

The meter sends 80 bytes per second, so speed does not matter at all.
Verified against FIPS-197 and against the `cryptography` package (see tests).
"""

from __future__ import annotations


def _build_sbox() -> list[int]:
    # Generate the S-Box (see Wikipedia "Rijndael S-box") instead of typing 256 constants
    sbox = [0] * 256
    p = q = 1
    while True:
        # p *= 3
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        # q /= 3
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        rot = lambda x, n: ((x << n) | (x >> (8 - n))) & 0xFF
        sbox[p] = q ^ rot(q, 1) ^ rot(q, 2) ^ rot(q, 3) ^ rot(q, 4) ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


_SBOX = _build_sbox()
_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x11B) & 0xFF if a & 0x100 else a


def _gmul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


_MUL9 = [_gmul(i, 9) for i in range(256)]
_MUL11 = [_gmul(i, 11) for i in range(256)]
_MUL13 = [_gmul(i, 13) for i in range(256)]
_MUL14 = [_gmul(i, 14) for i in range(256)]

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _expand_key(key: bytes) -> list[list[int]]:
    if len(key) != 16:
        raise ValueError("AES-128 needs a 16 byte key")
    words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = list(words[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // 4 - 1]
        words.append([a ^ b for a, b in zip(words[i - 4], t)])
    # 11 round keys of 16 bytes
    return [sum(words[r * 4:r * 4 + 4], []) for r in range(11)]


def decrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    s = [b ^ k for b, k in zip(block, round_keys[10])]
    for rnd in range(9, -1, -1):
        # InvShiftRows (state is column-major: index = row + 4 * col)
        t = [0] * 16
        for c in range(4):
            for r in range(4):
                t[r + 4 * ((c + r) % 4)] = s[r + 4 * c]
        # InvSubBytes
        t = [_INV_SBOX[b] for b in t]
        # AddRoundKey
        t = [b ^ k for b, k in zip(t, round_keys[rnd])]
        if rnd:
            # InvMixColumns
            s = []
            for c in range(4):
                a0, a1, a2, a3 = t[4 * c:4 * c + 4]
                s += [
                    _MUL14[a0] ^ _MUL11[a1] ^ _MUL13[a2] ^ _MUL9[a3],
                    _MUL9[a0] ^ _MUL14[a1] ^ _MUL11[a2] ^ _MUL13[a3],
                    _MUL13[a0] ^ _MUL9[a1] ^ _MUL14[a2] ^ _MUL11[a3],
                    _MUL11[a0] ^ _MUL13[a1] ^ _MUL9[a2] ^ _MUL14[a3],
                ]
        else:
            s = t
    return bytes(s)


def cbc_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC decrypt without padding handling."""
    if len(data) % 16:
        raise ValueError("data length must be a multiple of 16")
    if len(iv) != 16:
        raise ValueError("IV must have 16 bytes")
    round_keys = _expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        plain = decrypt_block(block, round_keys)
        out += bytes(a ^ b for a, b in zip(plain, prev))
        prev = block
    return bytes(out)
