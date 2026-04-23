#coding:gbk
"""
======================================================================
QMT 订阅单只股票实时行情 - 验证程序
======================================================================

【功能说明】
在 QMT 实盘/模拟模式下，订阅 510300.SH（沪深300ETF）的实时行情，
通过回调函数接收并打印行情数据，验证 QMT 订阅行情功能是否正常。

【使用方法】
1. 将此文件复制到 QMT 的 python 目录，或在 QMT 策略研究中导入
2. 在 QMT 中新建策略，选择此文件
3. 运行模式选择：实盘/模拟（不是回测模式！）
4. 在交易时间段内运行，观察 QMT 输出日志中的行情数据

【输出文件】
日志同时输出到 QMT 控制台和文件，文件路径：
  d:\\codex_n_workspace\\gd_qmt_cx_trae_cn\\code\\run_qmt\\outputs\\subscribe_quote_510300.log

【注意事项】
- 必须在交易时间段内运行才能收到实时行情
- handlebar 函数保持最小化，本程序的核心逻辑在订阅回调中
- 如果长时间未收到回调，检查 QMT 是否已连接行情服务器

======================================================================
"""

import os
import time

STOCK_CODE = '510300.SH'

ENABLE_LOG_FILE = True

ENABLE_TICK = True
ENABLE_1M_KLINE = False

LOG_DIR = r'd:\codex_n_workspace\gd_qmt_cx_trae_cn\code\run_qmt\outputs'
LOG_FILE = os.path.join(LOG_DIR, 'subscribe_quote_510300.log')

_log_fo = None
_prev_volume = {}
_prev_pvolume = {}
_prev_trans_num = {}


def _format_stime(stime):
    try:
        s = str(stime).split('.')[0]
        return '%s-%s-%s %s:%s:%s' % (s[:4], s[4:6], s[6:8], s[8:10], s[10:12], s[12:14])
    except Exception:
        return str(stime)


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
    """格式化价格为3位小数"""
    try:
        return '%.3f' % float(val)
    except Exception:
        return str(val)


def _fv(val):
    """格式化成交量/额为整数"""
    try:
        return '%d' % int(val)
    except Exception:
        return str(val)


def _format_price_list(price_list):
    """格式化价格列表（五档买卖价）"""
    if not isinstance(price_list, (list, tuple)):
        return str(price_list)
    return '[' + ', '.join([_fp(p) for p in price_list]) + ']'


def _format_vol_list(vol_list):
    """格式化量列表（五档买卖量）"""
    if not isinstance(vol_list, (list, tuple)):
        return str(vol_list)
    return '[' + ', '.join([_fv(v) for v in vol_list]) + ']'


def _format_tick_data(datas):
    lines = []
    lines.append('-' * 60)
    lines.append('[subscribe_quote 回调触发]')
    for stock_code, fields in datas.items():
        lines.append('  标的: %s' % stock_code)
        if isinstance(fields, dict):
            lines.append('    时间: %s' % _format_stime(fields.get('stime', '')))
            lines.append('    最新价: %s' % _fp(fields.get('lastPrice', 0)))
            lines.append('    开盘: %s  最高: %s  最低: %s  昨收: %s' % (
                _fp(fields.get('open', 0)),
                _fp(fields.get('high', 0)),
                _fp(fields.get('low', 0)),
                _fp(fields.get('lastClose', 0)),
            ))
            cur_vol = int(fields.get('volume', 0))
            cur_pvol = int(fields.get('pvolume', 0))
            cur_trans = int(fields.get('transactionNum', 0))
            prev_vol = _prev_volume.get(stock_code, 0)
            prev_pvol = _prev_pvolume.get(stock_code, 0)
            prev_trans = _prev_trans_num.get(stock_code, 0)
            delta_vol = cur_vol - prev_vol
            delta_pvol = cur_pvol - prev_pvol
            delta_trans = cur_trans - prev_trans
            _prev_volume[stock_code] = cur_vol
            _prev_pvolume[stock_code] = cur_pvol
            _prev_trans_num[stock_code] = cur_trans
            lines.append('    累计成交量: %s手  本次tick增量: %s手(%s股)' % (
                _fv(cur_vol), _fv(delta_vol), _fv(delta_pvol),
            ))
            lines.append('    累计成交笔数: %s  本次tick增量: %s笔' % (
                _fv(cur_trans), _fv(delta_trans),
            ))
            lines.append('    成交额: %s' % _fv(fields.get('amount', 0)))
            ask_price = fields.get('askPrice', [])
            bid_price = fields.get('bidPrice', [])
            ask_vol = fields.get('askVol', [])
            bid_vol = fields.get('bidVol', [])
            if ask_price:
                lines.append('    卖价: %s' % _format_price_list(ask_price))
                lines.append('    卖量: %s' % _format_vol_list(ask_vol))
            if bid_price:
                lines.append('    买价: %s' % _format_price_list(bid_price))
                lines.append('    买量: %s' % _format_vol_list(bid_vol))
        else:
            lines.append('    data = %s' % str(fields))
    lines.append('-' * 60)
    return '\n'.join(lines)


