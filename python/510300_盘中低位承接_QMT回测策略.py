#coding:utf-8
"""
510300 盘中低位承接策略 - QMT 历史回测版

用途：
- 在 QMT 策略研究的历史回测模式中运行。
- 运行周期建议选择 5分钟。
- 复现外部 Python 监控程序的核心口径：
  1. 日线弱势低位背景
  2. 盘中弱势低位 + 量能修复预警
  3. 信号后第 2/3 根 5m K 线候选买入
  4. 主模拟买入为信号后第 3 根 5m K 线开盘
  5. 第 3 个交易日尾盘退出

重要边界：
- 本文件用于 QMT 内回测对照，不用于实盘自动交易。
- 策略参数来自当前已冻结的盘中低位承接口径。
"""

import csv
import json
import os


STOCK = '510300.SH'
ACCOUNT_ID = 'testS'
STRATEGY_NAME = '510300盘中低位承接-QMT回测'

ENTRY_RULE_NAME = 'intraday_weak_volume_repair'
ENTRY_RULE_LABEL = '盘中弱势低位-量能修复'
WATCH_ENTRY_OFFSETS = [2, 3]
PRIMARY_ENTRY_OFFSET = 3
EXIT_HOLD_DAYS = 3
LIVE_EXIT_TIME = '145500'

EXPECTED_5M_BARS = 48
MIN_SIGNAL_BAR_POS = 24

FEE_RATE = 0.0003
BUY_CASH_RATIO = 0.95
LOT_SIZE = 100

# 仅建议在 QMT 历史回测模式运行本文件。
# 如果你只是想先核对日志，不想让 QMT 回测系统生成订单，可改为 False。
ENABLE_QMT_ORDER = True


EVENT_COLUMNS = [
    'event_time',
    'trade_date',
    'event_type',
    'event_label',
    'stock',
    'price',
    'entry_offset_bars',
    'position_id',
    'status',
    'message',
]

TRADE_COLUMNS = [
    'position_id',
    'signal_time',
    'entry_time',
    'entry_date',
    'entry_price',
    'exit_time',
    'exit_date',
    'exit_price',
    'return_pct',
    'mae_pct',
    'mfe_pct',
]


def _to_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _fmt_pct(value):
    try:
        return '%.2f%%' % (float(value) * 100.0)
    except Exception:
        return ''


def _safe_date_from_time(text):
    text = str(text)
    if len(text) >= 8:
        return text[:8]
    return ''


def _safe_time(ContextInfo):
    try:
        return timetag_to_datetime(ContextInfo.get_bar_timetag(ContextInfo.barpos), '%Y%m%d%H%M%S')
    except Exception:
        try:
            return timetag_to_datetime(ContextInfo.get_bar_timetag(ContextInfo.barpos), '%Y%m%d')
        except Exception:
            return ''


def _get_latest_field(ContextInfo, stock, period, field):
    try:
        data = ContextInfo.get_history_data(1, period, field, 3)
        if stock in data and len(data[stock]) > 0:
            return _to_float(data[stock][-1])
    except Exception:
        pass
    return 0.0


def _get_history_list(ContextInfo, stock, count, period, field):
    try:
        data = ContextInfo.get_history_data(count, period, field, 3)
        if stock in data:
            return [_to_float(x) for x in data[stock]]
    except Exception:
        pass
    return []


def _mean(values):
    values = [float(x) for x in values]
    if not values:
        return None
    return sum(values) / float(len(values))


