#!/usr/bin/env python3

from pprint import pprint
import sys
import time

# 1. 动态引入 QMT 库路径（请根据你实际的 bin 路径调整）
# 这里的路径通常是 QMT 安装目录下的 xtquant 文件夹所在位置

try:
    from xtquant import xtdata
    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
    print("✅ xtquant 库加载成功")
except ImportError:
    print("❌ 未找到 xtquant 库，请检查路径设置")
    sys.exit()

# 2. 测试行情连接 (无需登录交易账号即可测试)
def test_data():
    stock_code = '510300.SH' # 以沪深300为例
    history = xtdata.download_history_data(stock_code, period='1d', start_time='2026-04-01', end_time='2026-04-30')
    pprint(history)
    data = xtdata.get_market_data(stock_list=[stock_code], period='1d')
    if data:
        print(f"✅ 行情连接正常，{stock_code} 最新数据已获取")
    else:
        print("⚠️ 行情库已加载，但未获取到数据，请确认 QMT 客户端已启动且数据已下载")
    pprint(data)

if __name__ == "__main__":
    test_data()