# -*- coding: utf-8 -*-
"""
港股实时订阅行情验证程序
=========================
目标：验证 xtdata 在港股交易时段能否正常收到实时推送

使用方式：
  在 MiniQMT 已登录的情况下运行：
    d:\python_envs\gd_qmt_env\python.exe subscribe_hk_stock.py

港股交易时间（北京时间）：
  开盘前时段: 09:00-09:30
  持续交易:   09:30-12:00
  午间休市:   12:00-13:00
  持续交易:   13:00-16:00
  收市竞价:   16:00-16:10

注意事项：
  - 必须在 MiniQMT 已登录的状态下运行
  - 港股代码格式为 数字.HK（如 00700.HK）
  - 港股无涨跌停限制，价格波动可能较大
"""

from xtquant import xtdata
import time
import signal
import sys
import datetime

# ==================== 配置区 ====================
# 运行模式（三选一）
#   1 = 单股 tick 精简模式（只看 TARGET_CODE 的 tick，不看日线）
#   2 = 日线模式（只看 DAILY_TARGETS 的日线，不看 tick）
#   3 = 标准模式（tick + 1d 都订阅）
RUN_MODE = 1

# 过滤开关：开启后本笔增量为 0 的 tick 自动跳过不打印
FILTER_ZERO_DELTA = True

# tick 单股目标（模式 1 和 3 时生效）
TARGET_CODE = "00700.HK"   # 腾讯控股

# 日线多股目标（模式 2 时生效，改这个列表加减标的）
DAILY_TARGETS = [
    "00700.HK",   # 腾讯控股
    # "09988.HK",   # 阿里巴巴-SW
    # "01810.HK",   # 小米集团-W
    # "03690.HK",   # 美团-W
    # "09888.HK",   # 百度集团-SW
]

TICK_PERIOD = "tick"
DAILY_PERIOD = "1d"
# ================================================

# 模式名称映射
MODE_NAMES = {
    1: "单股tick精简",
    2: "日线模式",
    3: "标准模式(tick+1d)",
}

running = True

# tick 累计量缓存
_prev_tick = {}


def fmt_price(value):
    """格式化价格，消除浮点精度尾巴"""
    if value == 'N/A' or not isinstance(value, (int, float)):
        return str(value)
    if value >= 100:
        return f"{value:.2f}"
    if value >= 10:
        return f"{value:.3f}"
    return f"{value:.3f}"


def fmt_amount(value):
    """格式化成交额（港股单位为港元）"""
    if value == 'N/A' or not isinstance(value, (int, float)):
        return str(value)
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f}亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.2f}万"
    return f"{value:.2f}"


def fmt_vol(value):
    """格式化成交量（港股1手=不同股数，这里按原始单位显示）"""
    if value == 'N/A' or not isinstance(value, (int, float)):
        return str(value)
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f}亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.2f}万"
    return f"{int(value)}"


def fmt_chg(price, pre_close):
    """格式化涨跌幅"""
    try:
        if (price != 'N/A' and pre_close != 'N/A'
                and isinstance(price, (int, float))
                and isinstance(pre_close, (int, float))
                and pre_close > 0):
            chg_pct = (price - pre_close) / pre_close * 100
            chg_val = price - pre_close
            return f"{chg_val:+.3f} ({chg_pct:+.2f}%)"
        return "N/A"
    except Exception:
        return "N/A"


def fmt_bid_ask(prices, volumes):
    """格式化五档盘口"""
    if not prices or not volumes:
        return "N/A"
    parts = []
    for i in range(min(5, len(prices))):
        p = fmt_price(prices[i]) if isinstance(prices[i], (int, float)) else str(prices[i])
        v = fmt_vol(volumes[i]) if i < len(volumes) else "-"
        parts.append(f"{p}/{v}")
    return "  ".join(parts)


def signal_handler(sig, frame):
    global running
    print("\n[INFO] 收到退出信号，正在停止...")
    running = False
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)


