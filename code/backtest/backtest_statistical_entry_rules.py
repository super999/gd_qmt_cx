#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd

from analyze_best_interval_entry_signals import (
    END_DATE,
    START_DATE,
    STOCK,
    add_daily_features,
    build_intraday_feature_frame,
    load_daily_frame,
)


OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "backtest_statistical_entry_rules"
HORIZONS = [3, 5, 10, 20]


RULES = [
    {
        "name": "stat_core",
        "label": "统计核心规则",
        "description": "日线明显超跌 + 当日弱势低位 + 低点偏晚 + 低点后量能不弱",
        "conditions": [
            ("pre_drawdown_from_high_10", "<=", -0.025),
            ("pre_close_vs_ma10", "<=", 0.0),
            ("pre_rsi6", "<=", 45.0),
            ("day_close_in_range", "<=", 0.45),
            ("day_return", "<=", 0.002),
            ("m5_low_pos_ratio", ">=", 0.45),
            ("m5_volume_ratio_after_low", ">=", -0.10),
        ],
    },
    {
        "name": "stat_balanced",
        "label": "统计平衡规则",
        "description": "保留日线超跌和弱势低位，放宽盘中量能条件以提高交易频率",
        "conditions": [
            ("pre_drawdown_from_high_10", "<=", -0.025),
            ("pre_close_vs_ma5", "<=", 0.0),
            ("day_close_in_range", "<=", 0.50),
            ("day_return", "<=", 0.003),
            ("m5_low_pos_ratio", ">=", 0.35),
            ("m5_volume_ratio_after_low", ">=", -0.30),
        ],
    },
    {
        "name": "daily_weak_low",
        "label": "日线弱势低位规则",
        "description": "只验证日线超跌和买入日弱势低位，不使用盘中量能",
        "conditions": [
            ("pre_drawdown_from_high_10", "<=", -0.025),
            ("pre_close_vs_ma10", "<=", 0.0),
            ("pre_rsi6", "<=", 48.0),
            ("day_close_in_range", "<=", 0.45),
            ("day_return", "<=", 0.003),
        ],
    },
]


def entry_midpoint(row):
    return float(row["low"]) + (float(row["close"]) - float(row["low"])) * 0.5


def exit_midpoint(row):
    return float(row["close"]) + (float(row["high"]) - float(row["close"])) * 0.5


def compare_value(value, op, threshold):
    if pd.isna(value):
        return False
    value = float(value)
    if op == "<=":
        return value <= threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == ">":
        return value > threshold
    raise ValueError("unsupported operator {}".format(op))


def build_dataset():
    daily_raw = load_daily_frame()
    daily_features = add_daily_features(daily_raw)
    daily_raw = daily_raw[(daily_raw["trade_date"] >= START_DATE) & (daily_raw["trade_date"] <= END_DATE)].copy()
    daily_features = daily_features[
        (daily_features["trade_date"] >= START_DATE) & (daily_features["trade_date"] <= END_DATE)
    ].copy()
    intraday = build_intraday_feature_frame(daily_features["trade_date"].tolist())
    dataset = daily_raw.merge(daily_features, on="trade_date", how="left")
    dataset = dataset.merge(intraday, on="trade_date", how="left")
    dataset = dataset.sort_values("trade_date").reset_index(drop=True)
    return dataset


def apply_rule(dataset, rule):
    mask = pd.Series(True, index=dataset.index)
    for feature, op, threshold in rule["conditions"]:
        mask &= dataset[feature].map(lambda value: compare_value(value, op, threshold))
    signals = dataset[mask].copy()
    signals["rule_name"] = rule["name"]
    signals["rule_label"] = rule["label"]
    return signals


def max_drawdown_from_lows(window):
    peak = None
    max_dd = 0.0
    for _, row in window.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        peak = high if peak is None else max(peak, high)
        if peak > 0:
            max_dd = min(max_dd, low / peak - 1.0)
    return max_dd


