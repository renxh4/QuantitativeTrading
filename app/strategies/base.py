from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.schemas import Indicators, Signal, Tick


class Strategy(ABC):
    @abstractmethod
    def on_tick(self, tick: Tick, indicators: Indicators) -> tuple[Signal, dict[str, Any]]:
        """
        Returns (signal, meta).
        meta is for UI/debug (e.g. reason, thresholds, crosses).
        """
        raise NotImplementedError

