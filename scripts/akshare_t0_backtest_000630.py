import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

# --- 1. 设定参数 ---
symbol = "000630"  # 铜陵有色
position_cash = 10000  # 每次做T投入1万元
commission_rate = 0.0003  # 佣金万3 (含最低5元逻辑)
stamp_duty = 0.0005  # 印花税万5
min_commission = 5.0
buy_threshold = -0.015  # 策略：跌1.5%就买入
sell_threshold = 0.012  # 策略：从买入点反弹1.2%就卖出


def calculate_fee(amount, is_selling=False):
    """计算单笔交易手续费"""
    comm = max(amount * commission_rate, min_commission)
    duty = amount * stamp_duty if is_selling else 0
    return comm + duty


# --- 2. 获取数据 ---
print(f"正在获取 {symbol} 的历史数据...")
# 获取最近30天的日线数据
df = ak.stock_zh_a_hist(
    symbol=symbol,
    period="daily",
    start_date=(datetime.now() - timedelta(days=30)).strftime("%Y%m%d"),
    end_date=datetime.now().strftime("%Y%m%d"),
    adjust="qfq",
)

# --- 3. 模拟策略回测 ---
total_profit = 0
successful_trades = 0

print("\n--- 模拟回测报告 (基于日内波动假设) ---")
for index, row in df.iterrows():
    date = row["日期"]
    open_p = row["开盘"]
    low_p = row["最低"]
    high_p = row["最高"]

    # 逻辑：如果在盘中跌到了我们的买入线
    trigger_buy_price = open_p * (1 + buy_threshold)

    if low_p <= trigger_buy_price:
        # 触发买入
        shares = (position_cash // (trigger_buy_price * 100)) * 100
        actual_buy_amt = shares * trigger_buy_price
        buy_fee = calculate_fee(actual_buy_amt)

        # 触发卖出逻辑：如果当天的最高价达到了我们的止盈线
        trigger_sell_price = trigger_buy_price * (1 + sell_threshold)

        if high_p >= trigger_sell_price:
            actual_sell_amt = shares * trigger_sell_price
            sell_fee = calculate_fee(actual_sell_amt, is_selling=True)

            day_profit = (actual_sell_amt - actual_buy_amt) - (buy_fee + sell_fee)
            total_profit += day_profit
            successful_trades += 1
            print(f"[{date}] 做T成功！利润: +{day_profit:.2f}元")
        else:
            # 没卖出去，假设收盘前保本平仓或计入浮动压力（此处简化为不计入利润）
            print(f"[{date}] 触发买入但未达止盈位")

print(f"\n总结：过去30天内，该策略成功触发并完成 {successful_trades} 次做T")
print(f"累计降低成本总额: {total_profit:.2f} 元")
print(f"如果你的持仓是1万股，相当于每股成本降低了: {total_profit/10000:.4f} 元")

