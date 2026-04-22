#!/usr/bin/env python3
# coding: utf-8
"""
510300 沪深300ETF 回测脚本
基于 xtquant 实现，独立于 QMT 策略框架

策略逻辑（沿用现有策略）：
- 买入条件：昨日收盘价 > MA20 且 MA20 > MA60
- 卖出条件：昨日收盘价 < MA20 或 MA20 < MA60
- 资金管理：使用95%现金买入
- 手续费率：万分之三（0.0003）

回测参数：
- 标的：510300.SH（沪深300ETF）
- 回测时间：2025-04-22 至 2026-04-22（过去一年）
- 初始资金：1,000,000 元
- 交易周期：日线
- 成交价格：当日开盘价
"""

import os
import sys
import json
import math
from datetime import datetime, timedelta
from collections import OrderedDict

import pandas as pd
import numpy as np

try:
    from xtquant import xtdata
    print("✅ xtquant 库加载成功")
except ImportError as e:
    print(f"❌ 未找到 xtquant 库: {e}")
    sys.exit(1)


class BacktestConfig:
    """回测配置"""
    STOCK_CODE = '510300.SH'
    INITIAL_CAPITAL = 1000000.0
    FEE_RATE = 0.0003
    BUY_CASH_RATIO = 0.95
    MA_SHORT = 20
    MA_LONG = 60
    
    START_DATE = '20250422'
    END_DATE = '20260422'
    
    LOT_SIZE = 100


class Trade:
    """交易记录"""
    def __init__(self, date, action, price, shares, fee, note=''):
        self.date = date
        self.action = action
        self.price = price
        self.shares = shares
        self.fee = fee
        self.amount = price * shares
        self.note = note
        
    def to_dict(self):
        return {
            'date': self.date,
            'action': self.action,
            'code': BacktestConfig.STOCK_CODE,
            'price': round(self.price, 4),
            'shares': self.shares,
            'amount': round(self.amount, 2),
            'fee': round(self.fee, 4),
            'note': self.note
        }


class Position:
    """持仓记录"""
    def __init__(self):
        self.shares = 0
        self.avg_cost = 0.0
        self.buy_date = None
        
    def is_holding(self):
        return self.shares > 0


