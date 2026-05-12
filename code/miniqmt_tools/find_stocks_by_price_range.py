#!/usr/bin/env python3
# coding: utf-8
r"""
按日期、股票代码前缀和价格区间筛选股票，并验证 MiniQMT 行情接口。

默认场景：
- 日期：2026-05-11
- 股票池：002xxx.SZ
- 价格：当天 low-high 区间与 [33.05, 33.71] 有交集

运行：
    d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/find_stocks_by_price_range.py
"""

from __future__ import annotations

import math
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
from xtquant import xtdata


# =========================
# 可复用参数区
# =========================

TARGET_DATE = "20260511"
CODE_PREFIX = "002"
MARKET_SUFFIX = ".SZ"
PRICE_LOW = 33.05
PRICE_HIGH = 33.71
PRICE_MATCH_MODE = "range_overlap"
DIVIDEND_TYPE = "none"
PERIOD = "1d"
FILL_DATA = False
DOWNLOAD_ALL_BEFORE_QUERY = True
DOWNLOAD_PROGRESS_EVERY = 100

SECTOR_CANDIDATES = [
    "深证A股",
    "深圳A股",
]

SUBSCRIBE_WAIT_SECONDS = 1.0


@dataclass
class ApiStatus:
    name: str
    status: str
    detail: str = ""


def print_title(title: str) -> None:
    print("")
    print("=" * 80)
    print(title)
    print("=" * 80)


def format_exception(exc: BaseException) -> str:
    return "{}: {}".format(type(exc).__name__, str(exc))


def add_status(statuses: List[ApiStatus], name: str, status: str, detail: str = "") -> None:
    statuses.append(ApiStatus(name=name, status=status, detail=detail))


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
    if not text:
        return None

    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]

    return None


def to_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def find_deep_a_sector(sector_list: Iterable[str]) -> str:
    sectors = list(sector_list)
    for candidate in SECTOR_CANDIDATES:
        if candidate in sectors:
            return candidate

    fuzzy_matches = [
        sector
        for sector in sectors
        if ("深" in sector) and ("A股" in sector or "Ａ股" in sector) and ("B股" not in sector)
    ]
    if fuzzy_matches:
        return fuzzy_matches[0]

    raise RuntimeError(
        "未能在板块列表中定位深市A股板块。可检查 xtdata.get_sector_list() 返回值后更新 SECTOR_CANDIDATES。"
    )


def build_stock_pool(statuses: List[ApiStatus]) -> Tuple[str, List[str]]:
    try:
        sectors = xtdata.get_sector_list()
        add_status(statuses, "get_sector_list", "OK", "板块数量: {}".format(len(sectors)))
    except Exception as exc:
        add_status(statuses, "get_sector_list", "ERROR", format_exception(exc))
        raise

    sector_name = find_deep_a_sector(sectors)

    try:
        stocks = xtdata.get_stock_list_in_sector(sector_name)
        add_status(
            statuses,
            "get_stock_list_in_sector",
            "OK",
            "板块: {}, 成分数量: {}".format(sector_name, len(stocks)),
        )
    except Exception as exc:
        add_status(statuses, "get_stock_list_in_sector", "ERROR", format_exception(exc))
        raise

    stock_pool = [
        code
        for code in stocks
        if code.startswith(CODE_PREFIX) and code.endswith(MARKET_SUFFIX)
    ]
    stock_pool = sorted(set(stock_pool))
    return sector_name, stock_pool


def fetch_market_data(codes: List[str], statuses: List[ApiStatus], label: str) -> Dict[str, pd.DataFrame]:
    try:
        data = xtdata.get_market_data_ex(
            field_list=[],
            stock_list=codes,
            period=PERIOD,
            start_time=TARGET_DATE,
            end_time=TARGET_DATE,
            count=-1,
            dividend_type=DIVIDEND_TYPE,
            fill_data=FILL_DATA,
        )
        valid_count = sum(1 for frame in data.values() if isinstance(frame, pd.DataFrame) and not frame.empty)
        add_status(
            statuses,
            "get_market_data_ex",
            "OK",
            "{}: 请求 {}, 返回 {}, 非空 {}".format(label, len(codes), len(data), valid_count),
        )
        return data
    except Exception as exc:
        add_status(statuses, "get_market_data_ex", "ERROR", format_exception(exc))
        raise


