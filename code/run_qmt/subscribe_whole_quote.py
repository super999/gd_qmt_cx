#coding:gbk
"""
======================================================================
QMT 订阅全推行情 - 验证程序
======================================================================

【功能说明】
在 QMT 实盘/模拟模式下，使用 ContextInfo.subscribe_whole_quote 订阅
全推行情数据，通过回调函数接收并打印行情快照，验证全推行情功能是否正常。

【使用方法】
1. 将此文件复制到 QMT 的 python 目录，或在 QMT 策略研究中导入
2. 在 QMT 中新建策略，选择此文件
3. 运行模式选择：实盘/模拟（不是回测模式！）
4. 在交易时间段内运行，观察 QMT 输出日志中的行情数据

【输出文件】
日志同时输出到 QMT 控制台和文件，文件路径：
  d:\\codex_n_workspace\\gd_qmt_cx_trae_cn\\code\\run_qmt\\outputs\\subscribe_whole_quote.log

【注意事项】
- 必须在交易时间段内运行才能收到实时行情
- 全市场订阅数据量极大，建议先用少量标的测试
- handlebar 函数保持最小化，核心逻辑在订阅回调中

======================================================================
"""

import os
import time
from datetime import datetime

SUBSCRIBE_MODE = 'stock_list'

STOCK_LIST = ['510300.SH', '159919.SZ', '510500.SH', '510050.SH']

MARKET_LIST = ['SH', 'SZ']

CALLBACK_COUNT_LIMIT = 200

ENABLE_LOG_FILE = True

LOG_DIR = r'd:\codex_n_workspace\gd_qmt_cx_trae_cn\code\run_qmt\outputs'
LOG_FILE = os.path.join(LOG_DIR, 'subscribe_whole_quote.log')

_callback_count = 0
_log_fo = None
_prev_volume = {}
_prev_pvolume = {}
_prev_trans_num = {}


def _ensure_log_file():
    global _log_fo
    if not ENABLE_LOG_FILE:
        return
    if _log_fo is None:
        try:
            if not os.path.exists(LOG_DIR):
                os.makedirs(LOG_DIR)
            _log_fo = open(LOG_FILE, 'a', encoding='utf-8')
        except Exception as e:
            print('[LOG_FILE_ERROR] %s' % str(e))


def _log(msg):
    print(msg)
    if not ENABLE_LOG_FILE:
        return
    _ensure_log_file()
    try:
        if _log_fo:
            _log_fo.write(msg + '\n')
            _log_fo.flush()
    except Exception:
        pass


def _fp(val):
    try:
        return '%.3f' % float(val)
    except Exception:
        return str(val)


def _fv(val):
    try:
        return '%d' % int(val)
    except Exception:
        return str(val)


def _format_price_list(price_list):
    if not isinstance(price_list, (list, tuple)):
        return str(price_list)
    return '[' + ', '.join([_fp(p) for p in price_list]) + ']'


def _format_vol_list(vol_list):
    if not isinstance(vol_list, (list, tuple)):
        return str(vol_list)
    return '[' + ', '.join([_fv(v) for v in vol_list]) + ']'


def _format_timestamp(ts):
    try:
        dt = datetime.fromtimestamp(int(ts) / 1000)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(ts)


