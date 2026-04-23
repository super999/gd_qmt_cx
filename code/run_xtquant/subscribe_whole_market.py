# -*- coding: utf-8 -*-
"""
全推行情订阅验证程序
===================
目标：验证 xtdata.subscribe_whole_quote 能否在开盘期间接收全市场/指定股票的实时推送

使用方式：
  在 MiniQMT 已登录且处于开盘时间的情况下运行：
    d:\python_envs\gd_qmt_env\python.exe subscribe_whole_market.py

功能：
  1. 模式A：订阅全市场 (SH + SZ)，实时统计推送的标的数量和样本数据
  2. 模式B：订阅指定 ETF 列表，实时打印行情变化
  3. 默认使用模式B（指定ETF列表），通过命令行参数切换
  4. 按 Ctrl+C 退出程序

注意事项：
  - 必须在 MiniQMT 已登录的状态下运行
  - 必须在交易时间（9:15-15:00）内运行才能收到实时推送
  - 全推行情的数据类型固定为 tick（分笔）
  - subscribe_whole_quote 回调格式与 subscribe_quote 不同：
    - subscribe_quote: { stock_code: [data1, data2, ...] }
    - subscribe_whole_quote: { stock1: data1, stock2: data2, ... }  (每只股票单个data对象)
"""

from xtquant import xtdata
import time
import signal
import sys
import datetime
import argparse

# ==================== 配置区 ====================
# 模式A：全市场订阅
MARKET_CODES = ["SH", "SZ"]

# 模式B：指定ETF列表订阅
ETF_LIST = [
    "510300.SH",   # 沪深300ETF
    "510050.SH",   # 上证50ETF
    "510500.SH",   # 中证500ETF
    "159919.SZ",   # 沪深300ETF(深)
    "588000.SH",   # 科创50ETF
]

# 统计相关
push_count = 0       # 总推送次数
stock_count = 0      # 累计不同标的数
stock_set = set()    # 已见过的标的集合
start_time = None    # 程序启动时间
# ================================================

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

def signal_handler(sig, frame):
    """Ctrl+C 退出处理"""
    global running
    print("\n[INFO] 收到退出信号，正在停止...")
    running = False
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)


def on_whole_market_data(datas):
    """
    全推行情回调 - 全市场模式
    
    回调数据格式: { stock1: data1, stock2: data2, ... }
    每个股票对应单个 data 对象（非列表）
    
    全市场模式下推送量很大，这里只做统计 + 打印重点标的
    """
    global push_count, stock_count, stock_set, start_time
    
    push_count += 1
    current_stocks = set(datas.keys())
    stock_set.update(current_stocks)
    stock_count = len(stock_set)
    
    now = datetime.datetime.now().strftime("%H:%M:%S")
    elapsed = (datetime.datetime.now() - start_time).total_seconds() if start_time else 0
    
    # 每 10 次推送打印一次统计摘要
    if push_count % 10 == 0:
        print(f"[{now}] [全推统计] 第{push_count}次推送 | "
              f"本次推送标的数: {len(datas)} | "
              f"累计不同标的: {stock_count} | "
              f"运行时长: {elapsed:.0f}s")
    
    # 打印重点标的的行情（如果在本次推送中）
    focus_codes = {"510300.SH", "510050.SH", "000001.SZ", "600519.SH"}
    for code in focus_codes:
        if code in datas:
            data = datas[code]
            last_price = data.get('lastPrice', 'N/A')
            last_vol = data.get('lastVolume', 'N/A')
            name = data.get('instrumentName', '')
            print(f"  [{now}] [FOCUS] {code} {name} | "
                  f"最新价: {fmt_price(last_price)} | 成交量: {last_vol}")


def on_etf_list_data(datas):
    """
    全推行情回调 - ETF列表模式
    
    回调数据格式: { stock1: data1, stock2: data2, ... }
    每个股票对应单个 data 对象（非列表）
    
    ETF列表模式下标的少，逐个打印详细信息
    """
    global push_count, start_time
    
    push_count += 1
    now = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    elapsed = (datetime.datetime.now() - start_time).total_seconds() if start_time else 0
    
    for code, data in datas.items():
        last_price = data.get('lastPrice', 'N/A')
        last_vol = data.get('lastVolume', 'N/A')
        amount = data.get('amount', 'N/A')
        open_p = data.get('open', 'N/A')
        high_p = data.get('high', 'N/A')
        low_p = data.get('low', 'N/A')
        pre_close = data.get('lastClose', 'N/A')
        name = data.get('instrumentName', '')
        
        # 计算涨跌幅
        try:
            if last_price != 'N/A' and pre_close != 'N/A' and pre_close > 0:
                chg_pct = (last_price - pre_close) / pre_close * 100
                chg_str = f"{chg_pct:+.2f}%"
            else:
                chg_str = "N/A"
        except Exception:
            chg_str = "N/A"
        
        print(f"[{now}] [{code}] {name} | "
              f"最新: {fmt_price(last_price)} | 涨跌: {chg_str} | "
              f"开: {fmt_price(open_p)} | 高: {fmt_price(high_p)} | 低: {fmt_price(low_p)} | "
              f"量: {last_vol} | 额: {fmt_amount(amount)}")
    
    if push_count % 20 == 0:
        print(f"  -- 累计推送 {push_count} 次 | 运行 {elapsed:.0f}s --")


