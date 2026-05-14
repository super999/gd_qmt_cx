#!/usr/bin/env python3
# coding: utf-8
r"""
演示：单只股票用 get_market_data_ex 取不到历史行情时，是否可能是因为没有先下载。

运行：
    d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/example_download_then_get_market_data_ex.py

说明：
- 第一步：不调用 download_history_data，直接读取目标日期行情。
- 第二步：调用 download_history_data 补充目标日期行情。
- 第三步：再次读取目标日期行情，并对比下载前后结果。

如果第一步为空、第三步有数据，说明这次取不到数据就是本地历史行情未下载导致的。
如果第一步已经有数据，说明本地此前已经下载过，不能用这只股票证明“未下载会为空”。
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

STOCK_CODE = "000001.SZ"
PERIOD = "1d"
START_TIME = "20260511"
END_TIME = "20260511"
DIVIDEND_TYPE = "none"
FILL_DATA = False


def print_title(title: str) -> None:
    print("")
    print("=" * 80)
    print(title)
    print("=" * 80)


def get_one_stock_frame() -> Optional[pd.DataFrame]:
    data = xtdata.get_market_data_ex(
        field_list=[],
        stock_list=[STOCK_CODE],
        period=PERIOD,
        start_time=START_TIME,
        end_time=END_TIME,
        count=-1,
        dividend_type=DIVIDEND_TYPE,
        fill_data=FILL_DATA,
    )
    frame = data.get(STOCK_CODE)
    if isinstance(frame, pd.DataFrame):
        return frame
    return None


def print_frame_summary(label: str, frame: Optional[pd.DataFrame]) -> bool:
    print_title(label)
    if frame is None:
        print("返回对象不是 DataFrame 或未返回目标代码。")
        return False

    print("DataFrame 行数:", len(frame))
    print("DataFrame 列:", list(frame.columns))

    if frame.empty:
        print("结果为空。")
        return False

    print(frame.to_string())
    return True


def main() -> int:
    print_title("download_history_data 前后对比示例")
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

        before_frame = get_one_stock_frame()
        before_has_data = print_frame_summary("步骤1：未主动下载，直接 get_market_data_ex", before_frame)

        print_title("步骤2：调用 download_history_data")
        result = xtdata.download_history_data(
            STOCK_CODE,
            period=PERIOD,
            start_time=START_TIME,
            end_time=END_TIME,
            incrementally=True,
        )
        print("download_history_data 返回:", repr(result))

        after_frame = get_one_stock_frame()
        after_has_data = print_frame_summary("步骤3：下载后再次 get_market_data_ex", after_frame)

        print_title("结论")
        if not before_has_data and after_has_data:
            print("结论：本次下载前取不到、下载后取到了，说明问题就是目标历史行情未下载。")
        elif before_has_data and after_has_data:
            print("结论：下载前已经能取到，说明本地此前已有这只股票这段历史行情。")
            print("这只股票不能证明“未下载会为空”，但仍符合官方推荐流程：先下载，再读取。")
        elif not before_has_data and not after_has_data:
            print("结论：下载后仍取不到。可能原因包括：日期不是交易日、代码/市场不对、数据源无该标的该日数据、MiniQMT行情连接异常。")
        else:
            print("结论：下载前有数据、下载后反而没有数据，这不符合预期，请检查 MiniQMT 数据源或参数。")

        return 0

    except Exception as exc:
        print_title("程序异常")
        print("{}: {}".format(type(exc).__name__, str(exc)))
        print(traceback.format_exc())
        print("请确认 MiniQMT 已启动，行情连接正常，并且股票代码、周期、日期参数正确。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