def evaluate_signal(dataset, signal_idx, rule, horizon, fill_mode):
    entry_row = dataset.iloc[signal_idx]
    if fill_mode == "research_midpoint":
        entry_idx = signal_idx
        entry_price = entry_midpoint(entry_row)
        entry_date = str(entry_row["trade_date"])
    elif fill_mode == "next_open":
        entry_idx = signal_idx + 1
        if entry_idx >= len(dataset):
            return None
        next_row = dataset.iloc[entry_idx]
        entry_price = float(next_row["open"])
        entry_date = str(next_row["trade_date"])
    else:
        raise ValueError("unsupported fill mode {}".format(fill_mode))

    exit_idx = entry_idx + horizon - 1
    if exit_idx >= len(dataset):
        return None
    exit_row = dataset.iloc[exit_idx]
    window = dataset.iloc[entry_idx : exit_idx + 1]
    exit_price = exit_midpoint(exit_row)
    return_pct = exit_price / entry_price - 1.0
    mae_pct = float(window["low"].min()) / entry_price - 1.0
    mfe_pct = float(window["high"].max()) / entry_price - 1.0
    max_dd_pct = max_drawdown_from_lows(window)

    item = {
        "rule_name": rule["name"],
        "rule_label": rule["label"],
        "fill_mode": fill_mode,
        "horizon_days": horizon,
        "signal_idx": int(signal_idx),
        "entry_idx": int(entry_idx),
        "exit_idx": int(exit_idx),
        "signal_date": str(entry_row["trade_date"]),
        "entry_date": entry_date,
        "entry_price": round(entry_price, 4),
        "exit_date": str(exit_row["trade_date"]),
        "exit_price": round(exit_price, 4),
        "return_pct": round(return_pct, 6),
        "mae_pct": round(mae_pct, 6),
        "mfe_pct": round(mfe_pct, 6),
        "max_drawdown_pct": round(max_dd_pct, 6),
    }
    for feature, _, _ in rule["conditions"]:
        item[feature] = round(float(entry_row[feature]), 6) if pd.notna(entry_row[feature]) else None
    return item


def evaluate_rules(dataset):
    trades = []
    signal_rows = []

    for rule in RULES:
        signals = apply_rule(dataset, rule)
        for idx, row in signals.iterrows():
            signal_rows.append(
                {
                    "rule_name": rule["name"],
                    "rule_label": rule["label"],
                    "signal_date": str(row["trade_date"]),
                    **{
                        feature: round(float(row[feature]), 6) if pd.notna(row[feature]) else None
                        for feature, _, _ in rule["conditions"]
                    },
                }
            )
            for fill_mode in ["research_midpoint", "next_open"]:
                for horizon in HORIZONS:
                    result = evaluate_signal(dataset, int(idx), rule, horizon, fill_mode)
                    if result is not None:
                        trades.append(result)

    return pd.DataFrame(signal_rows), pd.DataFrame(trades)


