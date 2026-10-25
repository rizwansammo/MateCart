"""Identifier generation.

MateCart primary keys are UUIDv7 (RFC 9562):
- globally unique, so rows from separate store databases never collide, which keeps moving a
  store between dedicated and shared deployments possible without re-keying;
- not enumerable by outsiders, unlike sequential integers;
- time-ordered, so B-tree indexes stay compact and inserts stay cache-friendly.

Python 3.14 adds uuid.uuid7(); this implementation exists because the services run on 3.13.
"""

import os
import time
import uuid

_RAND_B_MASK = (1 << 62) - 1


def _timestamp_ms() -> int:
    return time.time_ns() // 1_000_000


def uuid7() -> uuid.UUID:
    """48-bit Unix millisecond timestamp, version 7, 12 + 62 random bits, RFC 4122 variant."""
    timestamp_ms = _timestamp_ms()
    random_bits = int.from_bytes(os.urandom(10), "big")  # 80 bits
    value = (timestamp_ms & 0xFFFF_FFFF_FFFF) << 80
    value |= 0x7 << 76
    value |= (random_bits >> 68) << 64  # rand_a: 12 bits
    value |= 0b10 << 62  # variant
    value |= random_bits & _RAND_B_MASK  # rand_b: 62 bits
    return uuid.UUID(int=value)
