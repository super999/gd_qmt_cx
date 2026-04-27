#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd

from analyze_best_interval_entry_signals import END_DATE, START_DATE, STOCK
from backtest_intraday_entry_offsets import build_bar_index
from backtest_intraday_statistical_warning import (
    build_daily_dataset,
    ensure_history,
    load_5m_frame,
    replay_intraday_signals,
)


OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "backtest_intraday_exit_rules"
ENTRY_RULE_NAME = "intraday_weak_volume_repair"
ENTRY_OFFSETS = [2, 3]


EXIT_RULES = [
    {
        "name": "time_3d",
        "label": "3日时间退出",
        "max_hold_days": 3,
    },
    {
        "name": "tp12_sl20_time3",
        "label": "止盈1.2止损2.0时间3日",
        "max_hold_days": 3,
        "take_profit_pct": 0.012,
        "stop_loss_pct": 0.020,
    },
    {
        "name": "tp18_sl25_time5",
        "label": "止盈1.8止损2.5时间5日",
        "max_hold_days": 5,
        "take_profit_pct": 0.018,
        "stop_loss_pct": 0.025,
    },
    {
        "name": "trail12_08_sl25_time5",
        "label": "浮盈1.2后回撤0.8保护时间5日",
        "max_hold_days": 5,
        "trail_activate_pct": 0.012,
        "trail_drawdown_pct": 0.008,
        "stop_loss_pct": 0.025,
    },
    {
        "name": "trail18_10_sl25_time10",
        "label": "浮盈1.8后回撤1.0保护时间10日",
        "max_hold_days": 10,
        "trail_activate_pct": 0.018,
        "trail_drawdown_pct": 0.010,
        "stop_loss_pct": 0.025,
    },
]


def trade_dates_between(all_dates, start_date, end_date):
    return [date for date in all_dates if start_date <= date <= end_date]


def last_bar_idx_for_date(data_5m, trade_date):
    matched = data_5m[data_5m["trade_date"] == trade_date]
    if matched.empty:
        return None
    return int(matched.index.max())


def next_executable_exit(data_5m, trigger_idx, entry_date):
    exit_idx = trigger_idx + 1
    while exit_idx < len(data_5m):
        row = data_5m.iloc[exit_idx]
        if str(row["trade_date"]) != entry_date:
            return int(exit_idx), float(row["open"]), str(row["bar_time"]), str(row["trade_date"])
        exit_idx += 1
    row = data_5m.iloc[trigger_idx]
    return int(trigger_idx), float(row["close"]), str(row["bar_time"]), str(row["trade_date"])


def build_entry_candidates(signals, data_5m):
    bar_index = build_bar_index(data_5m)
    rows = []
    filtered = signals[signals["rule_name"] == ENTRY_RULE_NAME].copy().sort_values("signal_time")

    for _, signal in filtered.iterrows():
        signal_bar_idx = bar_index.get(str(signal["signal_time"]))
        if signal_bar_idx is None:
            continue
        for offset in ENTRY_OFFSETS:
            entry_idx = signal_bar_idx + offset
            if entry_idx >= len(data_5m):
                continue
            entry_bar = data_5m.iloc[entry_idx]
            if str(entry_bar["trade_date"]) != str(signal["trade_date"]):
                continue
            rows.append(
                {
                    "rule_name": signal["rule_name"],
                    "rule_label": signal["rule_label"],
                    "entry_offset_bars": offset,
                    "signal_date": str(signal["trade_date"]),
                    "signal_time": str(signal["signal_time"]),
                    "signal_price": round(float(signal["signal_price"]), 4),
                    "signal_low_so_far": round(float(signal["signal_low_so_far"]), 4),
                    "entry_idx": int(entry_idx),
                    "entry_time": str(entry_bar["bar_time"]),
                    "entry_date": str(entry_bar["trade_date"]),
                    "entry_price": round(float(entry_bar["open"]), 4),
                }
            )
    return pd.DataFrame(rows)


