#!/usr/bin/env python3
# coding: utf-8
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from config import StrategyConfig
from portfolio import DailyRebalanceBacktester, PortfolioSelector
from reporting import MetricsCalculator
from run_portfolio_experiments import build_buffer_values, parse_float_list, parse_int_list, summarize_daily_nav
from utils import print_title


FilterFunc = Callable[[pd.DataFrame], pd.Series]


FINANCIAL_FILTER_DESCRIPTIONS: Dict[str, str] = {
    "none": "不过滤，作为同一份预测结果的组合基线。",
    "avoid_bad_financial": "风险过滤：排除成长、ROE 或负债率明显落后的股票。",
    "growth_q50": "成长过滤：营收同比和归母净利润同比均高于当日截面中位数。",
    "growth_q70": "强成长过滤：营收同比和归母净利润同比均高于当日截面 70% 分位。",
    "quality_q50": "质量过滤：ROE 高于中位数，销售现金流高于中位数，资产负债率不高于 70% 分位。",
    "growth_quality_q50": "成长 + 质量过滤：同时满足 growth_q50 和 quality_q50。",
    "bp_value_q50": "低 PB 代理：BPS/close 高于当日截面中位数。",
    "bp_value_q70": "强低 PB 代理：BPS/close 高于当日截面 70% 分位。",
    "growth_quality_value_q50": "成长 + 质量 + 低 PB 代理：同时满足 growth_quality_q50 和 bp_value_q50。",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于已有财务增强 predictions.csv 测试财务因子过滤选股")
    parser.add_argument("--run-dirs", nargs="+", required=True, help="一个或多个 LightGBM 输出 run 目录，需包含 predictions.csv")
    parser.add_argument("--labels", nargs="*", default=None, help="可选标签，数量需与 run-dirs 一致")
    parser.add_argument(
        "--filters",
        default="none,avoid_bad_financial,growth_q50,growth_q70,quality_q50,growth_quality_q50,bp_value_q50,bp_value_q70,growth_quality_value_q50",
        help="逗号分隔过滤器名称",
    )
    parser.add_argument("--top-n-values", default="100,150", help="逗号分隔 TopN 列表")
    parser.add_argument("--rebalance-frequency", default="weekly", choices=["daily", "weekly"])
    parser.add_argument("--buffer-multipliers", default="3", help="逗号分隔排名缓冲倍数；例如 0,2,3")
    parser.add_argument("--min-holding-days", type=int, default=1)
    parser.add_argument(
        "--financial-cache-dir",
        default=None,
        help="财务缓存目录；默认 code/ml_stock_selection/outputs/financial_cache。用于低 PB 代理过滤。",
    )
    parser.add_argument(
        "--skip-value-filters-if-missing",
        action="store_true",
        help="若无法构造 BPS/close，跳过 value 过滤器；默认直接报错。",
    )
    return parser.parse_args()


def parse_str_list(value: str) -> List[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


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


def validate_args(args: argparse.Namespace) -> Tuple[List[Path], List[str], List[str]]:
    run_dirs = [Path(path) for path in args.run_dirs]
    labels = args.labels
    if labels and len(labels) != len(run_dirs):
        raise ValueError("--labels 数量必须与 --run-dirs 一致。")
    filter_names = parse_str_list(args.filters)
    unknown = sorted(set(filter_names) - set(FINANCIAL_FILTER_DESCRIPTIONS))
    if unknown:
        raise ValueError("不支持的过滤器: {}".format(", ".join(unknown)))
    return run_dirs, labels or [load_run_label(run_dir, run_dir.name) for run_dir in run_dirs], filter_names


def percentile_mask(day_df: pd.DataFrame, column: str, quantile: float, higher_is_better: bool = True) -> pd.Series:
    series = pd.to_numeric(day_df[column], errors="coerce") if column in day_df.columns else pd.Series(pd.NA, index=day_df.index)
    threshold = series.quantile(quantile)
    if pd.isna(threshold):
        return pd.Series(False, index=day_df.index)
    if higher_is_better:
        return series.notna() & (series >= threshold)
    return series.notna() & (series <= threshold)


def positive_mask(day_df: pd.DataFrame, column: str) -> pd.Series:
    series = pd.to_numeric(day_df[column], errors="coerce") if column in day_df.columns else pd.Series(pd.NA, index=day_df.index)
    return series.notna() & (series > 0)


def build_growth_mask(day_df: pd.DataFrame, quantile: float) -> pd.Series:
    return percentile_mask(day_df, "fin_inc_revenue_rate", quantile) & percentile_mask(day_df, "fin_inc_net_profit_rate", quantile)


def build_quality_mask(day_df: pd.DataFrame) -> pd.Series:
    roe = percentile_mask(day_df, "fin_du_return_on_equity", 0.5)
    cash = percentile_mask(day_df, "fin_sales_cash_flow", 0.5)
    debt = percentile_mask(day_df, "fin_gear_ratio", 0.7, higher_is_better=False)
    return roe & cash & debt


def build_avoid_bad_mask(day_df: pd.DataFrame) -> pd.Series:
    revenue = percentile_mask(day_df, "fin_inc_revenue_rate", 0.2)
    net_profit = percentile_mask(day_df, "fin_inc_net_profit_rate", 0.2)
    roe_positive = positive_mask(day_df, "fin_du_return_on_equity")
    debt_not_extreme = percentile_mask(day_df, "fin_gear_ratio", 0.9, higher_is_better=False)
    return revenue & net_profit & roe_positive & debt_not_extreme


def build_value_mask(day_df: pd.DataFrame, quantile: float) -> pd.Series:
    return percentile_mask(day_df, "fin_book_to_price", quantile)


def build_filter_mask(day_df: pd.DataFrame, filter_name: str) -> pd.Series:
    if filter_name == "none":
        return pd.Series(True, index=day_df.index)
    if filter_name == "avoid_bad_financial":
        return build_avoid_bad_mask(day_df)
    if filter_name == "growth_q50":
        return build_growth_mask(day_df, 0.5)
    if filter_name == "growth_q70":
        return build_growth_mask(day_df, 0.7)
    if filter_name == "quality_q50":
        return build_quality_mask(day_df)
    if filter_name == "growth_quality_q50":
        return build_growth_mask(day_df, 0.5) & build_quality_mask(day_df)
    if filter_name == "bp_value_q50":
        return build_value_mask(day_df, 0.5)
    if filter_name == "bp_value_q70":
        return build_value_mask(day_df, 0.7)
    if filter_name == "growth_quality_value_q50":
        return build_growth_mask(day_df, 0.5) & build_quality_mask(day_df) & build_value_mask(day_df, 0.5)
    raise ValueError("不支持的过滤器: {}".format(filter_name))


def value_filters_requested(filter_names: Iterable[str]) -> bool:
    return any("value" in name or name.startswith("bp_") for name in filter_names)


def load_value_source(financial_cache_dir: Path) -> pd.DataFrame:
    path = financial_cache_dir / "raw_PershareIndex.csv"
    if not path.exists():
        raise FileNotFoundError("未找到财务缓存: {}".format(path))
    source = pd.read_csv(path, encoding="utf-8-sig")
    required = {"code", "m_anntime", "m_timetag", "s_fa_bps"}
    missing = sorted(required - set(source.columns))
    if missing:
        raise RuntimeError("raw_PershareIndex.csv 缺少低 PB 代理字段: {}".format(", ".join(missing)))
    df = source[["code", "m_anntime", "m_timetag", "s_fa_bps"]].copy()
    df["announce_dt"] = pd.to_datetime(df["m_anntime"], format="%Y%m%d", errors="coerce")
    df["report_dt"] = pd.to_datetime(df["m_timetag"], format="%Y%m%d", errors="coerce")
    df["fin_bps"] = pd.to_numeric(df["s_fa_bps"], errors="coerce")
    df = df.dropna(subset=["announce_dt", "fin_bps"])
    return df.sort_values(["code", "announce_dt", "report_dt"])


def add_value_proxy(pred_df: pd.DataFrame, financial_cache_dir: Path) -> pd.DataFrame:
    if "fin_book_to_price" in pred_df.columns:
        return pred_df
    source = load_value_source(financial_cache_dir)
    if source.empty:
        raise RuntimeError("raw_PershareIndex.csv 中没有可用 BPS 数据，无法构造 BPS/close。")

    frames: List[pd.DataFrame] = []
    left = pred_df.copy()
    left["trade_dt"] = pd.to_datetime(left["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
    left["_row_id"] = range(len(left))
    for code, code_df in left.groupby("code", sort=False):
        right = source[source["code"] == code]
        if right.empty:
            tmp = code_df.copy()
            tmp["fin_bps"] = pd.NA
            tmp["fin_book_to_price"] = pd.NA
            frames.append(tmp)
            continue
        right = right.sort_values(["announce_dt", "report_dt"]).drop_duplicates("announce_dt", keep="last")
        merged = pd.merge_asof(
            code_df.sort_values("trade_dt"),
            right[["announce_dt", "fin_bps"]].sort_values("announce_dt"),
            left_on="trade_dt",
            right_on="announce_dt",
            direction="backward",
            allow_exact_matches=False,
        )
        frames.append(merged)

    result = pd.concat(frames, ignore_index=True).sort_values("_row_id")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result["fin_book_to_price"] = pd.to_numeric(result["fin_bps"], errors="coerce") / result["close"]
    return result.drop(columns=[col for col in ["trade_dt", "_row_id", "announce_dt"] if col in result.columns])


def apply_financial_filter(pred_df: pd.DataFrame, filter_name: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rows: List[Dict[str, Any]] = []
    frames: List[pd.DataFrame] = []
    for trade_date, day_df in pred_df.groupby("trade_date", sort=True):
        mask = build_filter_mask(day_df, filter_name)
        filtered = day_df[mask].copy()
        rows.append(
            {
                "trade_date": str(trade_date),
                "filter_name": filter_name,
                "original_count": int(len(day_df)),
                "filtered_count": int(len(filtered)),
                "pass_rate": float(len(filtered) / len(day_df)) if len(day_df) else 0.0,
            }
        )
        if not filtered.empty:
            frames.append(filtered)
    filtered_df = pd.concat(frames, ignore_index=True) if frames else pred_df.iloc[0:0].copy()
    return filtered_df, pd.DataFrame(rows)


def summarize_filter_daily(filter_daily: pd.DataFrame, top_n: int) -> Dict[str, Any]:
    if filter_daily.empty:
        return {
            "avg_original_count": 0.0,
            "avg_filtered_count": 0.0,
            "min_filtered_count": 0,
            "median_filtered_count": 0.0,
            "avg_filter_pass_rate": 0.0,
            "days_below_top_n": 0,
        }
    return {
        "avg_original_count": float(filter_daily["original_count"].mean()),
        "avg_filtered_count": float(filter_daily["filtered_count"].mean()),
        "min_filtered_count": int(filter_daily["filtered_count"].min()),
        "median_filtered_count": float(filter_daily["filtered_count"].median()),
        "avg_filter_pass_rate": float(filter_daily["pass_rate"].mean()),
        "days_below_top_n": int((filter_daily["filtered_count"] < top_n).sum()),
    }


def build_config(top_n: int, frequency: str, buffer_rank: int, min_holding_days: int) -> StrategyConfig:
    return StrategyConfig(
        top_n=top_n,
        rebalance_frequency=frequency,
        hold_rank_buffer=buffer_rank,
        min_holding_days=min_holding_days,
    )


def run_one_experiment(
    label: str,
    run_dir: Path,
    pred_df: pd.DataFrame,
    output_dir: Path,
    filter_name: str,
    filter_daily: pd.DataFrame,
    top_n: int,
    frequency: str,
    buffer_rank: int,
    min_holding_days: int,
) -> Dict[str, Any]:
    config = build_config(top_n, frequency, buffer_rank, min_holding_days)
    experiment_name = "{}_top{}_{}_buffer{}".format(filter_name, top_n, frequency, buffer_rank)
    experiment_dir = output_dir / label / experiment_name
    experiment_dir.mkdir(parents=True, exist_ok=True)

    selections = PortfolioSelector(config).select(pred_df)
    daily_df, trades_df = DailyRebalanceBacktester(config).run(selections)
    selections.to_csv(experiment_dir / "selected_portfolio.csv", index=False, encoding="utf-8-sig")
    daily_df.to_csv(experiment_dir / "daily_nav.csv", index=False, encoding="utf-8-sig")
    trades_df.to_csv(experiment_dir / "trades.csv", index=False, encoding="utf-8-sig")
    filter_daily.to_csv(experiment_dir / "filter_daily.csv", index=False, encoding="utf-8-sig")

    summary = summarize_daily_nav(label, run_dir, experiment_name, config, daily_df, selections)
    summary.update(
        {
            "filter_name": filter_name,
            "filter_description": FINANCIAL_FILTER_DESCRIPTIONS[filter_name],
            **summarize_filter_daily(filter_daily, top_n),
        }
    )
    (experiment_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def build_report(summary_df: pd.DataFrame, output_dir: Path, filter_names: List[str]) -> str:
    lines = [
        "# 财务过滤组合实验报告",
        "",
        "- 输出目录：{}".format(output_dir),
        "- 实验数量：{}".format(len(summary_df)),
        "",
        "## 过滤器说明",
        "",
        "| filter | 说明 |",
        "| --- | --- |",
    ]
    for name in filter_names:
        lines.append("| `{}` | {} |".format(name, FINANCIAL_FILTER_DESCRIPTIONS[name]))

    lines.extend(
        [
            "",
            "## 汇总排序",
            "",
            "| source | filter | experiment | return | max_drawdown | avg_turnover | avg_candidates | min_candidates | pass_rate | days_below_top_n |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    ranked = summary_df.sort_values(["total_return_with_cost", "max_drawdown_with_cost"], ascending=[False, False])
    for _, row in ranked.iterrows():
        lines.append(
            "| {} | `{}` | {} | {:.2%} | {:.2%} | {:.4f} | {:.1f} | {} | {:.2%} | {} |".format(
                row["source_label"],
                row["filter_name"],
                row["experiment_name"],
                row["total_return_with_cost"],
                row["max_drawdown_with_cost"],
                row["avg_turnover"],
                row["avg_filtered_count"],
                int(row["min_filtered_count"]),
                row["avg_filter_pass_rate"],
                int(row["days_below_top_n"]),
            )
        )
    lines.extend(
        [
            "",
            "## 读取说明",
            "",
            "- 本实验不重训 LightGBM，只读取已有 `predictions.csv`。",
            "- 财务过滤发生在组合构造之前：先缩小候选池，再按 `pred_return_5d` 和 `pred_up_prob` 排名。",
            "- `bp_value_*` 使用 `raw_PershareIndex.csv` 中的 `s_fa_bps / close` 作为低 PB 代理，按 `m_anntime < trade_date` 点时合并。",
            "- `days_below_top_n` 越高，说明过滤条件过窄，实际持仓可能不足 TopN。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    run_dirs, labels, filter_names = validate_args(args)
    top_n_values = parse_int_list(args.top_n_values)
    buffer_multipliers = parse_float_list(args.buffer_multipliers)
    financial_cache_dir = Path(args.financial_cache_dir) if args.financial_cache_dir else StrategyConfig().financial_cache_dir
    output_dir = Path(__file__).resolve().parent / "outputs" / "financial_filter_experiments" / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    print_title("财务过滤组合实验")
    print("输出目录: {}".format(output_dir))
    print("过滤器: {}".format(", ".join(filter_names)))

    summaries: List[Dict[str, Any]] = []
    for label, run_dir in zip(labels, run_dirs):
        predictions_path = run_dir / "predictions.csv"
        if not predictions_path.exists():
            raise FileNotFoundError("未找到 predictions.csv: {}".format(predictions_path))
        print("读取预测: {} -> {}".format(label, predictions_path))
        pred_df = pd.read_csv(predictions_path, encoding="utf-8-sig")
        pred_df["trade_date"] = pred_df["trade_date"].astype(str)

        if value_filters_requested(filter_names):
            try:
                print("合并低 PB 代理字段: {}".format(financial_cache_dir / "raw_PershareIndex.csv"))
                pred_df = add_value_proxy(pred_df, financial_cache_dir)
            except Exception:
                if not args.skip_value_filters_if_missing:
                    raise
                filter_names = [name for name in filter_names if not ("value" in name or name.startswith("bp_"))]
                print("无法构造低 PB 代理，已跳过 value 过滤器。")

        filtered_cache: Dict[str, Tuple[pd.DataFrame, pd.DataFrame]] = {}
        for filter_name in filter_names:
            filtered_df, filter_daily = apply_financial_filter(pred_df, filter_name)
            filtered_cache[filter_name] = (filtered_df, filter_daily)
            print(
                "{} {}: avg_candidates={:.1f}, pass_rate={:.2%}".format(
                    label,
                    filter_name,
                    filter_daily["filtered_count"].mean() if not filter_daily.empty else 0.0,
                    filter_daily["pass_rate"].mean() if not filter_daily.empty else 0.0,
                )
            )

        for filter_name, (filtered_df, filter_daily) in filtered_cache.items():
            for top_n in top_n_values:
                for buffer_rank in build_buffer_values(top_n, buffer_multipliers):
                    summary = run_one_experiment(
                        label,
                        run_dir,
                        filtered_df,
                        output_dir,
                        filter_name,
                        filter_daily,
                        top_n,
                        args.rebalance_frequency,
                        buffer_rank,
                        args.min_holding_days,
                    )
                    summaries.append(summary)
                    print(
                        "{} {}: return={:.2%}, mdd={:.2%}, turnover={:.4f}, avg_candidates={:.1f}".format(
                            label,
                            summary["experiment_name"],
                            summary["total_return_with_cost"],
                            summary["max_drawdown_with_cost"],
                            summary["avg_turnover"],
                            summary["avg_filtered_count"],
                        )
                    )

    summary_df = pd.DataFrame(summaries)
    summary_path = output_dir / "financial_filter_summary.csv"
    report_path = output_dir / "financial_filter_report.md"
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    report_path.write_text(build_report(summary_df, output_dir, filter_names), encoding="utf-8")
    print_title("完成")
    print("summary: {}".format(summary_path))
    print("report: {}".format(report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
