#coding:utf-8
"""
510300 盘中低位承接策略 - QMT 实时盯盘版

用途：
- 在 QMT 实盘/模拟运行环境中盯盘。
- 订阅 510300.SH 的 1m K 线推送，内部聚合成 5m 观察结构。
- 出现买入预警、候选买入、模拟买入、退出提示时打印并写日志。

重要边界：
- 本文件不自动下单。
- 本文件服务盘中提醒和人工观察。
- 历史回测请使用：510300_盘中低位承接_QMT回测策略.py
"""

import csv
import json
import os
from datetime import datetime


STOCK = '510300.SH'
STRATEGY_NAME = '510300盘中低位承接-QMT实时盯盘'

ENTRY_RULE_NAME = 'intraday_weak_volume_repair'
ENTRY_RULE_LABEL = '盘中弱势低位-量能修复'
WATCH_ENTRY_OFFSETS = [2, 3]
PRIMARY_ENTRY_OFFSET = 3
EXIT_HOLD_DAYS = 3
LIVE_EXIT_TIME = '145500'

EXPECTED_5M_BARS = 48
MIN_SIGNAL_BAR_POS = 24

EVENT_COLUMNS = [
    'local_time',
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


_CTX = None
_STATE = {}


def _to_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _mean(values):
    values = [float(x) for x in values]
    if not values:
        return None
    return sum(values) / float(len(values))


def _fmt_pct(value):
    try:
        return '%.2f%%' % (float(value) * 100.0)
    except Exception:
        return ''


def _now_text():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _parse_time(fields):
    for key in ['stime', 'time', 'timetag']:
        value = fields.get(key)
        if value:
            text = str(value).split('.')[0]
            if len(text) >= 14 and text[:14].isdigit():
                return text[:14]
            if len(text) >= 13 and text[:13].isdigit():
                try:
                    return datetime.fromtimestamp(int(text[:13]) / 1000.0).strftime('%Y%m%d%H%M%S')
                except Exception:
                    pass
    return datetime.now().strftime('%Y%m%d%H%M%S')


def _bucket_5m(time_text):
    # 以本地 1m 推送时间近似归并到 5m 观察桶。
    try:
        dt = datetime.strptime(time_text[:14], '%Y%m%d%H%M%S')
        minute = (dt.minute // 5) * 5
        bucket = dt.replace(minute=minute, second=0)
        return bucket.strftime('%Y%m%d%H%M%S')
    except Exception:
        return time_text[:12] + '00'


def _output_dir():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base_dir = os.getcwd()
    path = os.path.join(base_dir, 'outputs', 'qmt_intraday_low_absorb_live')
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def _event_path():
    return os.path.join(_output_dir(), 'qmt_live_event_log.csv')


def _state_path():
    return os.path.join(_output_dir(), 'qmt_live_state.json')


def _append_event(event):
    path = _event_path()
    exists = os.path.exists(path)
    with open(path, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=EVENT_COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerow(event)


def _save_state():
    state = {
        'strategy_name': STRATEGY_NAME,
        'stock': STOCK,
        'latest_local_time': _STATE.get('latest_local_time', ''),
        'latest_bar_time': _STATE.get('latest_bar_time', ''),
        'latest_price': _STATE.get('latest_price', ''),
        'latest_status': _STATE.get('latest_status', ''),
        'active_trade_date': _STATE.get('active_trade_date', ''),
        'warning_signal_time': _STATE.get('warning_signal_time', ''),
        'position': _STATE.get('position'),
    }
    with open(_state_path(), 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _log_event(event_type, event_label, event_time, trade_date, price='', entry_offset_bars='', position_id='', status='', message=''):
    event = {
        'local_time': _now_text(),
        'event_time': event_time,
        'trade_date': trade_date,
        'event_type': event_type,
        'event_label': event_label,
        'stock': STOCK,
        'price': price,
        'entry_offset_bars': entry_offset_bars,
        'position_id': position_id,
        'status': status,
        'message': message,
    }
    _append_event(event)
    print('[%s] %s %s price=%s status=%s %s' % (
        event['local_time'],
        event_time,
        event_label,
        str(price),
        status,
        message,
    ))


def _get_history_list(ContextInfo, stock, count, period, field):
    try:
        data = ContextInfo.get_history_data(count, period, field, 3)
        if stock in data:
            return [_to_float(x) for x in data[stock]]
    except Exception:
        pass
    return []


def _compute_rsi6_at_previous_day(closes):
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
    stock = STOCK
    highs = _get_history_list(ContextInfo, stock, 40, '1d', 'high')
    lows = _get_history_list(ContextInfo, stock, 40, '1d', 'low')
    closes = _get_history_list(ContextInfo, stock, 40, '1d', 'close')
    if len(highs) < 12 or len(lows) < 12 or len(closes) < 12:
        return None
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


def _reset_for_date(trade_date):
    _STATE['active_trade_date'] = trade_date
    _STATE['today_bars'] = []
    _STATE['current_5m'] = None
    _STATE['warning_signal_time'] = ''
    _STATE['warning_bar_index'] = None
    _STATE['emitted_offsets'] = {}
    _STATE['daily_features'] = _daily_background(_CTX) if _CTX is not None else None
    print('[DATE_RESET] trade_date=%s daily_features=%s' % (trade_date, str(_STATE['daily_features'] is not None)))


def _finalize_current_5m():
    bar = _STATE.get('current_5m')
    if not bar:
        return
    bars = _STATE.setdefault('today_bars', [])
    if bars and bars[-1].get('bar_time') == bar.get('bar_time'):
        bars[-1] = bar
    else:
        bars.append(bar)
    _STATE['latest_bar_time'] = bar.get('bar_time', '')
    _STATE['latest_price'] = bar.get('close', '')
    _evaluate_completed_5m()


def _evaluate_completed_5m():
    bars = _STATE.get('today_bars', [])
    daily = _STATE.get('daily_features')
    if not bars or not daily:
        _STATE['latest_status'] = '缺少日线背景或5m数据'
        return

    bar = bars[-1]
    trade_date = bar['trade_date']
    features = {}
    features.update(daily)
    features.update(_partial_intraday_features(bars, daily['prev_close']))
    features['trade_date'] = trade_date
    features['signal_time'] = bar['bar_time']

    if not _STATE.get('warning_signal_time') and _rule_passes(features):
        _STATE['warning_signal_time'] = bar['bar_time']
        _STATE['warning_bar_index'] = len(bars) - 1
        _log_event(
            'BUY_WARNING',
            '买入预警',
            bar['bar_time'],
            trade_date,
            round(float(bar['close']), 4),
            '',
            '',
            '已触发',
            '%s：%s' % (ENTRY_RULE_LABEL, _condition_message(features)),
        )

    if _STATE.get('warning_bar_index') is not None:
        current_idx = len(bars) - 1
        for offset in WATCH_ENTRY_OFFSETS:
            emitted = _STATE.setdefault('emitted_offsets', {})
            if emitted.get(str(offset)):
                continue
            entry_idx = _STATE['warning_bar_index'] + offset
            if current_idx >= entry_idx and entry_idx < len(bars):
                entry_bar = bars[entry_idx]
                _log_event(
                    'ENTRY_CANDIDATE',
                    '候选买入提示',
                    entry_bar['bar_time'],
                    entry_bar['trade_date'],
                    round(float(entry_bar['open']), 4),
                    offset,
                    '',
                    '可人工观察',
                    '信号后第 %s 根 5m K 线开盘，候选买入价=%s。' % (
                        offset,
                        round(float(entry_bar['open']), 4),
                    ),
                )
                emitted[str(offset)] = True
                if offset == PRIMARY_ENTRY_OFFSET and not _STATE.get('position'):
                    _STATE['position'] = {
                        'position_id': 1,
                        'entry_time': entry_bar['bar_time'],
                        'entry_date': entry_bar['trade_date'],
                        'entry_price': round(float(entry_bar['open']), 4),
                    }
                    _log_event(
                        'SIM_BUY',
                        '模拟买入',
                        entry_bar['bar_time'],
                        entry_bar['trade_date'],
                        round(float(entry_bar['open']), 4),
                        offset,
                        1,
                        '开仓',
                        '按主口径：信号后第 %s 根 5m K 线开盘模拟买入；计划第 %s 个交易日尾盘退出。' % (
                            offset,
                            EXIT_HOLD_DAYS,
                        ),
                    )

    position = _STATE.get('position')
    if position:
        seen = _STATE.setdefault('seen_trade_dates', [])
        if trade_date not in seen:
            seen.append(trade_date)
        holding_dates = [x for x in seen if position['entry_date'] <= x <= trade_date]
        if len(holding_dates) >= EXIT_HOLD_DAYS and bar['bar_time'][-6:] >= LIVE_EXIT_TIME:
            entry_price = float(position['entry_price'])
            exit_price = float(bar['close'])
            ret = exit_price / entry_price - 1.0 if entry_price else 0.0
            _log_event(
                'EXIT_REMINDER',
                '退出提示',
                bar['bar_time'],
                trade_date,
                round(exit_price, 4),
                '',
                position.get('position_id', 1),
                '时间退出',
                '第 %s 个交易日尾盘退出提示；模拟收益=%s。' % (EXIT_HOLD_DAYS, _fmt_pct(ret)),
            )
            _STATE['position'] = None

    _STATE['latest_status'] = '已触发' if _STATE.get('warning_signal_time') else '未触发'
    _STATE['latest_local_time'] = _now_text()
    _save_state()


def _update_5m_from_1m(fields):
    time_text = _parse_time(fields)
    trade_date = time_text[:8]
    if _STATE.get('active_trade_date') != trade_date:
        _reset_for_date(trade_date)

    bucket = _bucket_5m(time_text)
    price_open = _to_float(fields.get('open', fields.get('lastPrice', fields.get('close', 0))))
    price_high = _to_float(fields.get('high', fields.get('lastPrice', fields.get('close', 0))))
    price_low = _to_float(fields.get('low', fields.get('lastPrice', fields.get('close', 0))))
    price_close = _to_float(fields.get('close', fields.get('lastPrice', 0)))
    volume = _to_float(fields.get('volume', 0))
    if price_close <= 0:
        return

    current = _STATE.get('current_5m')
    if current and current.get('bar_time') != bucket:
        _finalize_current_5m()
        current = None

    if not current:
        current = {
            'bar_time': bucket,
            'trade_date': trade_date,
            'open': price_open or price_close,
            'high': price_high or price_close,
            'low': price_low or price_close,
            'close': price_close,
            'volume': volume,
        }
    else:
        current['high'] = max(float(current['high']), price_high or price_close)
        current['low'] = min(float(current['low']), price_low or price_close)
        current['close'] = price_close
        current['volume'] = float(current.get('volume', 0)) + volume
    _STATE['current_5m'] = current
    _STATE['latest_local_time'] = _now_text()
    _STATE['latest_price'] = round(price_close, 4)
    _save_state()


def on_1m_kline(datas):
    try:
        if STOCK not in datas:
            return
        fields = datas[STOCK]
        if not isinstance(fields, dict):
            return
        _update_5m_from_1m(fields)
    except Exception as exc:
        print('[LIVE_CALLBACK_ERROR] %s' % str(exc))


def init(ContextInfo):
    global _CTX
    _CTX = ContextInfo
    ContextInfo.stock = STOCK
    ContextInfo.set_universe([STOCK])

    _STATE.clear()
    _STATE['active_trade_date'] = ''
    _STATE['today_bars'] = []
    _STATE['current_5m'] = None
    _STATE['warning_signal_time'] = ''
    _STATE['warning_bar_index'] = None
    _STATE['emitted_offsets'] = {}
    _STATE['position'] = None
    _STATE['seen_trade_dates'] = []

    print('=' * 70)
    print(STRATEGY_NAME)
    print('标的: %s' % STOCK)
    print('模式: QMT 实时盯盘，不自动下单')
    print('行情: subscribe_quote 订阅 1m K 线，内部聚合为 5m 观察')
    print('输出目录: %s' % _output_dir())
    print('=' * 70)

    sub_id = ContextInfo.subscribe_quote(
        STOCK,
        period='1m',
        dividend_type='none',
        result_type='dict',
        callback=on_1m_kline
    )
    print('[INIT] subscribe_quote(1m) 返回订阅号: %s' % str(sub_id))
    print('[INIT] 等待行情推送...')


def handlebar(ContextInfo):
    # 实时盯盘主要由订阅回调驱动；handlebar 只用于在 QMT 面板上画出简单状态。
    try:
        price = _to_float(_STATE.get('latest_price', 0))
        signal = 1 if _STATE.get('warning_signal_time') else 0
        holding = 1 if _STATE.get('position') else 0
        ContextInfo.paint('latest_price', price, -1, 0)
        ContextInfo.paint('buy_warning', signal, -1, 0)
        ContextInfo.paint('sim_holding', holding, -1, 0)
    except Exception:
        pass
