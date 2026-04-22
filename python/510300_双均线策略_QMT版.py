#coding:gbk
"""
======================================================================
510300 沪深300ETF 双均线策略（QMT 专用版）
======================================================================

【文件说明】
本文件是专门为 QMT 平台编写的策略文件，用于验证与独立回测结果的一致性。

【重要：关于复权】
⚠️ 复权方式会影响回测结果！

- 本策略使用 np.mean() 手动计算均线，不依赖 QMT 的内置指标
- 但数据的复权方式仍然非常重要！
- QMT 默认可能使用不复权数据，分红会导致价格跳空
- 建议在 QMT 中确认数据复权设置

【与 xtquant 独立回测的对比】
| 项目 | xtquant 独立回测 | QMT 版本 |
|------|-----------------|----------|
| 复权方式 | 前复权 (dividend_type='front') | ⚠️ 需确认 QMT 设置 |
| 数据获取 | get_market_data_ex() | get_history_data() |
| 均线计算 | df.rolling().mean() | np.mean() |
| 计算逻辑 | 完全一致 | 完全一致 |

【策略逻辑】（与独立回测脚本完全一致）
- 标的：510300.SH（沪深300ETF）
- 买入条件：昨日收盘价 > MA20 且 MA20 > MA60
- 卖出条件：昨日收盘价 < MA20 或 MA20 < MA60
- 资金管理：使用 95% 可用现金买入
- 手续费率：0.03%（万分之三）
- 成交价格：当日开盘价

【关于指标函数的重要说明】
⚠️ QMT 和 xtquant 都没有内置的 MA()、EMA()、MACD() 等函数！

本策略的实现方式（两边都能用）：
1. 使用 get_history_data() 获取历史收盘价列表
2. 使用 np.mean(close_list[-21:-1]) 手动计算 MA20
3. 使用 np.mean(close_list[-61:-1]) 手动计算 MA60

这种方式的优点：
- 不依赖平台特有函数
- 计算逻辑完全透明
- 容易在 QMT 和 xtquant 之间保持一致

【回测参数】
- 回测区间：2025-04-22 至 2026-04-22（过去一年）
- 初始资金：1,000,000 元
- 交易周期：日线

【文件位置】
- 工作区路径：d:\codex_n_workspace\gd_qmt_cx_trae_cn\python\510300_双均线策略_QMT版.py
- QMT 示例目录：D:\光大证券金阳光QMT实盘\python\510300_双均线策略_QMT版.py

【输出文件】
- 交易记录：outputs/qmt_trade_log.json
- 回测结果：outputs/qmt_result.json
- 详细指标：outputs/qmt_metrics.json

【对比参考】
- 独立回测脚本：d:\codex_n_workspace\gd_qmt_cx_trae_cn\code\run_xtquant\backtest_510300.py
- 独立回测结果：d:\codex_n_workspace\gd_qmt_cx_trae_cn\code\run_xtquant\outputs\

【使用方法】
1. 将此文件复制到 QMT 的 python 目录
2. 在 QMT 中打开策略研究
3. 新建策略，导入此文件
4. 设置回测参数：
   - 标的：510300.SH
   - 开始日期：2025-04-22
   - 结束日期：2026-04-22
   - 初始资金：1,000,000
   - 周期：日线
5. ⚠️ 确认 QMT 的数据复权设置（建议前复权）
6. 运行回测
7. 查看输出的 JSON 文件，与独立回测结果对比

======================================================================
"""

import os
import json
import numpy as np
from datetime import datetime


def _get_output_dir():
    """获取输出目录"""
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base_dir = os.getcwd()
    output_dir = os.path.join(base_dir, 'outputs')
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    return output_dir


def _to_float(value):
    """安全转换为浮点数"""
    try:
        return float(value)
    except Exception:
        return 0.0


def _safe_date(ContextInfo, d):
    """安全获取日期字符串"""
    try:
        return timetag_to_datetime(ContextInfo.get_bar_timetag(d), '%Y%m%d')
    except Exception:
        return ''


def _snapshot_holding(ContextInfo, market_price):
    """获取持仓快照"""
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
    """记录交易"""
    record = {
        'date': date_str,
        'action': action,
        'code': ContextInfo.stock,
        'price': _to_float(price),
        'shares': int(shares),
        'amount': round(_to_float(price) * int(shares), 2),
        'fee': round(_to_float(fee), 4),
        'cash_after': round(_to_float(ContextInfo.cash), 2),
        'holdings_after': _snapshot_holding(ContextInfo, ContextInfo.last_price),
        'note': note
    }
    ContextInfo.trade_log.append(record)
    return record


