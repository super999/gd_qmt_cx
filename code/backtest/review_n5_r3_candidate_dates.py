#!/usr/bin/env python3
# coding: utf-8

from pathlib import Path

import pandas as pd

import minimal_stock_backtest as base


STOCK = "510300.SH"
DAILY_LOOKBACK = 3
DAILY_LOOKAHEAD = 3

RECOMMENDED_BANDS = (
    Path(__file__).resolve().parent
    / "outputs"
    / "analyze_n5_r3_candidate_rule_bands"
    / "recommended_band_dates.csv"
)
SCORED_DATASET = (
    Path(__file__).resolve().parent
    / "outputs"
    / "build_n5_r3_bg_trigger_scores"
    / "bg_trigger_scored_dataset.csv"
)
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "review_n5_r3_candidate_dates"
TRACKED_REPORT = (
    Path(__file__).resolve().parents[2]
    / "报告"
    / "研究结论"
    / "当前主线"
    / "n5_r3候选A_B日期复盘.md"
)


KEY_COLUMNS = [
    "drawdown_from_high_10",
    "drawdown_from_high_20",
    "low_vs_ma20",
    "volatility_5",
    "ret_5d",
    "m5_low_pos_ratio",
    "m5_rebound_to_close",
    "m5_up_close_streak_after_low",
    "m5_up_bar_ratio_after_low",
    "m5_close_in_range",
    "m1_up_close_streak_after_low",
]


FEATURE_CN = {
    "drawdown_from_high_10": "相对近10日高点回撤",
    "drawdown_from_high_20": "相对近20日高点回撤",
    "low_vs_ma20": "最低价相对20日均线偏离",
    "volatility_5": "近5日收益波动率",
    "ret_5d": "近5日涨跌幅",
    "m5_low_pos_ratio": "5分钟最低点出现位置占全天比例",
    "m5_rebound_to_close": "5分钟最低点到收盘反弹幅度",
    "m5_up_close_streak_after_low": "5分钟低点后连续抬高收盘的最长根数",
    "m5_up_bar_ratio_after_low": "5分钟低点后阳线占比",
    "m5_close_in_range": "5分钟收盘在日内振幅中的位置",
    "m1_up_close_streak_after_low": "1分钟低点后连续抬高收盘的最长根数",
}


def load_candidates():
    band_df = pd.read_csv(RECOMMENDED_BANDS, dtype={"trade_date": str})
    band_df = band_df[band_df["band_name"].isin(["候选A-严格", "候选B-平衡"])].copy()
    scored = pd.read_csv(SCORED_DATASET, dtype={"trade_date": str})

    grouped = (
        band_df.groupby("trade_date")["band_name"]
        .apply(lambda s: " / ".join(sorted(set(s), key=lambda x: ("A" not in x, x))))
        .reset_index()
        .rename(columns={"band_name": "band_membership"})
    )
    merged = grouped.merge(scored, on="trade_date", how="left")
    merged["review_label"] = merged.apply(build_review_label, axis=1)
    return merged.sort_values("trade_date").reset_index(drop=True)


def build_review_label(row):
    bg = int(row["background_score"])
    tg = int(row["trigger_score"])
    if bg >= 4 and tg >= 5:
        return "强背景 + 强触发"
    if bg >= 3 and tg >= 2:
        return "背景达标 + 触发达标"
    if bg >= 3 and tg <= 1:
        return "背景达标 + 触发偏弱"
    if bg <= 2 and tg >= 6:
        return "背景一般 + 触发很强"
    return "结构混合"


def load_daily_context():
    start_date = "20240101"
    end_date = "20260424"
    base.ensure_history_download([STOCK], base.DAILY_PERIOD, start_date, end_date)
    daily = base.load_price_frame(STOCK, base.DAILY_PERIOD, start_date, end_date)
    daily = base.enrich_daily_indicators(daily)
    daily = daily.copy()
    daily["trade_date"] = daily.index.astype(str).str[:8]
    daily["daily_return"] = daily["close"].pct_change()
    return daily.reset_index(drop=False)


def extract_daily_window(daily_df, trade_date):
    idx_list = daily_df.index[daily_df["trade_date"] == trade_date].tolist()
    if not idx_list:
        return pd.DataFrame()
    idx = idx_list[0]
    start = max(0, idx - DAILY_LOOKBACK)
    end = min(len(daily_df), idx + DAILY_LOOKAHEAD + 1)
    window = daily_df.iloc[start:end][["trade_date", "open", "high", "low", "close", "daily_return"]].copy()
    window["event_day"] = window["trade_date"] == trade_date
    return window


