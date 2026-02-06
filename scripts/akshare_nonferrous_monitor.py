from __future__ import annotations

import argparse
import platform
import random
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s or s in {"-", "None", "nan", "NaN"}:
        return None
    s = s.replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def _pick_col(df, candidates: list[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    lowered = {str(c).lower(): c for c in list(df.columns)}
    for c in candidates:
        if c.lower() in lowered:
            return str(lowered[c.lower()])
    return None


def _try_call(fn, kwargs_list: list[dict[str, Any]]):
    last_err: Optional[Exception] = None
    for kw in kwargs_list:
        try:
            return fn(**kw)
        except TypeError as e:
            last_err = e
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    raise RuntimeError("call failed")


def call_with_retries(
    action_name: str,
    fn,
    *,
    kwargs_list: list[dict[str, Any]],
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
            return _try_call(fn, kwargs_list)
        except Exception as e:
            if attempt > max_retries:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = delay * (0.7 + random.random() * 0.6)  # jitter 0.7~1.3x
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] {action_name} 失败，重试 {attempt}/{max_retries}：{type(e).__name__}: {e}")
            time.sleep(delay)


def show_alert(title: str, message: str) -> None:
    """
    Best-effort popup alert:
    - macOS: osascript
    - Windows: MessageBoxW
    - Linux: notify-send (if available)
    Always prints to stdout as fallback.
    """
    print(f"[ALERT] {title}: {message}")

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
        # Linux
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], check=False)
            return
    except Exception:
        # ignore any popup errors; stdout is enough
        return


@dataclass
class MonitorConfig:
    industry_name: str = "有色金属"
    refresh_seconds: int = 20
    board_drop_threshold: float = -3.0  # 板块平均涨跌幅 <= -3%
    tongling_code: str = "000630"
    tongling_drop_limit: float = -2.0  # 铜陵有色跌幅 > -2%（跌幅小于2%）
    alert_cooldown_seconds: int = 300
    max_retries: int = 3
    constituents_refresh_seconds: int = 6 * 3600  # 成分股列表每 6 小时刷新一次


def fetch_industry_constituents(industry_name: str) -> list[str]:
    """
    使用 AkShare 获取行业板块成分股代码列表（东方财富行业板块）。
    """
    import akshare as ak  # type: ignore

    fn = ak.stock_board_industry_cons_em
    df = call_with_retries(
        "拉取板块成分股",
        fn,
        kwargs_list=[{"symbol": industry_name}],
        max_retries=3,
    )
    code_col = _pick_col(df, ["代码", "股票代码", "证券代码"])
    if code_col is None:
        raise RuntimeError(f"无法识别成分股代码列，实际列名: {list(df.columns)}")

    codes: list[str] = []
    for x in df[code_col].tolist():
        s = str(x).strip()
        if len(s) == 6 and s.isdigit():
            codes.append(s)

    # 去重且保持顺序
    seen = set()
    dedup = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            dedup.append(c)
    return dedup


def fetch_a_share_spot() -> Any:
    """
    拉取全 A 股实时行情（DataFrame），再由调用方过滤需要的股票。
    """
    import akshare as ak  # type: ignore

    fn = ak.stock_zh_a_spot_em
    # Some akshare versions accept timeout; try both.
    return call_with_retries(
        "拉取全市场实时行情",
        fn,
        kwargs_list=[{"timeout": 10}, {}],
        max_retries=3,
    )


def compute_board_average_pct(spot_df, codes: list[str]) -> tuple[float, int]:
    code_col = _pick_col(spot_df, ["代码", "股票代码", "证券代码"])
    pct_col = _pick_col(spot_df, ["涨跌幅", "涨跌幅(%)", "涨幅"])
    if code_col is None or pct_col is None:
        raise RuntimeError(f"无法识别实时行情列，实际列名: {list(spot_df.columns)}")

    code_set = set(codes)
    total = 0.0
    n = 0
    for _, row in spot_df.iterrows():  # type: ignore[attr-defined]
        code = str(row.get(code_col, "")).strip()
        if code not in code_set:
            continue
        pct = _to_float(row.get(pct_col))
        if pct is None:
            continue
        total += float(pct)
        n += 1

    if n == 0:
        raise RuntimeError("板块成分股在实时行情中匹配为 0，无法计算平均涨跌幅")
    return total / n, n


