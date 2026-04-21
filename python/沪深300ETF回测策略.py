#coding:gbk
"""
沪深300 ETF 回测策略（入门版）

这是一份给新手使用的 QMT 回测策略，目标是：
1. 先用一只 ETF 跑通买卖流程
2. 让日志尽量清楚，方便看懂策略在做什么
3. 逻辑保持简单，后面容易改成你自己的规则

当前默认交易标的是：
- 510300.SH  （沪深300ETF）

策略思路：
- 当价格站上 20 日均线，且 20 日均线在 60 日均线之上时，买入
- 当价格跌破 20 日均线，或者 20 日均线跌回 60 日均线下方时，卖出

这不是高频策略，更像一个“顺着大方向做”的简单择时策略。
"""

import numpy as np


def init(ContextInfo):
	ContextInfo.stock = '510300.SH'
	ContextInfo.set_universe([ContextInfo.stock])
	ContextInfo.accountID = 'testS'

	# holding 记录“手数”，1 表示 100 股
	ContextInfo.holding = 0
	ContextInfo.buy_price = 0
	ContextInfo.cash = ContextInfo.capital
	ContextInfo.profit = 0
	ContextInfo.fee_rate = 0.0003


def handlebar(ContextInfo):
	d = ContextInfo.barpos
	if d < 60:
		return

	stock = ContextInfo.stock
	now_date = timetag_to_datetime(ContextInfo.get_bar_timetag(d), '%Y%m%d')

	close_data = ContextInfo.get_history_data(61, '1d', 'close', 3)
	open_data = ContextInfo.get_history_data(1, '1d', 'open', 3)

	if stock not in close_data or stock not in open_data:
		return
	if len(close_data[stock]) < 61 or len(open_data[stock]) < 1:
		return

	close_list = close_data[stock]
	open_price = open_data[stock][-1]

	# 用昨天收盘价和均线做判断，避免一边看今天收盘一边按今天开盘成交
	prev_close = close_list[-2]
	ma20 = np.mean(close_list[-21:-1])
	ma60 = np.mean(close_list[-61:-1])

	buy_signal = 0
	sell_signal = 0

	# 买入条件：价格在 20 日线上方，并且短期趋势强于长期趋势
	if ContextInfo.holding == 0 and prev_close > ma20 and ma20 > ma60:
		buy_signal = 1
		shares_lot = int((ContextInfo.cash * 0.95) / open_price) / 100
		shares_lot = int(shares_lot)
		if shares_lot > 0:
			order_shares(stock, shares_lot * 100, 'fix', open_price, ContextInfo, ContextInfo.accountID)
			trade_amount = open_price * shares_lot * 100
			fee = trade_amount * ContextInfo.fee_rate
			ContextInfo.cash -= trade_amount + fee
			ContextInfo.holding = shares_lot
			ContextInfo.buy_price = open_price
			ContextInfo.profit -= fee
			print('[TRADE_DATE]', now_date)
			print('[BUY]', stock, 'price=', open_price, 'shares=', shares_lot * 100, 'amount=', trade_amount, 'fee=', fee)
			print('[STATE]', 'cash=', ContextInfo.cash, 'holding=', ContextInfo.holding * 100)

	# 卖出条件：跌破 20 日线，或者短期趋势转弱
	elif ContextInfo.holding > 0 and (prev_close < ma20 or ma20 < ma60):
		sell_signal = 1
		order_shares(stock, -ContextInfo.holding * 100, 'fix', open_price, ContextInfo, ContextInfo.accountID)
		trade_amount = open_price * ContextInfo.holding * 100
		fee = trade_amount * ContextInfo.fee_rate
		ContextInfo.cash += trade_amount - fee
		ContextInfo.profit += (open_price - ContextInfo.buy_price) * ContextInfo.holding * 100 - fee
		print('[TRADE_DATE]', now_date)
		print('[SELL]', stock, 'price=', open_price, 'shares=', ContextInfo.holding * 100, 'amount=', trade_amount, 'fee=', fee)
		print('[STATE]', 'cash=', ContextInfo.cash, 'holding=', 0)
		ContextInfo.holding = 0
		ContextInfo.buy_price = 0

	# 持仓状态日志。为了避免刷屏，只在每 20 根 bar 打一次
	elif d % 20 == 0:
		print('[CHECK]', now_date, 'prev_close=', prev_close, 'ma20=', ma20, 'ma60=', ma60, 'holding=', ContextInfo.holding * 100, 'cash=', ContextInfo.cash)

	profit_ratio = ContextInfo.profit / ContextInfo.capital
	ContextInfo.paint('profit_ratio', profit_ratio, -1, 0)
	ContextInfo.paint('buy_signal', buy_signal, -1, 0)
	ContextInfo.paint('sell_signal', sell_signal, -1, 0)
