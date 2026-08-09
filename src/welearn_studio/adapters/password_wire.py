"""Password transformation required by the identity wire protocol."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True, slots=True)
class PasswordWireValue:
    encoded: str = field(repr=False)
    timestamp: int = field(repr=False)


def encode_password_for_wire(
    password: str,
    *,
    now_milliseconds: Callable[[], int] | None = None,
) -> PasswordWireValue:
    """Return the timestamp and encoded password expected by identity login."""
    if not isinstance(password, str):
        raise TypeError("password must be text")

    clock = now_milliseconds or (lambda: int(time.time() * 1000))
    original_timestamp = int(clock())
    if original_timestamp < 0:
        raise ValueError("timestamp must be non-negative")

    password_bytes = password.encode("utf-8")
    accumulator = (original_timestamp >> 16) & 0xFF
    for value in password_bytes:
        accumulator ^= value

    timestamp = (original_timestamp // 100) * 100 + accumulator % 100
    clear_wire_text = f"{timestamp}*{password_bytes.hex()}".encode("utf-8")
    encoded = base64.b64encode(clear_wire_text).decode("ascii")
    return PasswordWireValue(encoded=encoded, timestamp=timestamp)
