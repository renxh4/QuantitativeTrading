from __future__ import annotations

import argparse
import random
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Callable, Literal

import akshare as ak
import httpx

try:
    from termcolor import colored  # type: ignore
except Exception:

    def colored(s: str, *_args: Any, **_kwargs: Any) -> str:  # type: ignore
        return s


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "").replace("%", "")
    if not s or s in {"-", "None", "nan", "NaN"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def show_alert(title: str, message: str) -> None:
    """
    Best-effort popup alert:
    - macOS: osascript
    - Windows: MessageBoxW
    - Linux: notify-send (if available)
    Always prints to stdout as fallback.
    """
    print(f"[弹窗] {title}: {message}")

    sysname = platform.system()
    try:
        if sysname == "Darwin":
            script = f'display alert "{title}" message "{message}" as critical'
            subprocess.run(["osascript", "-e", script], check=False)
            return
        if sysname == "Windows":
            import ctypes  # noqa: PLC0415

            ctypes.windll.user32.MessageBoxW(0, message, title, 0x30)  # MB_ICONWARNING
            return
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], check=False)
            return
    except Exception:
        return


def prompt_choice(title: str, message: str, *, yes_text: str, no_text: str, default_yes: bool = False) -> bool:
    """
    弹窗让用户做选择，并返回 True/False。
    - True: 选择 yes_text（例如“买入/卖出”）
    - False: 选择 no_text（例如“不买/不卖”）
    尽量使用系统弹窗；如果系统不支持则回退到终端输入。
    """
    sysname = platform.system()

    # macOS: AppleScript dialog with buttons
    if sysname == "Darwin":
        # Return format: "button returned:买入"
        default_btn = yes_text if default_yes else no_text
        script = (
            f'display dialog "{message}" with title "{title}" '
            f'buttons {{"{no_text}","{yes_text}"}} default button "{default_btn}"'
        )
        try:
            p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
            out = (p.stdout or "").strip()
            if f"button returned:{yes_text}" in out:
                return True
            if f"button returned:{no_text}" in out:
                return False
            # user canceled -> treat as NO
            return False
        except Exception:
            return False

    # Windows: MessageBox Yes/No
    if sysname == "Windows":
        try:
            import ctypes  # noqa: PLC0415

            MB_YESNO = 0x04
            MB_ICONQUESTION = 0x20
            IDYES = 6
            r = ctypes.windll.user32.MessageBoxW(0, message, title, MB_YESNO | MB_ICONQUESTION)
            return bool(r == IDYES)
        except Exception:
            return False

    # Linux/others: best effort in terminal
    try:
        ans = input(f"{title}: {message} [{yes_text}/{no_text}] (默认{'是' if default_yes else '否'}): ").strip()
        if not ans:
            return default_yes
        if ans in {yes_text, "y", "Y", "yes", "YES", "是"}:
            return True
        if ans in {no_text, "n", "N", "no", "NO", "否"}:
            return False
    except Exception:
        pass
    return default_yes


