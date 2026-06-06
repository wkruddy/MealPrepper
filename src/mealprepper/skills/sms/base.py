from __future__ import annotations

from abc import ABC, abstractmethod


class SMSBackend(ABC):
    """Protocol for pluggable SMS delivery backends."""

    @abstractmethod
    def send(self, to: str, body: str) -> bool:
        ...