def on_tick_data(datas):
    try:
        _log(_format_tick_data(datas))
        if STOCK_CODE in datas:
            fields = datas[STOCK_CODE]
            if isinstance(fields, dict):
                last_price = fields.get('lastPrice', fields.get('close', 0))
                _log('[TICK] %s 最新价=%s' % (STOCK_CODE, _fp(last_price)))
    except Exception as e:
        _log('[TICK_CALLBACK_ERROR] %s' % str(e))


def on_1m_kline_data(datas):
    try:
        _log('[1m_KLINE 回调触发]')
        for stock_code, fields in datas.items():
            if isinstance(fields, dict):
                _log('[1m_KLINE] %s 开=%s 高=%s 低=%s 收=%s 量=%s' % (
                    stock_code,
                    _fp(fields.get('open', 0)),
                    _fp(fields.get('high', 0)),
                    _fp(fields.get('low', 0)),
                    _fp(fields.get('close', 0)),
                    _fv(fields.get('volume', 0)),
                ))
            else:
                _log('[1m_KLINE] %s data=%s' % (stock_code, str(fields)[:200]))
    except Exception as e:
        _log('[1M_KLINE_CALLBACK_ERROR] %s' % str(e))


def init(ContextInfo):
    _log('=' * 60)
    _log('QMT 订阅单只股票实时行情 - 验证程序')
    _log('=' * 60)
    _log('订阅标的: %s' % STOCK_CODE)
    subs = []
    if ENABLE_TICK:
        subs.append('tick')
    if ENABLE_1M_KLINE:
        subs.append('1m')
    _log('订阅周期: %s' % (' + '.join(subs) if subs else '无'))
    _log('运行模式: 实盘/模拟')
    _log('日志文件: %s' % LOG_FILE)
    _log('说明: 请在交易时间段内运行')
    _log('=' * 60)

    ContextInfo.set_universe([STOCK_CODE])

    if ENABLE_TICK:
        sub_id_tick = ContextInfo.subscribe_quote(
            STOCK_CODE,
            period='tick',
            dividend_type='none',
            result_type='dict',
            callback=on_tick_data
        )
        _log('[INIT] subscribe_quote(tick) 返回订阅号: %s' % sub_id_tick)
    else:
        _log('[INIT] tick 订阅已关闭 (ENABLE_TICK=False)')

    if ENABLE_1M_KLINE:
        sub_id_1m = ContextInfo.subscribe_quote(
            STOCK_CODE,
            period='1m',
            dividend_type='none',
            result_type='dict',
            callback=on_1m_kline_data
        )
        _log('[INIT] subscribe_quote(1m) 返回订阅号: %s' % sub_id_1m)
    else:
        _log('[INIT] 1m K线订阅已关闭 (ENABLE_1M_KLINE=False)')

    all_subs = ContextInfo.get_all_subscription()
    _log('[INIT] 当前所有订阅: %s' % str(all_subs))

    _log('[INIT] 订阅设置完成，等待行情回调...')


def handlebar(ContextInfo):
    pass