def _calculate_metrics(ContextInfo):
    """计算详细指标（用于与独立回测对比）"""
    metrics = {
        'strategy_name': '沪深300ETF双均线策略(QMT版)',
        'stock_code': ContextInfo.stock,
        'start_date': ContextInfo.start_date,
        'end_date': ContextInfo.end_date,
        'initial_capital': _to_float(ContextInfo.capital),
        'final_capital': _to_float(ContextInfo.cash),
        'total_profit': 0,
        'total_return': 0,
        'total_return_pct': 0,
        
        'win_trades': 0,
        'lose_trades': 0,
        'total_trades': 0,
        'win_rate': 0,
        'win_rate_pct': 0,
        
        'max_drawdown': 0,
        'max_drawdown_pct': 0,
        'max_drawdown_date': None,
        
        'total_hold_days': 0,
        'hold_period_count': 0,
        'avg_hold_days': 0,
        
        'max_consecutive_losses': 0,
        'consecutive_losses_current': 0,
        
        'params': {
            'ma_short': 20,
            'ma_long': 60,
            'fee_rate': 0.0003,
            'buy_cash_ratio': 0.95,
            'lot_size': 100
        },
        
        'trades_summary': []
    }
    
    buy_trades = [t for t in ContextInfo.trade_log if t['action'] == 'buy']
    sell_trades = [t for t in ContextInfo.trade_log if t['action'] == 'sell']
    
    metrics['total_trades'] = min(len(buy_trades), len(sell_trades))
    
    equity_peak = _to_float(ContextInfo.capital)
    max_drawdown = 0
    max_drawdown_date = None
    
    consecutive_losses = 0
    max_consecutive_losses = 0
    
    total_hold_days = 0
    hold_period_count = 0
    
    for i in range(metrics['total_trades']):
        buy = buy_trades[i]
        sell = sell_trades[i]
        
        profit = (sell['price'] - buy['price']) * buy['shares'] - buy['fee'] - sell['fee']
        profit_ratio = (sell['price'] - buy['price']) / buy['price'] if buy['price'] > 0 else 0
        
        try:
            buy_date = datetime.strptime(buy['date'], '%Y%m%d')
            sell_date = datetime.strptime(sell['date'], '%Y%m%d')
            hold_days = (sell_date - buy_date).days
        except:
            hold_days = 0
        
        trade_summary = {
            'trade_index': i + 1,
            'buy_date': buy['date'],
            'buy_price': buy['price'],
            'buy_shares': buy['shares'],
            'sell_date': sell['date'],
            'sell_price': sell['price'],
            'sell_shares': sell['shares'],
            'profit': round(profit, 2),
            'profit_ratio': round(profit_ratio * 100, 2),
            'hold_days': hold_days,
            'result': 'win' if profit > 0 else 'lose'
        }
        
        metrics['trades_summary'].append(trade_summary)
        
        if profit > 0:
            metrics['win_trades'] += 1
            consecutive_losses = 0
        else:
            metrics['lose_trades'] += 1
            consecutive_losses += 1
            if consecutive_losses > max_consecutive_losses:
                max_consecutive_losses = consecutive_losses
        
        if hold_days > 0:
            total_hold_days += hold_days
            hold_period_count += 1
    
    if metrics['total_trades'] > 0:
        metrics['win_rate'] = metrics['win_trades'] / metrics['total_trades']
        metrics['win_rate_pct'] = metrics['win_rate'] * 100
    
    metrics['max_consecutive_losses'] = max_consecutive_losses
    metrics['consecutive_losses_current'] = consecutive_losses
    
    metrics['total_hold_days'] = total_hold_days
    metrics['hold_period_count'] = hold_period_count
    if hold_period_count > 0:
        metrics['avg_hold_days'] = total_hold_days / hold_period_count
    
    if ContextInfo.daily_equity:
        for date, equity in ContextInfo.daily_equity.items():
            if equity > equity_peak:
                equity_peak = equity
            drawdown = (equity_peak - equity) / equity_peak if equity_peak > 0 else 0
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_drawdown_date = date
    
    metrics['max_drawdown'] = max_drawdown
    metrics['max_drawdown_pct'] = max_drawdown * 100
    metrics['max_drawdown_date'] = max_drawdown_date
    
    final_capital = _to_float(ContextInfo.cash)
    if ContextInfo.holding > 0 and ContextInfo.last_price > 0:
        final_capital += ContextInfo.holding * 100 * ContextInfo.last_price
    
    metrics['final_capital'] = final_capital
    metrics['total_profit'] = final_capital - _to_float(ContextInfo.capital)
    metrics['total_return'] = metrics['total_profit'] / _to_float(ContextInfo.capital) if _to_float(ContextInfo.capital) > 0 else 0
    metrics['total_return_pct'] = metrics['total_return'] * 100
    
    return metrics


