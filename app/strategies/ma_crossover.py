from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.config import MACrossoverConfig
from app.schemas import Indicators, Signal, Tick
from app.strategies.base import Strategy


@dataclass
class _State:
    prev_diff: Optional[float] = None


class MACrossoverStrategy(Strategy):
    def __init__(self, cfg: MACrossoverConfig):
        self._cfg = cfg
        self._state_by_symbol: dict[str, _State] = {}

    def _state(self, symbol: str) -> _State:
        st = self._state_by_symbol.get(symbol)
        if st is None:
            st = _State()
            self._state_by_symbol[symbol] = st
        return st

    def on_tick(self, tick: Tick, indicators: Indicators) -> tuple[Signal, dict[str, Any]]:
        if indicators.ma_short is None or indicators.ma_long is None:
            return "HOLD", {"reason": "not_enough_data"}

        diff = float(indicators.ma_short - indicators.ma_long)
        st = self._state(tick.symbol)

        signal: Signal = "HOLD"
        reason = "no_cross"
        if st.prev_diff is not None:
            if st.prev_diff <= 0 and diff > 0:
                signal = "BUY"
                reason = "golden_cross"
            elif st.prev_diff >= 0 and diff < 0:
                signal = "SELL"
                reason = "death_cross"

        st.prev_diff = diff
        return signal, {"reason": reason, "diff": diff}