def main():
    global start_time
    start_time = datetime.datetime.now()
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="全推行情订阅验证程序")
    parser.add_argument(
        "--mode", 
        choices=["market", "etf"], 
        default="etf",
        help="订阅模式: market=全市场(SH+SZ), etf=指定ETF列表 (默认: etf)"
    )
    args = parser.parse_args()
    
    print("=" * 60)
    print("  全推行情订阅验证程序")
    print("=" * 60)
    print(f"  订阅模式: {'全市场 (SH+SZ)' if args.mode == 'market' else '指定ETF列表'}")
    print(f"  当前时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  运行环境: Python {sys.version.split()[0]}")
    print("=" * 60)
    
    if args.mode == "market":
        # ---- 模式A：全市场订阅 ----
        code_list = MARKET_CODES
        callback = on_whole_market_data
        print(f"\n[STEP 1] 准备订阅全市场行情: {code_list}")
    else:
        # ---- 模式B：指定ETF列表订阅 ----
        code_list = ETF_LIST
        callback = on_etf_list_data
        print(f"\n[STEP 1] 准备订阅 ETF 列表: {code_list}")
    
    # 先查询各标的基础信息
    print("\n[STEP 2] 查询标的基础信息...")
    for code in code_list:
        # 跳过市场代码(如SH, SZ)，只查询具体合约
        if '.' in code:
            try:
                detail = xtdata.get_instrument_detail(code)
                if detail:
                    name = detail.get('InstrumentName', '未知')
                    print(f"  -> {code}: {name}")
                else:
                    print(f"  -> {code}: 未找到基础信息")
            except Exception as e:
                print(f"  -> {code}: 查询异常 - {e}")
        else:
            print(f"  -> {code}: 市场代码（全市场订阅）")
    
    # 订阅全推行情
    print(f"\n[STEP 3] 调用 subscribe_whole_quote 订阅...")
    try:
        seq = xtdata.subscribe_whole_quote(code_list, callback=callback)
        if seq and seq > 0:
            print(f"  -> 全推订阅成功! 订阅号: {seq}")
        else:
            print(f"  -> 全推订阅失败! 返回值: {seq}")
            print("  -> 请确认 MiniQMT 已登录")
            return
    except Exception as e:
        print(f"  -> 全推订阅异常: {e}")
        return
    
    # 等待初始数据到达
    print("\n[STEP 4] 等待 3 秒，让全推数据首次到达...")
    time.sleep(3)
    
    # 打印当前统计
    if args.mode == "market":
        print(f"\n[STEP 5] 当前统计: 累计推送 {push_count} 次, 不同标的 {stock_count} 只")
    else:
        print(f"\n[STEP 5] 当前统计: 累计推送 {push_count} 次")
    
    if push_count == 0:
        print("  -> 注意: 未收到任何推送数据")
        print("  -> 可能原因: 1)非交易时间 2)MiniQMT未登录 3)网络问题")
        print("  -> 程序将继续监听，交易时间开始后会自动接收推送")
    
    # 进入 xtdata.run() 阻塞循环
    print("\n" + "=" * 60)
    print("  进入全推行情监听模式 (xtdata.run)")
    print("  如果在交易时间内，有新 tick 时回调会被自动触发")
    print("  按 Ctrl+C 退出")
    print("=" * 60 + "\n")
    
    try:
        xtdata.run()
    except KeyboardInterrupt:
        print("\n[INFO] 用户中断，程序退出")
        print(f"\n[最终统计] 总推送: {push_count} 次 | "
              f"不同标的: {stock_count} 只 | "
              f"运行时长: {(datetime.datetime.now() - start_time).total_seconds():.0f}s")
    except Exception as e:
        print(f"\n[ERROR] xtdata.run() 异常: {e}")


if __name__ == "__main__":
    main()
