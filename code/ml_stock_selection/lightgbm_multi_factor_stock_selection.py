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
from utils import print_title


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股全市场 LightGBM 多因子选股离线研究 v1")
    parser.add_argument("--start-date", default="20230101")
    parser.add_argument("--end-date", default="20260511")
    parser.add_argument("--max-stocks", type=int, default=None, help="小样本冒烟时限制股票数量；默认全量")
    parser.add_argument("--min-train-samples", type=int, default=3000)
    parser.add_argument("--min-prediction-date", default="20240101")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> StrategyConfig:
    return StrategyConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        min_train_samples=args.min_train_samples,
        min_prediction_date=args.min_prediction_date,
    )


def main() -> int:
    args = parse_args()
    config = build_config(args)
    print_title("A股全市场 LightGBM 多因子选股 v1")
    print("LightGBM: {}".format(lgb.__version__))
    print("日期范围: {} 至 {}".format(config.start_date, config.end_date))
    print("max_stocks: {}".format(args.max_stocks))
    print("交易成本: 单边 {}".format(config.transaction_cost_rate))
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
        print("- 如果 LightGBM 导入失败，请先安装 lightgbm。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
