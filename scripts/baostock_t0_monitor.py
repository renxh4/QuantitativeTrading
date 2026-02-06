from __future__ import annotations

"""
Baostock 监控脚本（分钟K轮询版）

需求实现：
- Base = 9:25 开盘价（若 Baostock 无法提供 9:25，则降级为“当日第一根分钟K的开盘价”，并提示）
- 买入报警（红色）：现价跌破 Base 的 1.5% 且成交量较前 5 分钟萎缩
- 买入后：
  - 卖出报警（绿色）：涨幅达 1.2% 或 时间到达 14:50
  - 熔断报警（蓝色）：亏损达 2%
- 使用 Baostock 获取数据 + 完善异常处理（重试/退避/空数据/登录失败）

注意：
Baostock 更偏历史/分钟K数据，严格意义上不是毫秒级实时源；
本脚本用“轮询最新一分钟K的收盘价/成交量”做近实时监控。
"""

import argparse
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple

try:
    from termcolor import colored  # type: ignore
except Exception:

    def colored(s: str, *_args, **_kwargs):  # type: ignore
        return s


def _sleep_backoff(attempt: int, base: float = 1.5, cap: float = 20.0) -> None:
    t = min(cap, base * (2 ** max(0, attempt - 1)))
    time.sleep(t)


def normalize_symbol_to_baostock(code: str) -> str:
    c = str(code).strip().lower()
    if c.startswith(("sh.", "sz.")) and len(c) == 9:
        return c
    if len(c) == 6 and c.isdigit():
        # 6xxxxx -> SH else -> SZ
        return ("sh." if c.startswith("6") else "sz.") + c
    raise ValueError(f"股票代码格式不支持: {code}（期望 000630 或 sz.000630）")


@dataclass
class Config:
    symbol: str
    interval_seconds: int = 10
    buy_drop_pct: float = 1.5  # 跌破 Base 的百分比
    sell_gain_pct: float = 1.2  # 买入后盈利百分比触发卖出
    stop_loss_pct: float = 2.0  # 买入后亏损百分比触发熔断
    sell_time_hhmm: str = "14:50"
    vol_shrink_ratio: float = 0.7  # 当前分钟量 < 前5分钟均量 * ratio 视为“萎缩”
    max_retries: int = 5


@dataclass
class PositionState:
    in_position: bool = False
    base_price: Optional[float] = None
    base_source: str = "-"
    buy_price: Optional[float] = None
    buy_time: Optional[str] = None


def hhmm(now: datetime) -> str:
    return now.strftime("%H:%M")


def is_trading_time(now: datetime) -> bool:
    # 9:25-11:30, 13:00-15:00
    if now.hour == 9 and now.minute >= 25:
        return True
    if 10 <= now.hour < 11:
        return True
    if now.hour == 11 and now.minute <= 30:
        return True
    if 13 <= now.hour < 15:
        return True
    return False


def _fetch_today_minute_bars_bs(symbol_bs: str, date_yyyymmdd: str):
    import baostock as bs  # type: ignore

    # fields: date,time,open,high,low,close,volume
    # frequency: "1" minute
    rs = bs.query_history_k_data_plus(
        symbol_bs,
        fields="date,time,open,high,low,close,volume",
        start_date=date_yyyymmdd,
        end_date=date_yyyymmdd,
        frequency="1",
        adjustflag="3",
    )
    if rs.error_code != "0":
        raise RuntimeError(f"baostock 查询失败: {rs.error_code} {rs.error_msg}")

    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return rows


def _parse_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except Exception:
        return None


def get_latest_price_and_volume(
    symbol_bs: str, date_yyyymmdd: str
) -> Tuple[Optional[float], Optional[float], int]:
    """
    返回 (latest_price, latest_minute_volume, bars_count)
    latest_price 使用最后一根 1分钟K 的 close
    volume 使用最后一根 1分钟K 的 volume
    """
    rows = _fetch_today_minute_bars_bs(symbol_bs, date_yyyymmdd)
    if not rows:
        return None, None, 0

    last = rows[-1]
    # row: [date,time,open,high,low,close,volume]
    close = _parse_float(last[5])
    vol = _parse_float(last[6])
    return close, vol, len(rows)


def get_volume_shrink_ok(symbol_bs: str, date_yyyymmdd: str, ratio: float) -> Optional[bool]:
    """
    判断“成交量较前5分钟萎缩”：
    - 取最后一根分钟K的 volume = v_now
    - 取前5根分钟K的 volume 均值 = v_avg
    - 若 v_now < v_avg * ratio => True
    返回 None 表示数据不足（<6根）
    """
    rows = _fetch_today_minute_bars_bs(symbol_bs, date_yyyymmdd)
    if len(rows) < 6:
        return None
    vols = []
    for r in rows[-6:-1]:
        v = _parse_float(r[6])
        if v is not None:
            vols.append(v)
    v_now = _parse_float(rows[-1][6])
    if v_now is None or len(vols) < 3:
        return None
    v_avg = sum(vols) / len(vols)
    return v_now < (v_avg * float(ratio))


