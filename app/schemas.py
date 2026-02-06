from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional


Signal = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True)
class Tick:
    symbol: str
    ts: datetime
    price: float


@dataclass(frozen=True)
class Indicators:
    ma_short: Optional[float] = None
    ma_long: Optional[float] = None
    rsi: Optional[float] = None


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: int = 0
    avg_price: float = 0.0

