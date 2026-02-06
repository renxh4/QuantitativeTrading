from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field


class SimulatedProviderConfig(BaseModel):
    start_price: float = 100.0
    drift: float = 0.0
    volatility: float = 0.01
    seed: Optional[int] = None


class EastmoneyProviderConfig(BaseModel):
    """
    Eastmoney quote API (unofficial). No token required.
    """

    base_url: str = "https://push2.eastmoney.com"
    timeout_seconds: float = 5.0
    # Optional proxy (e.g. "http://127.0.0.1:7890")
    proxy: Optional[str] = None


class ProviderConfig(BaseModel):
    type: Literal["simulated", "eastmoney"] = "simulated"
    simulated: SimulatedProviderConfig = Field(default_factory=SimulatedProviderConfig)
    eastmoney: EastmoneyProviderConfig = Field(default_factory=EastmoneyProviderConfig)


class MACrossoverConfig(BaseModel):
    short_window: int = 10
    long_window: int = 30


class RSIConfig(BaseModel):
    period: int = 14
    buy_below: float = 30.0
    sell_above: float = 70.0


class StrategyConfig(BaseModel):
    type: Literal["ma_crossover", "rsi"] = "ma_crossover"
    ma_crossover: MACrossoverConfig = Field(default_factory=MACrossoverConfig)
    rsi: RSIConfig = Field(default_factory=RSIConfig)


class BrokerConfig(BaseModel):
    starting_cash: float = 100000.0
    order_size: int = 10


class AppConfig(BaseModel):
    interval_seconds: float = 1.0
    symbols: list[str] = Field(default_factory=lambda: ["AAPL"])


class Config(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)


def load_config(config_path: str | Path) -> Config:
    p = Path(config_path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)