def summarize(trades):
    rows = []
    for keys, group in trades.groupby(["rule_name", "rule_label", "fill_mode", "horizon_days"]):
        rule_name, rule_label, fill_mode, horizon = keys
        returns = pd.to_numeric(group["return_pct"], errors="coerce")
        maes = pd.to_numeric(group["mae_pct"], errors="coerce")
        max_dd = pd.to_numeric(group["max_drawdown_pct"], errors="coerce")
        rows.append(
            {
                "rule_name": rule_name,
                "rule_label": rule_label,
                "fill_mode": fill_mode,
                "horizon_days": int(horizon),
                "trade_count": int(len(group)),
                "month_count": int(group["signal_date"].str[:6].nunique()),
                "win_rate": round(float((returns > 0).mean()), 6),
                "avg_return": round(float(returns.mean()), 6),
                "median_return": round(float(returns.median()), 6),
                "min_return": round(float(returns.min()), 6),
                "avg_mae": round(float(maes.mean()), 6),
                "worst_mae": round(float(maes.min()), 6),
                "avg_max_drawdown": round(float(max_dd.mean()), 6),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["fill_mode", "horizon_days", "avg_return"],
        ascending=[True, True, False],
    )


def summarize_non_overlapping_strategy(trades):
    rows = []
    selected_rows = []
    for keys, group in trades.groupby(["rule_name", "rule_label", "fill_mode", "horizon_days"]):
        rule_name, rule_label, fill_mode, horizon = keys
        group = group.sort_values(["signal_idx", "entry_idx"]).copy()
        available_after_idx = -1
        selected = []
        for _, row in group.iterrows():
            entry_idx = int(row["entry_idx"])
            exit_idx = int(row["exit_idx"])
            if entry_idx <= available_after_idx:
                continue
            selected.append(row)
            available_after_idx = exit_idx
            item = row.to_dict()
            item["strategy_trade_no"] = len(selected)
            selected_rows.append(item)

        if not selected:
            continue
        selected_df = pd.DataFrame(selected)
        returns = pd.to_numeric(selected_df["return_pct"], errors="coerce")
        maes = pd.to_numeric(selected_df["mae_pct"], errors="coerce")
        max_dd = pd.to_numeric(selected_df["max_drawdown_pct"], errors="coerce")
        compounded_return = float((1.0 + returns).prod() - 1.0)
        rows.append(
            {
                "rule_name": rule_name,
                "rule_label": rule_label,
                "fill_mode": fill_mode,
                "horizon_days": int(horizon),
                "strategy_trade_count": int(len(selected_df)),
                "month_count": int(selected_df["signal_date"].str[:6].nunique()),
                "win_rate": round(float((returns > 0).mean()), 6),
                "avg_return": round(float(returns.mean()), 6),
                "median_return": round(float(returns.median()), 6),
                "compounded_return": round(compounded_return, 6),
                "min_return": round(float(returns.min()), 6),
                "avg_mae": round(float(maes.mean()), 6),
                "worst_mae": round(float(maes.min()), 6),
                "avg_max_drawdown": round(float(max_dd.mean()), 6),
            }
        )

    summary = pd.DataFrame(rows).sort_values(
        ["fill_mode", "horizon_days", "compounded_return"],
        ascending=[True, True, False],
    )
    selected_trades = pd.DataFrame(selected_rows)
    return summary, selected_trades


def markdown_table(frame):
    columns = frame.columns.tolist()
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in columns) + " |")
    return "\n".join(lines)


def format_summary_table(summary):
    table = summary.copy()
    for col in ["win_rate", "avg_return", "median_return", "min_return", "avg_mae", "worst_mae", "avg_max_drawdown"]:
        table[col] = table[col].map(lambda value: "{:.2%}".format(float(value)))
    return table


def format_strategy_summary_table(summary):
    table = summary.copy()
    for col in [
        "win_rate",
        "avg_return",
        "median_return",
        "compounded_return",
        "min_return",
        "avg_mae",
        "worst_mae",
        "avg_max_drawdown",
    ]:
        table[col] = table[col].map(lambda value: "{:.2%}".format(float(value)))
    return table