def get_date_row(frame: pd.DataFrame, target_date: str) -> Optional[pd.Series]:
    if frame is None or frame.empty:
        return None

    for index, row in frame.iterrows():
        if normalize_date(index) == target_date:
            return row

    if len(frame) == 1:
        row = frame.iloc[0]
        return row

    return None


def split_available_and_missing(
    codes: List[str],
    data: Dict[str, pd.DataFrame],
    target_date: str,
) -> Tuple[Dict[str, pd.Series], List[str]]:
    rows: Dict[str, pd.Series] = {}
    missing: List[str] = []

    for code in codes:
        row = get_date_row(data.get(code), target_date)
        if row is None:
            missing.append(code)
        else:
            rows[code] = row

    return rows, missing


def download_history_batch(codes: List[str], statuses: List[ApiStatus], status_name: str) -> None:
    if not codes:
        add_status(statuses, status_name, "SKIP", "没有标的需要下载")
        return

    ok_count = 0
    failed: List[str] = []
    for index, code in enumerate(codes, start=1):
        try:
            xtdata.download_history_data(
                code,
                period=PERIOD,
                start_time=TARGET_DATE,
                end_time=TARGET_DATE,
                incrementally=True,
            )
            ok_count += 1
        except Exception:
            failed.append(code)

        if DOWNLOAD_PROGRESS_EVERY > 0 and index % DOWNLOAD_PROGRESS_EVERY == 0:
            print("已处理历史行情下载: {}/{}".format(index, len(codes)))

    if failed:
        add_status(
            statuses,
            status_name,
            "PARTIAL",
            "成功 {}, 失败 {}, 失败样例: {}".format(ok_count, len(failed), ", ".join(failed[:10])),
        )
    else:
        add_status(statuses, status_name, "OK", "成功下载/确认 {} 个标的".format(ok_count))


def download_missing_history(codes: List[str], statuses: List[ApiStatus]) -> None:
    download_history_batch(codes, statuses, "download_history_data_missing")


def price_matches(row: pd.Series) -> Tuple[bool, str]:
    low = to_float(row.get("low"))
    high = to_float(row.get("high"))

    if low is None or high is None:
        return False, "缺少 low/high"

    if PRICE_MATCH_MODE == "range_overlap":
        hit = high >= PRICE_LOW and low <= PRICE_HIGH
        reason = "high >= {:.2f} 且 low <= {:.2f}".format(PRICE_LOW, PRICE_HIGH)
        return hit, reason

    raise ValueError("不支持的 PRICE_MATCH_MODE: {}".format(PRICE_MATCH_MODE))


def get_instrument_name(code: str) -> str:
    try:
        detail = xtdata.get_instrument_detail(code, iscomplete=False)
        name = detail.get("InstrumentName") or detail.get("instrument_name") or ""
        return str(name)
    except Exception:
        return ""


def build_hit_table(rows: Dict[str, pd.Series]) -> pd.DataFrame:
    records: List[Dict[str, Any]] = []

    for code, row in rows.items():
        hit, reason = price_matches(row)
        if not hit:
            continue

        records.append(
            {
                "代码": code,
                "名称": get_instrument_name(code),
                "日期": TARGET_DATE,
                "open": to_float(row.get("open")),
                "high": to_float(row.get("high")),
                "low": to_float(row.get("low")),
                "close": to_float(row.get("close")),
                "volume": to_float(row.get("volume")),
                "amount": to_float(row.get("amount")),
                "命中原因": reason,
            }
        )

    if not records:
        return pd.DataFrame(
            columns=["代码", "名称", "日期", "open", "high", "low", "close", "volume", "amount", "命中原因"]
        )

    result = pd.DataFrame(records)
    return result.sort_values(["代码"]).reset_index(drop=True)


def verify_subscribe_quote(sample_code: str, statuses: List[ApiStatus]) -> None:
    try:
        seq = xtdata.subscribe_quote(sample_code, period=PERIOD, count=-1)
        time.sleep(SUBSCRIBE_WAIT_SECONDS)
        data = xtdata.get_market_data_ex(
            field_list=[],
            stock_list=[sample_code],
            period=PERIOD,
            count=-1,
            dividend_type=DIVIDEND_TYPE,
            fill_data=FILL_DATA,
        )
        frame = data.get(sample_code)
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            add_status(
                statuses,
                "subscribe_quote",
                "OK",
                "订阅号: {}, 订阅后 get_market_data_ex 返回 {} 行".format(seq, len(frame)),
            )
        else:
            add_status(
                statuses,
                "subscribe_quote",
                "WARN",
                "订阅号: {}, 但订阅后未拉到非空日线数据".format(seq),
            )
    except Exception as exc:
        add_status(statuses, "subscribe_quote", "ERROR", format_exception(exc))


def print_hit_table(table: pd.DataFrame) -> None:
    print_title("筛选结果")
    print(
        "条件: 日期={}, 股票={}, 价格区间=[{:.2f}, {:.2f}], 口径={}".format(
            TARGET_DATE,
            CODE_PREFIX + "xxx" + MARKET_SUFFIX,
            PRICE_LOW,
            PRICE_HIGH,
            PRICE_MATCH_MODE,
        )
    )
    print("复权口径: {}".format(DIVIDEND_TYPE))
    print("命中数量: {}".format(len(table)))

    if table.empty:
        print("未找到符合条件的股票。")
        return

    display_table = table.copy()
    for column in ["open", "high", "low", "close"]:
        display_table[column] = display_table[column].map(lambda x: "" if x is None else "{:.3f}".format(x))
    for column in ["volume", "amount"]:
        display_table[column] = display_table[column].map(lambda x: "" if x is None else "{:.0f}".format(x))

    print(display_table.to_string(index=False))


def print_status_summary(statuses: List[ApiStatus]) -> None:
    print_title("MiniQMT 接口状态摘要")
    for item in statuses:
        detail = " - {}".format(item.detail) if item.detail else ""
        print("[{}] {}{}".format(item.status, item.name, detail))


def print_failure_hint() -> None:
    print("")
    print("排查提示:")
    print("- 请确认 MiniQMT 已启动，并且行情连接正常。")
    print("- 如果历史行情为空，请在 MiniQMT 中确认对应日期数据已下载，或稍后重试。")
    print("- 如果 subscribe_quote 失败，请确认当前客户端行情服务可用；非交易时段不要求 tick 推送。")


def main() -> int:
    statuses: List[ApiStatus] = []

    print_title("MiniQMT 股票价格区间查询")
    print("Python: {}".format(sys.executable))
    print("pandas: {}".format(pd.__version__))
    print(
        "参数: TARGET_DATE={}, CODE_PREFIX={}, MARKET_SUFFIX={}, PRICE_LOW={}, PRICE_HIGH={}, DIVIDEND_TYPE={}".format(
            TARGET_DATE,
            CODE_PREFIX,
            MARKET_SUFFIX,
            PRICE_LOW,
            PRICE_HIGH,
            DIVIDEND_TYPE,
        )
    )

    try:
        sector_name, stock_pool = build_stock_pool(statuses)
        print("使用板块: {}".format(sector_name))
        print("股票池数量: {}".format(len(stock_pool)))

        if not stock_pool:
            raise RuntimeError("股票池为空，请检查 CODE_PREFIX/MARKET_SUFFIX 或板块名称。")

        if DOWNLOAD_ALL_BEFORE_QUERY:
            print("开始增量补下载目标日期历史行情: {} 个标的".format(len(stock_pool)))
            download_history_batch(stock_pool, statuses, "download_history_data_all")

        data = fetch_market_data(stock_pool, statuses, "首次读取")
        rows, missing = split_available_and_missing(stock_pool, data, TARGET_DATE)

        if missing:
            print("首次读取缺失 {} 个标的，开始尝试补下载后重取。".format(len(missing)))
            download_missing_history(missing, statuses)
            retry_data = fetch_market_data(missing, statuses, "缺失重取")
            retry_rows, still_missing = split_available_and_missing(missing, retry_data, TARGET_DATE)
            rows.update(retry_rows)
            missing = still_missing

        print("有效取到 {} 行 {} 日线行情。".format(len(rows), TARGET_DATE))
        if missing:
            print("仍缺失 {} 个标的，样例: {}".format(len(missing), ", ".join(missing[:10])))

        hit_table = build_hit_table(rows)
        print_hit_table(hit_table)

        sample_for_subscribe = hit_table.iloc[0]["代码"] if not hit_table.empty else stock_pool[0]
        verify_subscribe_quote(str(sample_for_subscribe), statuses)

        print_status_summary(statuses)
        return 0

    except Exception as exc:
        add_status(statuses, "program", "ERROR", format_exception(exc))
        print_title("程序异常")
        print(format_exception(exc))
        print(traceback.format_exc())
        print_status_summary(statuses)
        print_failure_hint()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
