"""
RAM ring buffer for log events.

Holds the most recent N events in memory using deque.
Events that classify as audit/security/checkpoint are
flushed via flush_callback. Others stay in RAM only.

Phase 3 wires this into cascadia/shared/logger.py as an
optional handler.
"""

from collections import deque
from threading import Lock
from typing import Callable, Optional


class RingBuffer:
    """Bounded in-memory buffer for log events."""

    def __init__(self,
                 capacity: int = 10000,
                 flush_callback: Optional[Callable] = None):
        self._buffer = deque(maxlen=capacity)
        self._lock = Lock()
        self._flush_callback = flush_callback
        self._dropped_count = 0

    def append(self, event: dict) -> None:
        with self._lock:
            if len(self._buffer) == self._buffer.maxlen:
                self._dropped_count += 1
            self._buffer.append(event)
        if self._flush_callback is not None:
            self._flush_callback(event)

    def snapshot(self) -> list:
        with self._lock:
            return list(self._buffer)

    def stats(self) -> dict:
        with self._lock:
            return {
                "current_size":  len(self._buffer),
                "capacity":      self._buffer.maxlen,
                "dropped_count": self._dropped_count,
            }

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._dropped_count = 0