def _compute_rsi6_at_previous_day(closes):
    # 与外部研究脚本保持同一类 RSI 含义：最近 6 日涨跌均值比。
    if len(closes) < 8:
        return None
    prev_idx = len(closes) - 2
    start = prev_idx - 6
    if start < 0:
        return None
    gains = []
    losses = []
    for i in range(start + 1, prev_idx + 1):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
    avg_gain = _mean(gains) or 0.0
    avg_loss = _mean(losses) or 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _daily_background(ContextInfo):
    stock = ContextInfo.stock
    highs = _get_history_list(ContextInfo, stock, 40, '1d', 'high')
    lows = _get_history_list(ContextInfo, stock, 40, '1d', 'low')
    closes = _get_history_list(ContextInfo, stock, 40, '1d', 'close')

    if len(highs) < 12 or len(lows) < 12 or len(closes) < 12:
        return None

    # QMT 盘中取日线时通常包含当日动态日线，因此用 -2 代表前一完整交易日。
    prev_idx = len(closes) - 2
    high10 = max(highs[prev_idx - 9: prev_idx + 1])
    close10 = closes[prev_idx - 9: prev_idx + 1]
    prev_low = lows[prev_idx]
    prev_close = closes[prev_idx]
    ma10 = _mean(close10)
    rsi6 = _compute_rsi6_at_previous_day(closes)
    if high10 <= 0 or not ma10 or ma10 <= 0 or rsi6 is None:
        return None
    return {
        'pre_drawdown_from_high_10': prev_low / high10 - 1.0,
        'pre_close_vs_ma10': prev_close / ma10 - 1.0,
        'pre_rsi6': rsi6,
        'prev_close': prev_close,
    }


def _partial_intraday_features(bars, prev_close):
    current = bars[-1]
    current_close = float(current['close'])
    high_so_far = max(float(x['high']) for x in bars)
    low_so_far = min(float(x['low']) for x in bars)
    day_range = high_so_far - low_so_far

    low_pos = 0
    low_value = None
    for idx, bar in enumerate(bars):
        value = float(bar['low'])
        if low_value is None or value < low_value:
            low_value = value
            low_pos = idx

    before_low = bars[: low_pos + 1]
    after_low = bars[low_pos + 1:]
    avg_before = _mean([x['volume'] for x in before_low]) or 0.0
    avg_after = _mean([x['volume'] for x in after_low]) or 0.0
    volume_ratio = avg_after / avg_before - 1.0 if avg_before else 0.0

    current_pos = len(bars) - 1
    return {
        'signal_bar_pos': current_pos,
        'signal_price': current_close,
        'signal_low_so_far': low_so_far,
        'est_day_return': current_close / prev_close - 1.0 if prev_close else 0.0,
        'est_day_close_in_range': (current_close - low_so_far) / day_range if day_range > 0 else 0.0,
        'm5_low_pos': low_pos,
        'm5_low_pos_ratio': low_pos / float(max(EXPECTED_5M_BARS - 1, 1)),
        'm5_current_pos_ratio': current_pos / float(max(EXPECTED_5M_BARS - 1, 1)),
        'm5_volume_ratio_after_low': volume_ratio,
    }


def _rule_passes(features):
    if features.get('signal_bar_pos', -1) < MIN_SIGNAL_BAR_POS:
        return False
    return (
        features.get('pre_drawdown_from_high_10', 0) <= -0.025
        and features.get('pre_close_vs_ma10', 0) <= 0.0
        and features.get('pre_rsi6', 100) <= 48.0
        and features.get('est_day_close_in_range', 1) <= 0.45
        and features.get('est_day_return', 1) <= 0.003
        and features.get('m5_low_pos_ratio', 0) >= 0.35
        and features.get('m5_volume_ratio_after_low', -1) >= -0.30
    )


def _condition_message(features):
    return (
        '前10日回撤=%s；昨收偏离MA10=%s；前日RSI6=%.2f；'
        '估算日内位置=%s；估算当日涨跌=%s；低点位置=%s；低点后量能修复=%s'
    ) % (
        _fmt_pct(features.get('pre_drawdown_from_high_10')),
        _fmt_pct(features.get('pre_close_vs_ma10')),
        _to_float(features.get('pre_rsi6')),
        _fmt_pct(features.get('est_day_close_in_range')),
        _fmt_pct(features.get('est_day_return')),
        _fmt_pct(features.get('m5_low_pos_ratio')),
        _fmt_pct(features.get('m5_volume_ratio_after_low')),
    )