def make_overview_table(candidates):
    cols = [
        "trade_date",
        "band_membership",
        "target",
        "background_score",
        "trigger_score",
        "review_label",
    ]
    return candidates[cols].copy()


def make_marginal_table(candidates):
    only_b = candidates[candidates["band_membership"] == "候选B-平衡"].copy()
    cols = [
        "trade_date",
        "target",
        "background_score",
        "trigger_score",
        "review_label",
    ]
    return only_b[cols].copy()


def format_value(feature, value):
    if pd.isna(value):
        return ""
    if feature.endswith("_streak_after_low"):
        return str(int(value))
    return f"{float(value):.6f}"


def table_lines(df):
    header = "| " + " | ".join(df.columns.astype(str)) + " |"
    separator = "| " + " | ".join(["---"] * len(df.columns)) + " |"
    lines = [header, separator]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in df.columns) + " |")
    return lines


def build_report(candidates, daily_df):
    overview = make_overview_table(candidates)
    marginal = make_marginal_table(candidates)

    lines = [
        "# n5_r3 候选A / 候选B 日期复盘",
        "",
        "## 目的",
        "",
        "- 这一步不继续扩评分模型，而是回看候选区间对应的真实日期。",
        "- 目标是判断：`候选A-严格` 是否已经足够像第一版候选规则，`候选B-平衡` 放宽后多出来的日期值不值得保留。",
        "",
        "## 1. 候选日期总览",
        "",
    ]
    lines.extend(table_lines(overview))
    lines.extend(
        [
            "",
            "## 2. 候选B 相比候选A新增的边缘日期",
            "",
        ]
    )
    lines.extend(table_lines(marginal))
    lines.extend(
        [
            "",
            "当前观察：",
            "",
            "1. `候选A-严格` 命中的 8 个日期全部是正样本。",
            "2. `候选B-平衡` 比 `候选A` 多出的两个日期是：`20260403` 和 `20260407`。",
            "3. 这两个新增日期的共同点是：`触发分 = 1`，也就是背景够，但日内修复结构明显偏弱。",
            "4. 其中 `20260403` 是负样本，`20260407` 是正样本，说明把触发门槛降到 1 会开始引入模糊样本。",
            "",
            "## 3. 逐日期复盘",
            "",
        ]
    )

    for _, row in candidates.iterrows():
        trade_date = row["trade_date"]
        lines.extend(
            [
                f"### {trade_date}",
                "",
                f"- 所属区间：`{row['band_membership']}`",
                f"- 标签结果：`target={int(row['target'])}`",
                f"- 结构判断：`{row['review_label']}`",
                f"- 背景分：`{int(row['background_score'])}`",
                f"- 触发分：`{int(row['trigger_score'])}`",
                "",
                "关键特征：",
                "",
            ]
        )
        feature_rows = []
        for feature in KEY_COLUMNS:
            feature_rows.append(
                {
                    "feature": feature,
                    "feature_cn": FEATURE_CN[feature],
                    "value": format_value(feature, row[feature]),
                }
            )
        lines.extend(table_lines(pd.DataFrame(feature_rows)))
        lines.extend(["", "日线窗口：", ""])
        window = extract_daily_window(daily_df, trade_date)
        if not window.empty:
            window = window.copy()
            window["daily_return"] = window["daily_return"].fillna(0.0).map(lambda x: f"{x:.6f}")
            lines.extend(table_lines(window))
        else:
            lines.append("无可用日线窗口")
        lines.append("")

    lines.extend(
        [
            "## 4. 收口判断",
            "",
            "1. `候选A-严格` 当前已经足够像第一版候选规则区间。",
            "2. `候选B-平衡` 的价值主要是当宽松对照组，不适合先作为主规则。",
            "3. 这轮复盘后，建议先冻结：",
            "   - 主候选：`背景分 >= 3` 且 `触发分 >= 2`",
            "   - 对照组：`背景分 >= 3` 且 `触发分 >= 1`",
            "4. 下一步不再继续加更多评分变体，而是把主候选区间翻译成第一版显式策略规则。",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TRACKED_REPORT.parent.mkdir(parents=True, exist_ok=True)

    print("Reviewing n5_r3 candidate A/B dates")
    candidates = load_candidates()
    daily_df = load_daily_context()
    report = build_report(candidates, daily_df)

    candidates.to_csv(OUTPUT_DIR / "candidate_dates_review.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / "n5_r3候选A_B日期复盘.md").write_text(report, encoding="utf-8")
    TRACKED_REPORT.write_text(report, encoding="utf-8")

    print("outputs:")
    print(" -", OUTPUT_DIR / "candidate_dates_review.csv")
    print(" -", TRACKED_REPORT)


if __name__ == "__main__":
    main()