def get_stock_pct(spot_df, code: str) -> Optional[float]:
    code_col = _pick_col(spot_df, ["代码", "股票代码", "证券代码"])
    pct_col = _pick_col(spot_df, ["涨跌幅", "涨跌幅(%)", "涨幅"])
    if code_col is None or pct_col is None:
        return None
    for _, row in spot_df.iterrows():  # type: ignore[attr-defined]
        c = str(row.get(code_col, "")).strip()
        if c == code:
            return _to_float(row.get(pct_col))
    return None


def run_monitor(cfg: MonitorConfig) -> None:
    print("启动监控：")
    print(f"- 行业板块: {cfg.industry_name}")
    print(f"- 刷新间隔: {cfg.refresh_seconds}s")
    print(
        f"- 触发条件: 板块平均涨跌幅 <= {cfg.board_drop_threshold:.2f}% 且 "
        f"{cfg.tongling_code} 涨跌幅 > {cfg.tongling_drop_limit:.2f}%"
    )
    print(f"- 弹窗冷却: {cfg.alert_cooldown_seconds}s")

    last_alert_at: float = 0.0
    codes: list[str] = []
    last_constituents_fetch_at: float = 0.0

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            # Refresh constituents only occasionally; do NOT clear on transient network errors.
            if (not codes) or (time.time() - last_constituents_fetch_at >= cfg.constituents_refresh_seconds):
                codes = fetch_industry_constituents(cfg.industry_name)
                last_constituents_fetch_at = time.time()
                print(f"[{now}] 成分股数量: {len(codes)}（已刷新）")

            spot = fetch_a_share_spot()
            avg_pct, matched_n = compute_board_average_pct(spot, codes)
            tongling_pct = get_stock_pct(spot, cfg.tongling_code)

            print(
                f"[{now}] 板块平均涨跌幅: {avg_pct:.2f}% (匹配 {matched_n}) | "
                f"{cfg.tongling_code} 涨跌幅: {tongling_pct if tongling_pct is not None else 'N/A'}"
            )

            should_alert = (
                avg_pct <= cfg.board_drop_threshold
                and tongling_pct is not None
                and tongling_pct > cfg.tongling_drop_limit
            )
            if should_alert and (time.time() - last_alert_at) >= cfg.alert_cooldown_seconds:
                show_alert("防范补跌风险", "板块整体大跌，但铜陵有色相对抗跌，注意补跌风险。")
                last_alert_at = time.time()

        except Exception as e:
            print(f"[{now}] 监控异常：{type(e).__name__}: {e}")
            # 网络/远端断开等异常：保留 codes，避免下一轮额外打成分股接口导致更容易被限流
            # 但如果当前 codes 为空，下一轮会自动重新拉取
            pass

        time.sleep(int(cfg.refresh_seconds))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", type=int, default=20, help="刷新间隔秒数（默认 20）")
    ap.add_argument("--industry", type=str, default="有色金属", help="行业板块名称（默认 有色金属）")
    ap.add_argument("--cooldown", type=int, default=300, help="弹窗冷却秒数（默认 300）")
    ap.add_argument("--retries", type=int, default=3, help="网络失败最大重试次数（默认 3）")
    args = ap.parse_args()

    cfg = MonitorConfig(
        industry_name=args.industry,
        refresh_seconds=args.refresh,
        alert_cooldown_seconds=args.cooldown,
        max_retries=args.retries,
    )
    run_monitor(cfg)


if __name__ == "__main__":
    main()

