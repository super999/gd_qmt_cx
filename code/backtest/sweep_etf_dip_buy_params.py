#!/usr/bin/env python3
# coding: utf-8

import itertools
import json
from pathlib import Path

import pandas as pd

import minimal_stock_backtest as base


OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "etf_dip_buy_param_sweep"
TRAIN_END_DATE = "20251231"

# This is a deliberately small robustness scan, not a brute-force optimizer.
PARAM_GRID = {
    "DEV_DRAWDOWN_FROM_HIGH": [0.008, 0.012, 0.016],
    "DEV_TREND_FLOOR": [0.980, 0.985, 0.990],
    "DEV_INTRADAY_REBOUND_FROM_LOW": [0.004, 0.006, 0.008],
    "DEV_INTRADAY_CLOSE_IN_RANGE": [0.60, 0.70, 0.80],
    "DEV_INTRADAY_SCORE_MIN": [4, 5],
    "MAX_HOLD_DAYS": [3, 5, 7],
    "STOP_LOSS_PCT": [0.020, 0.030],
    "TAKE_PROFIT_PCT": [0.030, 0.040, 0.050],
}

BASELINE_PARAMS = {
    "DEV_DRAWDOWN_FROM_HIGH": base.DEV_DRAWDOWN_FROM_HIGH,
    "DEV_TREND_FLOOR": base.DEV_TREND_FLOOR,
    "DEV_INTRADAY_REBOUND_FROM_LOW": base.DEV_INTRADAY_REBOUND_FROM_LOW,
    "DEV_INTRADAY_CLOSE_IN_RANGE": base.DEV_INTRADAY_CLOSE_IN_RANGE,
    "DEV_INTRADAY_SCORE_MIN": base.DEV_INTRADAY_SCORE_MIN,
    "MAX_HOLD_DAYS": base.MAX_HOLD_DAYS,
    "STOP_LOSS_PCT": base.STOP_LOSS_PCT,
    "TAKE_PROFIT_PCT": base.TAKE_PROFIT_PCT,
}


def iter_param_sets():
    names = list(PARAM_GRID.keys())
    values = [PARAM_GRID[name] for name in names]
    for combo in itertools.product(*values):
        yield dict(zip(names, combo))


def apply_params(params):
    for name, value in params.items():
        setattr(base, name, value)


def is_baseline(params):
    return all(params[name] == BASELINE_PARAMS[name] for name in BASELINE_PARAMS)


def split_closed_trade_stats(trades_df, train_end_date):
    if trades_df.empty:
        return {
            "train_closed_trades": 0,
            "train_win_rate": 0.0,
            "train_pnl": 0.0,
            "valid_closed_trades": 0,
            "valid_win_rate": 0.0,
            "valid_pnl": 0.0,
        }

    sells = trades_df[trades_df["action"] == "sell"].copy()
    if sells.empty:
        return {
            "train_closed_trades": 0,
            "train_win_rate": 0.0,
            "train_pnl": 0.0,
            "valid_closed_trades": 0,
            "valid_win_rate": 0.0,
            "valid_pnl": 0.0,
        }

    sells["trade_date"] = sells["trade_date"].astype(str)
    train = sells[sells["trade_date"] <= train_end_date]
    valid = sells[sells["trade_date"] > train_end_date]

    def summarize(frame, prefix):
        if frame.empty:
            return {
                prefix + "_closed_trades": 0,
                prefix + "_win_rate": 0.0,
                prefix + "_pnl": 0.0,
            }
        return {
            prefix + "_closed_trades": int(len(frame)),
            prefix + "_win_rate": round(float((frame["pnl"] > 0).mean()), 6),
            prefix + "_pnl": round(float(frame["pnl"].sum()), 2),
        }

    stats = {}
    stats.update(summarize(train, "train"))
    stats.update(summarize(valid, "valid"))
    return stats


def rank_score(row):
    if row["closed_trade_count"] < 3:
        return -999.0
    if row["total_return"] <= 0:
        return -100.0 + row["total_return"]

    return round(
        row["total_return"] * 100.0
        + row["win_rate"] * 1.5
        - abs(row["max_drawdown"]) * 40.0
        - row["max_consecutive_losses"] * 0.10
        + min(row["closed_trade_count"], 8) * 0.03,
        6,
    )


def prepare_data():
    stock = base.STOCK_LIST[0]
    base.ensure_history_download(base.STOCK_LIST, base.DAILY_PERIOD, base.START_DATE, base.END_DATE)
    base.ensure_history_download(
        base.STOCK_LIST, base.INTRADAY_PERIOD, base.START_DATE, base.END_DATE
    )

    daily_frame = base.load_price_frame(
        stock, base.DAILY_PERIOD, base.START_DATE, base.END_DATE
    )
    intraday_frame = base.load_price_frame(
        stock, base.INTRADAY_PERIOD, base.START_DATE, base.END_DATE
    )
    daily_frame = base.enrich_daily_indicators(daily_frame)
    instrument_detail = base.xtdata.get_instrument_detail(stock, iscomplete=False)
    return stock, daily_frame, intraday_frame, instrument_detail


