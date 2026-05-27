#!/usr/bin/env python3

import argparse
from datetime import datetime, timedelta
from pprint import pprint
import sys
import time

import pandas as pd

# 1. 动态引入 QMT 库路径（请根据你实际的 bin 路径调整）
# 这里的路径通常是 QMT 安装目录下的 xtquant 文件夹所在位置

try:
    from xtquant import xtdata
    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
    print("[OK] xtquant 库加载成功")
except ImportError:
    print("[ERROR] 未找到 xtquant 库，请检查路径设置")
    sys.exit()

def compact_date(value: str) -> str:
    return value.replace("-", "").strip()


def dashed_date(value: str) -> str:
    value = compact_date(value)
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def default_start_date(end_date: str, days: int = 20) -> str:
    end_dt = datetime.strptime(compact_date(end_date), "%Y%m%d")
    return (end_dt - timedelta(days=days)).strftime("%Y%m%d")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MiniQMT 单标的日线调试工具：打印指定代码、指定日期的本地 1d 行情。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--code", default="510300.SH", help="要检查的证券代码，例如 510300.SH、000001.SZ。")
    parser.add_argument("--date", default="20260526", help="要打印的目标日期，格式 YYYYMMDD 或 YYYY-MM-DD。")
    parser.add_argument("--start-date", default="", help="读取区间开始日期；不填则默认目标日前 20 个自然日。")
    parser.add_argument("--end-date", default="", help="读取区间结束日期；不填则等于 --date。")
    parser.add_argument("--download", action="store_true", help="读取前先调用 download_history_data 下载该区间。")
    parser.add_argument("--tail", type=int, default=10, help="额外打印最近 N 行，便于看最新本地日期。")
    return parser


def print_dataframe_info(code: str, df, target_date: str, tail: int) -> None:
    if df is None:
        print(f"[ERROR] get_local_data 没有返回 {code} 的 DataFrame。")
        return

    if getattr(df, "empty", True):
        print(f"[ERROR] {code} 本地日线 DataFrame 为空。")
        return

    print(f"\n================================================================================")
    print(f"{code} 本地日线概览")
    print(f"================================================================================")
    print(f"行数: {len(df)}")
    print(f"列: {list(df.columns)}")
    print(f"索引类型: {type(df.index).__name__}")

    work = df.copy()
    index_dates = work.index.astype(str).str.replace("-", "", regex=False).str.slice(0, 8)
    if index_dates.str.fullmatch(r"\d{8}").any():
        work["_date"] = index_dates
    elif "time" in work.columns:
        work["_date"] = pd.to_datetime(work["time"], unit="ms", errors="coerce").dt.strftime("%Y%m%d")
    elif "stime" in work.columns:
        work["_date"] = pd.to_datetime(work["stime"], unit="ms", errors="coerce").dt.strftime("%Y%m%d")
    else:
        work["_date"] = index_dates

    latest_date = work["_date"].max()
    print(f"本地区间: {work['_date'].min()} 至 {latest_date}")

    print(f"\n最近 {tail} 行:")
    print(work.tail(tail).to_string())

    target_rows = work[work["_date"] == target_date]
    print(f"\n目标日期 {target_date} 明细:")
    if target_rows.empty:
        print(f"[ERROR] 未读取到 {code} 在 {target_date} 的本地日线。")
        print("判断: 该标的这一天本地日线不可用，不能作为模型 end_date 或实盘候选依据。")
        return

    print(target_rows.to_string())

    row = target_rows.iloc[-1]
    volume = row.get("volume")
    amount = row.get("amount")
    print("\n完整性判断:")
    print(f"volume={volume}, amount={amount}")
    try:
        if float(volume) == 0 and float(amount) == 0:
            print("[WARN] 行情行存在，但 volume 和 amount 都是 0，疑似占位数据或未完整落地。")
        else:
            print("[OK] 目标日期存在，且 volume/amount 看起来不是零值占位。")
    except (TypeError, ValueError):
        print("[WARN] volume/amount 不是标准数值，请人工检查上面的原始行。")


# 2. 测试行情连接 (无需登录交易账号即可测试)
def test_data(args: argparse.Namespace) -> int:
    stock_code = args.code.strip()
    target_date = compact_date(args.date)
    end_date = compact_date(args.end_date or args.date)
    start_date = compact_date(args.start_date or default_start_date(end_date))

    print("\n================================================================================")
    print("MiniQMT 单标的日线调试")
    print("================================================================================")
    print(f"代码: {stock_code}")
    print(f"目标日期: {target_date}")
    print(f"读取区间: {start_date} 至 {end_date}")
    print(f"是否先下载: {args.download}")

    if args.download:
        print("\n调用 download_history_data...")
        result = xtdata.download_history_data(
            stock_code,
            period="1d",
            start_time=start_date,
            end_time=end_date,
        )
        print("download_history_data 返回值:")
        pprint(result)

    print("\n调用 get_local_data(fill_data=False)...")
    try:
        local_data = xtdata.get_local_data(
            field_list=[],
            stock_list=[stock_code],
            period="1d",
            start_time=start_date,
            end_time=end_date,
            fill_data=False,
        )
    except Exception as exc:
        print(f"[ERROR] get_local_data 调用失败: {type(exc).__name__}: {exc}")
        return 1

    print(f"get_local_data 返回键: {list(local_data.keys()) if isinstance(local_data, dict) else type(local_data)}")
    df = local_data.get(stock_code) if isinstance(local_data, dict) else None
    print_dataframe_info(stock_code, df, target_date, args.tail)

    print("\n调用 get_market_data 简单连通性检查...")
    try:
        data = xtdata.get_market_data(stock_list=[stock_code], period="1d")
        if data:
            print(f"[OK] get_market_data 有返回，{stock_code} 行情接口连通。")
        else:
            print("[WARN] get_market_data 返回空。")
    except Exception as exc:
        print(f"[WARN] get_market_data 调用失败: {type(exc).__name__}: {exc}")

    return 0

if __name__ == "__main__":
    raise SystemExit(test_data(build_parser().parse_args()))
