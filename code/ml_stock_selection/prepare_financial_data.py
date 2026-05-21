#!/usr/bin/env python3
# coding: utf-8
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path
from queue import Empty
from typing import Any, Dict, List, Tuple

import pandas as pd

from config import StrategyConfig
from data import StockUniverseService
from financial_data import financial_cache_path
from utils import print_title


DEFAULT_TABLES = ["PershareIndex"]
TABLE_ALIASES = {
    "pershareindex": "PershareIndex",
    "pershare_index": "PershareIndex",
    "pershare": "PershareIndex",
    "balance": "Balance",
    "income": "Income",
    "cashflow": "CashFlow",
}
SUMMARY_COLUMNS = [
    "code",
    "table",
    "table_used",
    "status",
    "download_method",
    "rows",
    "cols",
    "first_announce_date",
    "last_announce_date",
    "detail",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="准备 MiniQMT 财务数据缓存，不训练模型")
    parser.add_argument("--start-date", default="20200101")
    parser.add_argument("--end-date", default="20260511")
    parser.add_argument("--max-stocks", type=int, default=None, help="小样本冒烟时限制股票数量；默认全量")
    parser.add_argument("--tables", default=",".join(DEFAULT_TABLES), help="逗号分隔财务表，例如 PershareIndex,Balance,Income,CashFlow")
    parser.add_argument("--timeout-seconds", type=int, default=30, help="单个 股票+表 子任务超时时间")
    parser.add_argument("--stock-batch-size", type=int, default=20, help="每个子任务批量处理的股票数量；批次超时会自动拆分")
    parser.add_argument(
        "--download-method",
        choices=["legacy", "range", "auto"],
        default="legacy",
        help="legacy 使用 download_financial_data；range 使用 download_financial_data2；auto 先 range 异常后 legacy。默认 legacy 更稳定。",
    )
    parser.add_argument("--cache-dir", default=None, help="财务缓存目录；默认 code/ml_stock_selection/outputs/financial_cache")
    return parser.parse_args()


def canonical_table_name(table: str) -> str:
    text = table.strip()
    return TABLE_ALIASES.get(text.lower(), text)


def table_candidates(table: str) -> List[str]:
    if table == "PershareIndex":
        return ["PershareIndex", "Pershareindex"]
    return [table]


def _download_with_fallback(
    xtdata: Any,
    codes: List[str],
    table: str,
    start_date: str,
    end_date: str,
    download_method: str,
) -> Tuple[str, str]:
    if download_method == "legacy":
        xtdata.download_financial_data(codes, table_list=[table])
        return "download_financial_data", ""

    if download_method in {"range", "auto"} and hasattr(xtdata, "download_financial_data2"):
        try:
            xtdata.download_financial_data2(
                codes,
                table_list=[table],
                start_time=start_date,
                end_time=end_date,
                callback=None,
            )
            return "download_financial_data2", ""
        except Exception as exc:
            fallback_detail = "download_financial_data2 失败: {}: {}".format(type(exc).__name__, exc)
            if download_method == "range":
                raise
        else:
            fallback_detail = ""
    else:
        fallback_detail = "download_financial_data2 不存在"

    xtdata.download_financial_data(codes, table_list=[table])
    return "download_financial_data", fallback_detail


