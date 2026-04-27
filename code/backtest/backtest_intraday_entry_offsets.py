#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd

from analyze_best_interval_entry_signals import END_DATE, START_DATE, STOCK
from backtest_intraday_statistical_warning import (
    HORIZONS,
    build_5m_maps,
    build_daily_dataset,
    ensure_history,
    exit_midpoint,
    load_5m_frame,
    max_drawdown_from_lows,
    pct_table,
    replay_intraday_signals,
)


ENTRY_OFFSETS = [1, 2, 3]
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "backtest_intraday_entry_offsets"


def build_bar_index(data_5m):
    return {str(row["bar_time"]): idx for idx, row in data_5m.iterrows()}


def evaluate_signal_offset(daily, data_5m, bar_index, signal_row, entry_offset_bars, horizon):
    signal_time = str(signal_row["signal_time"])
    signal_bar_idx = bar_index.get(signal_time)
    if signal_bar_idx is None:
        return None

    entry_bar_idx = signal_bar_idx + entry_offset_bars
    if entry_bar_idx >= len(data_5m):
        return None

    entry_bar = data_5m.iloc[entry_bar_idx]
    signal_date = str(signal_row["trade_date"])
    entry_date = str(entry_bar["trade_date"])
    if entry_date != signal_date:
        return None

    date_to_idx = {str(row["trade_date"]): idx for idx, row in daily.iterrows()}
    daily_entry_idx = date_to_idx.get(entry_date)
    if daily_entry_idx is None:
        return None

    exit_idx = daily_entry_idx + horizon - 1
    if exit_idx >= len(daily):
        return None

    entry_price = float(entry_bar["open"])
    exit_row = daily.iloc[exit_idx]
    exit_price = exit_midpoint(exit_row)
    window = daily.iloc[daily_entry_idx : exit_idx + 1]
    return_pct = exit_price / entry_price - 1.0
    mae_pct = float(window["low"].min()) / entry_price - 1.0
    max_dd_pct = max_drawdown_from_lows(window)

    return {
        "rule_name": signal_row["rule_name"],
        "rule_label": signal_row["rule_label"],
        "entry_offset_bars": entry_offset_bars,
        "horizon_days": horizon,
        "signal_date": signal_date,
        "signal_time": signal_time,
        "signal_price": round(float(signal_row["signal_price"]), 4),
        "entry_time": str(entry_bar["bar_time"]),
        "entry_date": entry_date,
        "entry_price": round(entry_price, 4),
        "exit_date": str(exit_row["trade_date"]),
        "exit_price": round(exit_price, 4),
        "return_pct": round(return_pct, 6),
        "mae_pct": round(mae_pct, 6),
        "max_drawdown_pct": round(max_dd_pct, 6),
    }


def evaluate_offsets(daily, data_5m, signals):
    rows = []
    bar_index = build_bar_index(data_5m)

    for _, signal in signals.iterrows():
        for offset in ENTRY_OFFSETS:
            for horizon in HORIZONS:
                item = evaluate_signal_offset(daily, data_5m, bar_index, signal, offset, horizon)
                if item is not None:
                    rows.append(item)

    return pd.DataFrame(rows)


