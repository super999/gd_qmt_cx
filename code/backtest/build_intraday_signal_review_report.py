#!/usr/bin/env python3
# coding: utf-8

from pathlib import Path

import pandas as pd


OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "backtest_intraday_v_reversal_signal"
SIGNALS_PATH = OUTPUT_DIR / "intraday_signals.csv"
TRADES_PATH = OUTPUT_DIR / "trades.csv"
REPORT_PATH = OUTPUT_DIR / "盘中即时信号逐笔复盘.md"
SIMPLE_REPORT_PATH = OUTPUT_DIR / "盘中即时信号简单复盘.md"
CSV_PATH = OUTPUT_DIR / "intraday_signal_trade_review.csv"


BACKGROUND_CONDITIONS = [
    ("drawdown_from_high_10", "相对近10日高点回撤", "<= -0.044051"),
    ("drawdown_from_high_20", "相对近20日高点回撤", "<= -0.050221"),
    ("low_vs_ma20", "最低价相对20日均线偏离", "<= -0.024041"),
    ("volatility_5", "近5日收益波动率", ">= 0.010574"),
    ("ret_5d", "近5日涨跌幅", "<= -0.014103"),
]

TRIGGER_CONDITIONS = [
    ("m5_low_before_last_quarter", "5分钟最低点出现在最后四分之一天之前", ">= 1"),
    ("m5_low_pos_ratio", "5分钟最低点出现位置占全天比例", "<= 0.319149"),
    ("m5_rebound_to_est_close", "5分钟低点到当前预估收盘反弹幅度", ">= 0.009317"),
    ("m5_up_close_streak_after_low", "5分钟低点后连续抬高收盘最长根数", ">= 4"),
    ("m5_up_bar_ratio_after_low", "5分钟低点后阳线占比", ">= 0.5"),
    ("m5_est_close_in_range", "5分钟当前收盘在日内振幅中的位置", ">= 0.722222"),
    ("m1_up_close_streak_after_low", "1分钟低点后连续抬高收盘最长根数", ">= 5"),
]


def yes_no(value):
    return "Y" if str(value) in {"1", "1.0", "True", "true"} else "N"


def fmt_float(value):
    try:
        return "{:.6f}".format(float(value))
    except Exception:
        return ""


def condition_summary(row, conditions):
    parts = []
    for feature, label, threshold in conditions:
        hit = yes_no(row.get(feature + "_hit", 0))
        value = fmt_float(row.get(feature, ""))
        parts.append("{} {} value={} hit={}".format(label, threshold, value, hit))
    return "；".join(parts)


def build_review_table():
    signals = pd.read_csv(SIGNALS_PATH, dtype=str)
    trades = pd.read_csv(TRADES_PATH, dtype=str)

    review = signals.merge(
        trades[
            [
                "band",
                "signal_time",
                "entry_time",
                "entry_price",
                "exit_time",
                "exit_price",
                "pnl",
                "return_pct",
                "holding_trade_days",
                "exit_reason",
            ]
        ],
        on=["band", "signal_time"],
        how="left",
    )
    review["is_traded"] = review["entry_time"].notna()
    review["background_conditions"] = review.apply(
        lambda row: condition_summary(row, BACKGROUND_CONDITIONS), axis=1
    )
    review["trigger_conditions"] = review.apply(
        lambda row: condition_summary(row, TRIGGER_CONDITIONS), axis=1
    )
    review["time_gate"] = review.apply(
        lambda row: (
            "low_pos={} current_pos={} wait_bars={} min_signal_pos={} pass={}".format(
                row.get("m5_low_pos", ""),
                row.get("m5_current_pos", ""),
                row.get("m5_dynamic_wait_bars", ""),
                row.get("m5_dynamic_min_signal_pos", ""),
                yes_no(row.get("m5_time_gate_pass", "")),
            )
        ),
        axis=1,
    )
    return review


def table_lines(df, columns):
    lines = []
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("| " + " | ".join(["---"] * len(columns)) + " |")
    for _, row in df.iterrows():
        values = [str(row.get(col, "")).replace("\n", " ") for col in columns]
        lines.append("| " + " | ".join(values) + " |")
    return lines


def build_report(review):
    lines = [
        "# 盘中即时信号逐笔复盘",
        "",
        "## 说明",
        "",
        "- 本报告把盘中信号与实际模拟交易合并，便于人工审查。",
        "- `is_traded=False` 表示该信号出现时仍在持仓窗口内，未重复开仓。",
        "- 背景层是日线门控，采用昨日以前日线 + 当前盘中价格估算。",
        "- 时间门控采用动态低点修复等待，不写死固定时刻。",
        "",
        "## 候选A 交易明细",
        "",
    ]
    main_cols = [
        "trade_date",
        "signal_time",
        "signal_price",
        "background_score",
        "trigger_score",
        "time_gate",
        "is_traded",
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "return_pct",
        "exit_reason",
    ]
    candidate_a = review[review["band"] == "候选A-严格"].copy()
    lines.extend(table_lines(candidate_a[main_cols], main_cols))

    lines.extend(["", "## 候选A 条件逐条展开", ""])
    detail_cols = [
        "trade_date",
        "signal_time",
        "background_conditions",
        "trigger_conditions",
    ]
    lines.extend(table_lines(candidate_a[detail_cols], detail_cols))

    lines.extend(["", "## 候选B 交易明细", ""])
    candidate_b = review[review["band"] == "候选B-平衡"].copy()
    lines.extend(table_lines(candidate_b[main_cols], main_cols))

    return "\n".join(lines) + "\n"


