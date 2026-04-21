#coding:gbk
"""
回测模型示例（非实盘交易策略）
# HS300日线下运行，20个交易日进行一次调仓
# 每次买入在买入备选中因子评分前10的股票
# 每支股票各分配当前可用资金的10%（权重可调整）
# 扩展数据需要在补完HS300成分股数据之后生成
# 本模型中扩展数据暂时使用VBA指标ATR和ADTM生成，命名为atr和adtm

本版本在尽量保留原策略结构的前提下，补充了两类能力：
1. 回测过程中的结构化交易记录
2. 回测期间和回测结束时自动输出 JSON 文件，方便后续程序继续读取分析
"""

import os
import json
import pandas as pd
import numpy as np
import time
import datetime


def _get_output_dir():
	"""
	输出目录优先放在当前策略文件旁边的 outputs 目录中。
	如果 __file__ 不可用，则退回当前工作目录。
	"""
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


def _get_market_value(ContextInfo, price_dict):
	total_value = 0.0
	for code, lot in list(ContextInfo.holdings.items()):
		if lot > 0 and code in price_dict and len(price_dict[code]) > 0:
			total_value += _to_float(price_dict[code][-1]) * lot * 100
	return total_value


def _snapshot_holdings(ContextInfo, price_dict):
	snapshot = {}
	for code, lot in list(ContextInfo.holdings.items()):
		if lot > 0:
			market_price = 0.0
			if code in price_dict and len(price_dict[code]) > 0:
				market_price = _to_float(price_dict[code][-1])
			snapshot[code] = {
				'lots': int(lot),
				'shares': int(lot * 100),
				'buy_price': _to_float(ContextInfo.buypoint.get(code, 0)),
				'market_price': market_price,
				'market_value': market_price * lot * 100
			}
	return snapshot


def _record_trade(ContextInfo, date_str, action, code, price, shares, fee, rebalance_index, note=''):
	amount = _to_float(price) * int(shares)
	record = {
		'date': date_str,
		'action': action,
		'code': code,
		'price': _to_float(price),
		'shares': int(shares),
		'amount': amount,
		'fee': _to_float(fee),
		'cash_after': _to_float(ContextInfo.money),
		'holdings_after': _snapshot_holdings(ContextInfo, ContextInfo.latest_price),
		'rebalance_index': int(rebalance_index),
		'note': note
	}
	ContextInfo.trade_log.append(record)


def _build_result(ContextInfo, current_date):
	final_market_value = _get_market_value(ContextInfo, ContextInfo.latest_price)
	final_total_asset = _to_float(ContextInfo.money) + final_market_value
	final_profit = final_total_asset - _to_float(ContextInfo.capital)
	profit_ratio = 0.0
	if _to_float(ContextInfo.capital) != 0:
		profit_ratio = final_profit / _to_float(ContextInfo.capital)

	buy_count = len([item for item in ContextInfo.trade_log if item['action'] == 'buy'])
	sell_count = len([item for item in ContextInfo.trade_log if item['action'] == 'sell'])

	result = {
		'strategy_name': ContextInfo.strategy_name,
		'start_date': ContextInfo.start_date,
		'end_date': current_date,
		'initial_capital': _to_float(ContextInfo.capital),
		'final_cash': _to_float(ContextInfo.money),
		'final_profit': final_profit,
		'profit_ratio': profit_ratio,
		'current_holdings': _snapshot_holdings(ContextInfo, ContextInfo.latest_price),
		'rebalance_count': int(ContextInfo.rebalance_count),
		'buy_count': int(buy_count),
		'sell_count': int(sell_count),
		'universe_size': int(len(ContextInfo.s)),
		'final_market_value': final_market_value,
		'final_total_asset': final_total_asset,
		'params': {
			'universe_sector': '000300.SH',
			'rebalance_interval_bars': 20,
			'max_holdings': 10,
			'weight': list(ContextInfo.weight),
			'fee_rate': _to_float(ContextInfo.fee_rate),
			'rank_formula': 'rank_total = 1.0 * atr_rank'
		},
		'notes': '回测结束回调不确定，因此本策略在每次调仓后都会覆盖写出 JSON；如果 QMT 支持 is_last_bar，则最后一根 bar 再写一次最终结果。'
	}
	return result


