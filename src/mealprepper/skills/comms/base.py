from __future__ import annotations

from abc import ABC, abstractmethod


class CommsBackend(ABC):
    """Protocol for pluggable meal-plan notification backends."""

    @abstractmethod
    def send(self, to: str, body: str) -> bool:
        ...
