#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd

import minimal_stock_backtest as base


STOCK = "510300.SH"
START_DATE = "20250425"
END_DATE = "20260424"
PERIOD = "1d"

MIN_HOLD_TRADE_DAYS = 2
MAX_HOLD_TRADE_DAYS = 20
TOP_RAW_N = 100
TOP_EVENT_N = 12

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "best_trade_intervals"


def load_daily_data():
    base.ensure_history_download([STOCK], PERIOD, START_DATE, END_DATE)
    frame = base.load_price_frame(STOCK, PERIOD, START_DATE, END_DATE)
    frame = frame.copy()
    frame.index = frame.index.astype(str)
    frame["trade_date"] = frame.index.str[:8]
    frame.index.name = None
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"]).sort_values("trade_date")
    return frame.reset_index(drop=True)


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


def enumerate_daily_intervals(frame):
    rows = []

    for entry_idx, entry in frame.iterrows():
        max_exit_idx = min(entry_idx + MAX_HOLD_TRADE_DAYS - 1, len(frame) - 1)
        min_exit_idx = entry_idx + MIN_HOLD_TRADE_DAYS - 1
        if min_exit_idx >= len(frame):
            continue

        entry_price = float(entry["close"])
        if entry_price <= 0:
            continue

        for exit_idx in range(min_exit_idx, max_exit_idx + 1):
            exit_row = frame.iloc[exit_idx]
            window = frame.iloc[entry_idx : exit_idx + 1]
            exit_price = float(exit_row["close"])
            return_pct = exit_price / entry_price - 1.0
            min_low = float(window["low"].min())
            max_high = float(window["high"].max())
            mae_pct = min_low / entry_price - 1.0
            mfe_pct = max_high / entry_price - 1.0
            max_dd_pct = max_drawdown_from_lows(window)
            risk_pct = max(abs(mae_pct), abs(max_dd_pct), 0.0001)
            holding_trade_days = int(exit_idx - entry_idx + 1)

            rows.append(
                {
                    "entry_date": str(entry["trade_date"]),
                    "entry_close": round(entry_price, 4),
                    "exit_date": str(exit_row["trade_date"]),
                    "exit_close": round(exit_price, 4),
                    "holding_trade_days": holding_trade_days,
                    "return_pct": round(return_pct, 6),
                    "mae_pct": round(mae_pct, 6),
                    "mfe_pct": round(mfe_pct, 6),
                    "max_drawdown_pct": round(max_dd_pct, 6),
                    "risk_pct": round(risk_pct, 6),
                    "return_risk_ratio": round(return_pct / risk_pct, 6),
                    "entry_idx": int(entry_idx),
                    "exit_idx": int(exit_idx),
                }
            )

    return pd.DataFrame(rows)


def score_intervals(intervals):
    scored = intervals[intervals["return_pct"] > 0].copy()
    if scored.empty:
        return scored

    scored["return_rank_pct"] = scored["return_pct"].rank(pct=True)
    scored["risk_low_rank_pct"] = (-scored["risk_pct"]).rank(pct=True)
    scored["ratio_rank_pct"] = scored["return_risk_ratio"].rank(pct=True)
    scored["quality_score"] = (
        scored["return_rank_pct"] * 0.45
        + scored["risk_low_rank_pct"] * 0.25
        + scored["ratio_rank_pct"] * 0.30
    )
    return scored.sort_values(
        ["quality_score", "return_pct", "return_risk_ratio"],
        ascending=[False, False, False],
    ).reset_index(drop=True)


def select_non_overlapping_events(scored):
    selected = []
    used_ranges = []

    for _, row in scored.iterrows():
        entry_idx = int(row["entry_idx"])
        exit_idx = int(row["exit_idx"])
        overlaps = any(not (exit_idx < start or entry_idx > end) for start, end in used_ranges)
        if overlaps:
            continue
        selected.append(row.to_dict())
        used_ranges.append((entry_idx, exit_idx))
        if len(selected) >= TOP_EVENT_N:
            break

    return pd.DataFrame(selected)


def format_pct_table(events):
    display_cols = [
        "entry_date",
        "entry_close",
        "exit_date",
        "exit_close",
        "holding_trade_days",
        "return_pct",
        "mae_pct",
        "max_drawdown_pct",
        "return_risk_ratio",
        "quality_score",
    ]
    table = events[display_cols].copy()
    for col in ["return_pct", "mae_pct", "max_drawdown_pct"]:
        table[col] = table[col].map(lambda x: "{:.2%}".format(float(x)))
    for col in ["return_risk_ratio", "quality_score"]:
        table[col] = table[col].map(lambda x: "{:.3f}".format(float(x)))
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


def write_report(events, summary):
    table = format_pct_table(events)
    lines = [
        "# 510300 近一年日线最优交易区间扫描（第1步）",
        "",
        "## 这一步只回答什么",
        "",
        "- 只看日线。",
        "- 不使用候选A/B规则。",
        "- 不使用分钟级盘中信号。",
        "- 不调整策略参数。",
        "- 目标是先找出近一年中“事后看收益高、持仓内风险低”的交易日期段。",
        "",
        "## 扫描口径",
        "",
        "- 标的：`{}`".format(STOCK),
        "- 区间：`{}` 至 `{}`".format(START_DATE, END_DATE),
        "- 周期：`{}`".format(PERIOD),
        "- 买入价：买入日收盘价",
        "- 卖出价：卖出日收盘价",
        "- 交易约束：T+1，最短持有 `{}` 个交易日，最长持有 `{}` 个交易日".format(
            MIN_HOLD_TRADE_DAYS, MAX_HOLD_TRADE_DAYS
        ),
        "- 风险指标：持仓内最大不利波动、持仓内最大回撤、收益/风险比",
        "- 排序方式：收益率、低风险、收益/风险比三者综合打分，再去除重叠区间",
        "",
        "## 扫描摘要",
        "",
        "- 日线K线数：`{}`".format(summary["bar_count"]),
        "- 枚举区间数：`{}`".format(summary["interval_count"]),
        "- 正收益区间数：`{}`".format(summary["positive_interval_count"]),
        "",
        "## 非重叠最优交易区间",
        "",
        markdown_table(table),
        "",
        "## 下一步",
        "",
        "下一步只围绕上表这些买入日期做信号分析：",
        "",
        "- 先分析买入日前的日线背景信号。",
        "- 再分析买入日当天的分钟级盘中信号。",
        "- 最后才允许把有统计区分度的信号写回策略。",
    ]

    report_path = Path("报告/研究结论/当前主线/510300近一年日线最优交易区间扫描.md")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = load_daily_data()
    intervals = enumerate_daily_intervals(frame)
    scored = score_intervals(intervals)
    events = select_non_overlapping_events(scored)

    summary = {
        "stock": STOCK,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "period": PERIOD,
        "bar_count": int(len(frame)),
        "interval_count": int(len(intervals)),
        "positive_interval_count": int((intervals["return_pct"] > 0).sum()) if not intervals.empty else 0,
    }

    intervals.to_csv(OUTPUT_DIR / "all_daily_intervals.csv", index=False, encoding="utf-8-sig")
    scored.head(TOP_RAW_N).to_csv(OUTPUT_DIR / "top_raw_daily_intervals.csv", index=False, encoding="utf-8-sig")
    events.to_csv(OUTPUT_DIR / "top_non_overlapping_daily_events.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path("报告/研究结论/数据摘要/510300近一年日线最优交易区间.csv").write_text(
        events.to_csv(index=False),
        encoding="utf-8-sig",
    )
    write_report(events, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(format_pct_table(events).to_string(index=False))


if __name__ == "__main__":
    main()
