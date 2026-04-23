# -*- coding: utf-8 -*-
"""
单股实时订阅行情验证程序
=========================
目标：验证 xtdata.subscribe_quote 在开盘期间能否实时获取 510300.ETF 的订阅行情

使用方式：
  在 MiniQMT 已登录且处于开盘时间的情况下运行：
    d:\python_envs\gd_qmt_env\python.exe subscribe_single_stock.py

功能：
  1. 订阅 510300.SH (沪深300ETF) 的 tick 级别实时行情
  2. 通过回调函数实时打印收到的行情数据
  3. 同时也订阅 1d 级别行情，对比两种粒度
  4. 按 Ctrl+C 退出程序

注意事项：
  - 必须在 MiniQMT 已登录的状态下运行
  - 必须在交易时间（9:15-15:00）内运行才能收到实时推送
  - 非交易时间运行只能验证"订阅调用是否成功"，但不会收到回调
"""

from xtquant import xtdata
import time
import signal
import sys
import datetime

# ==================== 配置区 ====================
TARGET_CODE = "510300.SH"   # 沪深300ETF
TICK_PERIOD = "tick"         # 分笔级别
DAILY_PERIOD = "1d"          # 日线级别
# ================================================

# 退出标志
running = True


def fmt_price(value):
    """
    格式化价格，消除浮点精度尾巴（如 3.0060000000000002 -> 3.006）
    
    规则：
      - 非数值（N/A 等）原样返回
      - >= 100: 2位小数（如股票高价股）
      - >= 10:  2位小数
      - < 10:   3位小数（ETF 常见价格区间）
    """
    if value == 'N/A' or not isinstance(value, (int, float)):
        return str(value)
    if value >= 10:
        return f"{value:.2f}"
    return f"{value:.3f}"


def fmt_amount(value):
    """
    格式化成交额，转为可读单位
    
    规则：
      - 非数值原样返回
      - >= 1亿: 显示为 X.XX亿
      - >= 1万: 显示为 X.XX万
      - < 1万:  原样显示（保留2位小数）
    """
    if value == 'N/A' or not isinstance(value, (int, float)):
        return str(value)
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f}亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.2f}万"
    return f"{value:.2f}"


def fmt_vol(value):
    """
    格式化成交量（股数），转为可读单位
    
    规则：
      - 非数值原样返回
      - >= 1万手(100万股): 显示为 X.XX万手
      - >= 1万: 显示为 X.XX万
      - < 1万:  取整显示
    """
    if value == 'N/A' or not isinstance(value, (int, float)):
        return str(value)
    if abs(value) >= 1e6:
        return f"{value / 1e5:.2f}万手"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.2f}万"
    return f"{int(value)}"


def fmt_chg(price, pre_close):
    """
    格式化涨跌幅，返回字符串如 +1.23% 或 -0.56%
    """
    try:
        if price != 'N/A' and pre_close != 'N/A' and isinstance(price, (int, float)) and isinstance(pre_close, (int, float)) and pre_close > 0:
            chg_pct = (price - pre_close) / pre_close * 100
            chg_val = price - pre_close
            return f"{chg_val:+.3f} ({chg_pct:+.2f}%)"
        return "N/A"
    except Exception:
        return "N/A"


def fmt_bid_ask(prices, volumes):
    """
    格式化五档盘口，返回形如 "4.776/4568  4.777/9213  ..." 的字符串
    """
    if not prices or not volumes:
        return "N/A"
    parts = []
    for i in range(min(5, len(prices))):
        p = fmt_price(prices[i]) if isinstance(prices[i], (int, float)) else str(prices[i])
        v = fmt_vol(volumes[i]) if i < len(volumes) else "-"
        parts.append(f"{p}/{v}")
    return "  ".join(parts)

def signal_handler(sig, frame):
    """Ctrl+C 退出处理"""
    global running
    print("\n[INFO] 收到退出信号，正在停止...")
    running = False
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)


def on_tick_data(datas):
    """
    tick 级别回调函数
    
    回调数据格式: { stock_code: [data1, data2, ...] }
    每个 data 是一条 tick 记录
    """
    now = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    for stock_code, tick_list in datas.items():
        for tick in tick_list:
            # tick 数据关键字段：lastPrice, lastVolume, amount 等
            last_price = tick.get('lastPrice', 'N/A')
            last_vol = tick.get('lastVolume', 'N/A')
            amount = tick.get('amount', 'N/A')
            open_price = tick.get('open', 'N/A')
            high = tick.get('high', 'N/A')
            low = tick.get('low', 'N/A')
            
            print(f"[{now}] [TICK] {stock_code} | "
                  f"最新价: {fmt_price(last_price)} | 成交量: {last_vol} | "
                  f"成交额: {fmt_amount(amount)} | 开: {fmt_price(open_price)} | "
                  f"高: {fmt_price(high)} | 低: {fmt_price(low)}")


