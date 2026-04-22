#coding:gbk
"""
沪深300 ETF 回测策略（入门版）

目标：
1. 只交易一只 ETF：510300.SH
2. 逻辑简单，方便新手看懂
3. 回测期间输出结构化 JSON，方便后续程序自动分析

策略思路：
- 当昨天收盘价在 20 日均线之上，且 20 日均线在 60 日均线之上时，买入
- 当昨天收盘价跌回 20 日均线之下，或者 20 日均线跌回 60 日均线之下时，卖出

输出文件：
- outputs/result.json
- outputs/trade_log.json
"""

import os
import json
import numpy as np


def _get_output_dir():
	try:
		base_dir = os.path.dirname(os.path.abspath(__file__))
	except Exception:
		base_dir = os.getcwd()
	output_dir = os.path.join(base_dir, 'outputs')
	if not os.path.exists(output_dir):
		os.makedirs(output_dir)
	return output_dir


def _to_float(value):
	try:
		return float(value)
	except Exception:
		return 0.0


def _safe_date(ContextInfo, d):
	try:
		return timetag_to_datetime(ContextInfo.get_bar_timetag(d), '%Y%m%d')
	except Exception:
		return ''


def _snapshot_holding(ContextInfo, market_price):
	if ContextInfo.holding <= 0:
		return {}
	return {
		ContextInfo.stock: {
			'lots': int(ContextInfo.holding),
			'shares': int(ContextInfo.holding * 100),
			'buy_price': _to_float(ContextInfo.buy_price),
			'market_price': _to_float(market_price),
			'market_value': _to_float(market_price) * ContextInfo.holding * 100
		}
	}


def _record_trade(ContextInfo, date_str, action, price, shares, fee, note=''):
	record = {
		'date': date_str,
		'action': action,
		'code': ContextInfo.stock,
		'price': _to_float(price),
		'shares': int(shares),
		'amount': _to_float(price) * int(shares),
		'fee': _to_float(fee),
		'cash_after': _to_float(ContextInfo.cash),
		'holdings_after': _snapshot_holding(ContextInfo, ContextInfo.last_price),
		'note': note
	}
	ContextInfo.trade_log.append(record)


def _build_result(ContextInfo, current_date):
	final_market_value = 0.0
	if ContextInfo.holding > 0:
		final_market_value = ContextInfo.last_price * ContextInfo.holding * 100
	final_total_asset = _to_float(ContextInfo.cash) + final_market_value
	final_profit = final_total_asset - _to_float(ContextInfo.capital)
	profit_ratio = 0.0
	if _to_float(ContextInfo.capital) != 0:
		profit_ratio = final_profit / _to_float(ContextInfo.capital)

	buy_count = len([item for item in ContextInfo.trade_log if item['action'] == 'buy'])
	sell_count = len([item for item in ContextInfo.trade_log if item['action'] == 'sell'])

	return {
		'strategy_name': ContextInfo.strategy_name,
		'start_date': ContextInfo.start_date,
		'end_date': current_date,
		'initial_capital': _to_float(ContextInfo.capital),
		'final_cash': _to_float(ContextInfo.cash),
		'final_profit': final_profit,
		'profit_ratio': profit_ratio,
		'current_holdings': _snapshot_holding(ContextInfo, ContextInfo.last_price),
		'rebalance_count': int(ContextInfo.rebalance_count),
		'buy_count': int(buy_count),
		'sell_count': int(sell_count),
		'universe_size': 1,
		'final_market_value': final_market_value,
		'final_total_asset': final_total_asset,
		'params': {
			'stock': ContextInfo.stock,
			'buy_rule': 'prev_close > ma20 and ma20 > ma60',
			'sell_rule': 'prev_close < ma20 or ma20 < ma60',
			'buy_cash_ratio': 0.95,
			'fee_rate': _to_float(ContextInfo.fee_rate)
		},
		'notes': '没有强依赖回测结束回调。每次发生买卖时覆盖写出 JSON；如果 QMT 支持 is_last_bar，则最后一根 bar 再写一次。'
	}


def _write_json_files(ContextInfo, current_date):
	output_dir = _get_output_dir()
	result_path = os.path.join(output_dir, 'result.json')
	trade_log_path = os.path.join(output_dir, 'trade_log.json')

	with open(result_path, 'w', encoding='utf-8') as f:
		json.dump(_build_result(ContextInfo, current_date), f, ensure_ascii=False, indent=2)

	with open(trade_log_path, 'w', encoding='utf-8') as f:
		json.dump(ContextInfo.trade_log, f, ensure_ascii=False, indent=2)


def _flush_outputs(ContextInfo, current_date):
	try:
		_write_json_files(ContextInfo, current_date)
	except Exception as e:
		print('[OUTPUT_ERROR] date=%s msg=%s' % (current_date, str(e)))


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

	# 结构化输出所需状态
	ContextInfo.strategy_name = '沪深300ETF回测策略'
	ContextInfo.trade_log = []
	ContextInfo.rebalance_count = 0
	ContextInfo.start_date = ''
	ContextInfo.end_date = ''
	ContextInfo.last_price = 0.0