def _write_json_files(ContextInfo, current_date):
	output_dir = _get_output_dir()
	result_path = os.path.join(output_dir, 'result.json')
	trade_log_path = os.path.join(output_dir, 'trade_log.json')

	result = _build_result(ContextInfo, current_date)

	with open(result_path, 'w', encoding='utf-8') as f:
		json.dump(result, f, ensure_ascii=False, indent=2)

	with open(trade_log_path, 'w', encoding='utf-8') as f:
		json.dump(ContextInfo.trade_log, f, ensure_ascii=False, indent=2)


def _flush_outputs(ContextInfo, current_date):
	"""
	兼容处理：
	1. 每次调仓后写一次，避免回测中断时完全拿不到结果
	2. 如果 QMT 支持 is_last_bar，则在最后一根 bar 再写一次最终结果
	"""
	try:
		_write_json_files(ContextInfo, current_date)
	except Exception as e:
		print('[OUTPUT_ERROR] date=%s msg=%s' % (current_date, str(e)))


def init(ContextInfo):
	ContextInfo.s = ContextInfo.get_sector('000300.SH')
	ContextInfo.set_universe(ContextInfo.s)
	ContextInfo.day = 0
	ContextInfo.holdings = {i: 0 for i in ContextInfo.s}
	ContextInfo.weight = [0.1] * 10
	ContextInfo.buypoint = {}
	ContextInfo.money = ContextInfo.capital
	ContextInfo.profit = 0
	ContextInfo.accountID = 'testS'
	ContextInfo.fee_rate = 0.0003

	# 结构化输出所需的状态
	ContextInfo.strategy_name = '多因子选股回测示例'
	ContextInfo.trade_log = []
	ContextInfo.rebalance_count = 0
	ContextInfo.start_date = ''
	ContextInfo.end_date = ''
	ContextInfo.latest_price = {}


