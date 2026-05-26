#!/usr/bin/env python3
# coding: utf-8
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd

from config import StrategyConfig
from reporting import MetricsCalculator
from run_financial_filter_experiments import (
    FINANCIAL_FILTER_DESCRIPTIONS,
    add_value_proxy,
    apply_financial_filter,
    parse_str_list,
    value_filters_requested,
)
from run_portfolio_experiments import describe_rebalance_frequency
from utils import print_title


DEFAULT_LIVE_EXPERIMENT_NAME = "bp_value_q70_max8_cash25_buffer24"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="小资金实盘约束近似回测：资金、最小买入金额、有限持仓数")
    parser.add_argument("--run-dirs", nargs="+", required=True, help="一个或多个 LightGBM 输出 run 目录，需包含 predictions.csv")
    parser.add_argument("--labels", nargs="*", default=None, help="可选标签，数量需与 run-dirs 一致")
    parser.add_argument("--filters", default="none,growth_q50,bp_value_q70", help="逗号分隔财务过滤器")
    parser.add_argument("--capital", type=float, default=400000.0, help="初始资金")
    parser.add_argument("--min-trade-amount", type=float, default=30000.0, help="单只股票最小目标买入金额")
    parser.add_argument("--max-holdings-values", default="5,8,10,12", help="逗号分隔最大持仓数量")
    parser.add_argument("--cash-reserve-rates", default="0.10,0.25", help="逗号分隔现金保留比例")
    parser.add_argument("--rebalance-frequency", default="weekly", choices=["daily", "weekly"])
    parser.add_argument("--buffer-multiplier", type=float, default=3.0, help="排名缓冲倍数，例如 3 表示 Top10 使用 buffer30")
    parser.add_argument("--transaction-cost-rate", type=float, default=0.0003)
    parser.add_argument("--max-risk-score", type=float, default=70.0)
    parser.add_argument("--financial-cache-dir", default=None, help="财务缓存目录；默认 outputs/financial_cache")
    return parser.parse_args()