def handlebar(ContextInfo):
	d = ContextInfo.barpos
	if d < 60:
		return

	stock = ContextInfo.stock
	now_date = _safe_date(ContextInfo, d)

	if not ContextInfo.start_date:
		ContextInfo.start_date = now_date
	ContextInfo.end_date = now_date

	close_data = ContextInfo.get_history_data(61, '1d', 'close', 3)
	open_data = ContextInfo.get_history_data(1, '1d', 'open', 3)

	if stock not in close_data or stock not in open_data:
		return
	if len(close_data[stock]) < 61 or len(open_data[stock]) < 1:
		return

	close_list = close_data[stock]
	open_price = _to_float(open_data[stock][-1])
	ContextInfo.last_price = open_price

	# 用昨天收盘价和均线做判断，避免一边看今天收盘一边按今天开盘成交
	prev_close = _to_float(close_list[-2])
	ma20 = _to_float(np.mean(close_list[-21:-1]))
	ma60 = _to_float(np.mean(close_list[-61:-1]))

	buy_signal = 0
	sell_signal = 0

	# 买入条件：价格在 20 日线上方，并且短期趋势强于长期趋势
	if ContextInfo.holding == 0 and prev_close > ma20 and ma20 > ma60:
		ContextInfo.rebalance_count += 1
		buy_signal = 1
		shares_lot = int((ContextInfo.cash * 0.95) / open_price) / 100
		shares_lot = int(shares_lot)
		if shares_lot > 0:
			shares = int(shares_lot * 100)
			trade_amount = open_price * shares
			fee = trade_amount * ContextInfo.fee_rate

			order_shares(stock, shares, 'fix', open_price, ContextInfo, ContextInfo.accountID)
			ContextInfo.cash -= trade_amount + fee
			ContextInfo.holding = shares_lot
			ContextInfo.buy_price = open_price
			ContextInfo.profit -= fee

			print('[REBALANCE] date=%s action=buy index=%s' % (now_date, ContextInfo.rebalance_count))
			print('[BUY] code=%s price=%.4f shares=%s amount=%.2f fee=%.2f' % (
				stock, open_price, shares, trade_amount, fee
			))
			print('[SUMMARY] cash=%.2f holding=%s total_asset=%.2f' % (
				ContextInfo.cash,
				ContextInfo.holding * 100,
				ContextInfo.cash + open_price * ContextInfo.holding * 100
			))
			_record_trade(ContextInfo, now_date, 'buy', open_price, shares, fee, 'trend_follow_buy')
			_flush_outputs(ContextInfo, now_date)

	# 卖出条件：跌破 20 日线，或者短期趋势转弱
	elif ContextInfo.holding > 0 and (prev_close < ma20 or ma20 < ma60):
		ContextInfo.rebalance_count += 1
		sell_signal = 1
		shares = int(ContextInfo.holding * 100)
		trade_amount = open_price * shares
		fee = trade_amount * ContextInfo.fee_rate

		order_shares(stock, -shares, 'fix', open_price, ContextInfo, ContextInfo.accountID)
		ContextInfo.cash += trade_amount - fee
		ContextInfo.profit += (open_price - ContextInfo.buy_price) * shares - fee

		print('[REBALANCE] date=%s action=sell index=%s' % (now_date, ContextInfo.rebalance_count))
		print('[SELL] code=%s price=%.4f shares=%s amount=%.2f fee=%.2f' % (
			stock, open_price, shares, trade_amount, fee
		))
		print('[SUMMARY] cash=%.2f holding=%s total_asset=%.2f' % (
			ContextInfo.cash,
			0,
			ContextInfo.cash
		))

		ContextInfo.holding = 0
		ContextInfo.buy_price = 0
		_record_trade(ContextInfo, now_date, 'sell', open_price, shares, fee, 'trend_follow_sell')
		_flush_outputs(ContextInfo, now_date)

	# 没有交易时，定期打印检查日志
	elif d % 20 == 0:
		total_asset = ContextInfo.cash + open_price * ContextInfo.holding * 100
		print('[CHECK] date=%s code=%s prev_close=%.4f ma20=%.4f ma60=%.4f holding=%s cash=%.2f total_asset=%.2f' % (
			now_date, stock, prev_close, ma20, ma60, ContextInfo.holding * 100, ContextInfo.cash, total_asset
		))

	final_market_value = 0.0
	if ContextInfo.holding > 0:
		final_market_value = open_price * ContextInfo.holding * 100
	final_total_asset = ContextInfo.cash + final_market_value
	profit_ratio = 0.0
	if _to_float(ContextInfo.capital) != 0:
		profit_ratio = (final_total_asset - ContextInfo.capital) / _to_float(ContextInfo.capital)

	ContextInfo.paint('profit_ratio', profit_ratio, -1, 0)
	ContextInfo.paint('buy_signal', buy_signal, -1, 0)
	ContextInfo.paint('sell_signal', sell_signal, -1, 0)

	try:
		if ContextInfo.is_last_bar():
			_flush_outputs(ContextInfo, now_date)
	except Exception:
		pass
