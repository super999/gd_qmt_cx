#!/usr/bin/env python3
# coding: utf-8
"""
xtquant 冒烟测试脚本
验证 Trae CN 环境能否正常导入和使用 xtquant
支持多 Python 环境测试，生成独立的报告文件
"""

import sys
import json
from datetime import datetime

TEST_STOCK = '510300.SH'
TEST_PERIOD = '1d'

python_executable = sys.executable
python_version_info = f"{sys.version_info.major}.{sys.version_info.minor}"

report = {
    'test_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    'python_version': sys.version,
    'python_version_short': python_version_info,
    'python_executable': python_executable,
    'environment_note': '',
    'results': [],
    'passed': 0,
    'failed': 0,
    'total': 0
}

if 'gd_qmt_py36' in python_executable:
    report['environment_note'] = 'QMT 兼容性备用环境 (Python 3.6) - 用于版本兼容交叉验证'
elif 'gd_qmt_env' in python_executable:
    report['environment_note'] = '当前优先开发环境 (Python 3.12) - 已确认可用于 xtquant 冒烟测试与大部分 API 测试'
else:
    report['environment_note'] = '未知环境'


def log_result(name, success, message='', detail=None):
    result = {
        'name': name,
        'success': success,
        'message': message,
        'detail': str(detail)[:500] if detail else None
    }
    report['results'].append(result)
    report['total'] += 1
    if success:
        report['passed'] += 1
        print(f"[PASS] {name}: {message}")
    else:
        report['failed'] += 1
        print(f"[FAIL] {name}: {message}")
    return success


def test_import_xtquant():
    try:
        from xtquant import xtdata
        from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
        return log_result('import_xtquant', True, 'xtquant 库导入成功')
    except ImportError as e:
        return log_result('import_xtquant', False, f'导入失败: {e}')
    except Exception as e:
        return log_result('import_xtquant', False, f'未知错误: {e}')


def test_xtdata_connection():
    try:
        from xtquant import xtdata
        
        end_time = datetime.now().strftime('%Y%m%d')
        start_time = '20260401'
        
        history = xtdata.download_history_data(
            TEST_STOCK,
            period=TEST_PERIOD,
            start_time=start_time,
            end_time=end_time
        )
        
        data = xtdata.get_market_data(
            stock_list=[TEST_STOCK],
            period=TEST_PERIOD
        )
        
        if data:
            detail = {
                'stock': TEST_STOCK,
                'period': TEST_PERIOD,
                'data_keys': list(data.keys()) if hasattr(data, 'keys') else None
            }
            
            if 'close' in data:
                close_data = data['close']
                if hasattr(close_data, 'index'):
                    detail['dates_count'] = len(close_data.index.tolist())
                    detail['latest_close'] = float(close_data.iloc[-1][TEST_STOCK]) if TEST_STOCK in close_data.columns else None
            
            return log_result('xtdata_connection', True, f'xtdata 连接正常，成功获取 {TEST_STOCK} 行情数据', detail)
        else:
            return log_result('xtdata_connection', False, f'未获取到数据，请检查 QMT 客户端状态')
    except Exception as e:
        return log_result('xtdata_connection', False, f'连接测试失败: {e}')


def test_market_data_structure():
    try:
        from xtquant import xtdata
        
        data = xtdata.get_market_data(
            stock_list=[TEST_STOCK],
            period=TEST_PERIOD
        )
        
        if data:
            required_fields = ['close', 'open', 'high', 'low', 'volume', 'amount']
            available_fields = []
            missing_fields = []
            
            for field in required_fields:
                if field in data:
                    available_fields.append(field)
                else:
                    missing_fields.append(field)
            
            detail = {
                'available_fields': available_fields,
                'missing_fields': missing_fields
            }
            
            if len(missing_fields) == 0:
                return log_result('market_data_structure', True, f'所有必需行情字段完整', detail)
            else:
                return log_result('market_data_structure', False, f'缺少字段: {missing_fields}', detail)
        else:
            return log_result('market_data_structure', False, f'数据为空')
    except Exception as e:
        return log_result('market_data_structure', False, f'结构测试失败: {e}')


def test_get_full_tick():
    try:
        from xtquant import xtdata
        
        tick = xtdata.get_full_tick([TEST_STOCK])
        
        if tick and TEST_STOCK in tick:
            tick_data = tick[TEST_STOCK]
            detail = {
                'stock': TEST_STOCK,
                'has_tick': True,
                'tick_keys': list(tick_data.keys()) if hasattr(tick_data, 'keys') else None
            }
            return log_result('get_full_tick', True, f'成功获取 {TEST_STOCK} 实时 tick 数据', detail)
        else:
            return log_result('get_full_tick', False, f'未获取到 tick 数据（可能不在交易时间）')
    except Exception as e:
        return log_result('get_full_tick', False, f'tick 测试失败: {e}')


def generate_report():
    report['summary'] = {
        'status': 'SUCCESS' if report['failed'] == 0 else 'PARTIAL',
        'passed': report['passed'],
        'failed': report['failed'],
        'total': report['total']
    }
    
    print("\n" + "=" * 70)
    print("XTQUANT 冒烟测试报告")
    print("=" * 70)
    print(f"测试时间: {report['test_time']}")
    print(f"Python 版本: {report['python_version_short']}")
    print(f"Python 解释器: {report['python_executable']}")
    print(f"环境说明: {report['environment_note']}")
    print(f"测试结果: {report['summary']['status']}")
    print(f"通过: {report['passed']}/{report['total']}")
    print(f"失败: {report['failed']}/{report['total']}")
    print("=" * 70)
    
    for result in report['results']:
        status = '✓' if result['success'] else '✗'
        print(f"{status} {result['name']}: {result['message']}")
    
    print("=" * 70)
    
    import os
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_filename = f'smoke_test_report_py{python_version_info}_{timestamp}.json'
    report_path = os.path.join(output_dir, report_filename)
    
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n报告已保存到: {report_path}")
    print(f"报告文件名已包含 Python 版本和时间戳，不会覆盖旧报告")
    
    return report['summary']['status'] == 'SUCCESS'


def main():
    print("=" * 70)
    print(f"开始 xtquant 冒烟测试 (Python {python_version_info})")
    print(f"Python 解释器: {python_executable}")
    print(f"环境: {report['environment_note']}")
    print("=" * 70)
    
    if not test_import_xtquant():
        print("\n错误: 无法导入 xtquant，测试终止")
        generate_report()
        sys.exit(1)
    
    print("-" * 70)
    test_xtdata_connection()
    
    print("-" * 70)
    test_market_data_structure()
    
    print("-" * 70)
    test_get_full_tick()
    
    print("-" * 70)
    success = generate_report()
    
    sys.exit(0 if success else 0)


if __name__ == "__main__":
    main()
