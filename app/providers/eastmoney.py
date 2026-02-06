from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import EastmoneyProviderConfig
from app.providers.base import MarketDataProvider
from app.schemas import Tick


_CODE_RE = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class EastmoneySecId:
    """
    Eastmoney secid format: "{market}.{code}"
    market: 1=SH, 0=SZ
    """

    market: int
    code: str

    def as_param(self) -> str:
        return f"{self.market}.{self.code}"


def parse_a_share_symbol(symbol: str) -> EastmoneySecId:
    """
    Accepts:
    - "600000.SH" / "000001.SZ"
    - "sh600000" / "sz000001"
    - "600000" / "000001" (auto infer: 6xxxxx -> SH, else -> SZ)
    """

    s = symbol.strip().upper()

    if s.startswith(("SH", "SZ")) and len(s) == 8 and _CODE_RE.match(s[2:]):
        code = s[2:]
        market = 1 if s.startswith("SH") else 0
        return EastmoneySecId(market=market, code=code)

    if s.endswith((".SH", ".SZ")) and len(s) == 9 and _CODE_RE.match(s[:6]):
        code = s[:6]
        market = 1 if s.endswith(".SH") else 0
        return EastmoneySecId(market=market, code=code)

    if _CODE_RE.match(s):
        code = s
        market = 1 if code.startswith("6") else 0
        return EastmoneySecId(market=market, code=code)

    raise ValueError(f"Unsupported A-share symbol format: {symbol}")


class EastmoneyProvider(MarketDataProvider):
    """
    Real-time quote via Eastmoney (unofficial).

    Endpoint:
      /api/qt/stock/get?secid=1.600000&fields=f43,f44,f45,f46,f47,f48,f58,f57,f59,f60,f170
    Notes:
      - f43: latest price * 100? (actually price * 100 in some endpoints; in stock/get it's usually price * 100)
      - We'll handle both int and float by dividing by 100 if it looks like a scaled int.
    """

    def __init__(self, cfg: EastmoneyProviderConfig):
        self._cfg = cfg
        # httpx>=0.28 uses `proxy=...` (singular). Some older versions used `proxies=...`.
        # We keep a small compatibility shim here.
        common_kwargs = dict(
            base_url=cfg.base_url,
            timeout=httpx.Timeout(cfg.timeout_seconds),
            headers={
                "User-Agent": "Mozilla/5.0 (QuantTool/0.1; +https://localhost)",
                "Accept": "application/json,text/plain,*/*",
            },
        )
        try:
            # httpx 0.28+
            self._client = httpx.AsyncClient(proxy=cfg.proxy, **common_kwargs)
        except TypeError:
            proxies: Optional[dict[str, str]] = None
            if cfg.proxy:
                proxies = {"http://": cfg.proxy, "https://": cfg.proxy}
            self._client = httpx.AsyncClient(proxies=proxies, **common_kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _normalize_price(v: object) -> float:
        """
        Eastmoney sometimes returns scaled integers (price * 100). Try to detect.
        """
        if v is None:
            raise ValueError("Missing price")
        px = float(v)
        # Heuristic: A-share price rarely > 10_000; if it's large, assume scaled by 100.
        if px > 10_000:
            px = px / 100.0
        return float(px)

    async def get_tick(self, symbol: str) -> Tick:
        sec = parse_a_share_symbol(symbol)
        params = {
            "secid": sec.as_param(),
            "fields": "f43,f58,f57,f59,f170,f44,f45,f46,f47,f48",
        }
        r = await self._client.get("/api/qt/stock/get", params=params)
        r.raise_for_status()
        j = r.json()
        data = j.get("data") or {}
        if not data:
            raise ValueError(f"Empty quote data: {j!r}")

        price = self._normalize_price(data.get("f43"))
        return Tick(symbol=symbol, ts=datetime.now(timezone.utc), price=price)