def on_tick_data(datas):
    """
    tick 级别回调函数
    """
    global _prev_tick
    for stock_code, tick_list in datas.items():
        for tick in tick_list:
            # 使用 tick 自带的成交时间
            tick_ts = tick.get('time', 0)
            # 判断时间戳是否有效（非空、非负数），否则使用当前时间
            if isinstance(tick_ts, (int, float)) and tick_ts > 0:
                tick_time = datetime.datetime.fromtimestamp(tick_ts / 1000).strftime("%H:%M:%S.%f")[:-3]
            else:
                tick_time = "?:?:?.???"

            last_price = tick.get('lastPrice', 'N/A')
            open_price = tick.get('open', 'N/A')
            high = tick.get('high', 'N/A')
            low = tick.get('low', 'N/A')
            pre_close = tick.get('lastClose', 'N/A')
            volume = tick.get('volume', 'N/A')
            amount = tick.get('amount', 'N/A')
            ask_prices = tick.get('askPrice', [])
            bid_prices = tick.get('bidPrice', [])
            ask_vols = tick.get('askVol', [])
            bid_vols = tick.get('bidVol', [])
            txn_num = tick.get('transactionNum', 'N/A')
            stock_status = tick.get('stockStatus', 'N/A')

            # 计算本笔增量
            delta_vol = 'N/A'
            delta_amt = 'N/A'
            if isinstance(volume, (int, float)) and isinstance(amount, (int, float)):
                prev = _prev_tick.get(stock_code)
                if prev is not None:
                    dv = volume - prev.get('volume', 0)
                    da = amount - prev.get('amount', 0)
                    if dv >= 0:
                        delta_vol = dv
                    if da >= 0:
                        delta_amt = da
                _prev_tick[stock_code] = {'volume': volume, 'amount': amount}

            # 过滤：本笔增量为 0 时跳过不打印
            if FILTER_ZERO_DELTA:
                if delta_vol != 'N/A' and delta_vol == 0 and delta_amt != 'N/A' and delta_amt == 0:
                    continue

            chg_str = fmt_chg(last_price, pre_close)

            print(f"\n[{tick_time}] [{stock_code}] "
                  f"最新: {fmt_price(last_price)} | 涨跌: {chg_str}")
            print(f"  开: {fmt_price(open_price)} | "
                  f"高: {fmt_price(high)} | "
                  f"低: {fmt_price(low)} | "
                  f"昨收: {fmt_price(pre_close)}")
            print(f"  本笔: {fmt_vol(delta_vol)} / {fmt_amount(delta_amt)} | "
                  f"累计: {fmt_vol(volume)} / {fmt_amount(amount)} | "
                  f"笔数: {txn_num} | 状态: {stock_status}")
            print(f"  卖五~卖一: {fmt_bid_ask(list(reversed(ask_prices)), list(reversed(ask_vols)))}")
            print(f"  买一~买五: {fmt_bid_ask(bid_prices, bid_vols)}")


def on_daily_data(datas):
    """
    日线级别回调函数
    
    日线数据字段：time(毫秒时间戳), open, high, low, close, volume, amount,
                 settlementPrice, openInterest, dr, totaldr, suspendFlag
    """
    for stock_code, kline_list in datas.items():
        for kline in kline_list:
            # 使用日线自带的 time 时间戳
            kline_ts = kline.get('time', 0)
            if isinstance(kline_ts, (int, float)) and kline_ts > 0:
                kline_time = datetime.datetime.fromtimestamp(kline_ts / 1000).strftime("%Y-%m-%d %H:%M")
            else:
                kline_time = "????-??-??"
            open_p = kline.get('open', 'N/A')
            high_p = kline.get('high', 'N/A')
            low_p = kline.get('low', 'N/A')
            close_p = kline.get('close', 'N/A')
            vol = kline.get('volume', 'N/A')
            amount = kline.get('amount', 'N/A')

            print(f"[{kline_time}] [1D] {stock_code} | "
                  f"开: {fmt_price(open_p)} | 高: {fmt_price(high_p)} | "
                  f"低: {fmt_price(low_p)} | 收: {fmt_price(close_p)} | "
                  f"量: {fmt_vol(vol)} | 额: {fmt_amount(amount)}")


