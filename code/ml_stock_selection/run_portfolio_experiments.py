#!/usr/bin/env python3
# coding: utf-8
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from config import StrategyConfig
from portfolio import DailyRebalanceBacktester, PortfolioSelector
from reporting import MetricsCalculator
from utils import print_title


TOP_N_VALUES = [20, 50, 100]
REBALANCE_FREQUENCIES = ["daily", "weekly"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于已有 predictions.csv 批量测试组合构造规则")
    parser.add_argument("--run-dirs", nargs="+", required=True, help="一个或多个 LightGBM 输出 run 目录")
    parser.add_argument("--labels", nargs="*", default=None, help="可选标签，数量需与 run-dirs 一致")
    parser.add_argument("--top-n-values", default="20,50,100", help="逗号分隔 TopN 列表")
    parser.add_argument("--rebalance-frequencies", default="daily,weekly", help="逗号分隔调仓频率")
    parser.add_argument("--buffer-multipliers", default="0,2", help="逗号分隔排名缓冲倍数；例如 0,1,1.5,2,3")
    parser.add_argument("--min-holding-days", type=int, default=1)
    return parser.parse_args()


def parse_int_list(value: str) -> List[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def parse_str_list(value: str) -> List[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_float_list(value: str) -> List[float]:
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def build_buffer_values(top_n: int, multipliers: List[float]) -> List[int]:
    values = [int(round(top_n * multiplier)) for multiplier in multipliers]
    return list(dict.fromkeys(values))


def describe_rebalance_frequency(frequency: str) -> str:
    if frequency == "daily":
        return "每日调仓"
    if frequency == "weekly":
        return "每周第一个可用预测日调仓"
    return frequency


def describe_portfolio_rule(top_n: int, frequency: str, buffer_rank: int, min_holding_days: int) -> str:
    parts = [
        "持有 {} 只股票".format(top_n),
        describe_rebalance_frequency(frequency),
    ]
    if buffer_rank > 0:
        parts.append("旧持仓只要当期排名仍在前 {} 名就优先保留".format(buffer_rank))
    else:
        parts.append("无排名缓冲，调仓时严格按当期排名重选")
    if min_holding_days > 1:
        parts.append("最少持有 {} 个交易日".format(min_holding_days))
    return "；".join(parts)


def load_run_label(run_dir: Path, fallback: str) -> str:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        return fallback
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return fallback
    if summary.get("use_financial_factors"):
        return "financial"
    return "market_only"


def build_config(top_n: int, frequency: str, buffer_rank: int, min_holding_days: int) -> StrategyConfig:
    return StrategyConfig(
        top_n=top_n,
        rebalance_frequency=frequency,
        hold_rank_buffer=buffer_rank,
        min_holding_days=min_holding_days,
    )


def summarize_daily_nav(
    label: str,
    run_dir: Path,
    experiment_name: str,
    config: StrategyConfig,
    daily_df: pd.DataFrame,
    selections: pd.DataFrame,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "source_label": label,
        "source_run_dir": str(run_dir),
        "experiment_name": experiment_name,
        "experiment_name_cn": describe_portfolio_rule(
            config.top_n,
            config.rebalance_frequency,
            config.hold_rank_buffer,
            config.min_holding_days,
        ),
        "top_n": config.top_n,
        "rebalance_frequency": config.rebalance_frequency,
        "hold_rank_buffer": config.hold_rank_buffer,
        "min_holding_days": config.min_holding_days,
        "days": 0,
        "trade_start_date": "",
        "trade_end_date": "",
        "total_return_with_cost": 0.0,
        "total_return_no_cost": 0.0,
        "return_cost_gap": 0.0,
        "annualized_return_with_cost": 0.0,
        "max_drawdown_with_cost": 0.0,
        "win_rate": 0.0,
        "avg_turnover": 0.0,
        "total_cost": 0.0,
        "avg_holding_count": 0.0,
        "avg_retained_count": 0.0,
        "avg_added_count": 0.0,
        "avg_removed_count": 0.0,
        "selection_rows": int(len(selections)),
    }
    if daily_df.empty:
        return row
    row.update(
        {
            "days": int(len(daily_df)),
            "trade_start_date": str(daily_df["trade_date"].min()),
            "trade_end_date": str(daily_df["trade_date"].max()),
            "total_return_with_cost": float(daily_df["nav"].iloc[-1] - 1.0),
            "total_return_no_cost": float(daily_df["nav_no_cost"].iloc[-1] - 1.0),
            "return_cost_gap": float(daily_df["nav_no_cost"].iloc[-1] - daily_df["nav"].iloc[-1]),
            "annualized_return_with_cost": float(MetricsCalculator.annualized_return(daily_df["nav"])),
            "max_drawdown_with_cost": float(MetricsCalculator.max_drawdown(daily_df["nav"])),
            "win_rate": float((daily_df["net_return"] > 0).mean()),
            "avg_turnover": float(daily_df["turnover"].mean()),
            "total_cost": float(daily_df["cost"].sum()),
            "avg_holding_count": float(daily_df["holding_count"].mean()),
            "avg_retained_count": float(daily_df["retained_count"].mean()) if "retained_count" in daily_df.columns else 0.0,
            "avg_added_count": float(daily_df["added_count"].mean()) if "added_count" in daily_df.columns else 0.0,
            "avg_removed_count": float(daily_df["removed_count"].mean()) if "removed_count" in daily_df.columns else 0.0,
        }
    )
    return row


def run_one_experiment(
    label: str,
    run_dir: Path,
    pred_df: pd.DataFrame,
    output_dir: Path,
    top_n: int,
    frequency: str,
    buffer_rank: int,
    min_holding_days: int,
) -> Dict[str, Any]:
    config = build_config(top_n, frequency, buffer_rank, min_holding_days)
    experiment_name = "top{}_{}_buffer{}".format(top_n, frequency, buffer_rank)
    experiment_dir = output_dir / label / experiment_name
    experiment_dir.mkdir(parents=True, exist_ok=True)

    selections = PortfolioSelector(config).select(pred_df)
    daily_df, trades_df = DailyRebalanceBacktester(config).run(selections)
    selections.to_csv(experiment_dir / "selected_portfolio.csv", index=False, encoding="utf-8-sig")
    daily_df.to_csv(experiment_dir / "daily_nav.csv", index=False, encoding="utf-8-sig")
    trades_df.to_csv(experiment_dir / "trades.csv", index=False, encoding="utf-8-sig")
    summary = summarize_daily_nav(label, run_dir, experiment_name, config, daily_df, selections)
    (experiment_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def build_report(summary_df: pd.DataFrame, output_dir: Path) -> str:
    lines = [
        "# 组合构造实验矩阵报告",
        "",
        "- 输出目录：{}".format(output_dir),
        "- 实验数量：{}".format(len(summary_df)),
        "",
        "## 汇总排序",
        "",
        "| source | experiment | 中文说明 | return | max_drawdown | avg_turnover | total_cost | win_rate |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    ranked = summary_df.sort_values(["total_return_with_cost", "max_drawdown_with_cost"], ascending=[False, False])
    for _, row in ranked.iterrows():
        lines.append(
            "| {} | {} | {} | {:.2%} | {:.2%} | {:.4f} | {:.2%} | {:.2%} |".format(
                row["source_label"],
                row["experiment_name"],
                row.get("experiment_name_cn", ""),
                row["total_return_with_cost"],
                row["max_drawdown_with_cost"],
                row["avg_turnover"],
                row["total_cost"],
                row["win_rate"],
            )
        )
    lines.extend(
        [
            "",
            "## 读取说明",
            "",
            "- `daily` 表示每日按排序调仓。",
            "- `weekly` 表示每周第一个可用预测日调仓，其余交易日沿用上一期持仓。",
            "- `buffer0` 表示无排名缓冲；其他 `bufferN` 表示旧持仓在对应排名内时优先保留。",
        ]
    )
    return "\n".join(lines) + "\n"


def validate_args(args: argparse.Namespace) -> List[str]:
    run_dirs = [Path(path) for path in args.run_dirs]
    labels = args.labels
    if labels and len(labels) != len(run_dirs):
        raise ValueError("--labels 数量必须与 --run-dirs 一致。")
    return labels or [load_run_label(run_dir, run_dir.name) for run_dir in run_dirs]


def main() -> int:
    args = parse_args()
    run_dirs = [Path(path) for path in args.run_dirs]
    labels = validate_args(args)
    top_n_values = parse_int_list(args.top_n_values)
    frequencies = parse_str_list(args.rebalance_frequencies)
    buffer_multipliers = parse_float_list(args.buffer_multipliers)
    output_dir = Path(__file__).resolve().parent / "outputs" / "portfolio_experiments" / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    print_title("组合构造实验矩阵")
    print("输出目录: {}".format(output_dir))

    summaries: List[Dict[str, Any]] = []
    for label, run_dir in zip(labels, run_dirs):
        predictions_path = run_dir / "predictions.csv"
        if not predictions_path.exists():
            raise FileNotFoundError("未找到 predictions.csv: {}".format(predictions_path))
        print("读取预测: {} -> {}".format(label, predictions_path))
        pred_df = pd.read_csv(predictions_path, encoding="utf-8-sig")
        pred_df["trade_date"] = pred_df["trade_date"].astype(str)
        for top_n in top_n_values:
            buffer_values = build_buffer_values(top_n, buffer_multipliers)
            for frequency in frequencies:
                for buffer_rank in buffer_values:
                    summary = run_one_experiment(
                        label,
                        run_dir,
                        pred_df,
                        output_dir,
                        top_n,
                        frequency,
                        buffer_rank,
                        args.min_holding_days,
                    )
                    summaries.append(summary)
                    print(
                        "{} {}: return={:.2%}, mdd={:.2%}, turnover={:.4f}".format(
                            label,
                            summary["experiment_name"],
                            summary["total_return_with_cost"],
                            summary["max_drawdown_with_cost"],
                            summary["avg_turnover"],
                        )
                    )

    summary_df = pd.DataFrame(summaries)
    summary_path = output_dir / "experiment_summary.csv"
    report_path = output_dir / "experiment_report.md"
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    report_path.write_text(build_report(summary_df, output_dir), encoding="utf-8")
    print_title("完成")
    print("summary: {}".format(summary_path))
    print("report: {}".format(report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
