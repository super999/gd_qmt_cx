#!/usr/bin/env python3
# coding: utf-8
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from reporting import MetricsCalculator
from utils import print_title


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分析组合实验的收益质量")
    parser.add_argument("--experiment-dirs", nargs="+", required=True, help="一个或多个 portfolio_experiments 或 financial_filter_experiments 输出目录")
    parser.add_argument("--output-dir", default=None, help="输出目录；默认 outputs/portfolio_quality/<run_id>")
    return parser.parse_args()


def resolve_output_dir(value: str | None) -> Path:
    if value:
        return Path(value)
    return Path(__file__).resolve().parent / "outputs" / "portfolio_quality" / datetime.now().strftime("%Y%m%d_%H%M%S")


def monthly_returns(daily_df: pd.DataFrame) -> pd.DataFrame:
    df = daily_df.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    df["month"] = df["trade_date"].str.slice(0, 6)
    rows: List[Dict[str, Any]] = []
    for month, month_df in df.groupby("month"):
        rows.append(
            {
                "month": month,
                "month_return": float((1.0 + month_df["net_return"]).prod() - 1.0),
                "month_gross_return": float((1.0 + month_df["gross_return"]).prod() - 1.0),
                "days": int(len(month_df)),
                "avg_turnover": float(month_df["turnover"].mean()),
                "total_cost": float(month_df["cost"].sum()),
            }
        )
    return pd.DataFrame(rows)


def max_consecutive_negative_months(month_df: pd.DataFrame) -> int:
    max_streak = 0
    current = 0
    for value in month_df["month_return"]:
        if value < 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def summarize_experiment(
    experiment_dir: Path,
    source_label: str,
    experiment_name: str,
    experiment_name_cn: str = "",
) -> tuple[Dict[str, Any], pd.DataFrame]:
    daily_path = experiment_dir / source_label / experiment_name / "daily_nav.csv"
    if not daily_path.exists():
        raise FileNotFoundError("未找到 daily_nav.csv: {}".format(daily_path))
    daily_df = pd.read_csv(daily_path, encoding="utf-8-sig")
    month_df = monthly_returns(daily_df)
    total_return = float(daily_df["nav"].iloc[-1] - 1.0) if not daily_df.empty else 0.0
    max_drawdown = float(MetricsCalculator.max_drawdown(daily_df["nav"])) if not daily_df.empty else 0.0
    annualized = float(MetricsCalculator.annualized_return(daily_df["nav"])) if not daily_df.empty else 0.0
    worst_month = month_df.sort_values("month_return").head(1)
    best_month = month_df.sort_values("month_return", ascending=False).head(1)
    month_df.insert(0, "experiment_name", experiment_name)
    month_df.insert(0, "source_label", source_label)
    summary = {
        "source_label": source_label,
        "experiment_name": experiment_name,
        "experiment_name_cn": experiment_name_cn,
        "days": int(len(daily_df)),
        "months": int(len(month_df)),
        "total_return_with_cost": total_return,
        "annualized_return_with_cost": annualized,
        "max_drawdown_with_cost": max_drawdown,
        "return_drawdown_ratio": total_return / abs(max_drawdown) if max_drawdown else 0.0,
        "annualized_drawdown_ratio": annualized / abs(max_drawdown) if max_drawdown else 0.0,
        "month_win_rate": float((month_df["month_return"] > 0).mean()) if not month_df.empty else 0.0,
        "avg_month_return": float(month_df["month_return"].mean()) if not month_df.empty else 0.0,
        "median_month_return": float(month_df["month_return"].median()) if not month_df.empty else 0.0,
        "worst_month": str(worst_month["month"].iloc[0]) if not worst_month.empty else "",
        "worst_month_return": float(worst_month["month_return"].iloc[0]) if not worst_month.empty else 0.0,
        "best_month": str(best_month["month"].iloc[0]) if not best_month.empty else "",
        "best_month_return": float(best_month["month_return"].iloc[0]) if not best_month.empty else 0.0,
        "negative_months": int((month_df["month_return"] < 0).sum()) if not month_df.empty else 0,
        "max_consecutive_negative_months": max_consecutive_negative_months(month_df) if not month_df.empty else 0,
        "avg_turnover": float(daily_df["turnover"].mean()) if not daily_df.empty else 0.0,
        "total_cost": float(daily_df["cost"].sum()) if not daily_df.empty else 0.0,
    }
    return summary, month_df


def build_report(summary_df: pd.DataFrame, output_dir: Path) -> str:
    ranked = summary_df.sort_values(["return_drawdown_ratio", "total_return_with_cost"], ascending=[False, False])
    lines = [
        "# 组合收益质量分析报告",
        "",
        "- 输出目录：{}".format(output_dir),
        "- 实验数量：{}".format(len(summary_df)),
        "",
        "## 收益回撤比排序",
        "",
        "| source | experiment | 中文说明 | return | mdd | return/mdd | month_win | worst_month | worst_return | avg_turnover |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for _, row in ranked.iterrows():
        lines.append(
            "| {} | {} | {} | {:.2%} | {:.2%} | {:.2f} | {:.2%} | {} | {:.2%} | {:.4f} |".format(
                row["source_label"],
                row["experiment_name"],
                row.get("experiment_name_cn", ""),
                row["total_return_with_cost"],
                row["max_drawdown_with_cost"],
                row["return_drawdown_ratio"],
                row["month_win_rate"],
                row["worst_month"],
                row["worst_month_return"],
                row["avg_turnover"],
            )
        )
    return "\n".join(lines) + "\n"


def load_experiment_matrix(experiment_root: Path) -> pd.DataFrame:
    candidates = [
        experiment_root / "experiment_summary.csv",
        experiment_root / "financial_filter_summary.csv",
    ]
    for path in candidates:
        if path.exists():
            return pd.read_csv(path, encoding="utf-8-sig")
    raise FileNotFoundError("未找到实验汇总文件: {}".format(" 或 ".join(str(path) for path in candidates)))


def main() -> int:
    args = parse_args()
    output_dir = resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries: List[Dict[str, Any]] = []
    monthly_frames: List[pd.DataFrame] = []

    print_title("组合收益质量分析")
    print("输出目录: {}".format(output_dir))

    for experiment_root in [Path(path) for path in args.experiment_dirs]:
        matrix = load_experiment_matrix(experiment_root)
        for _, row in matrix.iterrows():
            summary, month_df = summarize_experiment(
                experiment_root,
                str(row["source_label"]),
                str(row["experiment_name"]),
                str(row.get("experiment_name_cn", "")),
            )
            summary["experiment_root"] = str(experiment_root)
            summaries.append(summary)
            monthly_frames.append(month_df)

    summary_df = pd.DataFrame(summaries)
    monthly_df = pd.concat(monthly_frames, ignore_index=True) if monthly_frames else pd.DataFrame()
    summary_path = output_dir / "quality_summary.csv"
    monthly_path = output_dir / "monthly_returns.csv"
    report_path = output_dir / "quality_report.md"
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    monthly_df.to_csv(monthly_path, index=False, encoding="utf-8-sig")
    report_path.write_text(build_report(summary_df, output_dir), encoding="utf-8")

    print("summary: {}".format(summary_path))
    print("monthly: {}".format(monthly_path))
    print("report: {}".format(report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
