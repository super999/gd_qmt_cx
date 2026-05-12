#!/usr/bin/env python3
# coding: utf-8
r"""
检查 MiniQMT 本地行情是否缺失。

默认只检查、不下载：
    d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/check_missing_market_data.py

输出：
- 控制台摘要
- outputs/missing_market_data_*.csv
- outputs/market_data_check_summary_*.md
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

# 留空表示自动按返回数据推断。单日检查时默认就是 CHECK_START_DATE。
EXPECTED_DATES: List[str] = []

TARGET_SECTORS = [
    "上证A股",
    "深证A股",
    "北证A股",
]

# 留空表示不过滤前缀/后缀。
CODE_PREFIXES: List[str] = []
MARKET_SUFFIXES = [".SH", ".SZ", ".BJ"]

PERIOD = "1d"
DIVIDEND_TYPE = "none"
FILL_DATA = False
BATCH_SIZE = 300

REQUIRED_FIELDS = ["open", "high", "low", "close", "volume", "amount"]
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"

# 默认只检查，不自动补下载。需要自动补下载时手动改成 True。
AUTO_DOWNLOAD_MISSING = False


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


def is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    try:
        number = float(value)
    except Exception:
        return False
    return math.isnan(number) or math.isinf(number)


def chunked(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


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


def fetch_batch(codes: List[str], statuses: List[CheckStatus]) -> Dict[str, pd.DataFrame]:
    try:
        return xtdata.get_market_data_ex(
            field_list=[],
            stock_list=codes,
            period=PERIOD,
            start_time=CHECK_START_DATE,
            end_time=CHECK_END_DATE,
            count=-1,
            dividend_type=DIVIDEND_TYPE,
            fill_data=FILL_DATA,
        )
    except Exception as exc:
        add_status(statuses, "get_market_data_ex", "ERROR", "批次失败: {}".format(format_exception(exc)))
        raise


def infer_expected_dates(data_by_code: Dict[str, pd.DataFrame]) -> List[str]:
    if EXPECTED_DATES:
        return sorted(set(EXPECTED_DATES))

    if CHECK_START_DATE == CHECK_END_DATE:
        return [CHECK_START_DATE]

    dates = set()
    for frame in data_by_code.values():
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        for index in frame.index:
            date = normalize_date(index)
            if date:
                dates.add(date)
    return sorted(dates)


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


def download_missing_codes(records: List[Dict[str, Any]], statuses: List[CheckStatus]) -> None:
    if not AUTO_DOWNLOAD_MISSING:
        add_status(statuses, "download_history_data", "SKIP", "AUTO_DOWNLOAD_MISSING=False，仅检查不下载")
        return

    pairs = sorted({(record["code"], record["date"]) for record in records})
    ok_count = 0
    failed: List[str] = []
    for code, date in pairs:
        try:
            xtdata.download_history_data(code, period=PERIOD, start_time=date, end_time=date, incrementally=True)
            ok_count += 1
        except Exception:
            failed.append("{}@{}".format(code, date))

    if failed:
        add_status(
            statuses,
            "download_history_data",
            "PARTIAL",
            "成功 {}, 失败 {}, 失败样例: {}".format(ok_count, len(failed), ", ".join(failed[:10])),
        )
    else:
        add_status(statuses, "download_history_data", "OK", "成功下载 {} 个 code/date".format(ok_count))


def write_outputs(records: List[Dict[str, Any]], statuses: List[CheckStatus], summary: Dict[str, Any]) -> Tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / "missing_market_data_{}.csv".format(timestamp)
    md_path = OUTPUT_DIR / "market_data_check_summary_{}.md".format(timestamp)

    df = pd.DataFrame(
        records,
        columns=["code", "sector", "date", "missing_type", "missing_fields", "detail"],
    )
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines = [
        "# MiniQMT 本地行情缺失检查摘要",
        "",
        "- 检查时间：{}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "- 日期范围：{} 至 {}".format(CHECK_START_DATE, CHECK_END_DATE),
        "- 周期：{}".format(PERIOD),
        "- 复权：{}".format(DIVIDEND_TYPE),
        "- fill_data：{}".format(FILL_DATA),
        "- 股票池数量：{}".format(summary["stock_count"]),
        "- 预期日期：{}".format(", ".join(summary["expected_dates"])),
        "- 缺失记录数：{}".format(summary["missing_record_count"]),
        "- 缺失股票数：{}".format(summary["missing_code_count"]),
        "",
        "## 接口状态",
        "",
    ]
    for status in statuses:
        detail = " - {}".format(status.detail) if status.detail else ""
        lines.append("- [{}] {}{}".format(status.status, status.name, detail))

    lines.extend(["", "## 缺失样例", ""])
    for record in records[:30]:
        lines.append(
            "- {code} {date} {missing_type} {missing_fields} {detail}".format(**record)
        )
    if len(records) > 30:
        lines.append("- ... 其余见 CSV 明细")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path


def print_status_summary(statuses: List[CheckStatus]) -> None:
    print_title("接口状态")
    for item in statuses:
        detail = " - {}".format(item.detail) if item.detail else ""
        print("[{}] {}{}".format(item.status, item.name, detail))


def main() -> int:
    statuses: List[CheckStatus] = []
    print_title("MiniQMT 本地行情缺失检查")
    print("Python: {}".format(sys.executable))
    print("日期范围: {} 至 {}".format(CHECK_START_DATE, CHECK_END_DATE))
    print("目标板块: {}".format(", ".join(TARGET_SECTORS)))
    print("只检查不下载: {}".format(not AUTO_DOWNLOAD_MISSING))

    try:
        codes, code_source = get_stock_pool(statuses)
        print("股票池数量: {}".format(len(codes)))
        if not codes:
            raise RuntimeError("股票池为空，请检查 TARGET_SECTORS/CODE_PREFIXES/MARKET_SUFFIXES。")

        all_data: Dict[str, pd.DataFrame] = {}
        for batch_index, batch_codes in enumerate(chunked(codes, BATCH_SIZE), start=1):
            print("读取行情批次 {}: {} 个标的".format(batch_index, len(batch_codes)))
            batch_data = fetch_batch(batch_codes, statuses)
            all_data.update(batch_data)

        add_status(
            statuses,
            "get_market_data_ex",
            "OK",
            "请求 {} 个标的，返回 {} 个键".format(len(codes), len(all_data)),
        )

        expected_dates = infer_expected_dates(all_data)
        if not expected_dates:
            raise RuntimeError("无法推断预期交易日期。单日检查请确认 CHECK_START_DATE/CHECK_END_DATE。")

        records = collect_missing_records(codes, code_source, all_data, expected_dates)
        download_missing_codes(records, statuses)

        summary = {
            "stock_count": len(codes),
            "expected_dates": expected_dates,
            "missing_record_count": len(records),
            "missing_code_count": len({record["code"] for record in records}),
        }
        csv_path, md_path = write_outputs(records, statuses, summary)

        print_title("检查结果")
        print("预期日期: {}".format(", ".join(expected_dates)))
        print("缺失记录数: {}".format(summary["missing_record_count"]))
        print("缺失股票数: {}".format(summary["missing_code_count"]))
        if records:
            print("缺失样例:")
            for record in records[:20]:
                print(
                    "- {code} {date} {missing_type} {missing_fields} {detail}".format(**record)
                )
            if len(records) > 20:
                print("- ... 其余见 CSV 明细")
        else:
            print("未发现缺失行情。")

        print("CSV 明细: {}".format(csv_path))
        print("摘要报告: {}".format(md_path))
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
        print("- 如果所有股票都缺失，请先在 MiniQMT 中补充对应日期历史行情。")
        print("- 本脚本默认不下载，只报告缺失；补完数据后可再次运行检查。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
