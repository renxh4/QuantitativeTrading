from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas import Tick


class MarketDataProvider(ABC):
    @abstractmethod
    async def get_tick(self, symbol: str) -> Tick:
        raise NotImplementedError