def handlebar(ContextInfo):
	rank1 = {}
	rank2 = {}
	rank_total = {}
	tmp_stock = []
	d = ContextInfo.barpos
	now_date = _safe_date(ContextInfo, d)
	price = ContextInfo.get_history_data(1, '1d', 'open', 3)
	ContextInfo.latest_price = price

	if not ContextInfo.start_date:
		ContextInfo.start_date = now_date
	ContextInfo.end_date = now_date

	if d > 60 and d % 20 == 0:
		ContextInfo.rebalance_count += 1
		print('[REBALANCE] date=%s index=%s' % (now_date, ContextInfo.rebalance_count))

		buys, sells = signal(ContextInfo)
		order = {}

		for k in list(buys.keys()):
			if buys[k] == 1:
				rank1[k] = ext_data_rank('atr', k[-2:] + k[0:6], 0, ContextInfo)
				rank2[k] = ext_data_rank('adtm', k[-2:] + k[0:6], 0, ContextInfo)
				rank_total[k] = 1.0 * rank1[k]

		tmp = sorted(list(rank_total.items()), key=lambda item: item[1])
		if len(tmp) >= 10:
			tmp_stock = [i[0] for i in tmp[:10]]
		else:
			tmp_stock = [i[0] for i in tmp]

		for k in list(buys.keys()):
			if k not in tmp_stock:
				buys[k] = 0

		if tmp_stock:
			sell_list = [k for k in ContextInfo.s if ContextInfo.holdings[k] > 0 and sells[k] == 1]
			buy_list = [k for k in tmp_stock if ContextInfo.holdings[k] == 0 and buys[k] == 1]

			print('[POOL] date=%s candidates=%s final_buy_pool=%s final_sell_pool=%s' % (
				now_date,
				len(rank_total),
				buy_list,
				sell_list
			))

			for k in sell_list:
				if k in price and len(price[k]) > 0:
					sell_price = _to_float(price[k][-1])
					shares = int(ContextInfo.holdings[k] * 100)
					amount = sell_price * shares
					fee = amount * ContextInfo.fee_rate

					order_shares(k, -shares, 'fix', sell_price, ContextInfo, ContextInfo.accountID)
					ContextInfo.money += amount - fee
					ContextInfo.profit += (sell_price - _to_float(ContextInfo.buypoint.get(k, 0))) * shares - fee
					ContextInfo.holdings[k] = 0

					print('[SELL] date=%s code=%s price=%.4f shares=%s amount=%.2f fee=%.2f cash=%.2f' % (
						now_date, k, sell_price, shares, amount, fee, ContextInfo.money
					))
					_record_trade(ContextInfo, now_date, 'sell', k, sell_price, shares, fee, ContextInfo.rebalance_count, 'rebalance_sell')

			ContextInfo.money_distribution = {k: i * ContextInfo.money for (k, i) in zip(tmp_stock, ContextInfo.weight)}

			for k in buy_list:
				if k in price and len(price[k]) > 0:
					buy_price = _to_float(price[k][-1])
					order[k] = int(ContextInfo.money_distribution[k] / buy_price) / 100
					order[k] = int(order[k])
					shares = int(order[k] * 100)

					if shares <= 0:
						print('[SKIP_BUY] date=%s code=%s reason=shares_is_zero cash=%.2f alloc=%.2f price=%.4f' % (
							now_date, k, ContextInfo.money, ContextInfo.money_distribution[k], buy_price
						))
						continue

					amount = buy_price * shares
					fee = amount * ContextInfo.fee_rate

					order_shares(k, shares, 'fix', buy_price, ContextInfo, ContextInfo.accountID)
					ContextInfo.buypoint[k] = buy_price
					ContextInfo.money -= amount + fee
					ContextInfo.profit -= fee
					ContextInfo.holdings[k] = order[k]

					print('[BUY] date=%s code=%s price=%.4f shares=%s amount=%.2f fee=%.2f cash=%.2f' % (
						now_date, k, buy_price, shares, amount, fee, ContextInfo.money
					))
					_record_trade(ContextInfo, now_date, 'buy', k, buy_price, shares, fee, ContextInfo.rebalance_count, 'rebalance_buy')

			final_market_value = _get_market_value(ContextInfo, price)
			final_total_asset = ContextInfo.money + final_market_value
			print('[SUMMARY] date=%s cash=%.2f market_value=%.2f total_asset=%.2f profit=%.2f holdings=%s' % (
				now_date,
				ContextInfo.money,
				final_market_value,
				final_total_asset,
				final_total_asset - ContextInfo.capital,
				_snapshot_holdings(ContextInfo, price)
			))

			# 调仓完成后立即覆盖写出，保证即使回测中断也有结果
			_flush_outputs(ContextInfo, now_date)

	profit_ratio = 0.0
	final_market_value = _get_market_value(ContextInfo, price)
	final_total_asset = ContextInfo.money + final_market_value
	if _to_float(ContextInfo.capital) != 0:
		profit_ratio = (final_total_asset - ContextInfo.capital) / _to_float(ContextInfo.capital)

	ContextInfo.paint('profit_ratio', profit_ratio, -1, 0)

	# 如果 QMT 支持最后一根 bar 判断，这里再写一次最终结果
	try:
		if ContextInfo.is_last_bar():
			_flush_outputs(ContextInfo, now_date)
	except Exception:
		pass


def signal(ContextInfo):
	buy = {i: 0 for i in ContextInfo.s}
	sell = {i: 0 for i in ContextInfo.s}
	data_high = ContextInfo.get_history_data(22, '1d', 'high', 3)
	data_high_pre = ContextInfo.get_history_data(2, '1d', 'high', 3)
	data_close60 = ContextInfo.get_history_data(62, '1d', 'close', 3)

	for k in ContextInfo.s:
		if k in data_close60:
			if len(data_high_pre[k]) == 2 and len(data_high[k]) == 22 and len(data_close60[k]) == 62:
				if data_high_pre[k][-2] > max(data_high[k][:-2]):
					buy[k] = 1
				elif data_high_pre[k][-2] < np.mean(data_close60[k][:-2]):
					sell[k] = 1
	return buy, sell