def _download_and_read_worker(
    codes: List[str],
    canonical_table: str,
    start_date: str,
    end_date: str,
    download_method: str,
    queue: mp.Queue,
) -> None:
    try:
        from xtquant import xtdata

        errors: List[str] = []
        for table in table_candidates(canonical_table):
            try:
                actual_download_method, fallback_detail = _download_with_fallback(
                    xtdata,
                    codes,
                    table,
                    start_date,
                    end_date,
                    download_method,
                )
                payload = xtdata.get_financial_data(
                    codes,
                    table_list=[table],
                    start_time=start_date,
                    end_time=end_date,
                    report_type="announce_time",
                )
                batch_results: List[Dict[str, Any]] = []
                for code in codes:
                    frame = payload.get(code, {}).get(table)
                    if not isinstance(frame, pd.DataFrame):
                        batch_results.append(
                            _empty_result(
                                code,
                                canonical_table,
                                status="error",
                                detail="{} 返回非 DataFrame".format(table),
                            )
                        )
                        continue
                    frame = frame.reset_index(drop=True).copy()
                    frame.insert(0, "code", code)
                    frame.insert(1, "source_table", table)
                    rows = len(frame)
                    first_announce = str(frame["m_anntime"].min()) if rows and "m_anntime" in frame.columns else ""
                    last_announce = str(frame["m_anntime"].max()) if rows and "m_anntime" in frame.columns else ""
                    detail = fallback_detail if fallback_detail else ""
                    batch_results.append(
                        {
                            "status": "ok",
                            "code": code,
                            "table": canonical_table,
                            "table_used": table,
                            "download_method": actual_download_method,
                            "rows": rows,
                            "cols": len(frame.columns),
                            "first_announce_date": first_announce,
                            "last_announce_date": last_announce,
                            "detail": detail,
                            "records": frame.to_dict("records"),
                            "columns": list(frame.columns),
                        }
                    )
                queue.put({"status": "batch_ok", "results": batch_results})
                return
            except Exception as exc:
                errors.append("{} {}: {}".format(table, type(exc).__name__, exc))
        queue.put({"status": "batch_error", "results": [_empty_result(code, canonical_table, "error", " | ".join(errors)) for code in codes]})
    except Exception as exc:
        queue.put({"status": "batch_error", "results": [_empty_result(code, canonical_table, "error", "{}: {}".format(type(exc).__name__, exc)) for code in codes]})


def _empty_result(code: str, table: str, status: str, detail: str) -> Dict[str, Any]:
    return {
        "status": status,
        "code": code,
        "table": table,
        "table_used": "",
        "download_method": "",
        "rows": 0,
        "cols": 0,
        "first_announce_date": "",
        "last_announce_date": "",
        "detail": detail,
        "records": [],
        "columns": [],
    }


def run_batch_with_timeout(
    codes: List[str],
    table: str,
    start_date: str,
    end_date: str,
    timeout_seconds: int,
    download_method: str,
) -> Dict[str, Any]:
    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue()
    process = ctx.Process(target=_download_and_read_worker, args=(codes, table, start_date, end_date, download_method, queue))
    process.start()
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            result = queue.get(timeout=0.2)
            if process.is_alive():
                process.terminate()
            process.join()
            return result
        except Empty:
            if not process.is_alive():
                process.join()
                try:
                    return queue.get_nowait()
                except Empty:
                    return {
                        "status": "batch_error",
                        "results": [_empty_result(code, table, "error", "子进程没有返回结果") for code in codes],
                    }
    if process.is_alive():
        process.terminate()
    process.join()
    return {
        "status": "batch_timeout",
        "results": [_empty_result(code, table, "timeout", "批量任务超过 {} 秒".format(timeout_seconds)) for code in codes],
    }


def run_adaptive_batch(
    codes: List[str],
    table: str,
    start_date: str,
    end_date: str,
    timeout_seconds: int,
    download_method: str,
) -> List[Dict[str, Any]]:
    result = run_batch_with_timeout(codes, table, start_date, end_date, timeout_seconds, download_method)
    if result["status"] == "batch_ok":
        return result["results"]
    if len(codes) <= 1:
        return result["results"]
    midpoint = len(codes) // 2
    print("批次 {} {} 失败，自动拆分为 {} + {}。".format(table, len(codes), midpoint, len(codes) - midpoint))
    return run_adaptive_batch(codes[:midpoint], table, start_date, end_date, timeout_seconds, download_method) + run_adaptive_batch(
        codes[midpoint:],
        table,
        start_date,
        end_date,
        timeout_seconds,
        download_method,
    )


def build_config(args: argparse.Namespace) -> StrategyConfig:
    cache_dir = Path(args.cache_dir) if args.cache_dir else StrategyConfig().financial_cache_dir
    return StrategyConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        financial_cache_dir=cache_dir,
    )