def summarize_hits(row, conditions):
    hit_labels = []
    for feature, label, _ in conditions:
        if yes_no(row.get(feature + "_hit", 0)) == "Y":
            hit_labels.append(label)
    return "、".join(hit_labels) if hit_labels else "无"


def build_trigger_text(row):
    return (
        "日线背景{}分：{}；动态时间门控通过：{}；盘中触发{}分：{}".format(
            row.get("background_score", ""),
            summarize_hits(row, BACKGROUND_CONDITIONS),
            row.get("time_gate", ""),
            row.get("trigger_score", ""),
            summarize_hits(row, TRIGGER_CONDITIONS),
        )
    )


def build_simple_report(review):
    candidate_a = review[(review["band"] == "候选A-严格") & (review["is_traded"])].copy()
    candidate_a["buy_date"] = candidate_a["entry_time"].str[:8]
    candidate_a["sell_date"] = candidate_a["exit_time"].str[:8]
    candidate_a["trigger_text"] = candidate_a.apply(build_trigger_text, axis=1)
    candidate_a["return_num"] = pd.to_numeric(candidate_a["return_pct"], errors="coerce")
    trade_count = int(len(candidate_a))
    win_rate = float((candidate_a["return_num"] > 0).mean()) if trade_count else 0.0
    compounded_return = float((1.0 + candidate_a["return_num"].fillna(0.0)).prod() - 1.0)
    profit_dates = "、".join(candidate_a[candidate_a["return_num"] > 0]["buy_date"].tolist())
    loss_dates = "、".join(candidate_a[candidate_a["return_num"] <= 0]["buy_date"].tolist())

    lines = [
        "# n5_r3 盘中即时信号简单复盘",
        "",
        "## 说明",
        "",
        "- 本报告只看当前主规则：`候选A-严格`。",
        "- 写法按人工复盘顺序：先看哪天买、哪天卖、为什么买，最后再看具体参数。",
        "- 动态时间门控已经启用，避免低点刚出现就过早触发。",
        "- 交易约束按 A 股 T+1 处理：买入当天不能卖出，最早下一交易日卖出。",
        "",
        "## 买卖记录",
        "",
        "| 序号 | 买入时间 | 买入价 | 卖出时间 | 卖出价 | 收益率 | 卖出原因 | 触发条件 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for idx, (_, row) in enumerate(candidate_a.iterrows(), start=1):
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                idx,
                row.get("entry_time", ""),
                row.get("entry_price", ""),
                row.get("exit_time", ""),
                row.get("exit_price", ""),
                row.get("return_pct", ""),
                row.get("exit_reason", ""),
                row.get("trigger_text", ""),
            )
        )

    lines.extend(
        [
            "",
            "## 直接结论",
            "",
            "- 当前主规则实际交易 `{}` 笔。".format(trade_count),
            "- 胜率 `{:.2%}`，复合收益 `{:.4%}`。".format(win_rate, compounded_return),
            "- 盈利买入日：`{}`。".format(profit_dates),
            "- 亏损买入日：`{}`。".format(loss_dates),
            "- 下一步如果要提高胜率，优先审查亏损日的共同特征，而不是继续随意加新因子。",
            "",
            "## 具体参数",
            "",
            "### 日线背景条件",
            "",
        ]
    )
    for feature, label, threshold in BACKGROUND_CONDITIONS:
        lines.append("- `{}`：{} {}".format(feature, label, threshold))

    lines.extend(["", "### 盘中触发条件", ""])
    for feature, label, threshold in TRIGGER_CONDITIONS:
        lines.append("- `{}`：{} {}".format(feature, label, threshold))

    lines.extend(
        [
            "",
            "### 动态时间门控",
            "",
            "- 先找到当日截至当前的 `5m` 最低点位置。",
            "- 根据低点后剩余的 `5m` bar 数量动态计算等待窗口。",
            "- 低点越早，等待确认越久；低点越晚，等待确认越短。",
            "- 当前实现：等待 `2` 到 `6` 根 `5m` K 线，不写死具体时刻。",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    review = build_review_table()
    review.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    REPORT_PATH.write_text(build_report(review), encoding="utf-8")
    SIMPLE_REPORT_PATH.write_text(build_simple_report(review), encoding="utf-8")
    print("outputs:")
    print(" -", CSV_PATH)
    print(" -", REPORT_PATH)
    print(" -", SIMPLE_REPORT_PATH)


if __name__ == "__main__":
    main()
