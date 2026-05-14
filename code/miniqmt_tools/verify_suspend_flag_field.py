#!/usr/bin/env python3
# coding: utf-8
r"""
单独验证行情接口里的 suspendFlag 字段是否可用。

运行：
    d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/verify_suspend_flag_field.py

验证口径：
1. 对正常有行情的样本，确认 get_market_data_ex/get_local_data 返回 suspendFlag 列。
2. 对当前合约状态停牌的样本，确认如果目标日没有行情行，则无法读取 suspendFlag。
3. 同时打印 get_instrument_detail 的 InstrumentStatus，避免把“无行情行”和“字段不可用”混为一谈。
"""

from __future__ import annotations

import sys
import traceback
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
from xtquant import xtdata


TARGET_DATE = "20260511"
PERIOD = "1d"
DIVIDEND_TYPE = "none"

# 002422.SZ 是已验证有 20260511 日线的正常样本。
# 后面几只是当前 get_instrument_detail 显示 InstrumentStatus >= 1 的样本。
SAMPLE_CODES = [
    "002422.SZ",
    "000004.SZ",
    "002731.SZ",
    "600193.SH",
]

FIELDS = ["open", "high", "low", "close", "volume", "amount", "suspendFlag"]


def print_title(title: str) -> None:
    print("")
    print("=" * 80)
    print(title)
    print("=" * 80)


def normalize_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y%m%d")
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y%m%d")
        except Exception:
            pass
    text = str(value).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def row_by_date(frame: pd.DataFrame, date: str) -> Optional[pd.Series]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    for index, row in frame.iterrows():
        if normalize_date(index) == date:
            return row
    return None


def describe_instrument_status(value: Any) -> str:
    try:
        status = int(float(value))
    except Exception:
        return "未知"
    if status == -1:
        return "复牌"
    if status <= 0:
        return "正常交易"
    return "停牌状态/停牌{}天".format(status)


def print_detail(code: str) -> None:
    detail = xtdata.get_instrument_detail(code)
    if not isinstance(detail, dict):
        print("{}: get_instrument_detail 未返回 dict".format(code))
        return

    name = detail.get("InstrumentName") or ""
    status = detail.get("InstrumentStatus")
    is_trading = detail.get("IsTrading")
    print(
        "{} {} | InstrumentStatus={}({}) | IsTrading={}".format(
            code,
            name,
            status,
            describe_instrument_status(status),
            is_trading,
        )
    )


def get_market_data_ex_data(codes: List[str], fill_data: bool) -> Dict[str, pd.DataFrame]:
    return xtdata.get_market_data_ex(
        field_list=FIELDS,
        stock_list=codes,
        period=PERIOD,
        start_time=TARGET_DATE,
        end_time=TARGET_DATE,
        count=-1,
        dividend_type=DIVIDEND_TYPE,
        fill_data=fill_data,
    )


def get_local_data_data(codes: List[str], fill_data: bool) -> Dict[str, pd.DataFrame]:
    return xtdata.get_local_data(
        field_list=FIELDS,
        stock_list=codes,
        period=PERIOD,
        start_time=TARGET_DATE,
        end_time=TARGET_DATE,
        count=-1,
        dividend_type=DIVIDEND_TYPE,
        fill_data=fill_data,
    )


def print_frame_result(api_name: str, code: str, frame: Optional[pd.DataFrame]) -> None:
    if not isinstance(frame, pd.DataFrame):
        print("{} {}: 未返回 DataFrame".format(api_name, code))
        return

    print("{} {}: DataFrame 行数={}, 列={}".format(api_name, code, len(frame), list(frame.columns)))
    row = row_by_date(frame, TARGET_DATE)
    if row is None:
        print("  目标日 {} 没有行情行，因此无法读取该日 suspendFlag。".format(TARGET_DATE))
        return

    if "suspendFlag" not in row.index:
        print("  目标日有行情行，但没有 suspendFlag 列。")
        return

    print(
        "  {} open={} high={} low={} close={} volume={} amount={} suspendFlag={}".format(
            TARGET_DATE,
            row.get("open", ""),
            row.get("high", ""),
            row.get("low", ""),
            row.get("close", ""),
            row.get("volume", ""),
            row.get("amount", ""),
            row.get("suspendFlag", ""),
        )
    )


def run_one_api(api_name: str, data_getter, codes: List[str], fill_data: bool) -> None:
    print_title("{} fill_data={}".format(api_name, fill_data))
    data = data_getter(codes, fill_data)
    print("{} 返回键数量: {}".format(api_name, len(data)))
    for code in codes:
        print_frame_result(api_name, code, data.get(code))


def main() -> int:
    print_title("suspendFlag 字段验证")
    print("Python: {}".format(sys.executable))
    print("股票: {}".format(", ".join(SAMPLE_CODES)))
    print("日期: {}".format(TARGET_DATE))
    print("周期: {}".format(PERIOD))
    print("字段: {}".format(", ".join(FIELDS)))

    try:
        print_title("合约基础状态")
        for code in SAMPLE_CODES:
            print_detail(code)

        for fill_data in (False, True):
            run_one_api("get_market_data_ex", get_market_data_ex_data, SAMPLE_CODES, fill_data)
            run_one_api("get_local_data", get_local_data_data, SAMPLE_CODES, fill_data)

        print_title("结论")
        print("如果正常样本能看到 suspendFlag=0，说明字段和接口链路可用。")
        print("如果停牌状态样本没有目标日行情行，则不是 suspendFlag 字段不可用，而是该日没有可读取的 K 线行。")
        print("缺失股票的停牌解释应看 get_instrument_detail 的 InstrumentStatus 交叉统计。")
        return 0
    except Exception as exc:
        print_title("程序异常")
        print("{}: {}".format(type(exc).__name__, exc))
        print(traceback.format_exc())
        print("排查提示: 请确认 MiniQMT 已启动，行情连接正常。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
