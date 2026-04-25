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

ENTRY_PRICE_MODE = "low_close_midpoint"
EXIT_PRICE_MODE = "close_high_midpoint"
ENTRY_MID_WEIGHT = 0.50
EXIT_MID_WEIGHT = 0.50

RETURN_SCORE_WEIGHT = 0.45
LOW_RISK_SCORE_WEIGHT = 0.25
RETURN_RISK_SCORE_WEIGHT = 0.30

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


def entry_price(row):
    low = float(row["low"])
    close = float(row["close"])
    return low + (close - low) * ENTRY_MID_WEIGHT


def exit_price(row):
    close = float(row["close"])
    high = float(row["high"])
    return close + (high - close) * EXIT_MID_WEIGHT


def enumerate_daily_intervals(frame):
    rows = []

    for entry_idx, entry in frame.iterrows():
        max_exit_idx = min(entry_idx + MAX_HOLD_TRADE_DAYS - 1, len(frame) - 1)
        min_exit_idx = entry_idx + MIN_HOLD_TRADE_DAYS - 1
        if min_exit_idx >= len(frame):
            continue

        buy_price = entry_price(entry)
        if buy_price <= 0:
            continue

        for exit_idx in range(min_exit_idx, max_exit_idx + 1):
            exit_row = frame.iloc[exit_idx]
            window = frame.iloc[entry_idx : exit_idx + 1]
            sell_price = exit_price(exit_row)
            return_pct = sell_price / buy_price - 1.0
            min_low = float(window["low"].min())
            max_high = float(window["high"].max())
            mae_pct = min_low / buy_price - 1.0
            mfe_pct = max_high / buy_price - 1.0
            max_dd_pct = max_drawdown_from_lows(window)
            risk_pct = max(abs(mae_pct), abs(max_dd_pct), 0.0001)
            holding_trade_days = int(exit_idx - entry_idx + 1)

            rows.append(
                {
                    "entry_date": str(entry["trade_date"]),
                    "entry_low": round(float(entry["low"]), 4),
                    "entry_close": round(float(entry["close"]), 4),
                    "entry_price": round(buy_price, 4),
                    "exit_date": str(exit_row["trade_date"]),
                    "exit_close": round(float(exit_row["close"]), 4),
                    "exit_high": round(float(exit_row["high"]), 4),
                    "exit_price": round(sell_price, 4),
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
        scored["return_rank_pct"] * RETURN_SCORE_WEIGHT
        + scored["risk_low_rank_pct"] * LOW_RISK_SCORE_WEIGHT
        + scored["ratio_rank_pct"] * RETURN_RISK_SCORE_WEIGHT
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


def covered_trade_days(row, month_indices):
    start = int(row["entry_idx"])
    end = int(row["exit_idx"])
    return len([idx for idx in month_indices if start <= idx <= end])


def select_monthly_required_events(scored, frame):
    selected = []
    used_ranges = []
    status_rows = []
    months = sorted(frame["trade_date"].str[:6].unique().tolist())

    for month in months:
        month_indices = frame.index[frame["trade_date"].str[:6] == month].tolist()
        required_holding_days = len(month_indices) // 2 + 1
        existing_entry_count = len(
            [row for row in selected if str(row["entry_date"]).startswith(month)]
        )
        existing_holding_days = sum(covered_trade_days(row, month_indices) for row in selected)

        if existing_entry_count > 0 or existing_holding_days >= required_holding_days:
            status_rows.append(
                {
                    "month": month,
                    "trading_days": len(month_indices),
                    "required_holding_days": required_holding_days,
                    "entry_signal_count": existing_entry_count,
                    "holding_days": existing_holding_days,
                    "status": "已满足",
                    "selected_entry_date": "",
                    "selected_exit_date": "",
                }
            )
            continue

        month_candidates = scored[scored["entry_date"].astype(str).str.startswith(month)]
        chosen = None
        for _, row in month_candidates.iterrows():
            entry_idx = int(row["entry_idx"])
            exit_idx = int(row["exit_idx"])
            overlaps = any(not (exit_idx < start or entry_idx > end) for start, end in used_ranges)
            if overlaps:
                continue
            chosen = row.to_dict()
            break

        if chosen is not None:
            selected.append(chosen)
            used_ranges.append((int(chosen["entry_idx"]), int(chosen["exit_idx"])))
            existing_entry_count = 1
            existing_holding_days = covered_trade_days(chosen, month_indices)
            status = "新增当月最佳信号"
            selected_entry_date = chosen["entry_date"]
            selected_exit_date = chosen["exit_date"]
        else:
            status = "未找到非重叠正收益信号"
            selected_entry_date = ""
            selected_exit_date = ""

        status_rows.append(
            {
                "month": month,
                "trading_days": len(month_indices),
                "required_holding_days": required_holding_days,
                "entry_signal_count": existing_entry_count,
                "holding_days": existing_holding_days,
                "status": status,
                "selected_entry_date": selected_entry_date,
                "selected_exit_date": selected_exit_date,
            }
        )

    events = pd.DataFrame(selected)
    if not events.empty:
        events = events.sort_values("entry_idx").reset_index(drop=True)
    return events, pd.DataFrame(status_rows)


def format_pct_table(events):
    display_cols = [
        "entry_date",
        "entry_low",
        "entry_close",
        "entry_price",
        "exit_date",
        "exit_close",
        "exit_high",
        "exit_price",
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


def format_monthly_status_table(monthly_status):
    table = monthly_status.copy()
    return table[
        [
            "month",
            "trading_days",
            "required_holding_days",
            "entry_signal_count",
            "holding_days",
            "status",
            "selected_entry_date",
            "selected_exit_date",
        ]
    ]


def markdown_table(frame):
    columns = frame.columns.tolist()
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in columns) + " |")
    return "\n".join(lines)


def write_report(best_events, monthly_events, monthly_status, summary):
    best_table = format_pct_table(best_events)
    monthly_table = format_pct_table(monthly_events)
    monthly_status_table = format_monthly_status_table(monthly_status)
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
        "- 买入价：买入日最低价与收盘价的中间值，当前权重 `{:.0%}`".format(
            ENTRY_MID_WEIGHT
        ),
        "- 卖出价：卖出日收盘价与最高价的中间值，当前权重 `{:.0%}`".format(
            EXIT_MID_WEIGHT
        ),
        "- 交易约束：T+1，最短持有 `{}` 个交易日，最长持有 `{}` 个交易日".format(
            MIN_HOLD_TRADE_DAYS, MAX_HOLD_TRADE_DAYS
        ),
        "- 风险指标：持仓内最大不利波动、持仓内最大回撤、收益/风险比",
        "- 排序方式：收益率、低风险、收益/风险比三者综合打分",
        "- 低风险权重含义：风险越小排名越靠前，再按 `{:.0%}` 的权重计入综合分".format(
            LOW_RISK_SCORE_WEIGHT
        ),
        "- 月度约束：每个月至少有 1 次买入信号；如果没有买入信号，则该月至少超过半个月处于持有状态",
        "",
        "## 扫描摘要",
        "",
        "- 日线K线数：`{}`".format(summary["bar_count"]),
        "- 枚举区间数：`{}`".format(summary["interval_count"]),
        "- 正收益区间数：`{}`".format(summary["positive_interval_count"]),
        "",
        "## 纯质量排序的非重叠最优区间",
        "",
        markdown_table(best_table),
        "",
        "## 满足月度交易期望的区间",
        "",
        markdown_table(monthly_table),
        "",
        "## 月度覆盖检查",
        "",
        markdown_table(monthly_status_table),
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
    best_events = select_non_overlapping_events(scored)
    monthly_events, monthly_status = select_monthly_required_events(scored, frame)

    summary = {
        "stock": STOCK,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "period": PERIOD,
        "entry_price_mode": ENTRY_PRICE_MODE,
        "exit_price_mode": EXIT_PRICE_MODE,
        "entry_mid_weight": ENTRY_MID_WEIGHT,
        "exit_mid_weight": EXIT_MID_WEIGHT,
        "bar_count": int(len(frame)),
        "interval_count": int(len(intervals)),
        "positive_interval_count": int((intervals["return_pct"] > 0).sum()) if not intervals.empty else 0,
        "best_event_count": int(len(best_events)),
        "monthly_event_count": int(len(monthly_events)),
    }

    intervals.to_csv(OUTPUT_DIR / "all_daily_intervals.csv", index=False, encoding="utf-8-sig")
    scored.head(TOP_RAW_N).to_csv(OUTPUT_DIR / "top_raw_daily_intervals.csv", index=False, encoding="utf-8-sig")
    best_events.to_csv(OUTPUT_DIR / "top_non_overlapping_daily_events.csv", index=False, encoding="utf-8-sig")
    monthly_events.to_csv(OUTPUT_DIR / "monthly_required_daily_events.csv", index=False, encoding="utf-8-sig")
    monthly_status.to_csv(OUTPUT_DIR / "monthly_coverage_status.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path("报告/研究结论/数据摘要/510300近一年日线最优交易区间.csv").write_text(
        best_events.to_csv(index=False),
        encoding="utf-8-sig",
    )
    Path("报告/研究结论/数据摘要/510300近一年日线月度可交易区间.csv").write_text(
        monthly_events.to_csv(index=False),
        encoding="utf-8-sig",
    )
    Path("报告/研究结论/数据摘要/510300近一年日线月度覆盖检查.csv").write_text(
        monthly_status.to_csv(index=False),
        encoding="utf-8-sig",
    )
    write_report(best_events, monthly_events, monthly_status, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Pure quality non-overlapping events")
    print(format_pct_table(best_events).to_string(index=False))
    print("Monthly-required events")
    print(format_pct_table(monthly_events).to_string(index=False))
    print("Monthly coverage")
    print(format_monthly_status_table(monthly_status).to_string(index=False))


if __name__ == "__main__":
    main()
