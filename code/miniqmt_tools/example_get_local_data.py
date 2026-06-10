#!/usr/bin/env python3
# coding: utf-8
r"""
演示：先 download_history_data，再用 get_local_data 从 MiniQMT 本地行情库读取数据。

运行：
    d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/example_get_local_data.py
"""

from __future__ import annotations

import sys
import traceback
from typing import Optional

import pandas as pd
from xtquant import xtdata


# =========================
# 可复用参数区
# =========================

STOCK_CODE = "510300.SH"
PERIOD = "1d"
START_TIME = "20260511"
END_TIME = "20260607"
DIVIDEND_TYPE = "none"
FILL_DATA = False


def print_title(title: str) -> None:
    print("")
    print("=" * 80)
    print(title)
    print("=" * 80)


def print_frame(label: str, frame: Optional[pd.DataFrame]) -> bool:
    print_title(label)
    if not isinstance(frame, pd.DataFrame):
        print("未返回 DataFrame。")
        return False

    print("行数:", len(frame))
    print("列:", list(frame.columns))
    if frame.empty:
        print("结果为空。")
        return False

    print(frame.to_string())
    return True


def main() -> int:
    print_title("get_local_data 本地行情读取示例")
    print("Python:", sys.executable)
    print("股票:", STOCK_CODE)
    print("周期:", PERIOD)
    print("日期范围:", START_TIME, "至", END_TIME)
    print("复权:", DIVIDEND_TYPE)
    print("fill_data:", FILL_DATA)

    try:
        try:
            detail = xtdata.get_instrument_detail(STOCK_CODE, iscomplete=False)
            print("证券名称:", detail.get("InstrumentName") if detail else "")
        except Exception as exc:
            print("证券信息读取失败:", type(exc).__name__, exc)

        print_title("步骤1：download_history_data 补充本地历史行情")
        result = xtdata.download_history_data(
            STOCK_CODE,
            period=PERIOD,
            start_time=START_TIME,
            end_time=END_TIME,
            incrementally=True,
        )
        print("download_history_data 返回:", repr(result))

        print_title("步骤2：get_local_data 从本地行情库读取")
        local_data = xtdata.get_local_data(
            field_list=[],
            stock_list=[STOCK_CODE],
            period=PERIOD,
            start_time=START_TIME,
            end_time=END_TIME,
            count=-1,
            dividend_type=DIVIDEND_TYPE,
            fill_data=FILL_DATA,
        )
        local_frame = local_data.get(STOCK_CODE)
        local_ok = print_frame("get_local_data 返回", local_frame)

        print_title("步骤3：get_market_data_ex 对照读取")
        market_data = xtdata.get_market_data_ex(
            field_list=[],
            stock_list=[STOCK_CODE],
            period=PERIOD,
            start_time=START_TIME,
            end_time=END_TIME,
            count=-1,
            dividend_type=DIVIDEND_TYPE,
            fill_data=FILL_DATA,
        )
        market_frame = market_data.get(STOCK_CODE)
        market_ok = print_frame("get_market_data_ex 返回", market_frame)

        print_title("结论")
        if local_ok:
            print("get_local_data 已成功从本地行情库读取到数据。")
        else:
            print("get_local_data 未读取到数据，请检查股票代码、日期是否为交易日，以及 MiniQMT 本地数据状态。")

        if local_ok and market_ok:
            same_shape = local_frame.shape == market_frame.shape
            print("get_local_data 与 get_market_data_ex 均返回数据，shape 是否一致:", same_shape)
        return 0

    except Exception as exc:
        print_title("程序异常")
        print("{}: {}".format(type(exc).__name__, str(exc)))
        print(traceback.format_exc())
        print("请确认 MiniQMT 已启动，行情连接正常，并且股票代码、周期、日期参数正确。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