class BacktestEngine:
    """回测引擎"""
    
    def __init__(self):
        self.config = BacktestConfig()
        self.position = Position()
        self.cash = self.config.INITIAL_CAPITAL
        self.trades = []
        self.daily_equity = OrderedDict()
        self.daily_positions = OrderedDict()
        
        self.equity_peak = self.config.INITIAL_CAPITAL
        self.max_drawdown = 0.0
        self.max_drawdown_date = None
        
        self.win_trades = 0
        self.lose_trades = 0
        self.total_trades = 0
        
        self.consecutive_losses = 0
        self.max_consecutive_losses = 0
        
        self.total_hold_days = 0
        self.hold_period_count = 0
        
        self.last_buy_price = 0.0
        self.last_buy_date = None
        
    def download_data(self):
        """下载历史数据"""
        print(f"\n📥 正在下载 {self.config.STOCK_CODE} 历史数据...")
        print(f"   时间范围: {self.config.START_DATE} 至 {self.config.END_DATE}")
        
        result = xtdata.download_history_data(
            self.config.STOCK_CODE,
            period='1d',
            start_time=self.config.START_DATE,
            end_time=self.config.END_DATE
        )
        
        if result is None:
            print("✅ 数据下载请求已提交")
        else:
            print(f"⚠️ 下载结果: {result}")
        
        return True
    
    def get_history_data(self):
        """获取历史行情数据"""
        print(f"\n📊 正在获取历史行情数据...")
        print(f"   复权方式: 前复权 (dividend_type='front')")
        
        data = xtdata.get_market_data_ex(
            field_list=[],
            stock_list=[self.config.STOCK_CODE],
            period='1d',
            start_time='',
            end_time='',
            count=-1,
            dividend_type='front',
            fill_data=True
        )
        
        if self.config.STOCK_CODE in data:
            df = data[self.config.STOCK_CODE]
            print(f"✅ 成功获取 {len(df)} 条日线数据（前复权）")
            return df
        else:
            print(f"❌ 未获取到 {self.config.STOCK_CODE} 的数据")
            return None
    
    def calculate_ma(self, df, window):
        """计算移动平均线"""
        return df['close'].rolling(window=window).mean()
    
    def prepare_features(self, df):
        """准备特征数据"""
        print(f"\n🔧 正在计算技术指标...")
        
        df = df.copy()
        
        df['ma20'] = self.calculate_ma(df, self.config.MA_SHORT)
        df['ma60'] = self.calculate_ma(df, self.config.MA_LONG)
        
        df['prev_close'] = df['close'].shift(1)
        df['prev_ma20'] = df['ma20'].shift(1)
        df['prev_ma60'] = df['ma60'].shift(1)
        
        df = df.dropna()
        
        print(f"✅ 技术指标计算完成，有效数据: {len(df)} 条")
        return df
    
    def run_backtest(self, df):
        """执行回测"""
        print(f"\n🚀 开始执行回测...")
        print(f"   初始资金: {self.config.INITIAL_CAPITAL:,.2f} 元")
        print(f"   手续费率: {self.config.FEE_RATE * 100:.2f}%")
        print(f"   MA参数: MA{self.config.MA_SHORT} / MA{self.config.MA_LONG}")
        print("-" * 70)
        
        for i in range(len(df)):
            current_date = df.index[i]
            current_data = df.iloc[i]
            
            open_price = current_data['open']
            high_price = current_data['high']
            low_price = current_data['low']
            close_price = current_data['close']
            
            prev_close = current_data['prev_close']
            prev_ma20 = current_data['prev_ma20']
            prev_ma60 = current_data['prev_ma60']
            
            current_ma20 = current_data['ma20']
            current_ma60 = current_data['ma60']
            
            buy_signal = False
            sell_signal = False
            
            if not self.position.is_holding():
                if prev_close > prev_ma20 and prev_ma20 > prev_ma60:
                    buy_signal = True
            else:
                if prev_close < prev_ma20 or prev_ma20 < prev_ma60:
                    sell_signal = True
            
            if buy_signal:
                self._execute_buy(current_date, open_price)
            
            if sell_signal:
                self._execute_sell(current_date, open_price)
            
            self._update_daily_equity(current_date, close_price)
        
        if self.position.is_holding():
            last_date = df.index[-1]
            last_close = df.iloc[-1]['close']
            print(f"\n📝 回测结束时仍有持仓，按最后收盘价清仓计算")
            self._execute_sell(last_date, last_close, force_close=True)
        
        print("-" * 70)
        print("✅ 回测执行完成")
    
    def _execute_buy(self, date, price):
        """执行买入"""
        if self.position.is_holding():
            return
        
        available_cash = self.cash * self.config.BUY_CASH_RATIO
        max_shares = int(available_cash / price / self.config.LOT_SIZE) * self.config.LOT_SIZE
        
        if max_shares <= 0:
            print(f"[警告] {date} 资金不足，无法买入")
            return
        
        trade_amount = max_shares * price
        fee = trade_amount * self.config.FEE_RATE
        total_cost = trade_amount + fee
        
        if total_cost > self.cash:
            max_shares = max_shares - self.config.LOT_SIZE
            if max_shares <= 0:
                return
            trade_amount = max_shares * price
            fee = trade_amount * self.config.FEE_RATE
            total_cost = trade_amount + fee
        
        self.position.shares = max_shares
        self.position.avg_cost = price
        self.position.buy_date = date
        
        self.cash -= total_cost
        self.last_buy_price = price
        self.last_buy_date = date
        
        trade = Trade(date, 'buy', price, max_shares, fee, 'trend_follow_buy')
        self.trades.append(trade)
        
        print(f"[买入] {date} 价格:{price:.4f} 数量:{max_shares} 金额:{trade_amount:.2f} 手续费:{fee:.4f}")
    
    def _date_to_datetime(self, date_str):
        """将日期字符串转换为datetime对象"""
        if isinstance(date_str, str):
            if len(date_str) == 8:
                return datetime.strptime(date_str, '%Y%m%d')
            elif '-' in date_str:
                return datetime.strptime(date_str, '%Y-%m-%d')
        return date_str
    
    def _calculate_hold_days(self, buy_date, sell_date):
        """计算持仓天数"""
        if buy_date is None or sell_date is None:
            return 0
        
        buy_dt = self._date_to_datetime(buy_date)
        sell_dt = self._date_to_datetime(sell_date)
        
        if isinstance(buy_dt, datetime) and isinstance(sell_dt, datetime):
            return (sell_dt - buy_dt).days
        return 0
    
    def _execute_sell(self, date, price, force_close=False):
        """执行卖出"""
        if not self.position.is_holding():
            return
        
        shares = self.position.shares
        trade_amount = shares * price
        fee = trade_amount * self.config.FEE_RATE
        total_receive = trade_amount - fee
        
        buy_price = self.last_buy_price
        profit = (price - buy_price) * shares - fee
        profit_ratio = (price - buy_price) / buy_price if buy_price > 0 else 0
        
        self.total_trades += 1
        if profit > 0:
            self.win_trades += 1
            self.consecutive_losses = 0
        else:
            self.lose_trades += 1
            self.consecutive_losses += 1
            if self.consecutive_losses > self.max_consecutive_losses:
                self.max_consecutive_losses = self.consecutive_losses
        
        if self.last_buy_date and self.last_buy_date != date:
            hold_days = self._calculate_hold_days(self.last_buy_date, date)
            if hold_days > 0:
                self.total_hold_days += hold_days
                self.hold_period_count += 1
        
        note = 'force_close' if force_close else 'trend_follow_sell'
        trade = Trade(date, 'sell', price, shares, fee, note)
        self.trades.append(trade)
        
        self.cash += total_receive
        self.position.shares = 0
        self.position.avg_cost = 0
        self.position.buy_date = None
        
        profit_status = "盈利" if profit > 0 else "亏损"
        print(f"[卖出] {date} 价格:{price:.4f} 数量:{shares} 金额:{trade_amount:.2f} 手续费:{fee:.4f}")
        print(f"       盈亏:{profit:,.2f} ({profit_status}) 收益率:{profit_ratio*100:.2f}%")
    
    def _update_daily_equity(self, date, close_price):
        """更新每日权益"""
        position_value = self.position.shares * close_price
        total_equity = self.cash + position_value
        
        self.daily_equity[date] = total_equity
        self.daily_positions[date] = {
            'shares': self.position.shares,
            'position_value': position_value,
            'cash': self.cash,
            'total_equity': total_equity
        }
        
        if total_equity > self.equity_peak:
            self.equity_peak = total_equity
        
        drawdown = (self.equity_peak - total_equity) / self.equity_peak
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown
            self.max_drawdown_date = date
    
    def calculate_metrics(self):
        """计算回测指标"""
        print(f"\n📈 正在计算回测指标...")
        
        metrics = {}
        
        if len(self.daily_equity) == 0:
            return metrics
        
        final_equity = list(self.daily_equity.values())[-1]
        initial_equity = self.config.INITIAL_CAPITAL
        
        total_return = (final_equity - initial_equity) / initial_equity
        
        if self.total_trades > 0:
            win_rate = self.win_trades / self.total_trades if self.total_trades > 0 else 0
        else:
            win_rate = 0
        
        avg_hold_days = 0
        if self.hold_period_count > 0:
            avg_hold_days = self.total_hold_days / self.hold_period_count
        
        equity_values = np.array(list(self.daily_equity.values()))
        running_max = np.maximum.accumulate(equity_values)
        drawdowns = (running_max - equity_values) / running_max
        max_drawdown = np.max(drawdowns)
        
        metrics = {
            'strategy_name': '沪深300ETF双均线策略',
            'stock_code': self.config.STOCK_CODE,
            'start_date': self.config.START_DATE,
            'end_date': self.config.END_DATE,
            'initial_capital': initial_equity,
            'final_capital': final_equity,
            'total_profit': final_equity - initial_equity,
            'total_return': total_return,
            'total_return_pct': total_return * 100,
            
            'win_trades': self.win_trades,
            'lose_trades': self.lose_trades,
            'total_trades': self.total_trades,
            'win_rate': win_rate,
            'win_rate_pct': win_rate * 100,
            
            'max_drawdown': max_drawdown,
            'max_drawdown_pct': max_drawdown * 100,
            'max_drawdown_date': str(self.max_drawdown_date) if self.max_drawdown_date else None,
            
            'total_hold_days': self.total_hold_days,
            'hold_period_count': self.hold_period_count,
            'avg_hold_days': avg_hold_days,
            
            'max_consecutive_losses': self.max_consecutive_losses,
            'consecutive_losses_current': self.consecutive_losses,
            
            'params': {
                'ma_short': self.config.MA_SHORT,
                'ma_long': self.config.MA_LONG,
                'fee_rate': self.config.FEE_RATE,
                'buy_cash_ratio': self.config.BUY_CASH_RATIO,
                'lot_size': self.config.LOT_SIZE
            }
        }
        
        print("✅ 指标计算完成")
        return metrics
    
    def generate_report(self, metrics):
        """生成分析报告"""
        print(f"\n{'='*70}")
        print(f"📊 510300 沪深300ETF 回测分析报告")
        print(f"{'='*70}")
        
        print(f"\n【一、回测基本信息】")
        print(f"  策略名称: {metrics['strategy_name']}")
        print(f"  交易标的: {metrics['stock_code']}")
        print(f"  回测区间: {metrics['start_date']} 至 {metrics['end_date']}")
        print(f"  初始资金: {metrics['initial_capital']:,.2f} 元")
        print(f"  最终资金: {metrics['final_capital']:,.2f} 元")
        print(f"  总盈亏: {metrics['total_profit']:,.2f} 元")
        print(f"  总收益率: {metrics['total_return_pct']:.2f}%")
        
        print(f"\n【二、关键绩效指标（重点关注）】")
        print(f"\n  1. 胜率")
        print(f"     - 总交易次数: {metrics['total_trades']} 次")
        print(f"     - 盈利次数: {metrics['win_trades']} 次")
        print(f"     - 亏损次数: {metrics['lose_trades']} 次")
        print(f"     - 胜率: {metrics['win_rate_pct']:.2f}%")
        
        print(f"\n  2. 最大回撤")
        print(f"     - 最大回撤率: {metrics['max_drawdown_pct']:.2f}%")
        if metrics['max_drawdown_date']:
            print(f"     - 最大回撤发生日期: {metrics['max_drawdown_date']}")
        
        print(f"\n  3. 平均持有天数")
        print(f"     - 总持仓天数: {metrics['total_hold_days']} 天")
        print(f"     - 持仓周期数: {metrics['hold_period_count']} 次")
        if metrics['hold_period_count'] > 0:
            print(f"     - 平均持有天数: {metrics['avg_hold_days']:.1f} 天")
        else:
            print(f"     - 平均持有天数: 无完整持仓周期")
        
        print(f"\n  4. 连续亏损次数")
        print(f"     - 最大连续亏损次数: {metrics['max_consecutive_losses']} 次")
        print(f"     - 当前连续亏损次数: {metrics['consecutive_losses_current']} 次")
        
        print(f"\n【三、策略参数】")
        print(f"  短期均线 (MA{metrics['params']['ma_short']})")
        print(f"  长期均线 (MA{metrics['params']['ma_long']})")
        print(f"  手续费率: {metrics['params']['fee_rate'] * 100:.2f}%")
        print(f"  买入资金比例: {metrics['params']['buy_cash_ratio'] * 100:.0f}%")
        
        print(f"\n【四、交易记录摘要】")
        if self.trades:
            for i, trade in enumerate(self.trades):
                action_icon = "🔴" if trade.action == 'buy' else "🟢"
                print(f"  {action_icon} [{i+1}] {trade.date} {trade.action.upper():4s} "
                      f"价格:{trade.price:.4f} 数量:{trade.shares:5d} 金额:{trade.amount:10,.2f}元")
        else:
            print(f"  无交易记录")
        
        print(f"\n{'='*70}")
        
        return metrics
    
    def save_results(self, metrics):
        """保存结果到文件"""
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        trades_dict = [trade.to_dict() for trade in self.trades]
        trades_path = os.path.join(output_dir, f'backtest_trades_{timestamp}.json')
        with open(trades_path, 'w', encoding='utf-8') as f:
            json.dump(trades_dict, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n💾 交易记录已保存: {trades_path}")
        
        equity_dict = {str(k): v for k, v in self.daily_equity.items()}
        equity_path = os.path.join(output_dir, f'backtest_equity_{timestamp}.json')
        with open(equity_path, 'w', encoding='utf-8') as f:
            json.dump(equity_dict, f, ensure_ascii=False, indent=2)
        print(f"💾 权益曲线已保存: {equity_path}")
        
        metrics_path = os.path.join(output_dir, f'backtest_metrics_{timestamp}.json')
        with open(metrics_path, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2, default=str)
        print(f"💾 回测指标已保存: {metrics_path}")
        
        report_path = os.path.join(output_dir, f'backtest_report_{timestamp}.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"{'='*70}\n")
            f.write(f"📊 510300 沪深300ETF 回测分析报告\n")
            f.write(f"{'='*70}\n")
            f.write(f"\n【一、回测基本信息】\n")
            f.write(f"  策略名称: {metrics['strategy_name']}\n")
            f.write(f"  交易标的: {metrics['stock_code']}\n")
            f.write(f"  回测区间: {metrics['start_date']} 至 {metrics['end_date']}\n")
            f.write(f"  初始资金: {metrics['initial_capital']:,.2f} 元\n")
            f.write(f"  最终资金: {metrics['final_capital']:,.2f} 元\n")
            f.write(f"  总盈亏: {metrics['total_profit']:,.2f} 元\n")
            f.write(f"  总收益率: {metrics['total_return_pct']:.2f}%\n")
            
            f.write(f"\n【二、关键绩效指标】\n")
            f.write(f"\n  1. 胜率\n")
            f.write(f"     - 总交易次数: {metrics['total_trades']} 次\n")
            f.write(f"     - 盈利次数: {metrics['win_trades']} 次\n")
            f.write(f"     - 亏损次数: {metrics['lose_trades']} 次\n")
            f.write(f"     - 胜率: {metrics['win_rate_pct']:.2f}%\n")
            
            f.write(f"\n  2. 最大回撤\n")
            f.write(f"     - 最大回撤率: {metrics['max_drawdown_pct']:.2f}%\n")
            if metrics['max_drawdown_date']:
                f.write(f"     - 最大回撤发生日期: {metrics['max_drawdown_date']}\n")
            
            f.write(f"\n  3. 平均持有天数\n")
            f.write(f"     - 总持仓天数: {metrics['total_hold_days']} 天\n")
            f.write(f"     - 持仓周期数: {metrics['hold_period_count']} 次\n")
            if metrics['hold_period_count'] > 0:
                f.write(f"     - 平均持有天数: {metrics['avg_hold_days']:.1f} 天\n")
            
            f.write(f"\n  4. 连续亏损次数\n")
            f.write(f"     - 最大连续亏损次数: {metrics['max_consecutive_losses']} 次\n")
            
            f.write(f"\n【三、决策建议】\n")
            f.write(self._generate_decision_advice(metrics))
        
        print(f"💾 分析报告已保存: {report_path}")
        
        return {
            'trades_path': trades_path,
            'equity_path': equity_path,
            'metrics_path': metrics_path,
            'report_path': report_path
        }
    
    def _generate_decision_advice(self, metrics):
        """生成决策建议"""
        advice = []
        
        win_rate = metrics['win_rate_pct']
        max_drawdown = metrics['max_drawdown_pct']
        total_return = metrics['total_return_pct']
        avg_hold_days = metrics['avg_hold_days']
        max_consecutive_losses = metrics['max_consecutive_losses']
        
        advice.append(f"\n  综合评估:\n")
        advice.append(f"  {'-'*50}\n")
        
        if total_return > 0:
            advice.append(f"  ✅ 策略在回测期间获得正收益: {total_return:.2f}%\n")
        else:
            advice.append(f"  ❌ 策略在回测期间亏损: {total_return:.2f}%\n")
        
        if win_rate >= 50:
            advice.append(f"  ✅ 胜率良好: {win_rate:.2f}% (>=50%)\n")
        elif win_rate >= 40:
            advice.append(f"  ⚠️ 胜率一般: {win_rate:.2f}% (40%-50%)\n")
        else:
            advice.append(f"  ❌ 胜率较低: {win_rate:.2f}% (<40%)\n")
        
        if max_drawdown <= 10:
            advice.append(f"  ✅ 最大回撤控制良好: {max_drawdown:.2f}% (<=10%)\n")
        elif max_drawdown <= 20:
            advice.append(f"  ⚠️ 最大回撤较大: {max_drawdown:.2f}% (10%-20%)\n")
        else:
            advice.append(f"  ❌ 最大回撤风险高: {max_drawdown:.2f}% (>20%)\n")
        
        if max_consecutive_losses <= 2:
            advice.append(f"  ✅ 最大连续亏损次数可控: {max_consecutive_losses} 次 (<=2)\n")
        elif max_consecutive_losses <= 4:
            advice.append(f"  ⚠️ 最大连续亏损次数较多: {max_consecutive_losses} 次 (3-4)\n")
        else:
            advice.append(f"  ❌ 最大连续亏损次数风险高: {max_consecutive_losses} 次 (>4)\n")
        
        advice.append(f"\n  决策建议:\n")
        advice.append(f"  {'-'*50}\n")
        
        score = 0
        if total_return > 0:
            score += 2
        if win_rate >= 45:
            score += 2
        if max_drawdown <= 15:
            score += 2
        if max_consecutive_losses <= 3:
            score += 2
        
        if score >= 6:
            advice.append(f"  【建议】可以考虑实盘试运行，但需注意以下几点：\n")
            advice.append(f"  1. 先用小资金测试（建议初始资金的10%-20%）\n")
            advice.append(f"  2. 设置严格的止损线（建议单笔亏损不超过5%）\n")
            advice.append(f"  3. 连续亏损3次后暂停交易，重新评估市场环境\n")
            advice.append(f"  4. 密切关注最大回撤，超过15%时考虑减仓或暂停\n")
        elif score >= 4:
            advice.append(f"  【建议】策略表现中等，建议进一步优化：\n")
            advice.append(f"  1. 考虑调整均线参数，寻找更优组合\n")
            advice.append(f"  2. 增加过滤条件，减少震荡市中的假信号\n")
            advice.append(f"  3. 优化止损止盈机制\n")
            advice.append(f"  4. 不建议直接实盘，建议继续观察\n")
        else:
            advice.append(f"  【建议】策略表现较差，不建议实盘：\n")
            advice.append(f"  1. 回测期间表现不佳，需要重新评估策略逻辑\n")
            advice.append(f"  2. 考虑更换策略或调整核心参数\n")
            advice.append(f"  3. 建议增加更多过滤条件\n")
            advice.append(f"  4. 可考虑结合其他指标（如成交量、MACD等）\n")
        
        advice.append(f"\n  风险提示:\n")
        advice.append(f"  {'-'*50}\n")
        advice.append(f"  - 回测结果不代表未来表现，市场环境可能发生变化\n")
        advice.append(f"  - 历史数据可能存在过拟合风险\n")
        advice.append(f"  - 实盘交易中可能存在滑点、冲击成本等未考虑因素\n")
        advice.append(f"  - 建议在实盘前进行充分的模拟交易验证\n")
        
        return ''.join(advice)


def main():
    """主函数"""
    print("=" * 70)
    print("📊 510300 沪深300ETF 回测程序启动")
    print("=" * 70)
    print(f"\n当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python 版本: {sys.version}")
    
    engine = BacktestEngine()
    
    engine.download_data()
    
    df = engine.get_history_data()
    if df is None or len(df) == 0:
        print("❌ 无法获取历史数据，回测终止")
        return
    
    df = engine.prepare_features(df)
    if len(df) == 0:
        print("❌ 有效数据不足，无法进行回测")
        return
    
    engine.run_backtest(df)
    
    metrics = engine.calculate_metrics()
    
    engine.generate_report(metrics)
    
    saved_files = engine.save_results(metrics)
    
    print(f"\n{'='*70}")
    print("✅ 回测完成！")
    print(f"{'='*70}")
    print(f"\n生成的文件:")
    for name, path in saved_files.items():
        print(f"  - {path}")
    
    print(f"\n【最终决策】")
    print(engine._generate_decision_advice(metrics))


if __name__ == "__main__":
    main()