def on_daily_data(datas):
    """
    日线级别回调函数
    
    回调数据格式: { stock_code: [data1, data2, ...] }
    每个 data 是一条日K记录
    """
    now = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    for stock_code, kline_list in datas.items():
        for kline in kline_list:
            open_p = kline.get('open', 'N/A')
            high_p = kline.get('high', 'N/A')
            low_p = kline.get('low', 'N/A')
            close_p = kline.get('close', 'N/A')
            vol = kline.get('volume', 'N/A')
            amount = kline.get('amount', 'N/A')
            
            print(f"[{now}] [1D]   {stock_code} | "
                  f"开: {fmt_price(open_p)} | 高: {fmt_price(high_p)} | "
                  f"低: {fmt_price(low_p)} | 收: {fmt_price(close_p)} | "
                  f"量: {vol} | 额: {fmt_amount(amount)}")


def main():
    print("=" * 60)
    print("  单股实时订阅行情验证程序")
    print("=" * 60)
    print(f"  目标标的: {TARGET_CODE}")
    print(f"  当前时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  运行环境: Python {sys.version.split()[0]}")
    print("=" * 60)
    
    # 1. 先查询标的基础信息，确认代码正确
    print(f"\n[STEP 1] 查询 {TARGET_CODE} 基础信息...")
    try:
        detail = xtdata.get_instrument_detail(TARGET_CODE)
        if detail:
            name = detail.get('InstrumentName', '未知')
            print(f"  -> 代码: {TARGET_CODE}, 名称: {name}")
        else:
            print(f"  -> 警告: 未找到 {TARGET_CODE} 的基础信息，请检查代码是否正确")
    except Exception as e:
        print(f"  -> 查询异常: {e}")
    
    # 2. 订阅 tick 级别行情
    print(f"\n[STEP 2] 订阅 {TARGET_CODE} 的 tick 行情...")
    try:
        seq_tick = xtdata.subscribe_quote(
            TARGET_CODE,
            period=TICK_PERIOD,
            count=-1,
            callback=on_tick_data
        )
        if seq_tick and seq_tick > 0:
            print(f"  -> tick 订阅成功! 订阅号: {seq_tick}")
        else:
            print(f"  -> tick 订阅失败! 返回值: {seq_tick}")
    except Exception as e:
        print(f"  -> tick 订阅异常: {e}")
    
    # 3. 订阅日线级别行情
    print(f"\n[STEP 3] 订阅 {TARGET_CODE} 的 1d 行情...")
    try:
        seq_daily = xtdata.subscribe_quote(
            TARGET_CODE,
            period=DAILY_PERIOD,
            count=-1,
            callback=on_daily_data
        )
        if seq_daily and seq_daily > 0:
            print(f"  -> 1d 订阅成功! 订阅号: {seq_daily}")
        else:
            print(f"  -> 1d 订阅失败! 返回值: {seq_daily}")
    except Exception as e:
        print(f"  -> 1d 订阅异常: {e}")
    
    # 4. 等待一小段时间让订阅数据到达
    print("\n[STEP 4] 等待 2 秒，让订阅数据到达...")
    time.sleep(2)
    
    # 5. 主动拉一次当前数据，验证订阅后 get_market_data_ex 能否拿到
    print(f"\n[STEP 5] 主动拉取 {TARGET_CODE} 当前行情（验证订阅后数据可用性）...")
    try:
        tick_data = xtdata.get_market_data_ex(
            [], [TARGET_CODE], period=TICK_PERIOD, count=5
        )
        if TARGET_CODE in tick_data:
            df = tick_data[TARGET_CODE]
            print(f"  -> tick 数据行数: {len(df)}")
            if len(df) > 0:
                print(f"  -> 最新 5 条 tick:")
                print(df.tail(5).to_string())
        else:
            print(f"  -> tick 数据中未找到 {TARGET_CODE}（可能非交易时间）")
    except Exception as e:
        print(f"  -> 拉取 tick 数据异常: {e}")
    
    # 6. 进入 xtdata.run() 阻塞循环，等待回调推送
    print("\n" + "=" * 60)
    print("  进入实时推送监听模式 (xtdata.run)")
    print("  如果在交易时间内，有新 tick 时回调会被自动触发")
    print("  按 Ctrl+C 退出")
    print("=" * 60 + "\n")
    
    try:
        xtdata.run()
    except KeyboardInterrupt:
        print("\n[INFO] 用户中断，程序退出")
    except Exception as e:
        print(f"\n[ERROR] xtdata.run() 异常: {e}")


if __name__ == "__main__":
    main()
