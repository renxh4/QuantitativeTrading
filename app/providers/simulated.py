from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import random

from app.config import SimulatedProviderConfig
from app.providers.base import MarketDataProvider
from app.schemas import Tick


@dataclass
class _SymbolSim:
    price: float


class SimulatedProvider(MarketDataProvider):
    """
    Simple geometric random walk generator. Useful for offline demos and UI testing.
    """

    def __init__(self, cfg: SimulatedProviderConfig):
        self._cfg = cfg
        self._rng = random.Random(cfg.seed)
        self._state: dict[str, _SymbolSim] = {}

    def _ensure(self, symbol: str) -> _SymbolSim:
        st = self._state.get(symbol)
        if st is None:
            st = _SymbolSim(price=float(self._cfg.start_price))
            self._state[symbol] = st
        return st

    async def get_tick(self, symbol: str) -> Tick:
        st = self._ensure(symbol)

        # dt=1 step; r ~ N(drift, vol)
        r = float(self._rng.gauss(mu=self._cfg.drift, sigma=self._cfg.volatility))
        st.price = max(0.01, st.price * math.exp(r))

        return Tick(symbol=symbol, ts=datetime.now(timezone.utc), price=float(st.price))