def get_base_price_0925_or_fallback(symbol_bs: str, date_yyyymmdd: str) -> Tuple[Optional[float], str]:
    """
    Base=9:25开盘价。
    Baostock 分钟K通常从 09:30 开始；若找不到 09:25，则用“当日第一根分钟K开盘价”。
    """
    rows = _fetch_today_minute_bars_bs(symbol_bs, date_yyyymmdd)
    if not rows:
        return None, "NO_DATA"

    # Try find exact 09:25 bar
    for r in rows:
        t = str(r[1])  # HH:MM:SS
        if t.startswith("09:25"):
            o = _parse_float(r[2])
            return o, "09:25"

    # Fallback: first bar open
    o0 = _parse_float(rows[0][2])
    t0 = str(rows[0][1])
    return o0, f"FALLBACK_FIRST_BAR_{t0}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="股票代码：000630 或 sz.000630")
    ap.add_argument("--interval", type=int, default=10, help="轮询间隔秒，默认 10")
    ap.add_argument("--vol-ratio", type=float, default=0.7, help="量能萎缩比率，默认 0.7")
    args = ap.parse_args()

    cfg = Config(
        symbol=normalize_symbol_to_baostock(args.symbol),
        interval_seconds=int(args.interval),
        vol_shrink_ratio=float(args.vol_ratio),
    )
    st = PositionState()

    print(colored(f"--- Baostock 做T监控启动：{cfg.symbol} ---", "cyan"))
    print(
        f"规则：Base=9:25开盘；跌破-{cfg.buy_drop_pct:.2f}% 且量能萎缩(<{cfg.vol_shrink_ratio:.2f}×前5分钟均量) -> 买入报警；"
        f"买入后 +{cfg.sell_gain_pct:.2f}% 或 14:50 -> 卖出报警；- {cfg.stop_loss_pct:.2f}% -> 熔断报警"
    )

    import baostock as bs  # type: ignore

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_code} {lg.error_msg}")

    try:
        while True:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            yyyymmdd = now.strftime("%Y-%m-%d")

            if not is_trading_time(now):
                print(f"[{now.strftime('%H:%M:%S')}] 非交易时段，等待中…")
                time.sleep(cfg.interval_seconds)
                continue

            # init base price once per day
            if st.base_price is None:
                attempt = 0
                while True:
                    attempt += 1
                    try:
                        base, src = get_base_price_0925_or_fallback(cfg.symbol, yyyymmdd)
                        if base is None:
                            raise RuntimeError("Base 价为空（分钟K无数据）")
                        st.base_price = float(base)
                        st.base_source = src
                        print(
                            colored(
                                f"[{now.strftime('%H:%M:%S')}] Base 已设置: {st.base_price:.3f}（来源={st.base_source}）",
                                "yellow",
                            )
                        )
                        break
                    except Exception as e:
                        print(colored(f"[{now.strftime('%H:%M:%S')}] 获取 Base 失败：{type(e).__name__}: {e}", "yellow"))
                        if attempt >= cfg.max_retries:
                            print(colored("获取 Base 多次失败，继续等待下一轮…", "yellow"))
                            break
                        _sleep_backoff(attempt)

            if st.base_price is None:
                time.sleep(cfg.interval_seconds)
                continue

            # fetch latest
            attempt = 0
            price = None
            vol = None
            bars = 0
            vol_shrink = None
            while True:
                attempt += 1
                try:
                    price, vol, bars = get_latest_price_and_volume(cfg.symbol, yyyymmdd)
                    vol_shrink = get_volume_shrink_ok(cfg.symbol, yyyymmdd, cfg.vol_shrink_ratio)
                    break
                except Exception as e:
                    print(colored(f"[{now.strftime('%H:%M:%S')}] 拉取分钟K失败：{type(e).__name__}: {e}", "yellow"))
                    if attempt >= cfg.max_retries:
                        break
                    _sleep_backoff(attempt)

            if price is None:
                time.sleep(cfg.interval_seconds)
                continue

            # status line
            drop_from_base = (price - st.base_price) / st.base_price
            vol_note = "量能:数据不足" if vol_shrink is None else ("量能:萎缩" if vol_shrink else "量能:不萎缩")
            print(
                f"[{now.strftime('%H:%M:%S')}] 现价={price:.3f} 相对Base={drop_from_base:.2%} bars={bars} {vol_note}"
            )

            # state machine
            if not st.in_position:
                buy_trigger = price <= st.base_price * (1 - cfg.buy_drop_pct / 100.0)
                vol_trigger = (vol_shrink is True)
                if buy_trigger and vol_trigger:
                    st.in_position = True
                    st.buy_price = price
                    st.buy_time = now.strftime("%H:%M:%S")
                    msg = f"买入报警：跌破Base-{cfg.buy_drop_pct:.2f}% 且量能萎缩 | price={price:.3f} base={st.base_price:.3f}"
                    print(colored(msg, "red", attrs=["bold"]))
            else:
                assert st.buy_price is not None
                pnl = (price - st.buy_price) / st.buy_price

                # circuit breaker
                if pnl <= -(cfg.stop_loss_pct / 100.0):
                    msg = f"熔断报警：亏损达到 {cfg.stop_loss_pct:.2f}% | buy={st.buy_price:.3f} now={price:.3f} pnl={pnl:.2%}"
                    print(colored(msg, "blue", attrs=["bold"]))
                    # reset position after circuit breaker
                    st.in_position = False
                    st.buy_price = None
                    st.buy_time = None
                else:
                    # sell by profit or time
                    if pnl >= (cfg.sell_gain_pct / 100.0) or hhmm(now) >= cfg.sell_time_hhmm:
                        reason = "达到止盈" if pnl >= (cfg.sell_gain_pct / 100.0) else "到达14:50"
                        msg = f"卖出报警：{reason} | buy={st.buy_price:.3f} now={price:.3f} pnl={pnl:.2%}"
                        print(colored(msg, "green", attrs=["bold"]))
                        st.in_position = False
                        st.buy_price = None
                        st.buy_time = None

            time.sleep(cfg.interval_seconds)

    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，退出。")
    finally:
        bs.logout()


if __name__ == "__main__":
    main()