def write_outputs(cache_dir: Path, tables: List[str], results: List[Dict[str, Any]]) -> Dict[str, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_paths = {
        "coverage": cache_dir / "financial_coverage_report.csv",
        "schema": cache_dir / "financial_schema_report.csv",
        "failures": cache_dir / "financial_download_failures.csv",
    }

    pd.DataFrame([{key: result.get(key, "") for key in SUMMARY_COLUMNS} for result in results]).to_csv(
        output_paths["coverage"],
        index=False,
        encoding="utf-8-sig",
    )
    failures = [result for result in results if result.get("status") != "ok"]
    pd.DataFrame([{key: result.get(key, "") for key in SUMMARY_COLUMNS} for result in failures], columns=SUMMARY_COLUMNS).to_csv(
        output_paths["failures"],
        index=False,
        encoding="utf-8-sig",
    )

    schema_rows: List[Dict[str, Any]] = []
    for table in tables:
        table_records: List[Dict[str, Any]] = []
        for result in results:
            if result.get("table") == table and result.get("records"):
                table_records.extend(result["records"])
        table_path = financial_cache_path(cache_dir, table)
        frame = pd.DataFrame(table_records)
        if frame.empty and table_path.exists():
            output_paths["raw_{}".format(table)] = table_path
            continue
        frame.to_csv(table_path, index=False, encoding="utf-8-sig")
        output_paths["raw_{}".format(table)] = table_path
        if frame.empty:
            continue
        for column in frame.columns:
            notna = frame[column].notna()
            schema_rows.append(
                {
                    "table": table,
                    "column": column,
                    "dtype": str(frame[column].dtype),
                    "non_null_rows": int(notna.sum()),
                    "non_null_rate": float(notna.mean()),
                }
            )
    pd.DataFrame(schema_rows).to_csv(output_paths["schema"], index=False, encoding="utf-8-sig")
    return output_paths


def print_result(result: Dict[str, Any], index: int, total: int) -> None:
    if result["status"] == "ok":
        print(
            "[{}/{}] {} {} 正常: {} 行, 表名 {}".format(
                index,
                total,
                result["code"],
                result["table"],
                result["rows"],
                result["table_used"],
            )
        )
        return
    print("[{}/{}] {} {} {}".format(index, total, result["code"], result["table"], result["status"]))


def print_batch_summary(table: str, batch_codes: List[str], results: List[Dict[str, Any]], index: int, total: int) -> None:
    ok_count = sum(1 for result in results if result.get("status") == "ok")
    fail_count = len(results) - ok_count
    print(
        "[批次 {}/{}] {} {} 只: 成功 {}, 失败/超时 {}".format(
            index,
            total,
            table,
            len(batch_codes),
            ok_count,
            fail_count,
        )
    )


def parse_tables(value: str) -> List[str]:
    tables = [canonical_table_name(part) for part in value.split(",") if part.strip()]
    return list(dict.fromkeys(tables))


def main() -> int:
    args = parse_args()
    config = build_config(args)
    tables = parse_tables(args.tables)

    print_title("准备 MiniQMT 财务数据缓存")
    print("Python: {}".format(sys.executable))
    print("日期范围: {} 至 {}".format(config.start_date, config.end_date))
    print("财务表: {}".format(", ".join(tables)))
    print("缓存目录: {}".format(config.financial_cache_dir))
    print("单任务超时: {} 秒".format(args.timeout_seconds))
    print("股票批量: {} 只/批".format(args.stock_batch_size))
    print("下载方式: {}".format(args.download_method))

    codes, _ = StockUniverseService(config).load(args.max_stocks)
    print("股票池数量: {}".format(len(codes)))
    stock_batch_size = max(1, args.stock_batch_size)
    batches = [codes[start : start + stock_batch_size] for start in range(0, len(codes), stock_batch_size)]
    total = len(batches) * len(tables)
    results: List[Dict[str, Any]] = []
    for index, (batch_codes, table) in enumerate(((batch, table) for table in tables for batch in batches), start=1):
        batch_results = run_adaptive_batch(
            batch_codes,
            table,
            config.start_date,
            config.end_date,
            args.timeout_seconds,
            args.download_method,
        )
        results.extend(batch_results)
        print_batch_summary(table, batch_codes, batch_results, index, total)

    output_paths = write_outputs(config.financial_cache_dir, tables, results)
    failures = [result for result in results if result.get("status") != "ok"]

    print_title("完成")
    for name, path in output_paths.items():
        print("{}: {}".format(name, path))
    print("失败/超时任务数: {}".format(len(failures)))
    return 1 if len(results) == len(failures) and failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