def simulate_exit(data_5m, daily, entry, exit_rule):
    entry_idx = int(entry["entry_idx"])
    entry_date = str(entry["entry_date"])
    entry_price = float(entry["entry_price"])
    daily_dates = daily["trade_date"].astype(str).tolist()
    if entry_date not in daily_dates:
        return None

    entry_date_pos = daily_dates.index(entry_date)
    max_date_pos = min(entry_date_pos + int(exit_rule["max_hold_days"]) - 1, len(daily_dates) - 1)
    max_date = daily_dates[max_date_pos]
    last_idx = last_bar_idx_for_date(data_5m, max_date)
    if last_idx is None:
        return None

    max_close = entry_price
    max_high = entry_price
    trail_active = False
    trigger_idx = last_idx
    exit_reason = "持有天数达到上限"

    for idx in range(entry_idx + 1, last_idx + 1):
        row = data_5m.iloc[idx]
        current_date = str(row["trade_date"])
        high = float(row["high"])
        close = float(row["close"])
        max_close = max(max_close, close)
        max_high = max(max_high, high)

        if current_date == entry_date:
            continue

        if "stop_loss_pct" in exit_rule and close <= entry_price * (1 - float(exit_rule["stop_loss_pct"])):
            trigger_idx = idx
            exit_reason = "盘中5m收盘触发固定止损"
            break

        if "take_profit_pct" in exit_rule and close >= entry_price * (1 + float(exit_rule["take_profit_pct"])):
            trigger_idx = idx
            exit_reason = "盘中5m收盘触发固定止盈"
            break

        if "trail_activate_pct" in exit_rule:
            if max_high >= entry_price * (1 + float(exit_rule["trail_activate_pct"])):
                trail_active = True
            if trail_active and close <= max_close * (1 - float(exit_rule["trail_drawdown_pct"])):
                trigger_idx = idx
                exit_reason = "浮盈后盘中回撤触发移动保护"
                break

    if trigger_idx == last_idx and exit_reason == "持有天数达到上限":
        exit_idx = last_idx
        exit_row = data_5m.iloc[exit_idx]
        exit_price = float(exit_row["close"])
        exit_time = str(exit_row["bar_time"])
        exit_date = str(exit_row["trade_date"])
    else:
        exit_idx, exit_price, exit_time, exit_date = next_executable_exit(data_5m, trigger_idx, entry_date)

    window = data_5m.iloc[entry_idx : exit_idx + 1]
    return {
        "exit_rule_name": exit_rule["name"],
        "exit_rule_label": exit_rule["label"],
        "exit_idx": int(exit_idx),
        "exit_time": exit_time,
        "exit_date": exit_date,
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "return_pct": round(exit_price / entry_price - 1.0, 6),
        "mae_pct": round(float(window["low"].min()) / entry_price - 1.0, 6),
        "mfe_pct": round(float(window["high"].max()) / entry_price - 1.0, 6),
        "holding_trade_days": len(trade_dates_between(daily_dates, entry_date, exit_date)),
    }


def evaluate_exits(entries, data_5m, daily):
    rows = []
    for _, entry in entries.iterrows():
        for exit_rule in EXIT_RULES:
            exit_info = simulate_exit(data_5m, daily, entry, exit_rule)
            if exit_info is None:
                continue
            item = entry.to_dict()
            item.update(exit_info)
            rows.append(item)
    return pd.DataFrame(rows)


def summarize_non_overlapping(trades):
    rows = []
    selected_rows = []
    for keys, group in trades.groupby(["entry_offset_bars", "exit_rule_name", "exit_rule_label"]):
        offset, exit_rule_name, exit_rule_label = keys
        group = group.sort_values(["signal_time", "entry_time"]).copy()
        available_after_idx = -1
        selected = []
        for _, row in group.iterrows():
            if int(row["entry_idx"]) <= available_after_idx:
                continue
            selected.append(row)
            available_after_idx = int(row["exit_idx"])
            item = row.to_dict()
            item["strategy_trade_no"] = len(selected)
            selected_rows.append(item)

        selected_df = pd.DataFrame(selected)
        returns = pd.to_numeric(selected_df["return_pct"], errors="coerce")
        maes = pd.to_numeric(selected_df["mae_pct"], errors="coerce")
        mfes = pd.to_numeric(selected_df["mfe_pct"], errors="coerce")
        rows.append(
            {
                "entry_rule_label": selected_df.iloc[0]["rule_label"],
                "entry_offset_bars": int(offset),
                "exit_rule_name": exit_rule_name,
                "exit_rule_label": exit_rule_label,
                "strategy_trade_count": int(len(selected_df)),
                "month_count": int(selected_df["signal_date"].astype(str).str[:6].nunique()),
                "win_rate": round(float((returns > 0).mean()), 6),
                "avg_return": round(float(returns.mean()), 6),
                "median_return": round(float(returns.median()), 6),
                "compounded_return": round(float((1.0 + returns).prod() - 1.0), 6),
                "min_return": round(float(returns.min()), 6),
                "avg_mae": round(float(maes.mean()), 6),
                "worst_mae": round(float(maes.min()), 6),
                "avg_mfe": round(float(mfes.mean()), 6),
                "avg_holding_trade_days": round(float(selected_df["holding_trade_days"].mean()), 3),
            }
        )
    summary = pd.DataFrame(rows).sort_values(
        ["entry_offset_bars", "compounded_return"],
        ascending=[True, False],
    )
    return summary, pd.DataFrame(selected_rows)


