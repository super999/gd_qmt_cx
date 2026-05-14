#!/usr/bin/env python3
# coding: utf-8
r"""
全市场 Local-First 行情缺失检查。

默认检查范围：
- 股票池：上证A股 + 深证A股
- 日期：2026-05-11
- 周期：1d

运行：
    d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/check_missing_market_data.py

流程：
1. 先用 get_local_data 读取本地行情，不改变本地数据状态。
2. 统计本地已有完整行情和缺失行情。
3. 如 DOWNLOAD_MISSING_AFTER_LOCAL_CHECK=True，只下载缺失股票。
4. 下载后再次用 get_local_data 复查仍缺失的股票。
"""

from __future__ import annotations

import math
import sys
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

CHECK_START_DATE = "20260511"
CHECK_END_DATE = "20260511"

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
INSTRUMENT_DETAIL_PROGRESS_EVERY = 500

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

# 默认只检查本地数据。改成 True 时，只下载初始检查发现缺失的股票。
DOWNLOAD_MISSING_AFTER_LOCAL_CHECK = False


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

    details: Dict[str, Dict[str, Any]] = {}
    failed: List[str] = []
    for index, code in enumerate(codes, start=1):
        try:
            detail = xtdata.get_instrument_detail(code)
            if isinstance(detail, dict):
                details[code] = detail
            else:
                failed.append(code)
        except Exception:
            failed.append(code)

        if INSTRUMENT_DETAIL_PROGRESS_EVERY > 0 and index % INSTRUMENT_DETAIL_PROGRESS_EVERY == 0:
            print("读取合约基础信息: {}/{}".format(index, len(codes)))

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
    for batch_index, batch_codes in enumerate(chunked(codes, BATCH_SIZE), start=1):
        print("{}读取本地行情批次 {}: {} 个标的".format(label, batch_index, len(batch_codes)))
        all_data.update(fetch_local_batch(batch_codes, statuses, label))
    return all_data


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

    for code in codes:
        frame = data_by_code.get(code)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            for date in expected_dates:
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

        for date in expected_dates:
            row = row_by_date(frame, date)
            if row is None:
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

    for code in codes:
        frame = data_by_code.get(code)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue

        for date in expected_dates:
            if (code, date) in missing_keys:
                continue

            row = row_by_date(frame, date)
            if row is None:
                continue

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
        "- 目标板块：{}".format(", ".join(TARGET_SECTORS)),
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
    statuses: List[CheckStatus] = []
    print_title("MiniQMT 全市场 Local-First 行情缺失检查")
    print("Python: {}".format(sys.executable))
    print("日期范围: {} 至 {}".format(CHECK_START_DATE, CHECK_END_DATE))
    print("目标板块: {}".format(", ".join(TARGET_SECTORS)))
    print("读取接口: get_local_data")
    print("周期: {}".format(PERIOD))
    print("fill_data: {}（缺失检查固定用 False）".format(FILL_DATA))
    print("合约基础信息检查: {}".format(CHECK_INSTRUMENT_DETAIL))
    print("缺失后下载: {}".format(DOWNLOAD_MISSING_AFTER_LOCAL_CHECK))

    try:
        expected_dates = expected_dates_from_config()
        codes, code_source = get_stock_pool(statuses)
        print("股票池数量: {}".format(len(codes)))
        if not codes:
            raise RuntimeError("股票池为空，请检查 TARGET_SECTORS/CODE_PREFIXES/MARKET_SUFFIXES。")

        details_by_code = fetch_instrument_details(codes, statuses)
        instrument_status_records = collect_instrument_status_records(codes, code_source, details_by_code, expected_dates)
        instrument_status_csv = write_instrument_status_csv(instrument_status_records, "instrument_status_initial")

        all_data = fetch_local_data(codes, statuses, "初始")
        initial_records = collect_missing_records(codes, code_source, all_data, expected_dates)
        initial_suspended_records = collect_suspended_records(
            codes,
            code_source,
            all_data,
            expected_dates,
            initial_records,
        )
        initial_summary = summarize(
            codes,
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