def _format_whole_quote(datas, max_stocks=5):
    lines = []
    lines.append('=' * 60)
    lines.append('[subscribe_whole_quote 回调触发]')

    stock_codes = list(datas.keys())
    total = len(stock_codes)
    lines.append('  收到标的数量: %d' % total)

    for i, code in enumerate(stock_codes[:max_stocks]):
        data = datas[code]
        if isinstance(data, dict):
            lines.append('  [%d] %s' % (i + 1, code))
            lines.append('      时间: %s' % _format_timestamp(data.get('time', 0)))
            lines.append('      最新=%s  开=%s  高=%s  低=%s  昨收=%s' % (
                _fp(data.get('lastPrice', 0)),
                _fp(data.get('open', 0)),
                _fp(data.get('high', 0)),
                _fp(data.get('low', 0)),
                _fp(data.get('lastClose', 0)),
            ))
            cur_vol = int(data.get('volume', 0))
            cur_pvol = int(data.get('pvolume', 0))
            cur_trans = int(data.get('transactionNum', 0))
            prev_vol = _prev_volume.get(code, 0)
            prev_pvol = _prev_pvolume.get(code, 0)
            prev_trans = _prev_trans_num.get(code, 0)
            delta_vol = cur_vol - prev_vol
            delta_pvol = cur_pvol - prev_pvol
            delta_trans = cur_trans - prev_trans
            _prev_volume[code] = cur_vol
            _prev_pvolume[code] = cur_pvol
            _prev_trans_num[code] = cur_trans
            lines.append('      累计成交量: %s手(%s股)  本次增量: %s手(%s股)' % (
                _fv(cur_vol), _fv(cur_pvol), _fv(delta_vol), _fv(delta_pvol),
            ))
            lines.append('      累计成交笔数: %s  本次增量: %s笔' % (
                _fv(cur_trans), _fv(delta_trans),
            ))
            lines.append('      成交额: %s' % _fv(data.get('amount', 0)))
            ask_price = data.get('askPrice', [])
            bid_price = data.get('bidPrice', [])
            ask_vol = data.get('askVol', [])
            bid_vol = data.get('bidVol', [])
            if ask_price and bid_price:
                lines.append('      买一=%s(%s)  卖一=%s(%s)' % (
                    _fp(bid_price[0]) if len(bid_price) > 0 else 'N/A',
                    _fv(bid_vol[0]) if len(bid_vol) > 0 else 'N/A',
                    _fp(ask_price[0]) if len(ask_price) > 0 else 'N/A',
                    _fv(ask_vol[0]) if len(ask_vol) > 0 else 'N/A',
                ))
                lines.append('      买价: %s' % _format_price_list(bid_price))
                lines.append('      买量: %s' % _format_vol_list(bid_vol))
                lines.append('      卖价: %s' % _format_price_list(ask_price))
                lines.append('      卖量: %s' % _format_vol_list(ask_vol))
        else:
            lines.append('  [%d] %s data=%s' % (i + 1, code, str(data)[:200]))

    if total > max_stocks:
        lines.append('  ... 省略剩余 %d 个标的' % (total - max_stocks))

    lines.append('=' * 60)
    return '\n'.join(lines)


def on_whole_quote(datas):
    global _callback_count
    _callback_count += 1

    try:
        _log(_format_whole_quote(datas))

        if _callback_count % 10 == 0:
            _log('[WHOLE_QUOTE] 已收到 %d 次回调' % _callback_count)

        if _callback_count >= CALLBACK_COUNT_LIMIT:
            _log('[WHOLE_QUOTE] 已达到回调次数上限(%d)，建议手动停止策略' % CALLBACK_COUNT_LIMIT)
    except Exception as e:
        _log('[WHOLE_QUOTE_CALLBACK_ERROR] count=%d msg=%s' % (_callback_count, str(e)))


def init(ContextInfo):
    global _callback_count
    _callback_count = 0

    _log('=' * 60)
    _log('QMT 订阅全推行情 - 验证程序')
    _log('=' * 60)
    _log('订阅模式: %s' % SUBSCRIBE_MODE)

    if SUBSCRIBE_MODE == 'market':
        code_list = MARKET_LIST
        _log('订阅市场: %s' % str(MARKET_LIST))
        _log('注意: 全市场订阅数据量极大，日志可能刷屏')
    else:
        code_list = STOCK_LIST
        _log('订阅标的: %s' % str(STOCK_LIST))

    _log('回调次数上限: %d' % CALLBACK_COUNT_LIMIT)
    _log('运行模式: 实盘/模拟')
    _log('日志文件: %s' % LOG_FILE)
    _log('说明: 请在交易时间段内运行')
    _log('=' * 60)

    ContextInfo.set_universe(code_list if SUBSCRIBE_MODE == 'stock_list' else ['510300.SH'])

    sub_id = ContextInfo.subscribe_whole_quote(code_list, callback=on_whole_quote)
    _log('[INIT] subscribe_whole_quote 返回订阅号: %s' % sub_id)

    if sub_id > 0:
        _log('[INIT] 全推行情订阅成功！')
    else:
        _log('[INIT] 全推行情订阅失败！sub_id=%s' % sub_id)

    all_subs = ContextInfo.get_all_subscription()
    _log('[INIT] 当前所有订阅: %s' % str(all_subs))

    _log('[INIT] 订阅设置完成，等待全推行情回调...')


def handlebar(ContextInfo):
    pass
