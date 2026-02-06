from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional
import traceback

from app.broker import PaperBroker
from app.config import BrokerConfig, Config, StrategyConfig
from app.indicators import RSIState, sma, update_rsi
from app.providers.base import MarketDataProvider
from app.schemas import Indicators, Signal, Tick
from app.strategies.base import Strategy
from app.strategies.ma_crossover import MACrossoverStrategy
from app.strategies.rsi import RSIStrategy
from app.ws import WSManager


def build_strategy(cfg: StrategyConfig) -> Strategy:
    if cfg.type == "ma_crossover":
        return MACrossoverStrategy(cfg.ma_crossover)
    if cfg.type == "rsi":
        return RSIStrategy(cfg.rsi)
    raise ValueError(f"Unknown strategy type: {cfg.type}")


class Engine:
    def __init__(
        self,
        cfg: Config,
        provider: MarketDataProvider,
        ws: WSManager,
    ) -> None:
        self.cfg = cfg
        self.provider = provider
        self.ws = ws

        self.strategy: Strategy = build_strategy(cfg.strategy)
        self.broker = PaperBroker(starting_cash=cfg.broker.starting_cash)
        self._order_size = int(cfg.broker.order_size)

        self._prices: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=5000))
        self._rsi_state: Dict[str, RSIState] = {}
        self._last_tick: Dict[str, Tick] = {}
        self._last_indicators: Dict[str, Indicators] = {}
        self._mark_prices: Dict[str, float] = {}

        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()
        self._last_ok_ts: Dict[str, Optional[str]] = {s: None for s in cfg.app.symbols}
        self._last_err: Dict[str, Optional[str]] = {s: None for s in cfg.app.symbols}
        self._tick_count: Dict[str, int] = {s: 0 for s in cfg.app.symbols}

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        self._task = None

    def health(self) -> dict[str, Any]:
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "engine_task_running": self._task is not None and not self._task.done(),
            "symbols": self.cfg.app.symbols,
            "last_ok_ts": self._last_ok_ts,
            "last_error": self._last_err,
            "tick_count": self._tick_count,
        }

    def snapshot(self) -> dict[str, Any]:
        def _tick_json(t: Tick) -> dict[str, Any]:
            return {"symbol": t.symbol, "ts": t.ts.isoformat(), "price": float(t.price)}

        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbols": self.cfg.app.symbols,
            "cash": self.broker.cash,
            "equity": self.broker.equity(self._mark_prices),
            "positions": [asdict(p) for p in self.broker.positions.values()],
            "provider_health": {
                "last_ok_ts": self._last_ok_ts,
                "last_error": self._last_err,
                "tick_count": self._tick_count,
            },
            "last": {
                sym: {
                    "tick": _tick_json(self._last_tick[sym]) if sym in self._last_tick else None,
                    "indicators": asdict(self._last_indicators[sym])
                    if sym in self._last_indicators
                    else None,
                }
                for sym in self.cfg.app.symbols
            },
        }

    def _ensure_rsi(self, symbol: str) -> RSIState:
        st = self._rsi_state.get(symbol)
        if st is None:
            st = RSIState(period=int(self.cfg.strategy.rsi.period))
            self._rsi_state[symbol] = st
        return st

    def _calc_indicators(self, symbol: str, price: float) -> Indicators:
        prices = self._prices[symbol]
        prices.append(float(price))

        ma_short = None
        ma_long = None
        if self.cfg.strategy.type == "ma_crossover":
            ma_short = sma(prices, int(self.cfg.strategy.ma_crossover.short_window))
            ma_long = sma(prices, int(self.cfg.strategy.ma_crossover.long_window))

        rsi_val = None
        if self.cfg.strategy.type == "rsi":
            rsi_val = update_rsi(self._ensure_rsi(symbol), float(price))

        return Indicators(ma_short=ma_short, ma_long=ma_long, rsi=rsi_val)

    def _execute_signal(self, symbol: str, signal: Signal, price: float) -> None:
        if signal == "BUY":
            self.broker.market_buy(symbol, qty=self._order_size, price=price)
        elif signal == "SELL":
            self.broker.market_sell(symbol, qty=self._order_size, price=price)

    async def _run_loop(self) -> None:
        interval = float(self.cfg.app.interval_seconds)

        while not self._stop.is_set():
            start = asyncio.get_event_loop().time()

            for symbol in self.cfg.app.symbols:
                try:
                    tick = await self.provider.get_tick(symbol)
                    self._last_tick[symbol] = tick
                    self._mark_prices[symbol] = float(tick.price)
                    self._last_ok_ts[symbol] = tick.ts.isoformat()
                    self._last_err[symbol] = None
                    self._tick_count[symbol] = int(self._tick_count.get(symbol, 0)) + 1

                    indicators = self._calc_indicators(symbol, float(tick.price))
                    self._last_indicators[symbol] = indicators

                    signal, meta = self.strategy.on_tick(tick, indicators)
                    self._execute_signal(symbol, signal, float(tick.price))

                    msg = {
                        "type": "tick",
                        "symbol": symbol,
                        "ts": tick.ts.isoformat(),
                        "price": float(tick.price),
                        "indicators": asdict(indicators),
                        "signal": signal,
                        "signal_meta": meta,
                        "broker": {
                            "cash": self.broker.cash,
                            "equity": self.broker.equity(self._mark_prices),
                            "positions": [asdict(p) for p in self.broker.positions.values()],
                            "last_order": asdict(self.broker.last_order)
                            if self.broker.last_order is not None
                            else None,
                        },
                    }
                    await self.ws.broadcast(msg)
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                    self._last_err[symbol] = err
                    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))[-2000:]
                    await self.ws.broadcast(
                        {
                            "type": "error",
                            "symbol": symbol,
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "error": err,
                            "trace": tb,
                        }
                    )

            elapsed = asyncio.get_event_loop().time() - start
            sleep_for = max(0.0, interval - elapsed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass

