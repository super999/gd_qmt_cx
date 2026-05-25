#!/usr/bin/env python3
# coding: utf-8
r"""
全市场 Local-First 行情缺失检查。

默认检查范围：
- 股票池：A 股全市场（上证A股 + 深证A股）
- 日期：从项目历史基准 20200101 到当前可识别最新交易日
- 周期：1d

运行：
    d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/check_missing_market_data.py

默认命令会做全市场自动体检，不需要手工指定日期或板块。

流程：
1. 先用 get_local_data 读取本地行情，不改变本地数据状态。
2. 统计本地已有完整行情和缺失行情。
3. 如 DOWNLOAD_MISSING_AFTER_LOCAL_CHECK=True，只下载缺失股票。
4. 下载后再次用 get_local_data 复查仍缺失的股票。
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
from xtquant import xtdata


# =========================
# 可复用参数区
# =========================

DEFAULT_HISTORY_START_DATE = "20200101"
CHECK_START_DATE = DEFAULT_HISTORY_START_DATE
CHECK_END_DATE = datetime.now().strftime("%Y%m%d")

# 单日检查默认预期日期就是 CHECK_START_DATE；多日检查时可手工列出交易日。
EXPECTED_DATES: List[str] = []

TARGET_SECTORS = [
    "上证A股",
    "深证A股",
]

# 留空表示不过滤前缀/后缀。
CODE_PREFIXES: List[str] = []
MARKET_SUFFIXES = [".SH", ".SZ"]

PERIOD = "1d"
DIVIDEND_TYPE = "none"

# 官方/本地 xtdata.py 说明：
# fill_data=True 会用前一条数据填补缺失K线：
# open/high/low/close=前一条close，amount/volume=0。
# 缺失检查必须用 False，避免填充数据掩盖真实缺口。
FILL_DATA = False

BATCH_SIZE = 300
DOWNLOAD_PROGRESS_EVERY = 100
INSTRUMENT_DETAIL_PROGRESS_EVERY = 100
INSTRUMENT_DETAIL_SLOW_SECONDS = 2.0

REQUIRED_FIELDS = ["open", "high", "low", "close", "volume", "amount"]
SUSPEND_FIELD = "suspendFlag"
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"

# 查询合约基础信息，用 OpenDate/ExpireDate 辅助解释目标日没有行情的原因。
CHECK_INSTRUMENT_DETAIL = True

OPEN_DATE_SPECIAL_VALUES = {
    "19700101": "新股",
    "19700102": "老股东增发",
    "19700103": "新债",
    "19700104": "可转债",
    "19700105": "配股",
    "19700106": "配号",
}
EXPIRE_DATE_NO_EXPIRE_VALUES = {"0", "99999999"}
PRODUCT_TYPE_LABELS = {
    -1: "默认/普通品种",
    0: "沪深股票期权认购",
    1: "期货/股指期货",
    2: "期权/能源期货",
    3: "组合套利/农业期货",
    4: "即期/金属期货",
    5: "期转现/利率期货",
    6: "期权(IF)/汇率期货",
    7: "数字货币期货",
    99: "自定义合约期货",
    107: "数字货币现货",
    201: "股票",
    202: "GDR",
    203: "ETF",
    204: "ETN",
    300: "其他",
}

# 脚本配置默认值；运行时可用 --download-missing / --check-only 覆盖。
DOWNLOAD_MISSING_AFTER_LOCAL_CHECK = True

# 自动用交易日历生成 EXPECTED_DATES。
CALENDAR_CODE = "510300.SH"
LOCAL_SCAN_START_DATE = "19900101"
DOWNLOAD_CALENDAR_DATA = True
AUTO_CALENDAR = True
CALENDAR_SOURCE = "auto"
MIN_EXPECTED_DATE_BY_CODE: Dict[str, str] = {}
ENABLE_CHECK_CACHE = True
RESET_CHECK_CACHE = False
CHECK_CACHE_PATH = OUTPUT_DIR / "market_data_check_cache.sqlite"
CACHED_OK_DATES_BY_CODE: Dict[str, set] = {}
CACHED_OK_COUNT_BY_CODE: Dict[str, int] = {}
CACHE_SKIPPED_CHECK_COUNT = 0
CONFIRMED_CACHE_STATUSES = ("ok", "missing_after_download")


@dataclass
class CheckStatus:
    name: str
    status: str
    detail: str = ""


def print_title(title: str) -> None:
    print("")
    print("=" * 80)
    print(title)
    print("=" * 80)


def log(message: str) -> None:
    print("[{}] {}".format(datetime.now().strftime("%H:%M:%S"), message), flush=True)


def add_status(statuses: List[CheckStatus], name: str, status: str, detail: str = "") -> None:
    statuses.append(CheckStatus(name=name, status=status, detail=detail))


def format_exception(exc: BaseException) -> str:
    return "{}: {}".format(type(exc).__name__, str(exc))


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


def raw_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"none", "nan", "nat"}:
        return ""
    return text


def raw_date_code(value: Any) -> str:
    text = raw_text(value)
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return digits


def format_yyyymmdd(value: Any) -> str:
    date = normalize_date(value)
    if not date:
        return ""
    return "{}-{}-{}".format(date[:4], date[4:6], date[6:8])


def int_date_or_none(value: Any, special_values: Optional[set] = None) -> Optional[int]:
    raw_date = raw_date_code(value)
    if not raw_date or (special_values and raw_date in special_values):
        return None

    date = normalize_date(value)
    if not date:
        return None
    try:
        datetime.strptime(date, "%Y%m%d")
    except Exception:
        return None
    try:
        date_int = int(date)
    except Exception:
        return None
    if date_int < 19000101:
        return None
    return date_int


def describe_open_date(value: Any) -> str:
    raw_date = raw_date_code(value)
    if not raw_date:
        return "未提供"
    if raw_date in OPEN_DATE_SPECIAL_VALUES:
        return "{}({})".format(raw_date, OPEN_DATE_SPECIAL_VALUES[raw_date])

    date_text = format_yyyymmdd(value)
    if date_text and int_date_or_none(value, OPEN_DATE_SPECIAL_VALUES):
        return date_text
    return "{}(非标准日期/特殊值)".format(raw_date)


def describe_expire_date(value: Any) -> str:
    raw = raw_text(value)
    raw_date = raw_date_code(value)
    if not raw:
        return "未提供"
    if raw in EXPIRE_DATE_NO_EXPIRE_VALUES or raw_date in EXPIRE_DATE_NO_EXPIRE_VALUES:
        return "暂无退市日/到期日"

    date_text = format_yyyymmdd(value)
    if date_text and int_date_or_none(value, EXPIRE_DATE_NO_EXPIRE_VALUES):
        return date_text
    return "{}(非标准日期/特殊值)".format(raw_date or raw)


def describe_instrument_status(value: Any) -> str:
    if raw_text(value) == "":
        return "未提供"
    try:
        status = int(float(value))
    except Exception:
        return "{}(未知状态值)".format(value)
    if status == -1:
        return "复牌"
    if status <= 0:
        return "正常交易"
    return "停牌状态/停牌{}天".format(status)


def describe_is_trading(value: Any) -> str:
    if value is True:
        return "可交易"
    if value is False:
        return "不可交易"
    return "未提供"


def describe_product_type(value: Any) -> str:
    if raw_text(value) == "":
        return "未提供"
    try:
        product_type = int(float(value))
    except Exception:
        return "{}(未知品种类型)".format(value)
    return PRODUCT_TYPE_LABELS.get(product_type, "{}(未收录枚举)".format(product_type))


def is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    try:
        number = float(value)
    except Exception:
        return False
    return math.isnan(number) or math.isinf(number)


def value_or_empty(row: pd.Series, field: str) -> Any:
    if field not in row.index:
        return ""
    value = row.get(field)
    return "" if is_missing_value(value) else value


def suspend_flag_value(row: pd.Series) -> Optional[int]:
    if SUSPEND_FIELD not in row.index:
        return None

    value = row.get(SUSPEND_FIELD)
    if is_missing_value(value):
        return None

    try:
        return int(float(value))
    except Exception:
        return None


def chunked(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def parse_csv_list(value: Optional[str]) -> List[str]:
    if value is None:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MiniQMT 全市场 Local-First 行情缺失检查与缺失补下载。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--calendar-source",
        choices=["auto", "xtdata", "akshare", "tushare", "local"],
        default=CALENDAR_SOURCE,
        help="自动交易日历来源；auto 会按 xtdata、akshare、tushare、本地参考标的顺序尝试。",
    )
    parser.add_argument(
        "--calendar-code",
        default=CALENDAR_CODE,
        help="自动模式下用于生成交易日清单的参考标的。",
    )
    parser.add_argument(
        "--local-scan-start",
        default=LOCAL_SCAN_START_DATE,
        help="自动模式下扫描目标股票本地最后日期的起始日期。",
    )
    parser.add_argument(
        "--no-download-calendar",
        action="store_true",
        help="自动模式下不预先下载参考标的日线，仅使用已有本地参考日历。",
    )
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="get_local_data 分批读取数量。")
    parser.add_argument("--no-cache", action="store_true", help="本次运行不读取/写入本地检查缓存。")
    parser.add_argument("--reset-cache", action="store_true", help="运行前清空本地检查缓存。")

    download_group = parser.add_mutually_exclusive_group()
    download_group.add_argument(
        "--download-missing",
        dest="download_missing",
        action="store_true",
        default=None,
        help="初始本地检查发现缺失后，调用 download_history_data 补下载。",
    )
    download_group.add_argument(
        "--check-only",
        dest="download_missing",
        action="store_false",
        default=None,
        help="只检查本地行情，不下载缺失数据。",
    )

    return parser


def apply_cli_args(args: argparse.Namespace) -> None:
    global CHECK_START_DATE
    global CHECK_END_DATE
    global BATCH_SIZE
    global DOWNLOAD_MISSING_AFTER_LOCAL_CHECK
    global CHECK_INSTRUMENT_DETAIL
    global CALENDAR_CODE
    global LOCAL_SCAN_START_DATE
    global DOWNLOAD_CALENDAR_DATA
    global AUTO_CALENDAR
    global CALENDAR_SOURCE
    global ENABLE_CHECK_CACHE
    global RESET_CHECK_CACHE

    CHECK_START_DATE = DEFAULT_HISTORY_START_DATE
    CHECK_END_DATE = datetime.now().strftime("%Y%m%d")
    AUTO_CALENDAR = True
    CHECK_INSTRUMENT_DETAIL = True
    CALENDAR_SOURCE = str(args.calendar_source).strip().lower()
    CALENDAR_CODE = str(args.calendar_code).strip()
    LOCAL_SCAN_START_DATE = normalize_date(args.local_scan_start) or str(args.local_scan_start)
    DOWNLOAD_CALENDAR_DATA = not bool(args.no_download_calendar)
    if args.batch_size <= 0:
        raise RuntimeError("--batch-size 必须大于 0。")
    BATCH_SIZE = args.batch_size
    ENABLE_CHECK_CACHE = not bool(args.no_cache)
    RESET_CHECK_CACHE = bool(args.reset_cache)
    if args.download_missing is not None:
        DOWNLOAD_MISSING_AFTER_LOCAL_CHECK = args.download_missing


def expected_dates_from_config() -> List[str]:
    if EXPECTED_DATES:
        return sorted(set(EXPECTED_DATES))
    if CHECK_START_DATE == CHECK_END_DATE:
        return [CHECK_START_DATE]
    raise RuntimeError("多日检查请显式填写 EXPECTED_DATES，避免误把自然日当交易日。")


def get_stock_pool(statuses: List[CheckStatus]) -> Tuple[List[str], Dict[str, str]]:
    try:
        sector_list = xtdata.get_sector_list()
        add_status(statuses, "get_sector_list", "OK", "板块数量: {}".format(len(sector_list)))
    except Exception as exc:
        add_status(statuses, "get_sector_list", "ERROR", format_exception(exc))
        raise

    sector_set = set(sector_list)
    missing_sectors = [sector for sector in TARGET_SECTORS if sector not in sector_set]
    if missing_sectors:
        add_status(statuses, "target_sector_check", "WARN", "未找到板块: {}".format(", ".join(missing_sectors)))

    codes: List[str] = []
    code_source: Dict[str, str] = {}
    for sector in TARGET_SECTORS:
        if sector not in sector_set:
            continue

        try:
            sector_codes = xtdata.get_stock_list_in_sector(sector)
            add_status(
                statuses,
                "get_stock_list_in_sector",
                "OK",
                "{}: {} 个合约".format(sector, len(sector_codes)),
            )
        except Exception as exc:
            add_status(statuses, "get_stock_list_in_sector", "ERROR", "{}: {}".format(sector, format_exception(exc)))
            raise

        for code in sector_codes:
            if CODE_PREFIXES and not any(code.startswith(prefix) for prefix in CODE_PREFIXES):
                continue
            if MARKET_SUFFIXES and not any(code.endswith(suffix) for suffix in MARKET_SUFFIXES):
                continue
            codes.append(code)
            code_source.setdefault(code, sector)

    return sorted(set(codes)), code_source


def fetch_instrument_details(codes: List[str], statuses: List[CheckStatus]) -> Dict[str, Dict[str, Any]]:
    if not CHECK_INSTRUMENT_DETAIL:
        add_status(statuses, "get_instrument_detail", "SKIP", "CHECK_INSTRUMENT_DETAIL=False")
        return {}

    log("开始读取合约基础信息 get_instrument_detail，总数 {}".format(len(codes)))
    details: Dict[str, Dict[str, Any]] = {}
    failed: List[str] = []
    start_time = time.perf_counter()
    for index, code in enumerate(codes, start=1):
        if index == 1 or index % INSTRUMENT_DETAIL_PROGRESS_EVERY == 0 or index > max(len(codes) - 30, 0):
            log("准备读取合约基础信息: {}/{} code={}".format(index, len(codes), code))

        call_start = time.perf_counter()
        try:
            detail = xtdata.get_instrument_detail(code)
            if isinstance(detail, dict):
                details[code] = detail
            else:
                failed.append(code)
        except Exception:
            failed.append(code)

        elapsed = time.perf_counter() - call_start
        if elapsed >= INSTRUMENT_DETAIL_SLOW_SECONDS:
            log("get_instrument_detail 慢调用: {} 用时 {:.2f}s，进度 {}/{}".format(code, elapsed, index, len(codes)))

        if INSTRUMENT_DETAIL_PROGRESS_EVERY > 0 and index % INSTRUMENT_DETAIL_PROGRESS_EVERY == 0:
            total_elapsed = time.perf_counter() - start_time
            log(
                "读取合约基础信息: {}/{}，成功 {}，失败 {}，累计 {:.1f}s".format(
                    index,
                    len(codes),
                    len(details),
                    len(failed),
                    total_elapsed,
                )
            )

    log(
        "完成读取合约基础信息: 成功 {}，失败 {}，总用时 {:.1f}s".format(
            len(details),
            len(failed),
            time.perf_counter() - start_time,
        )
    )

    if failed:
        add_status(
            statuses,
            "get_instrument_detail",
            "PARTIAL",
            "成功 {}, 失败 {}, 失败样例: {}".format(len(details), len(failed), ", ".join(failed[:10])),
        )
    else:
        add_status(statuses, "get_instrument_detail", "OK", "成功读取 {} 个标的".format(len(details)))

    return details


def fetch_local_batch(codes: List[str], statuses: List[CheckStatus], label: str) -> Dict[str, pd.DataFrame]:
    try:
        data = xtdata.get_local_data(
            field_list=[],
            stock_list=codes,
            period=PERIOD,
            start_time=CHECK_START_DATE,
            end_time=CHECK_END_DATE,
            count=-1,
            dividend_type=DIVIDEND_TYPE,
            fill_data=FILL_DATA,
        )
        add_status(
            statuses,
            "get_local_data",
            "OK",
            "{}: 请求 {} 个标的，返回 {} 个键".format(label, len(codes), len(data)),
        )
        return data
    except Exception as exc:
        add_status(statuses, "get_local_data", "ERROR", "{}: {}".format(label, format_exception(exc)))
        raise


def fetch_local_data(codes: List[str], statuses: List[CheckStatus], label: str) -> Dict[str, pd.DataFrame]:
    all_data: Dict[str, pd.DataFrame] = {}
    total_batches = math.ceil(len(codes) / BATCH_SIZE) if codes else 0
    start_time = time.perf_counter()
    log("{}开始读取本地行情，总标的 {}，批次数 {}".format(label, len(codes), total_batches))
    for batch_index, batch_codes in enumerate(chunked(codes, BATCH_SIZE), start=1):
        log("{}读取本地行情批次 {}/{}: {} 个标的".format(label, batch_index, total_batches, len(batch_codes)))
        all_data.update(fetch_local_batch(batch_codes, statuses, label))
    log("{}完成读取本地行情，返回 {} 个键，总用时 {:.1f}s".format(label, len(all_data), time.perf_counter() - start_time))
    return all_data


def fetch_local_range(
    codes: List[str],
    start_date: str,
    end_date: str,
    statuses: List[CheckStatus],
    label: str,
) -> Dict[str, pd.DataFrame]:
    try:
        data = xtdata.get_local_data(
            field_list=[],
            stock_list=codes,
            period=PERIOD,
            start_time=start_date,
            end_time=end_date,
            count=-1,
            dividend_type=DIVIDEND_TYPE,
            fill_data=FILL_DATA,
        )
        add_status(
            statuses,
            "get_local_data",
            "OK",
            "{}: {} 至 {}, 请求 {} 个标的，返回 {} 个键".format(label, start_date, end_date, len(codes), len(data)),
        )
        return data
    except Exception as exc:
        add_status(statuses, "get_local_data", "ERROR", "{}: {}".format(label, format_exception(exc)))
        raise


def frame_dates(frame: Optional[pd.DataFrame]) -> List[str]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    dates = [date for date in (normalize_date(index) for index in frame.index) if date]
    return sorted(set(dates))


def frame_date_position_map(frame: Optional[pd.DataFrame]) -> Dict[str, int]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    mapping: Dict[str, int] = {}
    for pos, index in enumerate(frame.index):
        date = normalize_date(index)
        if date:
            mapping[date] = pos
    return mapping


def next_yyyymmdd(date: str) -> str:
    return (pd.Timestamp(datetime.strptime(date, "%Y%m%d")) + pd.Timedelta(days=1)).strftime("%Y%m%d")


def calendar_from_xtdata(start_date: str, end_date: str, statuses: List[CheckStatus]) -> List[str]:
    dates: List[str] = []
    errors: List[str] = []
    for market in ["SH", "SZ"]:
        try:
            raw_dates = xtdata.get_trading_calendar(market, start_date, end_date)
            market_dates = [date for date in (normalize_date(item) for item in raw_dates) if date]
            dates.extend(market_dates)
            add_status(statuses, "get_trading_calendar", "OK", "{}: {} 个交易日".format(market, len(market_dates)))
        except Exception as exc:
            errors.append("{}: {}".format(market, format_exception(exc)))
    if dates:
        return sorted(set(dates))
    add_status(statuses, "get_trading_calendar", "WARN", "; ".join(errors[:2]))
    return []


def calendar_from_akshare(start_date: str, end_date: str, statuses: List[CheckStatus]) -> List[str]:
    try:
        import akshare as ak  # type: ignore
    except Exception as exc:
        add_status(statuses, "akshare_calendar", "SKIP", "未安装或不可导入: {}".format(format_exception(exc)))
        return []

    try:
        frame = ak.tool_trade_date_hist_sina()
        if "trade_date" not in frame.columns:
            add_status(statuses, "akshare_calendar", "WARN", "返回结果缺少 trade_date 字段")
            return []
        dates = [date for date in (normalize_date(item) for item in frame["trade_date"]) if date]
        filtered = [date for date in dates if start_date <= date <= end_date]
        add_status(statuses, "akshare_calendar", "OK", "{} 个交易日".format(len(filtered)))
        return sorted(set(filtered))
    except Exception as exc:
        add_status(statuses, "akshare_calendar", "WARN", format_exception(exc))
        return []


def calendar_from_tushare(start_date: str, end_date: str, statuses: List[CheckStatus]) -> List[str]:
    try:
        import os
        import tushare as ts  # type: ignore
    except Exception as exc:
        add_status(statuses, "tushare_calendar", "SKIP", "未安装或不可导入: {}".format(format_exception(exc)))
        return []

    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        add_status(statuses, "tushare_calendar", "SKIP", "未设置 TUSHARE_TOKEN 环境变量")
        return []

    try:
        ts.set_token(token)
        pro = ts.pro_api()
        frame = pro.trade_cal(exchange="", start_date=start_date, end_date=end_date, is_open="1")
        if "cal_date" not in frame.columns:
            add_status(statuses, "tushare_calendar", "WARN", "返回结果缺少 cal_date 字段")
            return []
        dates = [date for date in (normalize_date(item) for item in frame["cal_date"]) if date]
        add_status(statuses, "tushare_calendar", "OK", "{} 个交易日".format(len(dates)))
        return sorted(set(dates))
    except Exception as exc:
        add_status(statuses, "tushare_calendar", "WARN", format_exception(exc))
        return []


def calendar_from_local_reference(start_date: str, end_date: str, statuses: List[CheckStatus]) -> List[str]:
    if DOWNLOAD_CALENDAR_DATA:
        try:
            xtdata.download_history_data(
                CALENDAR_CODE,
                period=PERIOD,
                start_time=start_date,
                end_time=end_date,
                incrementally=True,
            )
            add_status(statuses, "calendar_reference_download", "OK", "{} {} 至 {}".format(CALENDAR_CODE, start_date, end_date))
        except Exception as exc:
            add_status(statuses, "calendar_reference_download", "WARN", format_exception(exc))

    data = fetch_local_range([CALENDAR_CODE], start_date, end_date, statuses, "参考交易日")
    dates = [date for date in frame_dates(data.get(CALENDAR_CODE)) if start_date <= date <= end_date]
    if dates:
        add_status(statuses, "local_reference_calendar", "OK", "{}: {} 个交易日".format(CALENDAR_CODE, len(dates)))
    else:
        add_status(statuses, "local_reference_calendar", "WARN", "{} 无可用本地日线".format(CALENDAR_CODE))
    return dates


def resolve_trading_dates(start_date: str, end_date: str, statuses: List[CheckStatus]) -> List[str]:
    sources = [CALENDAR_SOURCE]
    if CALENDAR_SOURCE == "auto":
        sources = ["xtdata", "akshare", "tushare", "local"]

    for source in sources:
        log("尝试生成交易日历: source={}，{} 至 {}".format(source, start_date, end_date))
        if source == "xtdata":
            dates = calendar_from_xtdata(start_date, end_date, statuses)
        elif source == "akshare":
            dates = calendar_from_akshare(start_date, end_date, statuses)
        elif source == "tushare":
            dates = calendar_from_tushare(start_date, end_date, statuses)
        elif source == "local":
            dates = calendar_from_local_reference(start_date, end_date, statuses)
        else:
            dates = []

        dates = sorted(set(date for date in dates if start_date <= date <= end_date))
        if dates:
            log("交易日历生成成功: source={}，交易日 {} 个，首日 {}，末日 {}".format(source, len(dates), dates[0], dates[-1]))
            add_status(statuses, "trading_calendar", "OK", "{} 来源生成 {} 个交易日".format(source, len(dates)))
            return dates
        log("交易日历来源不可用或为空: source={}".format(source))

    raise RuntimeError("无法生成交易日清单：MiniQMT/akshare/tushare/本地参考标的均不可用。")


def apply_auto_expected_dates(codes: List[str], statuses: List[CheckStatus]) -> List[str]:
    global EXPECTED_DATES

    EXPECTED_DATES = resolve_trading_dates(CHECK_START_DATE, CHECK_END_DATE, statuses)
    return EXPECTED_DATES


def apply_listing_date_floor(
    codes: List[str],
    details_by_code: Dict[str, Dict[str, Any]],
    expected_dates: List[str],
) -> None:
    if not expected_dates:
        return

    log("开始按 OpenDate 计算每只股票实际检查起点")
    first_expected_date = expected_dates[0]
    adjusted_count = 0
    for code in codes:
        detail = details_by_code.get(code)
        if not isinstance(detail, dict):
            continue
        open_date = int_date_or_none(detail.get("OpenDate"), set(OPEN_DATE_SPECIAL_VALUES))
        if not open_date:
            continue

        open_date_text = str(open_date)
        if open_date_text > first_expected_date:
            current_floor = MIN_EXPECTED_DATE_BY_CODE.get(code)
            if not current_floor or open_date_text > current_floor:
                MIN_EXPECTED_DATE_BY_CODE[code] = open_date_text
                adjusted_count += 1
    log("完成 OpenDate 起点处理: 调整 {} 只股票".format(adjusted_count))


def expected_dates_for_code(code: str, expected_dates: List[str]) -> List[str]:
    min_date = MIN_EXPECTED_DATE_BY_CODE.get(code)
    if not min_date:
        return expected_dates
    return [date for date in expected_dates if date >= min_date]


def init_check_cache(statuses: List[CheckStatus]) -> None:
    if not ENABLE_CHECK_CACHE:
        add_status(statuses, "check_cache", "SKIP", "--no-cache")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log("初始化检查缓存: {}".format(CHECK_CACHE_PATH))
    with sqlite3.connect(CHECK_CACHE_PATH) as conn:
        if RESET_CHECK_CACHE:
            log("清空检查缓存表")
            conn.execute("DROP TABLE IF EXISTS checked_daily_data")
            add_status(statuses, "check_cache", "OK", "已清空缓存: {}".format(CHECK_CACHE_PATH))

        log("确保检查缓存表存在")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checked_daily_data (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                period TEXT NOT NULL,
                dividend_type TEXT NOT NULL,
                status TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (code, trade_date, period, dividend_type)
            )
            """
        )
        log("确保检查缓存索引 idx_checked_daily_status_date 存在")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checked_daily_status_date "
            "ON checked_daily_data(status, trade_date)"
        )
        log("确保检查缓存索引 idx_checked_daily_code_status_period_date 存在")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checked_daily_code_status_period_date "
            "ON checked_daily_data(code, status, period, dividend_type, trade_date)"
        )
    log("检查缓存初始化完成")
    add_status(statuses, "check_cache", "OK", "缓存路径: {}".format(CHECK_CACHE_PATH))


def load_cached_ok_counts(codes: List[str], expected_dates: List[str], statuses: List[CheckStatus]) -> None:
    global CACHED_OK_COUNT_BY_CODE

    CACHED_OK_COUNT_BY_CODE = {}
    if not ENABLE_CHECK_CACHE or not codes or not expected_dates or not CHECK_CACHE_PATH.exists():
        return

    min_date = min(expected_dates)
    max_date = max(expected_dates)
    total_rows = 0
    total_batches = math.ceil(len(codes) / 500)
    log("开始读取检查缓存计数，缓存文件 {} MB，批次数 {}".format(round(CHECK_CACHE_PATH.stat().st_size / 1024 / 1024, 1), total_batches))
    with sqlite3.connect(CHECK_CACHE_PATH) as conn:
        for batch_index, batch_codes in enumerate(chunked(codes, 500), start=1):
            log("读取检查缓存计数批次 {}/{}: {} 个标的".format(batch_index, total_batches, len(batch_codes)))
            placeholders = ",".join("?" for _ in batch_codes)
            status_placeholders = ",".join("?" for _ in CONFIRMED_CACHE_STATUSES)
            params = (
                list(CONFIRMED_CACHE_STATUSES)
                + [PERIOD, DIVIDEND_TYPE, min_date, max_date]
                + batch_codes
            )
            rows = conn.execute(
                """
                SELECT code, COUNT(*)
                FROM checked_daily_data
                WHERE status IN ({})
                  AND period = ?
                  AND dividend_type = ?
                  AND trade_date BETWEEN ? AND ?
                  AND code IN ({})
                GROUP BY code
                """.format(status_placeholders, placeholders),
                params,
            ).fetchall()
            for code, count in rows:
                count_int = int(count)
                CACHED_OK_COUNT_BY_CODE[str(code)] = count_int
                total_rows += count_int

    log("完成读取检查缓存计数: 已确认 code/date {}".format(total_rows))
    add_status(statuses, "check_cache", "OK", "命中已确认 code/date 计数: {}".format(total_rows))


def load_cached_ok_dates(codes: List[str], expected_dates: List[str], statuses: List[CheckStatus]) -> None:
    global CACHED_OK_DATES_BY_CODE

    CACHED_OK_DATES_BY_CODE = {}
    if not ENABLE_CHECK_CACHE or not codes or not expected_dates or not CHECK_CACHE_PATH.exists():
        return

    min_date = min(expected_dates)
    max_date = max(expected_dates)
    total_rows = 0
    total_batches = math.ceil(len(codes) / 500)
    log("开始读取本轮需检查股票的缓存明细日期，股票 {}，批次数 {}".format(len(codes), total_batches))
    with sqlite3.connect(CHECK_CACHE_PATH) as conn:
        for batch_index, batch_codes in enumerate(chunked(codes, 500), start=1):
            log("读取缓存明细日期批次 {}/{}: {} 个标的".format(batch_index, total_batches, len(batch_codes)))
            placeholders = ",".join("?" for _ in batch_codes)
            status_placeholders = ",".join("?" for _ in CONFIRMED_CACHE_STATUSES)
            params = (
                list(CONFIRMED_CACHE_STATUSES)
                + [PERIOD, DIVIDEND_TYPE, min_date, max_date]
                + batch_codes
            )
            rows = conn.execute(
                """
                SELECT code, trade_date
                FROM checked_daily_data
                WHERE status IN ({})
                  AND period = ?
                  AND dividend_type = ?
                  AND trade_date BETWEEN ? AND ?
                  AND code IN ({})
                """.format(status_placeholders, placeholders),
                params,
            ).fetchall()
            total_rows += len(rows)
            for code, trade_date in rows:
                CACHED_OK_DATES_BY_CODE.setdefault(str(code), set()).add(str(trade_date))

    log("完成读取缓存明细日期: {}".format(total_rows))
    add_status(statuses, "check_cache", "OK", "命中已确认 code/date 明细: {}".format(total_rows))


def filter_codes_with_unchecked_dates(
    codes: List[str],
    expected_dates: List[str],
    statuses: List[CheckStatus],
) -> List[str]:
    global CACHE_SKIPPED_CHECK_COUNT

    CACHE_SKIPPED_CHECK_COUNT = 0
    active_codes: List[str] = []
    total_pairs = 0
    log("开始计算缓存命中，股票 {}，交易日 {}".format(len(codes), len(expected_dates)))
    for code in codes:
        code_dates = expected_dates_for_code(code, expected_dates)
        total_pairs += len(code_dates)
        cached_count = min(CACHED_OK_COUNT_BY_CODE.get(code, 0), len(code_dates))
        CACHE_SKIPPED_CHECK_COUNT += cached_count
        if cached_count < len(code_dates):
            active_codes.append(code)

    add_status(
        statuses,
        "check_cache",
        "OK",
        "应检查 code/date {}，缓存跳过 {}，本轮需检查股票 {}".format(
            total_pairs,
            CACHE_SKIPPED_CHECK_COUNT,
            len(active_codes),
        ),
    )
    log(
        "完成缓存命中计算: 应检查 code/date {}，缓存跳过 {}，本轮需检查股票 {}".format(
            total_pairs,
            CACHE_SKIPPED_CHECK_COUNT,
            len(active_codes),
        )
    )
    return active_codes


def record_successful_checks(
    codes: List[str],
    data_by_code: Dict[str, pd.DataFrame],
    expected_dates: List[str],
    missing_records: List[Dict[str, Any]],
    statuses: List[CheckStatus],
    source: str,
) -> None:
    if not ENABLE_CHECK_CACHE or not codes or not expected_dates:
        return

    missing_keys = {(record["code"], record["date"]) for record in missing_records}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: List[Tuple[str, str, str, str, str, str, str]] = []
    for index, code in enumerate(codes, start=1):
        if index % 500 == 0:
            print("{} 缓存写入准备进度: {}/{}".format(source, index, len(codes)))

        frame = data_by_code.get(code)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        cached_dates = CACHED_OK_DATES_BY_CODE.get(code, set())
        date_pos = frame_date_position_map(frame)
        for date in expected_dates_for_code(code, expected_dates):
            if date in cached_dates or (code, date) in missing_keys:
                continue
            pos = date_pos.get(date)
            if pos is None:
                continue
            row = frame.iloc[pos]
            missing_fields = [field for field in REQUIRED_FIELDS if field not in row.index or is_missing_value(row.get(field))]
            if missing_fields:
                continue
            rows.append((code, date, PERIOD, DIVIDEND_TYPE, "ok", now, source))

    if not rows:
        add_status(statuses, "check_cache", "SKIP", "{} 无新增成功检查记录".format(source))
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(CHECK_CACHE_PATH) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO checked_daily_data
                (code, trade_date, period, dividend_type, status, checked_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    add_status(statuses, "check_cache", "OK", "{} 写入成功检查记录 {} 条".format(source, len(rows)))


def record_persistent_missing_checks(
    missing_records: List[Dict[str, Any]],
    statuses: List[CheckStatus],
    source: str,
) -> None:
    if not ENABLE_CHECK_CACHE or not missing_records:
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        (
            record["code"],
            record["date"],
            PERIOD,
            DIVIDEND_TYPE,
            "missing_after_download",
            now,
            source,
        )
        for record in missing_records
    ]

    with sqlite3.connect(CHECK_CACHE_PATH) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO checked_daily_data
                (code, trade_date, period, dividend_type, status, checked_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    add_status(statuses, "check_cache", "OK", "{} 写入下载后仍缺失记录 {} 条".format(source, len(rows)))


def row_by_date(frame: pd.DataFrame, date: str) -> Optional[pd.Series]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    for index, row in frame.iterrows():
        if normalize_date(index) == date:
            return row
    return None


def collect_missing_records(
    codes: List[str],
    code_source: Dict[str, str],
    data_by_code: Dict[str, pd.DataFrame],
    expected_dates: List[str],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []

    for index, code in enumerate(codes, start=1):
        if index % 500 == 0:
            print("缺失比对进度: {}/{}".format(index, len(codes)))

        frame = data_by_code.get(code)
        code_expected_dates = expected_dates_for_code(code, expected_dates)
        cached_dates = CACHED_OK_DATES_BY_CODE.get(code, set())
        if cached_dates:
            code_expected_dates = [date for date in code_expected_dates if date not in cached_dates]
        if not code_expected_dates:
            continue

        if not isinstance(frame, pd.DataFrame) or frame.empty:
            for date in code_expected_dates:
                records.append(
                    {
                        "code": code,
                        "sector": code_source.get(code, ""),
                        "date": date,
                        "missing_type": "missing_row",
                        "missing_fields": ",".join(REQUIRED_FIELDS),
                        "detail": "无本地行情行",
                    }
                )
            continue

        date_pos = frame_date_position_map(frame)
        available_dates = set(date_pos)
        for date in code_expected_dates:
            if date not in available_dates:
                records.append(
                    {
                        "code": code,
                        "sector": code_source.get(code, ""),
                        "date": date,
                        "missing_type": "missing_row",
                        "missing_fields": ",".join(REQUIRED_FIELDS),
                        "detail": "缺少指定日期行情行",
                    }
                )
                continue

            row = frame.iloc[date_pos[date]]
            missing_fields = [field for field in REQUIRED_FIELDS if field not in row.index or is_missing_value(row.get(field))]
            if missing_fields:
                records.append(
                    {
                        "code": code,
                        "sector": code_source.get(code, ""),
                        "date": date,
                        "missing_type": "missing_fields",
                        "missing_fields": ",".join(missing_fields),
                        "detail": "行情行存在但关键字段缺失",
                    }
                )

    return records


def collect_instrument_status_records(
    codes: List[str],
    code_source: Dict[str, str],
    details_by_code: Dict[str, Dict[str, Any]],
    expected_dates: List[str],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not details_by_code:
        return records

    target_date_int = int(expected_dates[0])
    for code in codes:
        detail = details_by_code.get(code)
        if not isinstance(detail, dict):
            continue

        open_date_raw = raw_text(detail.get("OpenDate"))
        expire_date_raw = raw_text(detail.get("ExpireDate"))
        product_type = detail.get("ProductType")
        open_date = int_date_or_none(detail.get("OpenDate"), set(OPEN_DATE_SPECIAL_VALUES))
        expire_date = int_date_or_none(detail.get("ExpireDate"), EXPIRE_DATE_NO_EXPIRE_VALUES)
        instrument_status = detail.get("InstrumentStatus")
        is_trading = detail.get("IsTrading")
        name = detail.get("InstrumentName") or ""

        status_type = ""
        status_label = ""
        status_detail = ""
        if open_date and open_date > target_date_int:
            status_type = "not_listed_on_target_date"
            status_label = "目标日尚未上市"
            status_detail = "上市日期 {} 晚于目标日期 {}".format(
                describe_open_date(detail.get("OpenDate")),
                format_yyyymmdd(expected_dates[0]) or expected_dates[0],
            )
        elif expire_date and expire_date not in (0, 99999999) and expire_date <= target_date_int:
            status_type = "delisted_or_expired_on_target_date"
            status_label = "目标日已退市/到期"
            status_detail = "退市/到期日 {} 早于或等于目标日期 {}".format(
                describe_expire_date(detail.get("ExpireDate")),
                format_yyyymmdd(expected_dates[0]) or expected_dates[0],
            )
        else:
            try:
                instrument_status_number = int(float(instrument_status))
            except Exception:
                instrument_status_number = 0
            if instrument_status_number >= 1:
                status_type = "current_instrument_suspended"
                status_label = "当前合约停牌状态"
                status_detail = "InstrumentStatus={}: {}".format(
                    raw_text(instrument_status),
                    describe_instrument_status(instrument_status),
                )

        if not status_type:
            continue

        records.append(
            {
                "code": code,
                "name": name,
                "sector": code_source.get(code, ""),
                "target_date": expected_dates[0],
                "status_type": status_type,
                "status_label": status_label,
                "OpenDate": open_date_raw,
                "OpenDateText": describe_open_date(detail.get("OpenDate")),
                "ExpireDate": expire_date_raw,
                "ExpireDateText": describe_expire_date(detail.get("ExpireDate")),
                "ProductType": raw_text(product_type),
                "ProductTypeText": describe_product_type(product_type),
                "InstrumentStatus": instrument_status if instrument_status is not None else "",
                "InstrumentStatusText": describe_instrument_status(instrument_status),
                "IsTrading": is_trading if is_trading is not None else "",
                "IsTradingText": describe_is_trading(is_trading),
                "detail": status_detail,
            }
        )

    return records


def collect_suspended_records(
    codes: List[str],
    code_source: Dict[str, str],
    data_by_code: Dict[str, pd.DataFrame],
    expected_dates: List[str],
    missing_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    missing_keys = {(record["code"], record["date"]) for record in missing_records}

    for index, code in enumerate(codes, start=1):
        if index % 500 == 0:
            print("停牌标记比对进度: {}/{}".format(index, len(codes)))

        frame = data_by_code.get(code)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        cached_dates = CACHED_OK_DATES_BY_CODE.get(code, set())
        code_expected_dates = expected_dates_for_code(code, expected_dates)
        if cached_dates:
            code_expected_dates = [date for date in code_expected_dates if date not in cached_dates]
        if not code_expected_dates:
            continue
        date_pos = frame_date_position_map(frame)

        for date in code_expected_dates:
            if (code, date) in missing_keys:
                continue

            pos = date_pos.get(date)
            if pos is None:
                continue
            row = frame.iloc[pos]

            flag = suspend_flag_value(row)
            if flag is None or flag == 0:
                continue

            records.append(
                {
                    "code": code,
                    "sector": code_source.get(code, ""),
                    "date": date,
                    "suspendFlag": flag,
                    "open": value_or_empty(row, "open"),
                    "high": value_or_empty(row, "high"),
                    "low": value_or_empty(row, "low"),
                    "close": value_or_empty(row, "close"),
                    "volume": value_or_empty(row, "volume"),
                    "amount": value_or_empty(row, "amount"),
                    "detail": "行情行存在，suspendFlag 非 0",
                }
            )

    return records


def summarize(
    codes: List[str],
    missing_records: List[Dict[str, Any]],
    suspended_records: List[Dict[str, Any]],
    instrument_status_records: List[Dict[str, Any]],
    expected_dates: List[str],
) -> Dict[str, Any]:
    missing_codes = {record["code"] for record in missing_records}
    suspended_codes = {record["code"] for record in suspended_records}
    not_listed_codes = {
        record["code"] for record in instrument_status_records if record["status_type"] == "not_listed_on_target_date"
    }
    delisted_codes = {
        record["code"] for record in instrument_status_records if record["status_type"] == "delisted_or_expired_on_target_date"
    }
    instrument_suspended_codes = {
        record["code"] for record in instrument_status_records if record["status_type"] == "current_instrument_suspended"
    }
    instrument_status_codes = {record["code"] for record in instrument_status_records}
    missing_with_not_listed_codes = missing_codes & not_listed_codes
    missing_with_delisted_codes = missing_codes & delisted_codes
    missing_with_instrument_suspended_codes = missing_codes & instrument_suspended_codes
    missing_without_instrument_status_codes = missing_codes - instrument_status_codes
    available_code_count = len(codes) - len(missing_codes)
    normal_code_count = max(available_code_count - len(suspended_codes), 0)
    coverage_rate = available_code_count / len(codes) if codes else 0.0
    normal_rate = normal_code_count / len(codes) if codes else 0.0
    return {
        "stock_count": len(codes),
        "expected_dates": expected_dates,
        "missing_record_count": len(missing_records),
        "missing_code_count": len(missing_codes),
        "suspended_record_count": len(suspended_records),
        "suspended_code_count": len(suspended_codes),
        "instrument_status_record_count": len(instrument_status_records),
        "not_listed_code_count": len(not_listed_codes),
        "delisted_code_count": len(delisted_codes),
        "instrument_suspended_code_count": len(instrument_suspended_codes),
        "missing_with_instrument_status_count": len(missing_codes & instrument_status_codes),
        "missing_with_not_listed_count": len(missing_with_not_listed_codes),
        "missing_with_delisted_count": len(missing_with_delisted_codes),
        "missing_with_instrument_suspended_count": len(missing_with_instrument_suspended_codes),
        "missing_without_instrument_status_count": len(missing_without_instrument_status_codes),
        "normal_code_count": normal_code_count,
        "available_code_count": available_code_count,
        "coverage_rate": coverage_rate,
        "normal_rate": normal_rate,
    }


def download_missing_codes(records: List[Dict[str, Any]], statuses: List[CheckStatus]) -> List[str]:
    missing_codes = sorted({record["code"] for record in records})
    if not missing_codes:
        add_status(statuses, "download_history_data", "SKIP", "没有缺失股票需要下载")
        return []

    if not DOWNLOAD_MISSING_AFTER_LOCAL_CHECK:
        add_status(
            statuses,
            "download_history_data",
            "SKIP",
            "DOWNLOAD_MISSING_AFTER_LOCAL_CHECK=False，仅检查本地现状",
        )
        return missing_codes

    ok_count = 0
    failed: List[str] = []
    for index, code in enumerate(missing_codes, start=1):
        try:
            xtdata.download_history_data(
                code,
                period=PERIOD,
                start_time=CHECK_START_DATE,
                end_time=CHECK_END_DATE,
                incrementally=True,
            )
            ok_count += 1
        except Exception:
            failed.append(code)

        if DOWNLOAD_PROGRESS_EVERY > 0 and index % DOWNLOAD_PROGRESS_EVERY == 0:
            print("已处理缺失股票下载: {}/{}".format(index, len(missing_codes)))

    if failed:
        add_status(
            statuses,
            "download_history_data",
            "PARTIAL",
            "缺失股票下载成功 {}, 失败 {}, 失败样例: {}".format(ok_count, len(failed), ", ".join(failed[:10])),
        )
    else:
        add_status(statuses, "download_history_data", "OK", "缺失股票下载成功处理 {} 个标的".format(ok_count))

    return missing_codes


def write_records_csv(records: List[Dict[str, Any]], prefix: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / "{}_{}.csv".format(prefix, timestamp)
    df = pd.DataFrame(
        records,
        columns=["code", "sector", "date", "missing_type", "missing_fields", "detail"],
    )
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path


def write_suspended_csv(records: List[Dict[str, Any]], prefix: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / "{}_{}.csv".format(prefix, timestamp)
    df = pd.DataFrame(
        records,
        columns=[
            "code",
            "sector",
            "date",
            "suspendFlag",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "detail",
        ],
    )
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path


def write_instrument_status_csv(records: List[Dict[str, Any]], prefix: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / "{}_{}.csv".format(prefix, timestamp)
    df = pd.DataFrame(
        records,
        columns=[
            "code",
            "name",
            "sector",
            "target_date",
            "status_type",
            "status_label",
            "OpenDate",
            "OpenDateText",
            "ExpireDate",
            "ExpireDateText",
            "ProductType",
            "ProductTypeText",
            "InstrumentStatus",
            "InstrumentStatusText",
            "IsTrading",
            "IsTradingText",
            "detail",
        ],
    )
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path


def write_summary_report(
    statuses: List[CheckStatus],
    initial_summary: Dict[str, Any],
    initial_missing_csv: Path,
    initial_suspended_csv: Path,
    instrument_status_csv: Path,
    after_download_summary: Optional[Dict[str, Any]] = None,
    after_download_missing_csv: Optional[Path] = None,
    after_download_suspended_csv: Optional[Path] = None,
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = OUTPUT_DIR / "market_data_check_summary_{}.md".format(timestamp)

    lines = [
        "# MiniQMT 全市场本地行情缺失检查摘要",
        "",
        "- 检查时间：{}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "- 日期范围：{} 至 {}".format(CHECK_START_DATE, CHECK_END_DATE),
        "- 股票池：A股全市场（上证A股 + 深证A股）",
        "- 自动交易日历：{}".format(AUTO_CALENDAR),
        "- 交易日历来源：{}".format(CALENDAR_SOURCE),
        "- 检查缓存：{}".format("启用，路径 {}".format(CHECK_CACHE_PATH) if ENABLE_CHECK_CACHE else "关闭"),
        "- 缓存跳过 code/date：{}".format(CACHE_SKIPPED_CHECK_COUNT),
        "- 周期：{}".format(PERIOD),
        "- 复权：{}".format(DIVIDEND_TYPE),
        "- fill_data：{}（缺失检查固定为 False，避免填充数据掩盖缺口）".format(FILL_DATA),
        "- 合约基础信息检查：{}".format(CHECK_INSTRUMENT_DETAIL),
        "- 缺失后下载：{}".format(DOWNLOAD_MISSING_AFTER_LOCAL_CHECK),
        "",
        "## 初始本地检查",
        "",
        "- 股票池数量：{}".format(initial_summary["stock_count"]),
        "- 已取到行情股票数：{}".format(initial_summary["available_code_count"]),
        "- 其中停牌标记股票数：{}".format(initial_summary["suspended_code_count"]),
        "- 正常行情股票数：{}".format(initial_summary["normal_code_count"]),
        "- 缺失股票数：{}".format(initial_summary["missing_code_count"]),
        "- 行情行覆盖率：{:.2%}".format(initial_summary["coverage_rate"]),
        "- 正常行情占比：{:.2%}".format(initial_summary["normal_rate"]),
        "- 缺失记录数：{}".format(initial_summary["missing_record_count"]),
        "- 停牌标记记录数：{}".format(initial_summary["suspended_record_count"]),
        "- 目标日尚未上市股票数：{}".format(initial_summary["not_listed_code_count"]),
        "- 目标日已退市/到期股票数：{}".format(initial_summary["delisted_code_count"]),
        "- 当前合约停牌状态股票数：{}".format(initial_summary["instrument_suspended_code_count"]),
        "- 缺失股票中有合约状态标记数：{}".format(initial_summary["missing_with_instrument_status_count"]),
        "- 缺失股票中当前合约停牌状态数：{}".format(initial_summary["missing_with_instrument_suspended_count"]),
        "- 缺失股票中目标日尚未上市数：{}".format(initial_summary["missing_with_not_listed_count"]),
        "- 缺失股票中目标日已退市/到期数：{}".format(initial_summary["missing_with_delisted_count"]),
        "- 缺失股票中暂未被合约状态解释数：{}".format(initial_summary["missing_without_instrument_status_count"]),
        "- 初始缺失CSV：{}".format(initial_missing_csv),
        "- 初始停牌标记CSV：{}".format(initial_suspended_csv),
        "- 合约状态CSV：{}".format(instrument_status_csv),
        "",
    ]

    if after_download_summary is not None and after_download_missing_csv is not None:
        lines.extend(
            [
                "## 下载后复查",
                "",
                "- 复查股票数：{}".format(after_download_summary["stock_count"]),
                "- 下载后已取到行情股票数：{}".format(after_download_summary["available_code_count"]),
                "- 下载后停牌标记股票数：{}".format(after_download_summary["suspended_code_count"]),
                "- 下载后仍缺失股票数：{}".format(after_download_summary["missing_code_count"]),
                "- 下载后行情行覆盖率：{:.2%}".format(after_download_summary["coverage_rate"]),
                "- 下载后仍缺失记录数：{}".format(after_download_summary["missing_record_count"]),
                "- 下载后仍缺失CSV：{}".format(after_download_missing_csv),
                "- 下载后停牌标记CSV：{}".format(after_download_suspended_csv or ""),
                "",
            ]
        )

    lines.extend(["## 接口状态", ""])
    for status in statuses:
        detail = " - {}".format(status.detail) if status.detail else ""
        lines.append("- [{}] {}{}".format(status.status, status.name, detail))

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


def print_summary(title: str, summary: Dict[str, Any]) -> None:
    print_title(title)
    print("预期日期: {}".format(", ".join(summary["expected_dates"])))
    print("缓存跳过 code/date: {}".format(CACHE_SKIPPED_CHECK_COUNT))
    print("总股票数: {}".format(summary["stock_count"]))
    print("已取到行情股票数: {}".format(summary["available_code_count"]))
    print("其中停牌标记股票数: {}".format(summary["suspended_code_count"]))
    print("正常行情股票数: {}".format(summary["normal_code_count"]))
    print("缺失股票数: {}".format(summary["missing_code_count"]))
    print("行情行覆盖率: {:.2%}".format(summary["coverage_rate"]))
    print("正常行情占比: {:.2%}".format(summary["normal_rate"]))
    print("缺失记录数: {}".format(summary["missing_record_count"]))
    print("停牌标记记录数: {}".format(summary["suspended_record_count"]))
    print("目标日尚未上市股票数: {}".format(summary["not_listed_code_count"]))
    print("目标日已退市/到期股票数: {}".format(summary["delisted_code_count"]))
    print("当前合约停牌状态股票数: {}".format(summary["instrument_suspended_code_count"]))
    print("缺失股票中有合约状态标记数: {}".format(summary["missing_with_instrument_status_count"]))
    print("缺失股票中当前合约停牌状态数: {}".format(summary["missing_with_instrument_suspended_count"]))
    print("缺失股票中目标日尚未上市数: {}".format(summary["missing_with_not_listed_count"]))
    print("缺失股票中目标日已退市/到期数: {}".format(summary["missing_with_delisted_count"]))
    print("缺失股票中暂未被合约状态解释数: {}".format(summary["missing_without_instrument_status_count"]))


def print_missing_samples(records: List[Dict[str, Any]]) -> None:
    if not records:
        print("未发现缺失行情。")
        return

    print("说明: 下面只是缺失子集样例，不代表所有股票都缺失。")
    print("缺失样例:")
    for record in records[:20]:
        print("- {code} {date} {missing_type} {missing_fields} {detail}".format(**record))
    if len(records) > 20:
        print("- ... 其余见 CSV 明细")


def print_suspended_samples(records: List[Dict[str, Any]]) -> None:
    if not records:
        print("未发现 suspendFlag 非 0 的行情行。")
        print("说明: suspendFlag 只能在已取到的行情行里读取；无行情行的缺失股票请看“缺失股票中当前合约停牌状态数”。")
        return

    print("停牌标记样例:")
    for record in records[:20]:
        print(
            "- {code} {date} suspendFlag={suspendFlag} "
            "open={open} high={high} low={low} close={close}".format(**record)
        )
    if len(records) > 20:
        print("- ... 其余见 CSV 明细")


def print_instrument_status_samples(records: List[Dict[str, Any]]) -> None:
    if not records:
        print("未发现未上市、已退市/到期或当前合约停牌状态记录。")
        return

    print("合约状态样例:")
    for record in records[:20]:
        print(
            "- {code} {name} | {status_label} | {detail} | "
            "上市={OpenDateText} | 退市/到期={ExpireDateText} | "
            "品种={ProductTypeText} | IsTrading={IsTradingText}".format(**record)
        )
    if len(records) > 20:
        print("- ... 其余见 CSV 明细")


def print_status_summary(statuses: List[CheckStatus]) -> None:
    print_title("接口状态")

    grouped: Dict[Tuple[str, str], List[CheckStatus]] = {}
    ordered_keys: List[Tuple[str, str]] = []
    for item in statuses:
        key = (item.name, item.status)
        if key not in grouped:
            ordered_keys.append(key)
            grouped[key] = []
        grouped[key].append(item)

    for key in ordered_keys:
        items = grouped[key]
        item = items[-1]
        if len(items) > 1:
            detail = " - {} 次调用；最后一次：{}".format(len(items), item.detail) if item.detail else " - {} 次调用".format(len(items))
            print("[{}] {}{}".format(item.status, item.name, detail))
            continue

        detail = " - {}".format(item.detail) if item.detail else ""
        print("[{}] {}{}".format(item.status, item.name, detail))


def main() -> int:
    try:
        apply_cli_args(build_arg_parser().parse_args())
    except SystemExit:
        raise
    except Exception as exc:
        print_title("参数错误")
        print(format_exception(exc))
        return 2

    statuses: List[CheckStatus] = []
    print_title("MiniQMT 全市场 Local-First 行情缺失检查")
    print("Python: {}".format(sys.executable))
    print("检查模式: A股全市场自动体检")
    print("历史基准: {}".format(CHECK_START_DATE))
    print("结束上限: {}".format(CHECK_END_DATE))
    print("股票池: A股全市场（上证A股 + 深证A股）")
    print("读取接口: get_local_data")
    print("周期: {}".format(PERIOD))
    print("fill_data: {}（缺失检查固定用 False）".format(FILL_DATA))
    print("合约基础信息检查: {}".format(CHECK_INSTRUMENT_DETAIL))
    print("缺失后下载: {}".format(DOWNLOAD_MISSING_AFTER_LOCAL_CHECK))
    print("自动交易日历: {}".format(AUTO_CALENDAR))
    print("检查缓存: {}".format("启用" if ENABLE_CHECK_CACHE else "关闭"))
    print("上市日期处理: 强制读取 get_instrument_detail，用 OpenDate 跳过上市前日期。")

    try:
        log("阶段 1/9: 初始化检查缓存")
        init_check_cache(statuses)
        log("阶段 2/9: 获取全市场股票池")
        codes, code_source = get_stock_pool(statuses)
        print("股票池数量: {}".format(len(codes)))
        if not codes:
            raise RuntimeError("股票池为空，请检查 TARGET_SECTORS/MARKET_SUFFIXES。")

        log("阶段 3/9: 生成交易日历")
        expected_dates = apply_auto_expected_dates(codes, statuses)
        if not expected_dates:
            print("没有需要检查的预期交易日。")
            print_status_summary(statuses)
            return 0

        log("阶段 4/9: 读取合约基础信息")
        details_by_code = fetch_instrument_details(codes, statuses)
        log("阶段 5/9: 根据 OpenDate 调整每只股票检查起点")
        apply_listing_date_floor(codes, details_by_code, expected_dates)
        log("阶段 6/9: 读取检查缓存并筛选本轮需检查股票")
        load_cached_ok_counts(codes, expected_dates, statuses)
        active_codes = filter_codes_with_unchecked_dates(codes, expected_dates, statuses)
        if not active_codes:
            print("缓存显示所有目标 code/date 都已经确认过，本轮无需重复检查。")
            print_status_summary(statuses)
            return 0
        load_cached_ok_dates(active_codes, expected_dates, statuses)

        log("阶段 7/9: 生成合约状态诊断文件")
        instrument_status_records = collect_instrument_status_records(active_codes, code_source, details_by_code, expected_dates)
        instrument_status_csv = write_instrument_status_csv(instrument_status_records, "instrument_status_initial")

        log("阶段 8/9: 读取本地行情并比对缺失")
        all_data = fetch_local_data(active_codes, statuses, "初始")
        initial_records = collect_missing_records(active_codes, code_source, all_data, expected_dates)
        initial_suspended_records = collect_suspended_records(
            active_codes,
            code_source,
            all_data,
            expected_dates,
            initial_records,
        )
        log("阶段 9/9: 写入检查缓存和输出报告")
        record_successful_checks(active_codes, all_data, expected_dates, initial_records, statuses, "initial_check")
        initial_summary = summarize(
            active_codes,
            initial_records,
            initial_suspended_records,
            instrument_status_records,
            expected_dates,
        )
        initial_csv = write_records_csv(initial_records, "missing_market_data_initial")
        initial_suspended_csv = write_suspended_csv(initial_suspended_records, "suspended_market_data_initial")

        print_summary("初始本地检查结果", initial_summary)
        print_missing_samples(initial_records)
        print_suspended_samples(initial_suspended_records)
        print_instrument_status_samples(instrument_status_records)
        print("初始缺失CSV: {}".format(initial_csv))
        print("初始停牌标记CSV: {}".format(initial_suspended_csv))
        print("合约状态CSV: {}".format(instrument_status_csv))

        after_download_summary = None
        after_download_csv = None
        after_download_suspended_csv = None
        missing_codes = download_missing_codes(initial_records, statuses)

        if DOWNLOAD_MISSING_AFTER_LOCAL_CHECK and missing_codes:
            log("开始补下载缺失股票并复查，缺失股票数 {}".format(len(missing_codes)))
            retry_data = fetch_local_data(missing_codes, statuses, "下载后复查")
            after_records = collect_missing_records(missing_codes, code_source, retry_data, expected_dates)
            after_suspended_records = collect_suspended_records(
                missing_codes,
                code_source,
                retry_data,
                expected_dates,
                after_records,
            )
            after_status_records = [
                record for record in instrument_status_records if record["code"] in set(missing_codes)
            ]
            record_successful_checks(missing_codes, retry_data, expected_dates, after_records, statuses, "after_download")
            record_persistent_missing_checks(after_records, statuses, "after_download")
            after_download_summary = summarize(
                missing_codes,
                after_records,
                after_suspended_records,
                after_status_records,
                expected_dates,
            )
            after_download_csv = write_records_csv(after_records, "missing_market_data_after_download")
            after_download_suspended_csv = write_suspended_csv(
                after_suspended_records,
                "suspended_market_data_after_download",
            )

            print_summary("下载后复查结果", after_download_summary)
            print_missing_samples(after_records)
            print_suspended_samples(after_suspended_records)
            print("下载后仍缺失CSV: {}".format(after_download_csv))
            print("下载后停牌标记CSV: {}".format(after_download_suspended_csv))

        summary_path = write_summary_report(
            statuses=statuses,
            initial_summary=initial_summary,
            initial_missing_csv=initial_csv,
            initial_suspended_csv=initial_suspended_csv,
            instrument_status_csv=instrument_status_csv,
            after_download_summary=after_download_summary,
            after_download_missing_csv=after_download_csv,
            after_download_suspended_csv=after_download_suspended_csv,
        )
        print("摘要报告: {}".format(summary_path))
        print_status_summary(statuses)
        return 0

    except Exception as exc:
        add_status(statuses, "program", "ERROR", format_exception(exc))
        print_title("程序异常")
        print(format_exception(exc))
        print(traceback.format_exc())
        print_status_summary(statuses)
        print("")
        print("排查提示:")
        print("- 请确认 MiniQMT 已启动，行情连接正常。")
        print("- 本工具默认只用 get_local_data 检查本地数据。")
        print("- 如需自动补缺失股票，把 DOWNLOAD_MISSING_AFTER_LOCAL_CHECK 改为 True。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