def run_sweep():
    stock, daily_frame, intraday_frame, instrument_detail = prepare_data()

    results = []
    for idx, params in enumerate(iter_param_sets(), start=1):
        apply_params(params)
        intraday_signals = base.build_intraday_signal_map(intraday_frame)
        summary, trades_df, _daily_df = base.run_backtest(
            stock, daily_frame, intraday_signals, instrument_detail
        )
        row = dict(params)
        row.update(
            {
                "case_id": idx,
                "is_baseline": is_baseline(params),
                "stock": summary["stock"],
                "final_equity": summary["final_equity"],
                "total_return": summary["total_return"],
                "trade_count": summary["trade_count"],
                "closed_trade_count": summary["closed_trade_count"],
                "win_rate": summary["win_rate"],
                "max_drawdown": summary["max_drawdown"],
                "avg_holding_days": summary["avg_holding_days"],
                "max_consecutive_losses": summary["max_consecutive_losses"],
            }
        )
        row.update(split_closed_trade_stats(trades_df, TRAIN_END_DATE))
        results.append(row)

    apply_params(BASELINE_PARAMS)
    result_df = pd.DataFrame(results)
    result_df["rank_score"] = result_df.apply(rank_score, axis=1)
    result_df = result_df.sort_values(
        ["rank_score", "total_return", "win_rate", "max_drawdown"],
        ascending=[False, False, False, False],
    )
    return result_df


def build_report(result_df):
    baseline = result_df[result_df["is_baseline"]].iloc[0].to_dict()
    eligible = result_df[
        (result_df["closed_trade_count"] >= 3)
        & (result_df["total_return"] > 0)
        & (result_df["max_drawdown"] >= -0.03)
    ].copy()
    top = result_df.head(10).copy()

    lines = [
        "# ETF低吸反弹参数稳健性扫描",
        "",
        "## 结论",
        "",
        "- 本文件是小范围网格扫描结果，不是最终参数优化结论。",
        "- 当前阶段样本仍然偏少，不能只按最高收益选择参数。",
        "- 更合理的筛选标准是：交易次数不过少、收益为正、最大回撤可控、参数附近结果不崩。",
        "",
        "## 基准参数结果",
        "",
        "- 总收益：`{}`".format(baseline["total_return"]),
        "- 胜率：`{}`".format(baseline["win_rate"]),
        "- 最大回撤：`{}`".format(baseline["max_drawdown"]),
        "- 闭合交易数：`{}`".format(baseline["closed_trade_count"]),
        "",
        "## 可接受候选数量",
        "",
        "- 满足 `闭合交易数 >= 3`、`总收益 > 0`、`最大回撤 >= -3%` 的参数组数量：`{}`".format(
            len(eligible)
        ),
        "",
        "## 排名前 10 参数组",
        "",
    ]

    show_cols = [
        "rank_score",
        "total_return",
        "win_rate",
        "max_drawdown",
        "closed_trade_count",
        "DEV_DRAWDOWN_FROM_HIGH",
        "DEV_TREND_FLOOR",
        "DEV_INTRADAY_REBOUND_FROM_LOW",
        "DEV_INTRADAY_CLOSE_IN_RANGE",
        "DEV_INTRADAY_SCORE_MIN",
        "MAX_HOLD_DAYS",
        "STOP_LOSS_PCT",
        "TAKE_PROFIT_PCT",
    ]
    display_top = top[show_cols].copy()
    header = "| " + " | ".join(display_top.columns) + " |"
    separator = "| " + " | ".join(["---"] * len(display_top.columns)) + " |"
    lines.append(header)
    lines.append(separator)
    for _, row in display_top.iterrows():
        values = [str(row[col]) for col in display_top.columns]
        lines.append("| " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            "## 使用建议",
            "",
            "- 不建议直接把排名第一的参数当成实盘参数。",
            "- 下一步应优先观察排名靠前参数是否集中在相似区间。",
            "- 如果高分参数都依赖极少交易或单一行情阶段，应该放弃继续细调，转向增加样本或换标的做交叉验证。",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Running ETF dip-buy parameter robustness sweep")
    print("grid cases:", len(list(iter_param_sets())))
    result_df = run_sweep()

    all_path = OUTPUT_DIR / "sweep_results.csv"
    top_path = OUTPUT_DIR / "top_candidates.csv"
    json_path = OUTPUT_DIR / "sweep_summary.json"
    report_path = OUTPUT_DIR / "参数稳健性扫描报告.md"

    result_df.to_csv(all_path, index=False, encoding="utf-8-sig")
    result_df.head(30).to_csv(top_path, index=False, encoding="utf-8-sig")
    summary = {
        "case_count": int(len(result_df)),
        "train_end_date": TRAIN_END_DATE,
        "baseline_params": BASELINE_PARAMS,
        "top_case": result_df.iloc[0].to_dict(),
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(build_report(result_df), encoding="utf-8")

    print("best:")
    print(result_df.head(5).to_string(index=False))
    print("outputs:")
    print(" -", all_path)
    print(" -", top_path)
    print(" -", json_path)
    print(" -", report_path)


if __name__ == "__main__":
    main()
