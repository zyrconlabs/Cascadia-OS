"""
base.py — Cascadia OS v0.47
Abstract base class for IoT sensor adapters.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Callable


class SensorAdapter(ABC):
    def __init__(self, config: dict) -> None:
        self._config = config
        self._callback: Callable | None = None
        self._connected = False

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def start(self, callback: Callable) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    def is_connected(self) -> bool:
        return self._connected
