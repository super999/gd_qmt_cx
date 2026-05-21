#!/usr/bin/env python3

from xtquant import xtdata

SAMPLE_CODES = [
    "000001.SZ"
]
xtdata.download_financial_data(SAMPLE_CODES) # 下载财务数据到本地
ret = xtdata.get_financial_data(SAMPLE_CODES, table_list=[], start_time='20200101', end_time='20260501', report_type='report_time')

print(ret)