def parse_int_list(value: str) -> List[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def parse_float_list(value: str) -> List[float]:
    return [float(part.strip()) for part in value.split(",") if part.strip()]


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


def validate_labels(run_dirs: List[Path], labels: Optional[List[str]]) -> List[str]:
    if labels and len(labels) != len(run_dirs):
        raise ValueError("--labels 数量必须与 --run-dirs 一致。")
    return labels or [load_run_label(run_dir, run_dir.name) for run_dir in run_dirs]


def describe_small_capital_rule(
    filter_name: str,
    max_holdings: int,
    cash_reserve_rate: float,
    buffer_rank: int,
    capital: float,
    min_trade_amount: float,
    frequency: str,
) -> str:
    filter_desc = FINANCIAL_FILTER_DESCRIPTIONS[filter_name].rstrip("。；; ")
    return (
        "{}；初始资金 {:.0f} 元；目标最多持有 {} 只；至少保留 {:.0%} 现金；"
        "单只目标买入金额不低于 {:.0f} 元；{}；旧持仓排名仍在前 {} 名就优先保留"
    ).format(
        filter_desc,
        capital,
        max_holdings,
        cash_reserve_rate,
        min_trade_amount,
        describe_rebalance_frequency(frequency),
        buffer_rank,
    )


def current_week(trade_date: str) -> str:
    return pd.Timestamp(str(trade_date)).strftime("%G-%V")


def should_rebalance(
    trade_date: str,
    frequency: str,
    has_holdings: bool,
    last_rebalance_week: str,
) -> bool:
    if not has_holdings:
        return True
    if frequency == "daily":
        return True
    if frequency == "weekly":
        return current_week(trade_date) != last_rebalance_week
    raise ValueError("不支持的调仓频率: {}".format(frequency))


def rank_candidates(day_df: pd.DataFrame, max_risk_score: float) -> pd.DataFrame:
    filtered = day_df[day_df["risk_score"] <= max_risk_score].copy()
    if filtered.empty:
        filtered = day_df.copy()
    ranked = filtered.sort_values(["pred_return_5d", "pred_up_prob"], ascending=[False, False]).copy()
    ranked["selection_rank"] = range(1, len(ranked) + 1)
    return ranked


def select_codes(
    ranked: pd.DataFrame,
    previous_codes: Set[str],
    max_holdings: int,
    buffer_rank: int,
) -> List[str]:
    keep_codes: List[str] = []
    if previous_codes and buffer_rank > 0:
        previous_ranked = ranked[ranked["code"].isin(previous_codes)]
        keep_codes.extend(previous_ranked[previous_ranked["selection_rank"] <= buffer_rank]["code"].tolist())
    keep_codes = list(dict.fromkeys(keep_codes))[:max_holdings]
    fill_count = max_holdings - len(keep_codes)
    if fill_count <= 0:
        return keep_codes
    filler = ranked[~ranked["code"].isin(set(keep_codes))].head(fill_count)["code"].tolist()
    return keep_codes + filler


def round_lot_shares(target_value: float, price: float) -> int:
    if price <= 0 or pd.isna(price):
        return 0
    return int(math.floor(target_value / price / 100.0) * 100)


def build_target_positions(
    day_df: pd.DataFrame,
    selected_codes: List[str],
    portfolio_value: float,
    cash_reserve_rate: float,
    min_trade_amount: float,
) -> Dict[str, Dict[str, float]]:
    if not selected_codes:
        return {}
    investable = portfolio_value * max(0.0, min(1.0, 1.0 - cash_reserve_rate))
    feasible_slots = int(math.floor(investable / min_trade_amount)) if min_trade_amount > 0 else len(selected_codes)
    target_count = min(len(selected_codes), feasible_slots)
    if target_count <= 0:
        return {}

    price_map = dict(zip(day_df["code"], pd.to_numeric(day_df["close"], errors="coerce")))
    target_value = investable / target_count
    positions: Dict[str, Dict[str, float]] = {}
    for code in selected_codes[:target_count]:
        price = float(price_map.get(code, 0.0) or 0.0)
        shares = round_lot_shares(target_value, price)
        value = shares * price
        if shares <= 0 or value < min_trade_amount:
            continue
        positions[code] = {"shares": float(shares), "value": float(value), "price": price}
    return positions


def compute_turnover(current: Dict[str, Dict[str, float]], target: Dict[str, Dict[str, float]]) -> float:
    all_codes = set(current) | set(target)
    return float(sum(abs(target.get(code, {}).get("value", 0.0) - current.get(code, {}).get("value", 0.0)) for code in all_codes))


def apply_daily_returns(
    positions: Dict[str, Dict[str, float]],
    day_df: pd.DataFrame,
) -> Tuple[Dict[str, Dict[str, float]], float]:
    if not positions:
        return {}, 0.0
    returns = dict(zip(day_df["code"], pd.to_numeric(day_df["realized_next_open_return"], errors="coerce").fillna(0.0)))
    next_positions: Dict[str, Dict[str, float]] = {}
    gross_pnl = 0.0
    for code, item in positions.items():
        ret = float(returns.get(code, 0.0) or 0.0)
        old_value = float(item["value"])
        new_value = old_value * (1.0 + ret)
        gross_pnl += new_value - old_value
        next_positions[code] = {
            "shares": float(item.get("shares", 0.0)),
            "value": float(new_value),
            "price": float(item.get("price", 0.0)),
        }
    return next_positions, gross_pnl


def simulate_small_capital(
    pred_df: pd.DataFrame,
    filter_name: str,
    label: str,
    run_dir: Path,
    output_dir: Path,
    capital: float,
    min_trade_amount: float,
    max_holdings: int,
    cash_reserve_rate: float,
    frequency: str,
    buffer_rank: int,
    transaction_cost_rate: float,
    max_risk_score: float,
) -> Dict[str, Any]:
    experiment_name = "{}_max{}_cash{}_buffer{}".format(
        filter_name,
        max_holdings,
        int(round(cash_reserve_rate * 100)),
        buffer_rank,
    )
    experiment_dir = output_dir / label / experiment_name
    experiment_dir.mkdir(parents=True, exist_ok=True)

    cash = float(capital)
    positions: Dict[str, Dict[str, float]] = {}
    last_week = ""
    daily_rows: List[Dict[str, Any]] = []
    holding_rows: List[Dict[str, Any]] = []
    trade_rows: List[Dict[str, Any]] = []

    previous_nav = float(capital)
    for trade_date, day_df in pred_df.groupby("trade_date", sort=True):
        trade_date = str(trade_date)
        day_df = day_df.copy()
        ranked = rank_candidates(day_df, max_risk_score)
        portfolio_value = cash + sum(item["value"] for item in positions.values())
        rebalance = should_rebalance(trade_date, frequency, bool(positions), last_week)
        turnover = 0.0
        cost = 0.0
        selected_codes: List[str] = list(positions)
        if rebalance:
            selected_codes = select_codes(ranked, set(positions), max_holdings, buffer_rank)
            target_positions = build_target_positions(day_df, selected_codes, portfolio_value, cash_reserve_rate, min_trade_amount)
            turnover = compute_turnover(positions, target_positions)
            cost = turnover * transaction_cost_rate
            cash = portfolio_value - sum(item["value"] for item in target_positions.values()) - cost
            trade_rows.extend(build_trade_rows(trade_date, positions, target_positions, ranked, cost))
            positions = target_positions
            last_week = current_week(trade_date)

        start_value = cash + sum(item["value"] for item in positions.values())
        positions, gross_pnl = apply_daily_returns(positions, day_df)
        end_value = cash + sum(item["value"] for item in positions.values())
        net_return = end_value / previous_nav - 1.0 if previous_nav else 0.0
        invested_value = sum(item["value"] for item in positions.values())
        daily_rows.append(
            {
                "trade_date": trade_date,
                "portfolio_value": end_value,
                "cash": cash,
                "invested_value": invested_value,
                "cash_rate": cash / end_value if end_value else 0.0,
                "holding_count": len(positions),
                "gross_pnl": gross_pnl,
                "turnover": turnover,
                "cost": cost,
                "net_return": net_return,
                "nav": end_value / capital if capital else 0.0,
                "rebalanced": rebalance,
                "selected_count_before_lot": len(selected_codes),
                "start_value_after_trade": start_value,
            }
        )
        holding_rows.extend(build_holding_rows(trade_date, positions, day_df))
        previous_nav = end_value

    daily_df = pd.DataFrame(daily_rows)
    holdings_df = pd.DataFrame(holding_rows)
    trades_df = pd.DataFrame(trade_rows)
    daily_df.to_csv(experiment_dir / "daily_nav.csv", index=False, encoding="utf-8-sig")
    holdings_df.to_csv(experiment_dir / "holdings.csv", index=False, encoding="utf-8-sig")
    trades_df.to_csv(experiment_dir / "trades.csv", index=False, encoding="utf-8-sig")
    summary = summarize_small_capital(
        label,
        run_dir,
        experiment_name,
        filter_name,
        capital,
        min_trade_amount,
        max_holdings,
        cash_reserve_rate,
        frequency,
        buffer_rank,
        daily_df,
        trades_df,
    )
    (experiment_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def build_trade_rows(
    trade_date: str,
    current: Dict[str, Dict[str, float]],
    target: Dict[str, Dict[str, float]],
    ranked: pd.DataFrame,
    cost: float,
) -> List[Dict[str, Any]]:
    rank_map = dict(zip(ranked["code"], ranked["selection_rank"]))
    rows: List[Dict[str, Any]] = []
    all_codes = sorted(set(current) | set(target))
    for code in all_codes:
        old_value = current.get(code, {}).get("value", 0.0)
        new_value = target.get(code, {}).get("value", 0.0)
        delta = new_value - old_value
        if abs(delta) < 1e-6:
            continue
        rows.append(
            {
                "trade_date": trade_date,
                "code": code,
                "action": "buy" if delta > 0 else "sell",
                "old_value": old_value,
                "new_value": new_value,
                "trade_value": abs(delta),
                "allocated_cost": cost * abs(delta) / sum(abs(target.get(item, {}).get("value", 0.0) - current.get(item, {}).get("value", 0.0)) for item in all_codes)
                if all_codes and sum(abs(target.get(item, {}).get("value", 0.0) - current.get(item, {}).get("value", 0.0)) for item in all_codes)
                else 0.0,
                "selection_rank": rank_map.get(code),
            }
        )
    return rows


def build_holding_rows(trade_date: str, positions: Dict[str, Dict[str, float]], day_df: pd.DataFrame) -> List[Dict[str, Any]]:
    info = day_df.set_index("code")
    rows: List[Dict[str, Any]] = []
    for code, item in positions.items():
        if code in info.index:
            row = info.loc[code]
            name = row.get("name", "")
            pred_return = row.get("pred_return_5d", pd.NA)
            pred_up_prob = row.get("pred_up_prob", pd.NA)
            risk_score = row.get("risk_score", pd.NA)
        else:
            name = ""
            pred_return = pd.NA
            pred_up_prob = pd.NA
            risk_score = pd.NA
        rows.append(
            {
                "trade_date": trade_date,
                "code": code,
                "name": name,
                "shares": item.get("shares", 0.0),
                "value": item.get("value", 0.0),
                "pred_return_5d": pred_return,
                "pred_up_prob": pred_up_prob,
                "risk_score": risk_score,
            }
        )
    return rows


def summarize_small_capital(
    label: str,
    run_dir: Path,
    experiment_name: str,
    filter_name: str,
    capital: float,
    min_trade_amount: float,
    max_holdings: int,
    cash_reserve_rate: float,
    frequency: str,
    buffer_rank: int,
    daily_df: pd.DataFrame,
    trades_df: pd.DataFrame,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "source_label": label,
        "source_run_dir": str(run_dir),
        "experiment_name": experiment_name,
        "experiment_name_cn": describe_small_capital_rule(filter_name, max_holdings, cash_reserve_rate, buffer_rank, capital, min_trade_amount, frequency),
        "filter_name": filter_name,
        "filter_description": FINANCIAL_FILTER_DESCRIPTIONS[filter_name],
        "capital": capital,
        "min_trade_amount": min_trade_amount,
        "max_holdings": max_holdings,
        "cash_reserve_rate": cash_reserve_rate,
        "rebalance_frequency": frequency,
        "hold_rank_buffer": buffer_rank,
        "days": 0,
        "trade_start_date": "",
        "trade_end_date": "",
        "total_return_with_cost": 0.0,
        "annualized_return_with_cost": 0.0,
        "max_drawdown_with_cost": 0.0,
        "win_rate": 0.0,
        "avg_turnover_amount": 0.0,
        "total_cost": 0.0,
        "avg_holding_count": 0.0,
        "min_holding_count": 0,
        "avg_cash_rate": 0.0,
        "max_single_day_loss": 0.0,
        "trade_count": int(len(trades_df)),
    }
    if daily_df.empty:
        return summary
    nav = daily_df["nav"]
    summary.update(
        {
            "days": int(len(daily_df)),
            "trade_start_date": str(daily_df["trade_date"].min()),
            "trade_end_date": str(daily_df["trade_date"].max()),
            "final_value": float(daily_df["portfolio_value"].iloc[-1]),
            "total_return_with_cost": float(nav.iloc[-1] - 1.0),
            "annualized_return_with_cost": float(MetricsCalculator.annualized_return(nav)),
            "max_drawdown_with_cost": float(MetricsCalculator.max_drawdown(nav)),
            "win_rate": float((daily_df["net_return"] > 0).mean()),
            "avg_turnover_amount": float(daily_df["turnover"].mean()),
            "total_cost": float(daily_df["cost"].sum()),
            "avg_holding_count": float(daily_df["holding_count"].mean()),
            "min_holding_count": int(daily_df["holding_count"].min()),
            "avg_cash_rate": float(daily_df["cash_rate"].mean()),
            "max_single_day_loss": float(daily_df["net_return"].min()),
        }
    )
    return summary


def build_report(summary_df: pd.DataFrame, output_dir: Path) -> str:
    ranked = summary_df.sort_values(["max_drawdown_with_cost", "total_return_with_cost"], ascending=[False, False])
    lines = [
        "# 小资金实盘约束近似回测报告",
        "",
        "- 输出目录：{}".format(output_dir),
        "- 实验数量：{}".format(len(summary_df)),
        "",
        "## 汇总排序",
        "",
        "| source | experiment | 中文说明 | return | mdd | worst_day | avg_cash | avg_holdings | total_cost |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in ranked.iterrows():
        lines.append(
            "| {} | {} | {} | {:.2%} | {:.2%} | {:.2%} | {:.2%} | {:.2f} | {:.2f} |".format(
                row["source_label"],
                row["experiment_name"],
                row["experiment_name_cn"],
                row["total_return_with_cost"],
                row["max_drawdown_with_cost"],
                row["max_single_day_loss"],
                row["avg_cash_rate"],
                row["avg_holding_count"],
                row["total_cost"],
            )
        )
    lines.extend(
        [
            "",
            "## 读取说明",
            "",
            "- 本脚本使用信号日 `close` 近似估算买入金额和 100 股整数倍，收益仍使用既有 `realized_next_open_return`。",
            "- 因预测文件没有次日开盘价，本结果用于评估小资金仓位约束，不等同于正式撮合回测。",
            "- 排序优先按最大回撤较小，其次按收益较高。",
        ]
    )
    return "\n".join(lines) + "\n"


def write_latest_candidates(output_dir: Path, summary_df: pd.DataFrame) -> Path:
    if summary_df.empty:
        raise RuntimeError("summary_df 为空，无法生成 latest_candidates.csv")

    preferred = summary_df[summary_df["experiment_name"] == DEFAULT_LIVE_EXPERIMENT_NAME]
    if preferred.empty:
        ranked = summary_df.sort_values(["max_drawdown_with_cost", "total_return_with_cost"], ascending=[False, False])
        selected = ranked.iloc[0]
    else:
        selected = preferred.iloc[0]

    experiment_dir = output_dir / str(selected["source_label"]) / str(selected["experiment_name"])
    holdings_path = experiment_dir / "holdings.csv"
    trades_path = experiment_dir / "trades.csv"
    if not holdings_path.exists():
        raise FileNotFoundError("未找到持仓候选文件: {}".format(holdings_path))

    holdings = pd.read_csv(holdings_path, encoding="utf-8-sig")
    if holdings.empty:
        candidates = pd.DataFrame()
    else:
        holdings["trade_date"] = holdings["trade_date"].astype(str)
        latest_date = str(holdings["trade_date"].max())
        candidates = holdings[holdings["trade_date"] == latest_date].copy()
        candidates["candidate_date"] = latest_date
        candidates["action_hint"] = "保留/持有"
        if trades_path.exists():
            trades = pd.read_csv(trades_path, encoding="utf-8-sig")
            if not trades.empty:
                trades["trade_date"] = trades["trade_date"].astype(str)
                latest_trades = trades[trades["trade_date"] == latest_date]
                action_map = dict(zip(latest_trades["code"], latest_trades["action"]))
                candidates["action_hint"] = candidates["code"].map(action_map).fillna("保留/持有")
                candidates["action_hint"] = candidates["action_hint"].replace({"buy": "新增/加仓", "sell": "减仓/卖出"})
        candidates["source_label"] = selected["source_label"]
        candidates["experiment_name"] = selected["experiment_name"]
        candidates["experiment_name_cn"] = selected["experiment_name_cn"]
        candidates["source_run_dir"] = selected["source_run_dir"]
        candidates = candidates[
            [
                "candidate_date",
                "code",
                "name",
                "action_hint",
                "shares",
                "value",
                "pred_return_5d",
                "pred_up_prob",
                "risk_score",
                "experiment_name",
                "experiment_name_cn",
                "source_run_dir",
            ]
        ].sort_values(["action_hint", "value"], ascending=[True, False])

    path = output_dir / "latest_candidates.csv"
    candidates.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def main() -> int:
    args = parse_args()
    run_dirs = [Path(path) for path in args.run_dirs]
    labels = validate_labels(run_dirs, args.labels)
    filter_names = parse_str_list(args.filters)
    unknown = sorted(set(filter_names) - set(FINANCIAL_FILTER_DESCRIPTIONS))
    if unknown:
        raise ValueError("不支持的过滤器: {}".format(", ".join(unknown)))

    max_holdings_values = parse_int_list(args.max_holdings_values)
    cash_reserve_rates = parse_float_list(args.cash_reserve_rates)
    financial_cache_dir = Path(args.financial_cache_dir) if args.financial_cache_dir else StrategyConfig().financial_cache_dir
    output_dir = Path(__file__).resolve().parent / "outputs" / "small_capital_experiments" / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    print_title("小资金实盘约束近似回测")
    print("输出目录: {}".format(output_dir))
    print("初始资金: {:.0f}, 单只最小买入金额: {:.0f}".format(args.capital, args.min_trade_amount))

    summaries: List[Dict[str, Any]] = []
    for label, run_dir in zip(labels, run_dirs):
        predictions_path = run_dir / "predictions.csv"
        if not predictions_path.exists():
            raise FileNotFoundError("未找到 predictions.csv: {}".format(predictions_path))
        pred_df = pd.read_csv(predictions_path, encoding="utf-8-sig")
        pred_df["trade_date"] = pred_df["trade_date"].astype(str)
        if value_filters_requested(filter_names):
            pred_df = add_value_proxy(pred_df, financial_cache_dir)

        filtered_cache: Dict[str, pd.DataFrame] = {}
        for filter_name in filter_names:
            filtered_df, filter_daily = apply_financial_filter(pred_df, filter_name)
            filtered_cache[filter_name] = filtered_df
            print(
                "{} {}: avg_candidates={:.1f}, pass_rate={:.2%}".format(
                    label,
                    filter_name,
                    filter_daily["filtered_count"].mean() if not filter_daily.empty else 0.0,
                    filter_daily["pass_rate"].mean() if not filter_daily.empty else 0.0,
                )
            )

        for filter_name, filtered_df in filtered_cache.items():
            for max_holdings in max_holdings_values:
                buffer_rank = int(round(max_holdings * args.buffer_multiplier))
                for cash_reserve_rate in cash_reserve_rates:
                    summary = simulate_small_capital(
                        filtered_df,
                        filter_name,
                        label,
                        run_dir,
                        output_dir,
                        args.capital,
                        args.min_trade_amount,
                        max_holdings,
                        cash_reserve_rate,
                        args.rebalance_frequency,
                        buffer_rank,
                        args.transaction_cost_rate,
                        args.max_risk_score,
                    )
                    summaries.append(summary)
                    print(
                        "{} {}: return={:.2%}, mdd={:.2%}, avg_holdings={:.2f}, avg_cash={:.2%}".format(
                            label,
                            summary["experiment_name"],
                            summary["total_return_with_cost"],
                            summary["max_drawdown_with_cost"],
                            summary["avg_holding_count"],
                            summary["avg_cash_rate"],
                        )
                    )

    summary_df = pd.DataFrame(summaries)
    summary_path = output_dir / "small_capital_summary.csv"
    report_path = output_dir / "small_capital_report.md"
    candidates_path = write_latest_candidates(output_dir, summary_df)
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    report_path.write_text(build_report(summary_df, output_dir), encoding="utf-8")
    print_title("完成")
    print("summary: {}".format(summary_path))
    print("report: {}".format(report_path))
    print("latest_candidates: {}".format(candidates_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