def main():
    mode_name = MODE_NAMES.get(RUN_MODE, f"未知模式({RUN_MODE})")
    print("=" * 60)
    print("  港股实时订阅行情验证程序")
    print("=" * 60)
    print(f"  模式: {mode_name} (RUN_MODE={RUN_MODE})")
    print(f"  过滤零增量: {'开' if FILTER_ZERO_DELTA else '关'}")
    if RUN_MODE == 1:
        print(f"  目标: {TARGET_CODE} (tick)")
    elif RUN_MODE == 2:
        print(f"  目标: {DAILY_TARGETS} (1d)")
    else:
        print(f"  目标: {TARGET_CODE} (tick + 1d)")
    print(f"  当前时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  运行环境: Python {sys.version.split()[0]}")
    print(f"  港股交易时间: 09:30-12:00 / 13:00-16:00 (北京时间)")
    print("=" * 60)

    # 1. 查询标的基础信息
    query_codes = DAILY_TARGETS if RUN_MODE == 2 else [TARGET_CODE]
    print(f"\n[STEP 1] 查询标的基础信息...")
    for code in query_codes:
        try:
            detail = xtdata.get_instrument_detail(code)
            if detail:
                name = detail.get('InstrumentName', '未知')
                print(f"  -> {code}: {name}")
            else:
                print(f"  -> {code}: 未找到（请检查代码格式，如 00700.HK）")
        except Exception as e:
            print(f"  -> {code}: 查询异常 - {e}")

    # 2. 订阅 tick 行情（模式 1 和 3）
    if RUN_MODE in (1, 3):
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

    # 3. 订阅日线行情（模式 2 和 3）
    if RUN_MODE in (2, 3):
        targets = DAILY_TARGETS if RUN_MODE == 2 else [TARGET_CODE]
        for code in targets:
            print(f"\n[STEP 3] 订阅 {code} 的 1d 行情...")
            try:
                seq_daily = xtdata.subscribe_quote(
                    code,
                    period=DAILY_PERIOD,
                    count=-1,
                    callback=on_daily_data
                )
                if seq_daily and seq_daily > 0:
                    print(f"  -> {code} 1d 订阅成功! 订阅号: {seq_daily}")
                else:
                    print(f"  -> {code} 1d 订阅失败! 返回值: {seq_daily}")
            except Exception as e:
                print(f"  -> {code} 1d 订阅异常: {e}")

    # 4. 等待数据到达
    print("\n[STEP 4] 等待 3 秒，让订阅数据到达...")
    time.sleep(3)

    # 5. 主动拉一次当前数据
    if RUN_MODE in (1, 3):
        print(f"\n[STEP 5] 主动拉取 {TARGET_CODE} 当前行情...")
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
    else:
        # 日线模式：拉一次各标的日线
        print(f"\n[STEP 5] 主动拉取日线数据...")
        for code in DAILY_TARGETS:
            try:
                daily_data = xtdata.get_market_data_ex(
                    [], [code], period=DAILY_PERIOD, count=3
                )
                if code in daily_data:
                    df = daily_data[code]
                    print(f"  -> {code} 最近 {len(df)} 日:")
                    if len(df) > 0:
                        print(df.tail(3).to_string())
                else:
                    print(f"  -> {code}: 未获取到日线数据")
            except Exception as e:
                print(f"  -> {code} 拉取日线异常: {e}")

    # 6. 进入监听
    print("\n" + "=" * 60)
    print("  进入港股实时推送监听模式 (xtdata.run)")
    print("  港股交易时间: 09:30-12:00 / 13:00-16:00 (北京时间)")
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