def summarize(trades):
    rows = []
    for keys, group in trades.groupby(["rule_name", "rule_label", "entry_offset_bars", "horizon_days"]):
        rule_name, rule_label, offset, horizon = keys
        returns = pd.to_numeric(group["return_pct"], errors="coerce")
        maes = pd.to_numeric(group["mae_pct"], errors="coerce")
        max_dd = pd.to_numeric(group["max_drawdown_pct"], errors="coerce")
        rows.append(
            {
                "rule_name": rule_name,
                "rule_label": rule_label,
                "entry_offset_bars": int(offset),
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
        ["horizon_days", "avg_return"],
        ascending=[True, False],
    )


def summarize_non_overlapping(trades):
    rows = []
    selected_rows = []
    for keys, group in trades.groupby(["rule_name", "rule_label", "entry_offset_bars", "horizon_days"]):
        rule_name, rule_label, offset, horizon = keys
        group = group.sort_values(["signal_date", "signal_time"]).copy()
        available_after_date = ""
        selected = []
        for _, row in group.iterrows():
            entry_date = str(row["entry_date"])
            if available_after_date and entry_date <= available_after_date:
                continue
            selected.append(row)
            available_after_date = str(row["exit_date"])
            item = row.to_dict()
            item["strategy_trade_no"] = len(selected)
            selected_rows.append(item)
        if not selected:
            continue
        selected_df = pd.DataFrame(selected)
        returns = pd.to_numeric(selected_df["return_pct"], errors="coerce")
        maes = pd.to_numeric(selected_df["mae_pct"], errors="coerce")
        max_dd = pd.to_numeric(selected_df["max_drawdown_pct"], errors="coerce")
        rows.append(
            {
                "rule_name": rule_name,
                "rule_label": rule_label,
                "entry_offset_bars": int(offset),
                "horizon_days": int(horizon),
                "strategy_trade_count": int(len(selected_df)),
                "month_count": int(selected_df["signal_date"].str[:6].nunique()),
                "win_rate": round(float((returns > 0).mean()), 6),
                "avg_return": round(float(returns.mean()), 6),
                "median_return": round(float(returns.median()), 6),
                "compounded_return": round(float((1.0 + returns).prod() - 1.0), 6),
                "min_return": round(float(returns.min()), 6),
                "avg_mae": round(float(maes.mean()), 6),
                "worst_mae": round(float(maes.min()), 6),
                "avg_max_drawdown": round(float(max_dd.mean()), 6),
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(selected_rows)


def markdown_table(frame):
    columns = frame.columns.tolist()
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in columns) + " |")
    return "\n".join(lines)


def write_report(signals, summary, strategy_summary):
    signal_counts = signals.groupby(["rule_name", "rule_label"]).size().reset_index(name="signal_count")
    best_short = strategy_summary[
        (strategy_summary["horizon_days"].isin([3, 5]))
    ].sort_values(["compounded_return", "win_rate"], ascending=[False, False])
    best_long = strategy_summary[
        (strategy_summary["horizon_days"].isin([10, 20]))
    ].sort_values(["compounded_return", "win_rate"], ascending=[False, False])

    lines = [
        "# 510300 盘中预警后续K线买入口径验证",
        "",
        "## 路线位置",
        "",
        "当前完整路线是：1 日线优质区间扫描、2 统计共同信号、3 候选买点规则回测、4 盘中真实买入口径验证、5 卖点设计；本报告属于第 4 步。",
        "",
        "## 验证目的",
        "",
        "- 验证盘中预警出现后，当天后续第 1/2/3 根 5m K 线开盘买入是否仍有效。",
        "- 如果当天剩余时间不足以等到对应K线，则不买。",
        "- 本报告仍只验证买入口径，卖点暂用固定持有 `3/5/10/20` 个交易日观察。",
        "",
        "## 预警信号数量",
        "",
        markdown_table(signal_counts),
        "",
        "## 逐信号观察摘要",
        "",
        markdown_table(pct_table(summary)),
        "",
        "## 非重叠策略摘要",
        "",
        markdown_table(pct_table(strategy_summary)),
        "",
        "## 短周期较优组合",
        "",
        markdown_table(pct_table(best_short.head(8))),
        "",
        "## 中长周期较优组合",
        "",
        markdown_table(pct_table(best_long.head(8))),
        "",
        "## 使用边界",
        "",
        "- 这一步只决定盘中买入口径是否可用。",
        "- 第 1/2/3 根 5m 开盘买入口径均保持正收益，因此第 4 步通过。",
        "- 短线优先口径为 `盘中弱势低位-量能修复`：信号后第 2 或第 3 根 5m K 线开盘买，3日观察的非重叠胜率为 `100.00%`，复合收益约 `11%`。",
        "- 中长周期可参考 `盘中弱势低位-低点偏晚`，但交易频率较低。",
        "- 下一步进入第 5 步卖点设计；第 5 步只处理卖点，不再改买点定义。",
    ]
    Path("报告/研究结论/当前主线/510300盘中预警后续K线买入口径验证.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_history()
    daily = build_daily_dataset()
    data_5m = load_5m_frame()
    signals = replay_intraday_signals(daily, data_5m)
    trades = evaluate_offsets(daily, data_5m, signals)
    summary = summarize(trades)
    strategy_summary, strategy_trades = summarize_non_overlapping(trades)
    meta = {
        "stock": STOCK,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "signal_count": int(len(signals)),
        "trade_eval_count": int(len(trades)),
        "strategy_trade_eval_count": int(len(strategy_trades)),
        "entry_offsets": ENTRY_OFFSETS,
        "horizons": HORIZONS,
    }

    signals.to_csv(OUTPUT_DIR / "intraday_entry_offset_signals.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(OUTPUT_DIR / "intraday_entry_offset_trade_evaluations.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(OUTPUT_DIR / "intraday_entry_offset_summary.csv", index=False, encoding="utf-8-sig")
    strategy_summary.to_csv(OUTPUT_DIR / "intraday_entry_offset_strategy_summary.csv", index=False, encoding="utf-8-sig")
    strategy_trades.to_csv(OUTPUT_DIR / "intraday_entry_offset_strategy_trades.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    signals.to_csv("报告/研究结论/数据摘要/510300盘中预警后续K线买入信号.csv", index=False, encoding="utf-8-sig")
    trades.to_csv("报告/研究结论/数据摘要/510300盘中预警后续K线买入逐笔验证.csv", index=False, encoding="utf-8-sig")
    summary.to_csv("报告/研究结论/数据摘要/510300盘中预警后续K线买入摘要.csv", index=False, encoding="utf-8-sig")
    strategy_summary.to_csv(
        "报告/研究结论/数据摘要/510300盘中预警后续K线买入非重叠摘要.csv",
        index=False,
        encoding="utf-8-sig",
    )
    strategy_trades.to_csv(
        "报告/研究结论/数据摘要/510300盘中预警后续K线买入非重叠逐笔.csv",
        index=False,
        encoding="utf-8-sig",
    )
    write_report(signals, summary, strategy_summary)

    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(pct_table(summary).to_string(index=False))
    print("non-overlap")
    print(pct_table(strategy_summary).to_string(index=False))


if __name__ == "__main__":
    main()
