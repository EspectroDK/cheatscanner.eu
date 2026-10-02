"""CS2 match share codes (``CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx``).

A share code packs three numbers: the match id, the reservation (outcome) id
and the GOTV port. It does not contain a download address; Valve's Game
Coordinator turns it into one (see ``services/demo-fetcher``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ALPHABET = "ABCDEFGHJKLMNOPQRSTUVWXYZabcdefhijkmnopqrstuvwxyz23456789"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}
_PATTERN = re.compile(r"^CSGO(-[" + _ALPHABET + r"]{5}){5}$")


@dataclass(frozen=True)
class ShareCode:
    match_id: int
    reservation_id: int
    tv_port: int


def is_share_code(code: str) -> bool:
    return bool(_PATTERN.match(code or ""))


def decode(code: str) -> ShareCode:
    if not is_share_code(code):
        raise ValueError("not a CS2 match share code (expected CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx)")
    n = 0
    for c in reversed(code[5:].replace("-", "")):
        n = n * len(_ALPHABET) + _INDEX[c]
    raw = n.to_bytes(18, "big")
    return ShareCode(int.from_bytes(raw[0:8], "little"), int.from_bytes(raw[8:16], "little"),
                     int.from_bytes(raw[16:18], "little"))


def encode(sc: ShareCode) -> str:
    raw = (sc.match_id.to_bytes(8, "little") + sc.reservation_id.to_bytes(8, "little")
           + sc.tv_port.to_bytes(2, "little"))
    n = int.from_bytes(raw, "big")
    chars = []
    for _ in range(25):
        n, r = divmod(n, len(_ALPHABET))
        chars.append(_ALPHABET[r])
    s = "".join(chars)
    return "CSGO-" + "-".join(s[i:i + 5] for i in range(0, 25, 5))