def _output_dir():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base_dir = os.getcwd()
    path = os.path.join(base_dir, 'outputs', 'qmt_intraday_low_absorb_backtest')
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def _add_event(ContextInfo, event):
    row = {}
    for col in EVENT_COLUMNS:
        row[col] = event.get(col, '')
    ContextInfo.event_log.append(row)
    print('[EVENT] %(event_time)s %(event_label)s price=%(price)s status=%(status)s %(message)s' % row)


def _write_csv(path, rows, columns):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _summarize_trades(trades):
    returns = [_to_float(x.get('return_pct')) for x in trades]
    if not returns:
        return {
            'trade_count': 0,
            'win_rate': 0.0,
            'avg_return': 0.0,
            'compounded_return': 0.0,
            'min_return': 0.0,
        }
    compounded = 1.0
    for value in returns:
        compounded *= 1.0 + value
    return {
        'trade_count': len(returns),
        'win_rate': len([x for x in returns if x > 0]) / float(len(returns)),
        'avg_return': sum(returns) / float(len(returns)),
        'compounded_return': compounded - 1.0,
        'min_return': min(returns),
    }


def _flush_outputs(ContextInfo):
    out = _output_dir()
    _write_csv(os.path.join(out, 'qmt_replay_event_log.csv'), ContextInfo.event_log, EVENT_COLUMNS)
    _write_csv(os.path.join(out, 'qmt_replay_simulated_trades.csv'), ContextInfo.trade_log, TRADE_COLUMNS)
    summary = {
        'strategy_name': STRATEGY_NAME,
        'stock': ContextInfo.stock,
        'entry_rule_name': ENTRY_RULE_NAME,
        'entry_rule_label': ENTRY_RULE_LABEL,
        'watch_entry_offsets': WATCH_ENTRY_OFFSETS,
        'primary_entry_offset': PRIMARY_ENTRY_OFFSET,
        'exit_hold_days': EXIT_HOLD_DAYS,
        'event_count': len(ContextInfo.event_log),
    }
    summary.update(_summarize_trades(ContextInfo.trade_log))
    with open(os.path.join(out, 'qmt_replay_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def _buy(ContextInfo, bar):
    price = float(bar['open'])
    shares = int((ContextInfo.cash * BUY_CASH_RATIO) / price / LOT_SIZE) * LOT_SIZE
    if shares <= 0:
        return False
    fee = price * shares * FEE_RATE
    if ENABLE_QMT_ORDER:
        order_shares(ContextInfo.stock, shares, 'fix', price, ContextInfo, ContextInfo.accountID)
    ContextInfo.cash -= price * shares + fee
    ContextInfo.holding_shares = shares
    ContextInfo.position_id += 1
    ContextInfo.position = {
        'position_id': ContextInfo.position_id,
        'signal_time': ContextInfo.warning_signal_time,
        'entry_time': bar['bar_time'],
        'entry_date': bar['trade_date'],
        'entry_price': price,
        'entry_idx': len(ContextInfo.all_bars) - 1,
    }
    _add_event(ContextInfo, {
        'event_time': bar['bar_time'],
        'trade_date': bar['trade_date'],
        'event_type': 'SIM_BUY',
        'event_label': '模拟买入',
        'stock': ContextInfo.stock,
        'price': round(price, 4),
        'entry_offset_bars': PRIMARY_ENTRY_OFFSET,
        'position_id': ContextInfo.position_id,
        'status': '开仓',
        'message': '信号后第 %s 根 5m K 线开盘模拟买入；计划第 %s 个交易日尾盘退出。' % (
            PRIMARY_ENTRY_OFFSET,
            EXIT_HOLD_DAYS,
        ),
    })
    return True


def _sell(ContextInfo, bar):
    pos = ContextInfo.position
    if not pos:
        return
    price = float(bar['close'])
    shares = int(ContextInfo.holding_shares)
    fee = price * shares * FEE_RATE
    if ENABLE_QMT_ORDER and shares > 0:
        order_shares(ContextInfo.stock, -shares, 'fix', price, ContextInfo, ContextInfo.accountID)
    ContextInfo.cash += price * shares - fee

    entry_price = float(pos['entry_price'])
    window = ContextInfo.all_bars[int(pos['entry_idx']):]
    lows = [float(x['low']) for x in window]
    highs = [float(x['high']) for x in window]
    ret = price / entry_price - 1.0 if entry_price else 0.0

    ContextInfo.trade_log.append({
        'position_id': pos['position_id'],
        'signal_time': pos['signal_time'],
        'entry_time': pos['entry_time'],
        'entry_date': pos['entry_date'],
        'entry_price': round(entry_price, 4),
        'exit_time': bar['bar_time'],
        'exit_date': bar['trade_date'],
        'exit_price': round(price, 4),
        'return_pct': round(ret, 6),
        'mae_pct': round(min(lows) / entry_price - 1.0, 6) if lows and entry_price else 0.0,
        'mfe_pct': round(max(highs) / entry_price - 1.0, 6) if highs and entry_price else 0.0,
    })
    _add_event(ContextInfo, {
        'event_time': bar['bar_time'],
        'trade_date': bar['trade_date'],
        'event_type': 'EXIT_REMINDER',
        'event_label': '退出提示',
        'stock': ContextInfo.stock,
        'price': round(price, 4),
        'position_id': pos['position_id'],
        'status': '时间退出',
        'message': '第 %s 个交易日尾盘退出提示；模拟收益=%s。' % (EXIT_HOLD_DAYS, _fmt_pct(ret)),
    })
    ContextInfo.position = None
    ContextInfo.holding_shares = 0
    _flush_outputs(ContextInfo)


def _reset_day_state(ContextInfo, trade_date):
    ContextInfo.current_trade_date = trade_date
    ContextInfo.today_bars = []
    ContextInfo.warning_signal_time = ''
    ContextInfo.warning_bar_index = None
    ContextInfo.emitted_offsets = {}
    ContextInfo.daily_features = None


def init(ContextInfo):
    ContextInfo.stock = STOCK
    ContextInfo.accountID = ACCOUNT_ID
    ContextInfo.set_universe([ContextInfo.stock])

    ContextInfo.cash = _to_float(getattr(ContextInfo, 'capital', 1000000.0), 1000000.0)
    ContextInfo.holding_shares = 0
    ContextInfo.position = None
    ContextInfo.position_id = 0

    ContextInfo.current_trade_date = ''
    ContextInfo.today_bars = []
    ContextInfo.all_bars = []
    ContextInfo.warning_signal_time = ''
    ContextInfo.warning_bar_index = None
    ContextInfo.emitted_offsets = {}
    ContextInfo.daily_features = None

    ContextInfo.event_log = []
    ContextInfo.trade_log = []
    ContextInfo.last_bar_time = ''

    print('=' * 70)
    print(STRATEGY_NAME)
    print('标的: %s' % ContextInfo.stock)
    print('周期: 请在 QMT 中选择 5分钟')
    print('规则: %s' % ENTRY_RULE_LABEL)
    print('主买入口径: 信号后第 %s 根 5m K 线开盘' % PRIMARY_ENTRY_OFFSET)
    print('退出: 第 %s 个交易日尾盘' % EXIT_HOLD_DAYS)
    print('ENABLE_QMT_ORDER=%s' % ENABLE_QMT_ORDER)
    print('=' * 70)


def handlebar(ContextInfo):
    bar_time = _safe_time(ContextInfo)
    if not bar_time or len(bar_time) < 8:
        return
    if bar_time == ContextInfo.last_bar_time:
        return
    ContextInfo.last_bar_time = bar_time

    stock = ContextInfo.stock
    trade_date = _safe_date_from_time(bar_time)
    if ContextInfo.current_trade_date != trade_date:
        _reset_day_state(ContextInfo, trade_date)

    bar = {
        'bar_time': bar_time,
        'trade_date': trade_date,
        'open': _get_latest_field(ContextInfo, stock, '5m', 'open'),
        'high': _get_latest_field(ContextInfo, stock, '5m', 'high'),
        'low': _get_latest_field(ContextInfo, stock, '5m', 'low'),
        'close': _get_latest_field(ContextInfo, stock, '5m', 'close'),
        'volume': _get_latest_field(ContextInfo, stock, '5m', 'volume'),
    }
    if bar['open'] <= 0 or bar['close'] <= 0:
        return

    ContextInfo.today_bars.append(bar)
    ContextInfo.all_bars.append(bar)

    if ContextInfo.daily_features is None:
        ContextInfo.daily_features = _daily_background(ContextInfo)
    if ContextInfo.daily_features is None:
        return

    features = {}
    features.update(ContextInfo.daily_features)
    features.update(_partial_intraday_features(ContextInfo.today_bars, ContextInfo.daily_features['prev_close']))
    features['trade_date'] = trade_date
    features['signal_time'] = bar_time

    if not ContextInfo.warning_signal_time and _rule_passes(features):
        ContextInfo.warning_signal_time = bar_time
        ContextInfo.warning_bar_index = len(ContextInfo.today_bars) - 1
        _add_event(ContextInfo, {
            'event_time': bar_time,
            'trade_date': trade_date,
            'event_type': 'BUY_WARNING',
            'event_label': '买入预警',
            'stock': stock,
            'price': round(float(bar['close']), 4),
            'status': '已触发',
            'message': '%s：%s' % (ENTRY_RULE_LABEL, _condition_message(features)),
        })

    if ContextInfo.warning_bar_index is not None:
        current_idx = len(ContextInfo.today_bars) - 1
        for offset in WATCH_ENTRY_OFFSETS:
            if ContextInfo.emitted_offsets.get(offset):
                continue
            if current_idx >= ContextInfo.warning_bar_index + offset:
                entry_bar = ContextInfo.today_bars[ContextInfo.warning_bar_index + offset]
                _add_event(ContextInfo, {
                    'event_time': entry_bar['bar_time'],
                    'trade_date': entry_bar['trade_date'],
                    'event_type': 'ENTRY_CANDIDATE',
                    'event_label': '候选买入提示',
                    'stock': stock,
                    'price': round(float(entry_bar['open']), 4),
                    'entry_offset_bars': offset,
                    'status': '可人工观察',
                    'message': '信号后第 %s 根 5m K 线开盘，候选买入价=%s。' % (
                        offset,
                        round(float(entry_bar['open']), 4),
                    ),
                })
                ContextInfo.emitted_offsets[offset] = True
                if offset == PRIMARY_ENTRY_OFFSET and ContextInfo.position is None:
                    _buy(ContextInfo, entry_bar)

    if ContextInfo.position is not None:
        entry_date = str(ContextInfo.position['entry_date'])
        dates = sorted(set([x['trade_date'] for x in ContextInfo.all_bars if entry_date <= x['trade_date'] <= trade_date]))
        if len(dates) >= EXIT_HOLD_DAYS and bar_time[-6:] >= LIVE_EXIT_TIME:
            _sell(ContextInfo, bar)

    total_asset = ContextInfo.cash + ContextInfo.holding_shares * float(bar['close'])
    profit_ratio = 0.0
    capital = _to_float(getattr(ContextInfo, 'capital', 1000000.0), 1000000.0)
    if capital:
        profit_ratio = (total_asset - capital) / capital

    try:
        ContextInfo.paint('buy_warning', 1 if ContextInfo.warning_signal_time == bar_time else 0, -1, 0)
        ContextInfo.paint('holding', 1 if ContextInfo.position is not None else 0, -1, 0)
        ContextInfo.paint('profit_ratio', profit_ratio, -1, 0)
    except Exception:
        pass

    try:
        if ContextInfo.is_last_bar():
            _flush_outputs(ContextInfo)
            print('QMT 回测输出目录: %s' % _output_dir())
    except Exception:
        pass
