from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from app.schemas import Position


@dataclass
class Order:
    symbol: str
    side: str  # BUY/SELL
    qty: int
    price: float


class PaperBroker:
    def __init__(self, starting_cash: float):
        self.cash: float = float(starting_cash)
        self.positions: Dict[str, Position] = {}
        self.last_order: Optional[Order] = None

    def _pos(self, symbol: str) -> Position:
        return self.positions.get(symbol, Position(symbol=symbol, qty=0, avg_price=0.0))

    def market_buy(self, symbol: str, qty: int, price: float) -> None:
        qty = int(qty)
        if qty <= 0:
            return
        price = float(price)
        cost = qty * price
        if cost > self.cash:
            # insufficient cash -> ignore
            return

        pos = self._pos(symbol)
        new_qty = pos.qty + qty
        new_avg = ((pos.avg_price * pos.qty) + (price * qty)) / new_qty if new_qty else 0.0
        self.positions[symbol] = Position(symbol=symbol, qty=new_qty, avg_price=float(new_avg))
        self.cash -= cost
        self.last_order = Order(symbol=symbol, side="BUY", qty=qty, price=price)

    def market_sell(self, symbol: str, qty: int, price: float) -> None:
        qty = int(qty)
        if qty <= 0:
            return
        price = float(price)

        pos = self._pos(symbol)
        if pos.qty <= 0:
            return

        sell_qty = min(qty, pos.qty)
        proceeds = sell_qty * price
        new_qty = pos.qty - sell_qty
        new_avg = pos.avg_price if new_qty else 0.0
        self.positions[symbol] = Position(symbol=symbol, qty=new_qty, avg_price=float(new_avg))
        self.cash += proceeds
        self.last_order = Order(symbol=symbol, side="SELL", qty=sell_qty, price=price)

    def equity(self, mark_prices: Dict[str, float]) -> float:
        eq = self.cash
        for sym, pos in self.positions.items():
            px = float(mark_prices.get(sym, 0.0))
            eq += pos.qty * px
        return float(eq)

