from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional


def sma(values: Deque[float], window: int) -> Optional[float]:
    if window <= 0:
        return None
    if len(values) < window:
        return None
    # deque slice is not supported; iterate.
    s = 0.0
    n = 0
    for v in list(values)[-window:]:
        s += float(v)
        n += 1
    return s / n if n else None


@dataclass
class RSIState:
    period: int
    prev: Optional[float] = None
    avg_gain: Optional[float] = None
    avg_loss: Optional[float] = None
    count: int = 0


def update_rsi(state: RSIState, price: float) -> Optional[float]:
    """
    Wilder's RSI. Returns RSI once enough samples are collected.
    """
    p = float(price)
    if state.prev is None:
        state.prev = p
        return None

    change = p - state.prev
    gain = max(change, 0.0)
    loss = max(-change, 0.0)
    state.prev = p

    if state.avg_gain is None or state.avg_loss is None:
        # Seed averages using simple average over first `period` deltas.
        if state.count == 0:
            state.avg_gain = 0.0
            state.avg_loss = 0.0
        state.avg_gain += gain
        state.avg_loss += loss
        state.count += 1

        if state.count < state.period:
            return None

        state.avg_gain /= state.period
        state.avg_loss /= state.period
    else:
        # Wilder smoothing
        state.avg_gain = (state.avg_gain * (state.period - 1) + gain) / state.period
        state.avg_loss = (state.avg_loss * (state.period - 1) + loss) / state.period

    if state.avg_loss == 0:
        return 100.0

    rs = state.avg_gain / state.avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(rsi)

