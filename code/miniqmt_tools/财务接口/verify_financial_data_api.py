#!/usr/bin/env python3
# coding: utf-8
r"""
MiniQMT / xtdata 财务数据接口验证脚本。

用途：
1. 逐标的、逐财务表验证 `download_financial_data`。
2. 逐标的、逐财务表验证 `get_financial_data` 的 `report_time` / `announce_time`。
3. 对每个子任务单独加超时，避免一个异常标的把整次检查拖死。

运行：
    d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/财务接口/verify_financial_data_api.py
"""

from __future__ import annotations

import multiprocessing as mp
import sys
from datetime import datetime
from pathlib import Path
from queue import Empty
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd


SAMPLE_CODES = ["000001.SZ", "600519.SH"]
TABLES = ["Balance", "Income", "CashFlow"]
REPORT_TYPES = ["report_time", "announce_time"]
START_TIME = "20200101"
END_TIME = "20260501"
TASK_TIMEOUT_SECONDS = 20
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"

# 2026-05-16 实测发现：该组合会阻塞较久，因此默认只留作文档记录，不放进常规烟测。
KNOWN_PROBLEM_CASES = [("002422.SZ", "Income")]


def print_title(title: str) -> None:
    print("")
    print("=" * 80)
    print(title)
    print("=" * 80)


def _download_worker(code: str, table: str, queue: mp.Queue) -> None:
    try:
        from xtquant import xtdata

        result = xtdata.download_financial_data([code], table_list=[table])
        queue.put({"status": "ok", "result": result})
    except Exception as exc:  # pragma: no cover - 子进程异常只做传递
        queue.put({"status": "error", "error_type": type(exc).__name__, "error": str(exc)})


def _read_worker(code: str, table: str, report_type: str, queue: mp.Queue) -> None:
    try:
        from xtquant import xtdata

        payload = xtdata.get_financial_data(
            [code],
            table_list=[table],
            start_time=START_TIME,
            end_time=END_TIME,
            report_type=report_type,
        )
        frame = payload.get(code, {}).get(table)
        if isinstance(frame, pd.DataFrame):
            queue.put(
                {
                    "status": "ok",
                    "rows": len(frame),
                    "cols": len(frame.columns),
                    "columns": list(frame.columns),
                }
            )
        else:
            queue.put({"status": "ok", "rows": None, "cols": None, "columns": []})
    except Exception as exc:  # pragma: no cover - 子进程异常只做传递
        queue.put({"status": "error", "error_type": type(exc).__name__, "error": str(exc)})


def run_with_timeout(target: Any, args: Tuple[Any, ...], timeout_seconds: int) -> Dict[str, Any]:
    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue()
    process = ctx.Process(target=target, args=(*args, queue))
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join()
        return {"status": "timeout"}

    try:
        return queue.get_nowait()
    except Empty:
        return {"status": "error", "error_type": "NoResult", "error": "子进程没有返回结果"}


def format_status(result: Dict[str, Any]) -> str:
    if result["status"] == "ok":
        return "正常"
    if result["status"] == "timeout":
        return "超时"
    return "异常"


def build_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    print_title("步骤1: download_financial_data")
    for code in SAMPLE_CODES:
        for table in TABLES:
            result = run_with_timeout(_download_worker, (code, table), TASK_TIMEOUT_SECONDS)
            row = {
                "operation": "download_financial_data",
                "code": code,
                "table": table,
                "report_type": "",
                "status": result["status"],
                "rows": "",
                "cols": "",
                "detail": result.get("error", ""),
            }
            rows.append(row)
            print("- {} {}: {}".format(code, table, format_status(result)))

    print_title("步骤2: get_financial_data")
    for report_type in REPORT_TYPES:
        print("口径: {}".format(report_type))
        for code in SAMPLE_CODES:
            for table in TABLES:
                result = run_with_timeout(_read_worker, (code, table, report_type), TASK_TIMEOUT_SECONDS)
                row = {
                    "operation": "get_financial_data",
                    "code": code,
                    "table": table,
                    "report_type": report_type,
                    "status": result["status"],
                    "rows": result.get("rows", ""),
                    "cols": result.get("cols", ""),
                    "detail": result.get("error", ""),
                }
                rows.append(row)
                if result["status"] == "ok":
                    print("- {} {}: 正常, {} 行".format(code, table, result.get("rows")))
                else:
                    print("- {} {}: {}".format(code, table, format_status(result)))
    return rows


def write_summary_csv(rows: Iterable[Dict[str, Any]]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "financial_api_summary_{}.csv".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def main() -> int:
    print_title("MiniQMT 财务数据接口验证")
    print("Python: {}".format(sys.executable))
    print("股票: {}".format(", ".join(SAMPLE_CODES)))
    print("财务表: {}".format(", ".join(TABLES)))
    print("时间范围: {} 至 {}".format(START_TIME, END_TIME))
    print("单任务超时: {} 秒".format(TASK_TIMEOUT_SECONDS))

    rows = build_rows()
    summary_path = write_summary_csv(rows)
    frame = pd.DataFrame(rows)

    print_title("结论")
    download_ok = (
        frame.loc[frame["operation"] == "download_financial_data", "status"] == "ok"
    ).all()
    report_ok = (
        frame.loc[
            (frame["operation"] == "get_financial_data") & (frame["report_type"] == "report_time"),
            "status",
        ]
        == "ok"
    ).all()
    announce_ok = (
        frame.loc[
            (frame["operation"] == "get_financial_data") & (frame["report_type"] == "announce_time"),
            "status",
        ]
        == "ok"
    ).all()

    print("download_financial_data 常规样例: {}".format("正常" if download_ok else "存在异常"))
    print("get_financial_data(report_time): {}".format("正常" if report_ok else "存在异常"))
    print("get_financial_data(announce_time): {}".format("正常" if announce_ok else "存在异常"))
    print("摘要CSV: {}".format(summary_path))
    print("已知需单独留意的组合: {}".format(", ".join("{} {}".format(c, t) for c, t in KNOWN_PROBLEM_CASES)))

    has_failure = not (download_ok and report_ok and announce_ok)
    return 1 if has_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