def pct_table(frame):
    table = frame.copy()
    percent_cols = [
        "win_rate",
        "avg_return",
        "median_return",
        "compounded_return",
        "min_return",
        "avg_mae",
        "worst_mae",
        "avg_mfe",
    ]
    for col in percent_cols:
        if col in table.columns:
            table[col] = table[col].map(lambda value: "{:.2%}".format(float(value)))
    return table


def markdown_table(frame):
    columns = frame.columns.tolist()
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in columns) + " |")
    return "\n".join(lines)


def write_report(summary, selected_trades):
    best = summary.sort_values(["compounded_return", "win_rate"], ascending=[False, False]).head(10)
    reason_counts = (
        selected_trades.groupby(["entry_offset_bars", "exit_rule_label", "exit_reason"])
        .size()
        .reset_index(name="count")
        .sort_values(["entry_offset_bars", "exit_rule_label", "count"], ascending=[True, True, False])
    )
    lines = [
        "# 510300 盘中低位承接卖点验证",
        "",
        "## 路线位置",
        "",
        "当前完整路线是：1 日线优质区间扫描、2 统计共同信号、3 候选买点规则回测、4 盘中真实买入口径验证、5 卖点设计；本报告属于第 5 步。",
        "",
        "## 验证边界",
        "",
        "- 买点固定为第 4 步确认的 `盘中弱势低位-量能修复`。",
        "- 只验证信号后第 2 或第 3 根 5m K 线开盘买。",
        "- 严格保留 A 股 T+1：买入当天不卖出。",
        "- 本报告只比较卖点，不再修改买点条件。",
        "",
        "## 卖点候选",
        "",
        markdown_table(pd.DataFrame([{"exit_rule": rule["label"], **{k: v for k, v in rule.items() if k not in {"name", "label"}}} for rule in EXIT_RULES])),
        "",
        "## 非重叠策略摘要",
        "",
        markdown_table(pct_table(summary)),
        "",
        "## 综合靠前的卖点组合",
        "",
        markdown_table(pct_table(best)),
        "",
        "## 退出原因分布",
        "",
        markdown_table(reason_counts),
        "",
        "## 使用边界",
        "",
        "- 本轮表现最好的是 `3日时间退出`，但这里的“持有天数达到上限”不是失控兜底，而是主动定义的时间卖点。",
        "- 固定止盈和移动保护虽然更主动，但在当前样本中会过早截断修复收益，复合收益低于 3 日时间退出。",
        "- 宽松到 5 日或 10 日的卖点会显著降低收益，说明当前买点的主要优势集中在买入后的短周期修复。",
        "- 当前第 5 步初版结论：短线实盘/模拟监控的卖点基线应先用 `第3个交易日尾盘退出`，止损只作为风控兜底继续保留研究。",
        "- 本轮仍是历史回放，不等于自动交易指令；进入模拟监控前，还需要把买入预警和第3日退出写成实时日志程序。",
    ]
    Path("报告/研究结论/当前主线/510300盘中低位承接卖点验证.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_history()
    daily = build_daily_dataset()
    data_5m = load_5m_frame()
    signals = replay_intraday_signals(daily, data_5m)
    entries = build_entry_candidates(signals, data_5m)
    trades = evaluate_exits(entries, data_5m, daily)
    summary, selected_trades = summarize_non_overlapping(trades)
    meta = {
        "stock": STOCK,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "entry_rule_name": ENTRY_RULE_NAME,
        "entry_offsets": ENTRY_OFFSETS,
        "exit_rule_count": int(len(EXIT_RULES)),
        "entry_candidate_count": int(len(entries)),
        "trade_eval_count": int(len(trades)),
        "selected_trade_eval_count": int(len(selected_trades)),
    }

    entries.to_csv(OUTPUT_DIR / "exit_entry_candidates.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(OUTPUT_DIR / "exit_trade_evaluations.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(OUTPUT_DIR / "exit_strategy_summary.csv", index=False, encoding="utf-8-sig")
    selected_trades.to_csv(OUTPUT_DIR / "exit_selected_trades.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    entries.to_csv("报告/研究结论/数据摘要/510300盘中低位承接卖点入场样本.csv", index=False, encoding="utf-8-sig")
    trades.to_csv("报告/研究结论/数据摘要/510300盘中低位承接卖点逐笔验证.csv", index=False, encoding="utf-8-sig")
    summary.to_csv("报告/研究结论/数据摘要/510300盘中低位承接卖点摘要.csv", index=False, encoding="utf-8-sig")
    selected_trades.to_csv("报告/研究结论/数据摘要/510300盘中低位承接卖点非重叠逐笔.csv", index=False, encoding="utf-8-sig")
    write_report(summary, selected_trades)

    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(pct_table(summary).to_string(index=False))


if __name__ == "__main__":
    main()
