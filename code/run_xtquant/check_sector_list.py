#!/usr/bin/env python3

# coding=utf-8
from xtquant import xtdata
# 获取板块列表
ret_sector_list = xtdata.get_sector_list()
print(f'获取板块目录: {ret_sector_list}')
# 根据板块列表找查询指数索引名称
ret_sector_data = xtdata.get_stock_list_in_sector('上证A股')
print(f'获取板块合约: {ret_sector_data}')
