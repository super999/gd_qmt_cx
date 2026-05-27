#!/usr/bin/env python3
# coding: utf-8
r"""
A股全市场 LightGBM 多因子选股离线研究 v1。

这个文件只保留命令行入口。完整流程请从 `pipeline.py` 开始阅读：

1. `data.py`：股票池与行情读取
2. `dataset.py`：清洗、特征、标签
3. `modeling.py`：LightGBM 与 walk-forward
4. `portfolio.py`：选股与回测
5. `reporting.py`：结果输出
"""

from __future__ import annotations

import argparse
import traceback
from datetime import datetime
from pathlib import Path

try:
    import lightgbm as lgb
except Exception as exc:  # pragma: no cover - explicit runtime guard
    raise RuntimeError(
        "LightGBM 未安装或无法导入。请先运行："
        "d:\\python_envs\\gd_qmt_env\\python.exe -m pip install lightgbm"
    ) from exc

from config import StrategyConfig
from pipeline import ResearchPipeline
from reporting import MetricsCalculator
from utils import normalize_date, print_title


REFERENCE_DAILY_CODES = ["000001.SZ", "600000.SH", "510300.SH"]


def latest_complete_daily_date(frame) -> str:
    if frame is None or frame.empty:
        return ""
    complete_dates: list[str] = []
    for index, row in frame.iterrows():
        date = normalize_date(index)
        if not date:
            continue
        try:
            volume = float(row.get("volume", 0) or 0)
            amount = float(row.get("amount", 0) or 0)
        except Exception:
            volume = 0.0
            amount = 0.0
        if volume > 0 and amount > 0:
            complete_dates.append(date)
    return max(complete_dates) if complete_dates else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股全市场 LightGBM 多因子选股离线研究 v1")
    parser.add_argument("--start-date", default="20230101")
    parser.add_argument("--end-date", default=None, help="数据结束日；不填时自动使用本地日线已有的最新交易日")
    parser.add_argument("--max-stocks", type=int, default=None, help="小样本冒烟时限制股票数量；默认全量")
    parser.add_argument("--min-train-samples", type=int, default=3000)
    parser.add_argument("--min-prediction-date", default="20240101")
    parser.add_argument("--recent-prediction-days", type=int, default=None, help="只预测最近 N 个可用交易日；日常刷新可用，历史研究留空")
    parser.add_argument("--top-n", type=int, default=20, help="组合持仓数量")
    parser.add_argument("--rebalance-frequency", choices=["daily", "weekly"], default="daily", help="调仓频率")
    parser.add_argument("--hold-rank-buffer", type=int, default=0, help="排名缓冲；例如 Top20 可设 40")
    parser.add_argument("--min-holding-days", type=int, default=1, help="最少持有交易日数")
    parser.add_argument("--use-financial-factors", action="store_true", help="启用本地缓存中的公告日口径财务因子")
    parser.add_argument("--financial-cache-dir", default=None, help="财务缓存目录；默认 code/ml_stock_selection/outputs/financial_cache")
    parser.add_argument("--factor-dataset-format", choices=["parquet", "csv"], default="parquet", help="大表 factor_dataset 的输出格式；parquet 更快更小")
    return parser.parse_args()


def resolve_latest_local_daily_date(start_date: str, reference_codes: list[str] | None = None) -> str:
    from xtquant import xtdata

    codes = reference_codes or REFERENCE_DAILY_CODES
    today = datetime.now().strftime("%Y%m%d")
    data = xtdata.get_local_data(
        field_list=[],
        stock_list=codes,
        period="1d",
        start_time=start_date,
        end_time=today,
        count=-1,
        dividend_type="front",
        fill_data=False,
    )
    latest_by_code: dict[str, str] = {}
    complete_latest_by_code: dict[str, str] = {}
    if isinstance(data, dict):
        for code in codes:
            frame = data.get(code)
            if frame is None or frame.empty:
                continue
            dates = [normalize_date(index) for index in frame.index]
            dates = [date for date in dates if date]
            if dates:
                latest_by_code[code] = max(dates)
            complete_latest = latest_complete_daily_date(frame)
            if complete_latest:
                complete_latest_by_code[code] = complete_latest
    if not complete_latest_by_code:
        raise RuntimeError(
            "未指定 --end-date，且无法从本地 {} 日线识别最新完整交易日。"
            "请先更新本地日线，或显式传入 --end-date YYYYMMDD。".format(",".join(codes))
        )
    latest = max(complete_latest_by_code.values())
    raw_detail = ", ".join("{}={}".format(code, date) for code, date in latest_by_code.items())
    complete_detail = ", ".join("{}={}".format(code, date) for code, date in complete_latest_by_code.items())
    print(
        "自动识别 end_date: {}（完整日线探针 volume/amount>0：{}；原始最新日期：{}；今日上限 {}）".format(
            latest,
            complete_detail,
            raw_detail,
            today,
        )
    )
    return latest