def _build_result(ContextInfo, current_date):
    """构建结果对象"""
    final_market_value = 0.0
    if ContextInfo.holding > 0 and ContextInfo.last_price > 0:
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
        'initial_capital': round(_to_float(ContextInfo.capital), 2),
        'final_cash': round(_to_float(ContextInfo.cash), 2),
        'final_market_value': round(final_market_value, 2),
        'final_total_asset': round(final_total_asset, 2),
        'final_profit': round(final_profit, 2),
        'profit_ratio': round(profit_ratio, 6),
        'profit_ratio_pct': round(profit_ratio * 100, 2),
        'current_holdings': _snapshot_holding(ContextInfo, ContextInfo.last_price),
        'rebalance_count': int(ContextInfo.rebalance_count),
        'buy_count': int(buy_count),
        'sell_count': int(sell_count),
        'universe_size': 1,
        'params': {
            'stock': ContextInfo.stock,
            'buy_rule': 'prev_close > ma20 and ma20 > ma60',
            'sell_rule': 'prev_close < ma20 or ma20 < ma60',
            'buy_cash_ratio': 0.95,
            'fee_rate': _to_float(ContextInfo.fee_rate)
        },
        'notes': 'QMT 版本，用于与独立回测结果对比'
    }


def _write_json_files(ContextInfo, current_date):
    """写入 JSON 文件"""
    output_dir = _get_output_dir()
    
    result_path = os.path.join(output_dir, 'qmt_result.json')
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(_build_result(ContextInfo, current_date), f, ensure_ascii=False, indent=2)
    
    trade_log_path = os.path.join(output_dir, 'qmt_trade_log.json')
    with open(trade_log_path, 'w', encoding='utf-8') as f:
        json.dump(ContextInfo.trade_log, f, ensure_ascii=False, indent=2)
    
    try:
        metrics = _calculate_metrics(ContextInfo)
        metrics_path = os.path.join(output_dir, 'qmt_metrics.json')
        with open(metrics_path, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print('[METRICS_ERROR] msg=%s' % str(e))


def _flush_outputs(ContextInfo, current_date):
    """刷新输出"""
    try:
        _write_json_files(ContextInfo, current_date)
    except Exception as e:
        print('[OUTPUT_ERROR] date=%s msg=%s' % (current_date, str(e)))


def init(ContextInfo):
    """初始化函数"""
    ContextInfo.stock = '510300.SH'
    ContextInfo.set_universe([ContextInfo.stock])
    ContextInfo.accountID = 'testS'

    ContextInfo.holding = 0
    ContextInfo.buy_price = 0
    ContextInfo.cash = ContextInfo.capital
    ContextInfo.profit = 0
    ContextInfo.fee_rate = 0.0003

    ContextInfo.strategy_name = '510300双均线策略(QMT版)'
    ContextInfo.trade_log = []
    ContextInfo.rebalance_count = 0
    ContextInfo.start_date = ''
    ContextInfo.end_date = ''
    ContextInfo.last_price = 0.0
    
    ContextInfo.daily_equity = {}
    ContextInfo.equity_peak = _to_float(ContextInfo.capital)
    ContextInfo.max_drawdown = 0
    
    print('=' * 70)
    print('策略初始化完成')
    print('=' * 70)
    print('策略名称:', ContextInfo.strategy_name)
    print('交易标的:', ContextInfo.stock)
    print('初始资金:', ContextInfo.capital)
    print('手续费率:', ContextInfo.fee_rate)
    print('买入规则: prev_close > MA20 and MA20 > MA60')
    print('卖出规则: prev_close < MA20 or MA20 < MA60')
    print('=' * 70)


def handlebar(ContextInfo):
    """主处理函数"""
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

    prev_close = _to_float(close_list[-2])
    ma20 = _to_float(np.mean(close_list[-21:-1]))
    ma60 = _to_float(np.mean(close_list[-61:-1]))

    buy_signal = 0
    sell_signal = 0

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

    if now_date:
        ContextInfo.daily_equity[now_date] = final_total_asset

    ContextInfo.paint('profit_ratio', profit_ratio, -1, 0)
    ContextInfo.paint('buy_signal', buy_signal, -1, 0)
    ContextInfo.paint('sell_signal', sell_signal, -1, 0)

    try:
        if ContextInfo.is_last_bar():
            print('=' * 70)
            print('回测结束')
            print('=' * 70)
            _flush_outputs(ContextInfo, now_date)
            print('输出文件已保存到 outputs 目录')
            print('=' * 70)
    except Exception:
        pass
