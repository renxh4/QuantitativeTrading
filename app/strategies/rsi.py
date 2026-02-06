from __future__ import annotations

from typing import Any

from app.config import RSIConfig
from app.schemas import Indicators, Signal, Tick
from app.strategies.base import Strategy


class RSIStrategy(Strategy):
    def __init__(self, cfg: RSIConfig):
        self._cfg = cfg

    def on_tick(self, tick: Tick, indicators: Indicators) -> tuple[Signal, dict[str, Any]]:
        if indicators.rsi is None:
            return "HOLD", {"reason": "not_enough_data"}

        rsi = float(indicators.rsi)
        if rsi <= self._cfg.buy_below:
            return "BUY", {"reason": "rsi_oversold", "rsi": rsi}
        if rsi >= self._cfg.sell_above:
            return "SELL", {"reason": "rsi_overbought", "rsi": rsi}
        return "HOLD", {"reason": "rsi_neutral", "rsi": rsi}