def build_config(args: argparse.Namespace) -> StrategyConfig:
    end_date = args.end_date or resolve_latest_local_daily_date(args.start_date)
    config = StrategyConfig(
        start_date=args.start_date,
        end_date=end_date,
        min_train_samples=args.min_train_samples,
        min_prediction_date=args.min_prediction_date,
        recent_prediction_days=args.recent_prediction_days,
        top_n=args.top_n,
        rebalance_frequency=args.rebalance_frequency,
        hold_rank_buffer=args.hold_rank_buffer,
        min_holding_days=args.min_holding_days,
        use_financial_factors=args.use_financial_factors,
        factor_dataset_format=args.factor_dataset_format,
    )
    if args.financial_cache_dir:
        return StrategyConfig(
            start_date=args.start_date,
            end_date=end_date,
            min_train_samples=args.min_train_samples,
            min_prediction_date=args.min_prediction_date,
            recent_prediction_days=args.recent_prediction_days,
            top_n=args.top_n,
            rebalance_frequency=args.rebalance_frequency,
            hold_rank_buffer=args.hold_rank_buffer,
            min_holding_days=args.min_holding_days,
            use_financial_factors=args.use_financial_factors,
            factor_dataset_format=args.factor_dataset_format,
            financial_cache_dir=Path(args.financial_cache_dir),
        )
    return config


def main() -> int:
    args = parse_args()
    config = build_config(args)
    print_title("A股全市场 LightGBM 多因子选股 v1")
    print("LightGBM: {}".format(lgb.__version__))
    print("日期范围: {} 至 {}".format(config.start_date, config.end_date))
    print("max_stocks: {}".format(args.max_stocks))
    print("最近预测天数限制: {}".format(config.recent_prediction_days))
    print("TopN: {}".format(config.top_n))
    print("调仓频率: {}".format(config.rebalance_frequency))
    print("排名缓冲: {}".format(config.hold_rank_buffer))
    print("最少持有天数: {}".format(config.min_holding_days))
    print("交易成本: 单边 {}".format(config.transaction_cost_rate))
    print("factor_dataset 输出格式: {}".format(config.factor_dataset_format))
    print("财务因子: {}".format("启用" if config.use_financial_factors else "未启用"))
    if config.use_financial_factors:
        print("财务缓存目录: {}".format(config.financial_cache_dir))
    try:
        artifacts = ResearchPipeline(config).run(args.max_stocks)
        print_title("完成")
        print("报告: {}".format(artifacts.output_paths["report"]))
        print("summary: {}".format(artifacts.output_paths["summary"]))
        if artifacts.daily_nav.empty:
            print("未生成净值。")
        else:
            print("总收益（含成本）: {:.4%}".format(artifacts.daily_nav["nav"].iloc[-1] - 1.0))
            print("总收益（不计成本）: {:.4%}".format(artifacts.daily_nav["nav_no_cost"].iloc[-1] - 1.0))
            print("最大回撤（含成本）: {:.4%}".format(MetricsCalculator.max_drawdown(artifacts.daily_nav["nav"])))
        return 0
    except Exception as exc:
        print_title("程序异常")
        print("{}: {}".format(type(exc).__name__, exc))
        print(traceback.format_exc())
        print("排查提示:")
        print("- 请确认 MiniQMT 已启动，并且本地日线行情已下载。")
        print("- 小样本冒烟可加 --max-stocks 50 --min-train-samples 200。")
        print("- 如果启用财务因子，请先运行 prepare_financial_data.py 生成本地财务缓存。")
        print("- 如果 LightGBM 导入失败，请先安装 lightgbm。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