def write_report(summary, strategy_summary, signals, trades):
    best_next_open = summary[
        (summary["fill_mode"] == "next_open") & (summary["horizon_days"].isin([5, 10, 20]))
    ].sort_values(["avg_return", "win_rate"], ascending=[False, False])
    report_summary = format_summary_table(summary)
    strategy_table = format_strategy_summary_table(strategy_summary)
    signal_count = signals.groupby(["rule_name", "rule_label"]).size().reset_index(name="signal_count")

    lines = [
        "# 510300 统计候选规则回测验证（第3步）",
        "",
        "## 验证边界",
        "",
        "- 本轮不沿用旧的强 V 反转规则。",
        "- 本轮只验证第2步统计结果整理出的候选买入规则。",
        "- 当前只验证买点预测力，卖点暂用固定持有 `3/5/10/20` 个交易日观察。",
        "- `research_midpoint` 为研究口径：买入日低点和收盘中间价买入。",
        "- `next_open` 为保守可执行口径：信号次日开盘买入。",
        "",
        "## 候选规则",
        "",
    ]
    for rule in RULES:
        lines.extend(
            [
                "### {}".format(rule["label"]),
                "",
                rule["description"],
                "",
                markdown_table(
                    pd.DataFrame(
                        [
                            {"feature": feature, "operator": op, "threshold": threshold}
                            for feature, op, threshold in rule["conditions"]
                        ]
                    )
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## 信号数量",
            "",
            markdown_table(signal_count),
            "",
            "## 回测摘要",
            "",
            markdown_table(report_summary),
            "",
            "## 非重叠策略回测摘要",
            "",
            markdown_table(strategy_table),
            "",
            "## 保守可执行口径下较好的组合",
            "",
            markdown_table(format_summary_table(best_next_open.head(8))),
            "",
            "## 结论",
            "",
            "- 如果 `next_open` 口径仍有正收益和较高胜率，说明买入规则有一定预测力。",
            "- 如果只在 `research_midpoint` 口径有效，而 `next_open` 失效，说明信号太依赖当天低位成交，盘中执行设计必须前移。",
            "- 当前仍未验证卖点，不能直接进入模拟盘自动交易。",
        ]
    )
    Path("报告/研究结论/当前主线/510300统计候选规则回测验证.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dataset = build_dataset()
    signals, trades = evaluate_rules(dataset)
    summary = summarize(trades)
    strategy_summary, strategy_trades = summarize_non_overlapping_strategy(trades)
    meta = {
        "stock": STOCK,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "bar_count": int(len(dataset)),
        "rule_count": int(len(RULES)),
        "signal_count": int(len(signals)),
        "trade_eval_count": int(len(trades)),
        "strategy_eval_count": int(len(strategy_trades)),
    }

    dataset.to_csv(OUTPUT_DIR / "stat_rule_dataset.csv", index=False, encoding="utf-8-sig")
    signals.to_csv(OUTPUT_DIR / "stat_rule_signals.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(OUTPUT_DIR / "stat_rule_trade_evaluations.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(OUTPUT_DIR / "stat_rule_summary.csv", index=False, encoding="utf-8-sig")
    strategy_trades.to_csv(OUTPUT_DIR / "stat_rule_strategy_trades.csv", index=False, encoding="utf-8-sig")
    strategy_summary.to_csv(OUTPUT_DIR / "stat_rule_strategy_summary.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    signals.to_csv(
        "报告/研究结论/数据摘要/510300统计候选规则信号.csv",
        index=False,
        encoding="utf-8-sig",
    )
    trades.to_csv(
        "报告/研究结论/数据摘要/510300统计候选规则逐笔验证.csv",
        index=False,
        encoding="utf-8-sig",
    )
    summary.to_csv(
        "报告/研究结论/数据摘要/510300统计候选规则回测摘要.csv",
        index=False,
        encoding="utf-8-sig",
    )
    strategy_summary.to_csv(
        "报告/研究结论/数据摘要/510300统计候选规则非重叠策略摘要.csv",
        index=False,
        encoding="utf-8-sig",
    )
    strategy_trades.to_csv(
        "报告/研究结论/数据摘要/510300统计候选规则非重叠逐笔.csv",
        index=False,
        encoding="utf-8-sig",
    )
    write_report(summary, strategy_summary, signals, trades)

    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(format_summary_table(summary).to_string(index=False))
    print("non-overlapping strategy")
    print(format_strategy_summary_table(strategy_summary).to_string(index=False))


if __name__ == "__main__":
    main()