def call_with_retries(
    action_name: str,
    fn: Callable[..., Any],
    *,
    kwargs: dict[str, Any],
    max_retries: int = 3,
    base_delay: float = 1.5,
    max_delay: float = 12.0,
) -> Any:
    """
    Best-effort retries for transient remote disconnect / rate limit / flaky network.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn(**kwargs)
        except Exception as e:
            if attempt > max_retries:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = delay * (0.7 + random.random() * 0.6)  # jitter 0.7~1.3x
            now = datetime.now().strftime("%H:%M:%S")
            print(f"[{now}] {action_name} 失败，重试 {attempt}/{max_retries}：{type(e).__name__}: {e}")
            time.sleep(delay)


def is_trading_time(now: datetime) -> bool:
    # A 股大致交易时段：9:25-11:30, 13:00-15:00
    if now.hour == 9 and now.minute >= 25:
        return True
    if 10 <= now.hour < 11:
        return True
    if now.hour == 11 and now.minute <= 30:
        return True
    if 13 <= now.hour < 15:
        return True
    return False


def _last_trading_day(today: datetime) -> datetime:
    """
    粗略取“最近一个交易日”（用于非交易时段回放）。
    - 周一：回退到上周五
    - 周六：回退到周五
    - 周日：回退到周五
    - 其他：回退到昨天
    """
    wd = today.weekday()  # Mon=0..Sun=6
    if wd == 0:
        return today - timedelta(days=3)
    if wd == 5:
        return today - timedelta(days=1)
    if wd == 6:
        return today - timedelta(days=2)
    return today - timedelta(days=1)


@dataclass
class ReplayState:
    trade_date: str
    df: Any
    source: str = "unknown"
    idx: int = 0

    def base_open(self) -> float:
        v = _to_float(self.df.iloc[0].get("开盘"))
        if v is None:
            raise RuntimeError("无法从回放数据取到昨日开盘价")
        return float(v)

    def step(self) -> tuple[Optional[float], Optional[float], str]:
        """
        每次推进 1 条 1分钟K；返回 (当前价, 累计成交额, 时间字符串)。
        当前价取当分钟收盘；累计成交额为从开盘到当前分钟成交额之和。
        """
        if self.df is None or len(self.df) == 0:
            return None, None, "--:--:--"
        if self.idx >= len(self.df):
            # 回放完则循环
            self.idx = 0

        row = self.df.iloc[self.idx]
        self.idx += 1

        price = _to_float(row.get("收盘"))
        if price is None:
            return None, None, "--:--:--"

        # 累计成交额（元）
        amt_series = self.df.iloc[: self.idx]["成交额"]
        total_amount = float(amt_series.fillna(0).sum())

        t = str(row.get("时间", ""))[-8:]
        return float(price), total_amount, t


def _fetch_minute_df_for_date(code: str, trade_date: str):
    start = f"{trade_date} 09:30:00"
    end = f"{trade_date} 15:00:00"
    df = call_with_retries(
        "拉取单股分时行情(回放)",
        ak.stock_zh_a_hist_min_em,
        kwargs={
            "symbol": str(code),
            "start_date": start,
            "end_date": end,
            "period": "1",
            "adjust": "",
        },
        max_retries=3,
    )
    if df is None or len(df) == 0:
        raise RuntimeError("回放分时数据为空")
    if "开盘" not in df.columns or "收盘" not in df.columns or "成交额" not in df.columns:
        raise RuntimeError(f"回放数据缺少列: 开盘/收盘/成交额; 实际列名={list(df.columns)}")
    return df


def _cache_path(symbol: str, trade_date: str) -> str:
    # keep simple: workspace-relative
    return f"data/replay_{symbol}_{trade_date}.csv"


def _load_cached_df(symbol: str, trade_date: str):
    try:
        import pandas as pd  # type: ignore

        p = _cache_path(symbol, trade_date)
        df = pd.read_csv(p)
        return df
    except Exception:
        return None


def _save_cached_df(df, symbol: str, trade_date: str) -> None:
    try:
        import os

        import pandas as pd  # type: ignore

        os.makedirs("data", exist_ok=True)
        p = _cache_path(symbol, trade_date)
        df.to_csv(p, index=False)
    except Exception:
        return


def _eastmoney_secid(code: str) -> str:
    c = str(code).strip()
    market = 1 if c.startswith("6") else 0
    return f"{market}.{c}"


def _fetch_minute_df_eastmoney(code: str, trade_date: str):
    """
    东方财富分时 1分钟K 作为 AkShare 失败时的兜底（更稳）。
    """
    import pandas as pd  # type: ignore

    secid = _eastmoney_secid(code)
    params = {
        "secid": secid,
        "klt": "1",
        "fqt": "0",
        "beg": f"{trade_date} 09:30:00",
        "end": f"{trade_date} 15:00:00",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    # NOTE: push2his sometimes ignores beg/end for minute; we'll filter by date anyway.
    r = httpx.get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params=params,
        timeout=10.0,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    r.raise_for_status()
    j = r.json()
    data = (j.get("data") or {})
    klines = data.get("klines") or []
    if not klines:
        raise RuntimeError(f"东方财富分钟K为空: {j!r}")

    rows = []
    actual_date: Optional[str] = None
    for s in klines:
        # f51 time, f52 open, f53 close, f54 high, f55 low, f56 vol, f57 amount, f58 amplitude, f59 pct, f60 change, f61 turnover
        parts = str(s).split(",")
        if len(parts) < 7:
            continue
        t = parts[0]
        if actual_date is None:
            actual_date = str(t)[:10]
        o = _to_float(parts[1])
        c = _to_float(parts[2])
        h = _to_float(parts[3])
        l = _to_float(parts[4])
        v = _to_float(parts[5])
        amt = _to_float(parts[6])
        rows.append(
            {
                "时间": t,
                "开盘": o,
                "收盘": c,
                "最高": h,
                "最低": l,
                "成交量": v,
                "成交额": amt,
                "均价": None,
            }
        )
    if not rows:
        raise RuntimeError("东方财富分钟K解析后为空")
    df = pd.DataFrame(rows)
    if actual_date is None:
        actual_date = trade_date
    return df, actual_date


def fetch_replay_df(symbol: str, trade_date: str):
    """
    取“昨日回放数据”，优先 AkShare，失败则使用本地缓存，再失败用东方财富兜底。
    """
    # 1) try akshare
    try:
        df = _fetch_minute_df_for_date(symbol, trade_date)
        _save_cached_df(df, symbol, trade_date)
        return df, "akshare", trade_date
    except Exception:
        pass

    # 2) cache
    dfc = _load_cached_df(symbol, trade_date)
    if dfc is not None and len(dfc) > 0:
        return dfc, "cache", trade_date

    # 3) eastmoney fallback
    df2, actual_date = _fetch_minute_df_eastmoney(symbol, trade_date)
    _save_cached_df(df2, symbol, actual_date)
    return df2, "eastmoney", actual_date


def _minute_df_akshare_today(code: str):
    now = datetime.now()
    start = now.strftime("%Y-%m-%d 09:30:00")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    df = call_with_retries(
        "拉取单股分时行情(实时/AkShare)",
        ak.stock_zh_a_hist_min_em,
        kwargs={
            "symbol": str(code),
            "start_date": start,
            "end_date": end,
            "period": "1",
            "adjust": "",
        },
        max_retries=2,
        base_delay=1.0,
        max_delay=6.0,
    )
    if df is None or len(df) == 0:
        raise RuntimeError("实时分时数据为空(AkShare)")
    return df


def _minute_df_eastmoney_today(code: str):
    today = datetime.now().strftime("%Y-%m-%d")
    df, actual_date = _fetch_minute_df_eastmoney(code, today)
    return df, actual_date


def get_realtime_price_with_source(code: str) -> tuple[Optional[float], Optional[float], str]:
    """
    返回 (最新价, 今日累计成交额, 数据源)。
    - 优先 AkShare 单股 1分钟分时
    - 失败则用 东方财富 push2his 分钟K 兜底
    """
    try:
        df = _minute_df_akshare_today(code)
        if "收盘" not in df.columns or "成交额" not in df.columns:
            raise RuntimeError(f"缺少列: 收盘/成交额; 实际列名={list(df.columns)}")
        last_close = _to_float(df.iloc[-1].get("收盘"))
        total_amount = float(df["成交额"].fillna(0).sum())
        return last_close, total_amount, "AkShare"
    except Exception as e:
        now = datetime.now().strftime("%H:%M:%S")
        print(colored(f"[{now}] 实时数据源 AkShare 失败，切换东方财富兜底：{type(e).__name__}: {e}", "yellow"))

    try:
        df2, actual_date = _minute_df_eastmoney_today(code)
        if "收盘" not in df2.columns or "成交额" not in df2.columns:
            raise RuntimeError(f"缺少列: 收盘/成交额; 实际列名={list(df2.columns)}")
        last_close = _to_float(df2.iloc[-1].get("收盘"))
        total_amount = float(df2["成交额"].fillna(0).sum())
        src = "东方财富"
        if actual_date and actual_date != datetime.now().strftime("%Y-%m-%d"):
            src += f"(返回日期={actual_date})"
        return last_close, total_amount, src
    except Exception as e:
        now = datetime.now().strftime("%H:%M:%S")
        print(colored(f"[{now}] 东方财富兜底也失败：{type(e).__name__}: {e}", "yellow"))
        return None, None, "N/A"


def get_realtime_price(code: str) -> tuple[Optional[float], Optional[float]]:
    """
    返回 (最新价, 今日累计成交额).

    使用单股接口：东方财富-每日分时 (1 分钟) `ak.stock_zh_a_hist_min_em`，
    取最后一根 K 的收盘价作为“当前价”，并把当日所有分钟的成交额求和作为“累计成交额”。
    这比拉取全市场快照稳定得多。
    """
    now = datetime.now()
    start = now.strftime("%Y-%m-%d 09:30:00")
    end = now.strftime("%Y-%m-%d %H:%M:%S")

    df = call_with_retries(
        "拉取单股分时行情",
        ak.stock_zh_a_hist_min_em,
        kwargs={
            "symbol": str(code),
            "start_date": start,
            "end_date": end,
            "period": "1",
            "adjust": "",
        },
        max_retries=3,
    )

    if df is None or len(df) == 0:
        return None, None

    if "收盘" not in df.columns or "成交额" not in df.columns:
        raise RuntimeError(f"缺少列: 收盘/成交额; 实际列名={list(df.columns)}")

    last_close = _to_float(df.iloc[-1].get("收盘"))
    # 今日累计成交额（元）
    amt_series = df["成交额"]
    total_amount = float(amt_series.fillna(0).sum())
    return last_close, total_amount


def get_today_open_price(code: str) -> Optional[float]:
    """
    交易时段基准价：当日开盘价。
    用单股 1分钟分时取第一根的开盘价作为“开盘价”。
    """
    now = datetime.now()
    start = now.strftime("%Y-%m-%d 09:30:00")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    df = call_with_retries(
        "拉取当日开盘价",
        ak.stock_zh_a_hist_min_em,
        kwargs={
            "symbol": str(code),
            "start_date": start,
            "end_date": end,
            "period": "1",
            "adjust": "",
        },
        max_retries=2,
        base_delay=1.0,
        max_delay=6.0,
    )
    if df is None or len(df) == 0:
        return None
    if "开盘" not in df.columns:
        return None
    return _to_float(df.iloc[0].get("开盘"))


@dataclass
class TradeConfig:
    position_cash: float = 10000.0  # 每次买入投入 1 万
    commission_rate: float = 0.0003  # 佣金万3
    min_commission: float = 5.0  # 最低 5 元
    stamp_duty: float = 0.0005  # 印花税万5（仅卖出）
    lot_size: int = 100  # A 股一手=100股


def calculate_fee(cfg: TradeConfig, amount: float, *, is_selling: bool) -> float:
    """
    手续费模型：
    - 佣金：万3，最低5元（买卖都收）
    - 印花税：万5（仅卖出收）
    """
    comm = max(float(amount) * float(cfg.commission_rate), float(cfg.min_commission))
    duty = float(amount) * float(cfg.stamp_duty) if is_selling else 0.0
    return float(comm + duty)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="000630", help="股票代码，默认 000630")
    ap.add_argument("--base-price", type=float, default=7.15, help="基准价（如周一开盘价），默认 7.15")
    ap.add_argument("--buy-line", type=float, default=0.985, help="买入线倍数，默认 0.985（跌1.5%）")
    ap.add_argument("--sell-line", type=float, default=1.012, help="卖出线倍数，默认 1.012（涨1.2%）")
    ap.add_argument("--check-interval", type=int, default=10, help="检查间隔秒数，默认 10")
    ap.add_argument("--max-loops", type=int, default=0, help="最多循环次数（0=无限），方便测试")
    ap.add_argument("--ignore-trading-hours", action="store_true", help="忽略交易时段限制（方便测试）")
    ap.add_argument("--alert-cooldown", type=int, default=60, help="触发信号弹窗冷却秒数，默认 60")
    ap.add_argument("--position-cash", type=float, default=10000.0, help="每次买入投入金额（元），默认 10000")
    args = ap.parse_args()

    symbol = args.symbol
    base_price_input = float(args.base_price)
    buy_line = float(args.buy_line)
    sell_line = float(args.sell_line)
    check_interval = int(args.check_interval)
    tcfg = TradeConfig(position_cash=float(args.position_cash))

    print(colored(f"--- 铜陵有色({symbol}) 实战做T监控器启动 ---", "cyan"))
    print(
        f"刷新间隔: {check_interval}s | 每次买入投入: {tcfg.position_cash:.0f}元 | "
        f"费用: 佣金万3(最低5元) + 卖出印花税万5 | "
        "非交易时段：自动回放最近交易日数据并自动设置基准价=昨日开盘价"
    )

    loops = 0
    mode: Literal["REALTIME", "REPLAY"] = "REALTIME"
    replay: Optional[ReplayState] = None
    base_price: float = base_price_input
    base_price_day: Optional[str] = None  # YYYY-MM-DD; used in REALTIME mode
    in_position: bool = False
    buy_price: Optional[float] = None
    buy_mode: Optional[str] = None  # REALTIME/REPLAY
    buy_time: Optional[str] = None
    buy_shares: int = 0
    buy_amount: float = 0.0
    buy_fee: float = 0.0
    last_alert_at: float = 0.0
    last_signal: Optional[str] = None  # BUY/SELL/None
    next_replay_init_at: float = 0.0
    defer_buy_prompt: bool = False
    defer_sell_prompt: bool = False

    while True:
        loops += 1
        now = datetime.now()

        trading = is_trading_time(now) or args.ignore_trading_hours

        # 1) 非交易时段 -> 回放昨日数据跑
        if not trading:
            if time.time() < next_replay_init_at and replay is None:
                print(f"[{now.strftime('%H:%M:%S')}] 非交易时段：回放数据初始化失败，等待稍后重试…")
                # do not hammer remote; still allow max_loops to end gracefully

            if mode != "REPLAY":
                # 切换到回放模式
                td = _last_trading_day(now).strftime("%Y-%m-%d")
                try:
                    df_y, src, actual_date = fetch_replay_df(symbol, td)
                    replay = ReplayState(trade_date=actual_date, df=df_y, source=src, idx=0)
                    base_price = replay.base_open()  # 2) 自动替换基准价=昨日开盘价
                    mode = "REPLAY"
                    # 回放/实时切换时，清空“买入价状态”，避免跨模式混淆
                    in_position = False
                    buy_price = None
                    buy_mode = None
                    buy_time = None
                    buy_shares = 0
                    buy_amount = 0.0
                    buy_fee = 0.0
                    if actual_date != td:
                        print(colored(f"[{now.strftime('%H:%M:%S')}] 昨日({td})数据不可用，已回退到 {actual_date}（来源={src}），基准价自动设置为 {base_price:.3f}", "yellow"))
                    else:
                        print(colored(f"[{now.strftime('%H:%M:%S')}] 非交易时段：进入回放模式（{actual_date}，来源={src}），基准价已自动设置为昨日开盘价 {base_price:.3f}", "yellow"))
                except Exception as e:
                    print(colored(f"[{now.strftime('%H:%M:%S')}] 回放模式初始化失败：{type(e).__name__}: {e}", "yellow"))
                    replay = None
                    # backoff to avoid hammering remote
                    next_replay_init_at = time.time() + 120.0

            if replay is None:
                print(f"[{now.strftime('%H:%M:%S')}] 非交易时段：回放数据不可用，等待下次…")
            else:
                current_price, amount, tstr = replay.step()
                if current_price is not None:
                    change = (current_price - base_price) / base_price
                    status = f"[{tstr}] (回放{replay.trade_date} | 数据源={replay.source}) 当前价: {current_price:.3f} | 相对基准涨跌: {change:.2%}"
                    if in_position and buy_price:
                        pnl = (current_price - buy_price) / buy_price
                        # 预计此价卖出盈亏（含费用）
                        sell_amount = float(buy_shares) * float(current_price)
                        sell_fee = calculate_fee(tcfg, sell_amount, is_selling=True)
                        est_profit = sell_amount - buy_amount - buy_fee - sell_fee
                        status += f" | 相对买入涨跌: {pnl:.2%} | 若此价卖出预计盈亏(含费用): {est_profit:+.2f}元"
                    else:
                        status += " | 相对买入涨跌: N/A"
                    if amount is not None:
                        status += f" | 当日累计成交额: {(amount / 1e8):.2f}亿"
                    # 信号判断
                    signal: Optional[str] = None
                    if current_price <= base_price * buy_line:
                        signal = "BUY"
                        print(colored(f"!!! [买入信号] !!! {status} - 跌幅满足，准备入场做T！", "red", attrs=["bold"]))
                    elif current_price >= base_price * sell_line:
                        signal = "SELL"
                        print(colored(f"$$$ [卖出信号] $$$ {status} - 利润达标，准备反手出仓！", "green", attrs=["bold"]))
                    else:
                        print(status + " | 监控中...")

                    # 避免“拒绝后每次循环都弹窗”：只有信号消失后才允许再次弹窗
                    if signal != "BUY":
                        defer_buy_prompt = False
                    if signal != "SELL":
                        defer_sell_prompt = False

                    # 交互式决策：买 / 不买
                    if signal == "BUY" and (not in_position) and (not defer_buy_prompt):
                        ok = prompt_choice("买入决策", status + "\n\n是否执行买入？", yes_text="买入", no_text="不买", default_yes=False)
                        defer_buy_prompt = True
                        if ok:
                            lot_cost = float(current_price) * float(tcfg.lot_size)
                            shares = int((tcfg.position_cash // lot_cost) * tcfg.lot_size) if lot_cost > 0 else 0
                            if shares <= 0:
                                print(colored(f"[{tstr}] 选择买入，但投入{tcfg.position_cash:.0f}元不足以买入1手，跳过记录仓位。", "yellow"))
                            else:
                                in_position = True
                                buy_price = float(current_price)
                                buy_mode = "REPLAY"
                                buy_time = tstr
                                buy_shares = shares
                                buy_amount = float(shares) * float(current_price)
                                buy_fee = calculate_fee(tcfg, buy_amount, is_selling=False)
                                print(
                                    colored(
                                        f"[{tstr}] 已买入(模拟)：买入价 {buy_price:.3f} | 股数 {buy_shares} | 买入额 {buy_amount:.2f} | 买入费用 {buy_fee:.2f}",
                                        "red",
                                        attrs=["bold"],
                                    )
                                )
                        else:
                            print(colored(f"[{tstr}] 选择不买，继续观察。", "yellow"))

                    # 交互式决策：卖 / 不卖
                    if signal == "SELL" and in_position and (not defer_sell_prompt):
                        ok = prompt_choice("卖出决策", status + "\n\n是否执行卖出？", yes_text="卖出", no_text="不卖", default_yes=False)
                        defer_sell_prompt = True
                        if ok:
                            sell_amount = float(buy_shares) * float(current_price)
                            sell_fee = calculate_fee(tcfg, sell_amount, is_selling=True)
                            profit = sell_amount - buy_amount - buy_fee - sell_fee
                            print(
                                colored(
                                    f"[{tstr}] 已卖出(模拟)结算：卖出额 {sell_amount:.2f} | 卖出费用 {sell_fee:.2f} | 本次盈亏(含费用) {profit:+.2f}元",
                                    "green",
                                    attrs=["bold"],
                                )
                            )
                            in_position = False
                            buy_price = None
                            buy_mode = None
                            buy_time = None
                            buy_shares = 0
                            buy_amount = 0.0
                            buy_fee = 0.0
                        else:
                            print(colored(f"[{tstr}] 选择不卖，继续持有观察。", "yellow"))

                    # 3) 触发警戒线弹窗（带冷却 + 同信号不重复刷屏）
                    if signal and (time.time() - last_alert_at >= int(args.alert_cooldown) or signal != last_signal):
                        show_alert("防范补跌风险" if signal == "BUY" else "止盈提醒", status)
                        last_alert_at = time.time()
                        last_signal = signal
                else:
                    print(f"[{now.strftime('%H:%M:%S')}] 回放数据推进失败，等待下次…")

        # 交易时段 -> 真实实时
        else:
            if mode != "REALTIME":
                mode = "REALTIME"
                replay = None
                base_price = base_price_input
                base_price_day = None
                in_position = False
                buy_price = None
                buy_mode = None
                buy_time = None
                buy_shares = 0
                buy_amount = 0.0
                buy_fee = 0.0
                print(colored(f"[{now.strftime('%H:%M:%S')}] 进入交易时段：切回实时模式，将自动设置基准价=当日开盘价（失败则使用输入值 {base_price_input:.3f}）", "cyan"))

            # 基准价：交易时段自动设置为当日开盘价（每天只设置一次）
            today = now.strftime("%Y-%m-%d")
            if base_price_day != today:
                try:
                    open_px = get_today_open_price(symbol)
                except Exception as e:
                    open_px = None
                    print(colored(f"[{now.strftime('%H:%M:%S')}] 获取当日开盘价失败：{type(e).__name__}: {e}", "yellow"))
                if open_px is not None:
                    base_price = float(open_px)
                    base_price_day = today
                    print(colored(f"[{now.strftime('%H:%M:%S')}] 基准价已设置为当日开盘价: {base_price:.3f}", "cyan"))
                else:
                    # fallback
                    base_price = base_price_input
                    base_price_day = today
                    print(colored(f"[{now.strftime('%H:%M:%S')}] 基准价开盘价不可用，使用输入值: {base_price:.3f}", "yellow"))

            try:
                current_price, amount, src = get_realtime_price_with_source(symbol)
            except Exception as e:
                print(colored(f"[{now.strftime('%H:%M:%S')}] 拉取实时行情失败：{type(e).__name__}: {e}", "yellow"))
                current_price, amount, src = None, None, "N/A"

            if current_price is not None:
                change = (current_price - base_price) / base_price
                status = f"[{now.strftime('%H:%M:%S')}] (实时 | 数据源={src}) 当前价: {current_price:.3f} | 相对基准涨跌: {change:.2%}"
                if in_position and buy_price:
                    pnl = (current_price - buy_price) / buy_price
                    sell_amount = float(buy_shares) * float(current_price)
                    sell_fee = calculate_fee(tcfg, sell_amount, is_selling=True)
                    est_profit = sell_amount - buy_amount - buy_fee - sell_fee
                    status += f" | 相对买入涨跌: {pnl:.2%} | 若此价卖出预计盈亏(含费用): {est_profit:+.2f}元"
                else:
                    status += " | 相对买入涨跌: N/A"
                if amount is not None:
                    status += f" | 今日成交额(估算): {(amount / 1e8):.2f}亿"

                signal: Optional[str] = None
                if current_price <= base_price * buy_line:
                    signal = "BUY"
                    print(colored(f"!!! [买入信号] !!! {status} - 跌幅满足，准备入场做T！", "red", attrs=["bold"]))
                elif current_price >= base_price * sell_line:
                    signal = "SELL"
                    print(colored(f"$$$ [卖出信号] $$$ {status} - 利润达标，准备反手出仓！", "green", attrs=["bold"]))
                else:
                    print(status + " | 监控中...")

                if signal != "BUY":
                    defer_buy_prompt = False
                if signal != "SELL":
                    defer_sell_prompt = False

                if signal == "BUY" and (not in_position) and (not defer_buy_prompt):
                    ok = prompt_choice("买入决策", status + "\n\n是否执行买入？", yes_text="买入", no_text="不买", default_yes=False)
                    defer_buy_prompt = True
                    if ok:
                        lot_cost = float(current_price) * float(tcfg.lot_size)
                        shares = int((tcfg.position_cash // lot_cost) * tcfg.lot_size) if lot_cost > 0 else 0
                        if shares <= 0:
                            print(colored(f"[{now.strftime('%H:%M:%S')}] 选择买入，但投入{tcfg.position_cash:.0f}元不足以买入1手，跳过记录仓位。", "yellow"))
                        else:
                            in_position = True
                            buy_price = float(current_price)
                            buy_mode = "REALTIME"
                            buy_time = now.strftime("%H:%M:%S")
                            buy_shares = shares
                            buy_amount = float(shares) * float(current_price)
                            buy_fee = calculate_fee(tcfg, buy_amount, is_selling=False)
                            print(
                                colored(
                                    f"[{buy_time}] 已买入(模拟)：买入价 {buy_price:.3f} | 股数 {buy_shares} | 买入额 {buy_amount:.2f} | 买入费用 {buy_fee:.2f}",
                                    "red",
                                    attrs=["bold"],
                                )
                            )
                    else:
                        print(colored(f"[{now.strftime('%H:%M:%S')}] 选择不买，继续观察。", "yellow"))

                if signal == "SELL" and in_position and (not defer_sell_prompt):
                    ok = prompt_choice("卖出决策", status + "\n\n是否执行卖出？", yes_text="卖出", no_text="不卖", default_yes=False)
                    defer_sell_prompt = True
                    if ok:
                        sell_amount = float(buy_shares) * float(current_price)
                        sell_fee = calculate_fee(tcfg, sell_amount, is_selling=True)
                        profit = sell_amount - buy_amount - buy_fee - sell_fee
                        print(
                            colored(
                                f"[{now.strftime('%H:%M:%S')}] 已卖出(模拟)结算：卖出额 {sell_amount:.2f} | 卖出费用 {sell_fee:.2f} | 本次盈亏(含费用) {profit:+.2f}元",
                                "green",
                                attrs=["bold"],
                            )
                        )
                        in_position = False
                        buy_price = None
                        buy_mode = None
                        buy_time = None
                        buy_shares = 0
                        buy_amount = 0.0
                        buy_fee = 0.0
                    else:
                        print(colored(f"[{now.strftime('%H:%M:%S')}] 选择不卖，继续持有观察。", "yellow"))

                if signal and (time.time() - last_alert_at >= int(args.alert_cooldown) or signal != last_signal):
                    show_alert("防范补跌风险" if signal == "BUY" else "止盈提醒", status)
                    last_alert_at = time.time()
                    last_signal = signal

        if args.max_loops and loops >= args.max_loops:
            print(f"达到 max_loops={args.max_loops}，测试结束。")
            break

        time.sleep(check_interval)


if __name__ == "__main__":
    main()

