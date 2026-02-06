from __future__ import annotations

import argparse
import time
from datetime import datetime
from typing import Any, Callable, Optional


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


def _parse_amount_to_yuan(x: Any) -> Optional[float]:
    """
    成交额统一转换为“元”。兼容数值、以及带单位字符串（万/亿/元）。
    """
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "")
    if not s or s in {"-", "None", "nan", "NaN"}:
        return None

    unit = 1.0
    if s.endswith("亿"):
        unit = 1e8
        s = s[:-1]
    elif s.endswith("万"):
        unit = 1e4
        s = s[:-1]
    elif s.endswith("元"):
        unit = 1.0
        s = s[:-1]

    v = _to_float(s)
    if v is None:
        return None
    return float(v * unit)


def _pick_col(df, candidates: list[str]) -> Optional[str]:
    cols = list(df.columns)
    for c in candidates:
        if c in df.columns:
            return c
    # loose match
    lowered = {str(c).lower(): c for c in cols}
    for c in candidates:
        if c.lower() in lowered:
            return str(lowered[c.lower()])
    return None


def _try_call(fn: Callable[..., Any], kwargs_list: list[dict[str, Any]]) -> Any:
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


def fetch_all_a_share_realtime_df():
    """
    使用 efinance 拉取全 A 股实时行情（DataFrame）。
    efinance 不同版本函数名/参数可能略有差异，这里做兼容尝试。
    """
    import efinance as ef  # type: ignore

    stock = getattr(ef, "stock", None)
    if stock is None:
        raise RuntimeError("efinance.stock not found")

    candidates: list[Callable[..., Any]] = []
    for name in ("get_realtime_quotes", "get_realtime_data", "get_latest_data"):
        fn = getattr(stock, name, None)
        if callable(fn):
            candidates.append(fn)

    if not candidates:
        raise RuntimeError("No realtime quotes function found in efinance.stock")

    # common kwargs combos across versions
    kwargs_list = [
        {},
        {"market": "沪深A"},
        {"market": "hs_a"},
        {"market": "all"},
        {"market": "A股"},
    ]

    last_err: Optional[Exception] = None
    for fn in candidates:
        try:
            df = _try_call(fn, kwargs_list)
            # Expect a pandas.DataFrame-like object
            if hasattr(df, "columns") and hasattr(df, "__len__"):
                return df
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Fetch realtime quotes failed: {last_err}")


def get_filtered_quotes(
    min_amount_yuan: float = 5e8,
    pct_low: float = 3.0,
    pct_high: float = 5.0,
) -> list[dict[str, Any]]:
    """
    获取 A 股实时行情，并过滤：
    - 成交额 > 5亿
    - 涨幅在 3% 到 5% 之间（含边界）

    返回字段包含：股票代码、名称、最新价、涨跌幅、成交额（元）。
    """
    df = fetch_all_a_share_realtime_df()

    code_col = _pick_col(df, ["股票代码", "代码", "证券代码", "code", "ts_code"])
    name_col = _pick_col(df, ["股票名称", "名称", "证券名称", "name"])
    price_col = _pick_col(df, ["最新价", "现价", "最新", "price", "最新价格"])
    pct_col = _pick_col(df, ["涨跌幅", "涨幅", "pct_chg", "change_percent", "涨跌幅(%)"])
    amt_col = _pick_col(df, ["成交额", "成交金额", "amount", "turnover", "成交额(元)"])

    missing = [k for k, v in {
        "code": code_col,
        "name": name_col,
        "price": price_col,
        "pct": pct_col,
        "amount": amt_col,
    }.items() if v is None]
    if missing:
        raise RuntimeError(
            "efinance 返回字段无法匹配，缺少: "
            + ", ".join(missing)
            + f"\n实际列名: {list(df.columns)}"
        )

    out: list[dict[str, Any]] = []

    # iterate rows (pandas DataFrame)
    for _, row in df.iterrows():  # type: ignore[attr-defined]
        code = row.get(code_col)
        name = row.get(name_col)
        price = _to_float(row.get(price_col))
        pct = _to_float(row.get(pct_col))
        amt = _parse_amount_to_yuan(row.get(amt_col))

        if code is None or name is None or price is None or pct is None or amt is None:
            continue
        if amt <= float(min_amount_yuan):
            continue
        if not (float(pct_low) <= pct <= float(pct_high)):
            continue

        out.append(
            {
                "股票代码": str(code),
                "名称": str(name),
                "最新价": float(price),
                "涨跌幅": float(pct),
                "成交额": float(amt),
            }
        )

    # sort by amount desc
    out.sort(key=lambda x: x["成交额"], reverse=True)
    return out


def run_loop(refresh_seconds: int = 60) -> None:
    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] 刷新：成交额>5亿 且 涨幅3%~5% 的股票")
        try:
            rows = get_filtered_quotes()
            if not rows:
                print("无符合条件的股票。")
            else:
                print(f"共 {len(rows)} 只：")
                print("-" * 80)
                for r in rows:
                    print(
                        f"{r['股票代码']} {r['名称']:<8}  "
                        f"最新价:{r['最新价']:.2f}  "
                        f"涨跌幅:{r['涨跌幅']:.2f}%  "
                        f"成交额:{r['成交额'] / 1e8:.2f}亿"
                    )
        except Exception as e:
            print(f"拉取/过滤失败：{type(e).__name__}: {e}")
        print(f"等待 {refresh_seconds} 秒…")
        time.sleep(int(refresh_seconds))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", type=int, default=60, help="刷新间隔秒数，默认 60")
    ap.add_argument("--once", action="store_true", help="只运行一次（不循环）")
    args = ap.parse_args()

    if args.once:
        rows = get_filtered_quotes()
        for r in rows:
            print(r)
        return

    run_loop(refresh_seconds=args.refresh)


if __name__ == "__main__":
    main()

