#!/usr/bin/env python3
# coding: utf-8

from pathlib import Path

import pandas as pd


OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "analyze_n5_r3_candidate_rule_bands"
SOURCE_DATASET = (
    Path(__file__).resolve().parent
    / "outputs"
    / "build_n5_r3_bg_trigger_scores"
    / "bg_trigger_scored_dataset.csv"
)
TRACKED_REPORT = (
    Path(__file__).resolve().parents[2]
    / "报告"
    / "研究结论"
    / "当前主线"
    / "n5_r3候选规则区间分析.md"
)
TRACKED_SUMMARY = (
    Path(__file__).resolve().parents[2]
    / "报告"
    / "研究结论"
    / "数据摘要"
    / "n5_r3候选规则区间摘要.csv"
)


RECOMMENDED_BANDS = [
    {"band_name": "候选A-严格", "background_min": 3, "trigger_min": 2},
    {"band_name": "候选B-平衡", "background_min": 3, "trigger_min": 1},
    {"band_name": "候选C-触发优先", "background_min": 1, "trigger_min": 6},
]


def prepare_scored_dataset():
    if SOURCE_DATASET.exists():
        return pd.read_csv(SOURCE_DATASET, dtype={"trade_date": str})

    import build_n5_r3_bg_trigger_scores as bgmod

    df = bgmod.load_merged_dataset()
    bg_thresholds = bgmod.build_threshold_table(
        df, bgmod.BACKGROUND_FEATURES, bgmod.BACKGROUND_DIRECTION, "background"
    )
    tg_thresholds = bgmod.build_threshold_table(
        df, bgmod.TRIGGER_FEATURES, bgmod.TRIGGER_DIRECTION, "trigger"
    )
    df, _ = bgmod.apply_score(df, bg_thresholds, "background")
    df, _ = bgmod.apply_score(df, tg_thresholds, "trigger")
    return df


def build_cumulative_band_summary(df):
    total_positive = int(df["target"].sum())
    rows = []
    for background_min in range(int(df["background_score"].min()), int(df["background_score"].max()) + 1):
        for trigger_min in range(int(df["trigger_score"].min()), int(df["trigger_score"].max()) + 1):
            subset = df[
                (df["background_score"] >= background_min) & (df["trigger_score"] >= trigger_min)
            ].copy()
            if subset.empty:
                continue
            positive_count = int(subset["target"].sum())
            sample_count = int(len(subset))
            negative_count = sample_count - positive_count
            rows.append(
                {
                    "background_min": background_min,
                    "trigger_min": trigger_min,
                    "sample_count": sample_count,
                    "positive_count": positive_count,
                    "negative_count": negative_count,
                    "positive_ratio": round(positive_count / sample_count, 6),
                    "positive_coverage": round(
                        positive_count / total_positive if total_positive else 0.0, 6
                    ),
                }
            )
    summary = pd.DataFrame(rows)
    return summary.sort_values(
        ["positive_ratio", "sample_count", "positive_coverage", "background_min", "trigger_min"],
        ascending=[False, False, False, False, False],
    ).reset_index(drop=True)


def build_exact_combo_summary(df):
    grouped = (
        df.groupby(["background_score", "trigger_score", "target"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
        .rename(columns={0: "negative_count", 1: "positive_count"})
    )
    grouped["sample_count"] = grouped["negative_count"] + grouped["positive_count"]
    grouped["positive_ratio"] = grouped["positive_count"] / grouped["sample_count"]
    return grouped.sort_values(
        ["positive_ratio", "sample_count", "background_score", "trigger_score"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)


def select_recommended_rows(summary_df):
    rows = []
    for band in RECOMMENDED_BANDS:
        subset = summary_df[
            (summary_df["background_min"] == band["background_min"])
            & (summary_df["trigger_min"] == band["trigger_min"])
        ].copy()
        if subset.empty:
            continue
        record = subset.iloc[0].to_dict()
        record["band_name"] = band["band_name"]
        rows.append(record)
    columns = [
        "band_name",
        "background_min",
        "trigger_min",
        "sample_count",
        "positive_count",
        "negative_count",
        "positive_ratio",
        "positive_coverage",
    ]
    return pd.DataFrame(rows)[columns]


def build_band_dates(df, recommended_df):
    rows = []
    for _, band in recommended_df.iterrows():
        subset = df[
            (df["background_score"] >= band["background_min"])
            & (df["trigger_score"] >= band["trigger_min"])
        ][["trade_date", "target", "background_score", "trigger_score"]].copy()
        subset["band_name"] = band["band_name"]
        rows.append(
            subset[
                ["band_name", "trade_date", "target", "background_score", "trigger_score"]
            ]
        )
    if not rows:
        return pd.DataFrame(
            columns=["band_name", "trade_date", "target", "background_score", "trigger_score"]
        )
    return pd.concat(rows, ignore_index=True).sort_values(["band_name", "trade_date"]).reset_index(
        drop=True
    )


def table_lines(df):
    header = "| " + " | ".join(df.columns.astype(str)) + " |"
    separator = "| " + " | ".join(["---"] * len(df.columns)) + " |"
    lines = [header, separator]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in df.columns) + " |")
    return lines


def build_report(summary_df, exact_df, recommended_df, band_dates_df):
    lines = [
        "# n5_r3 候选规则区间分析",
        "",
        "## 目的",
        "",
        "- 这一步不再继续扩评分模型，而是把评分模型收口成可讨论的候选规则区间。",
        "- 目标是回答：哪些 `背景分 >= x` 且 `触发分 >= y` 的组合，值得进入下一阶段候选规则。",
        "- 本文仍然不是最终交易策略，但它已经比单纯评分更接近可落地规则。",
        "",
        "## 1. 累积规则区间 Top 12",
        "",
    ]
    lines.extend(
        table_lines(
            summary_df.head(12)[
                [
                    "background_min",
                    "trigger_min",
                    "sample_count",
                    "positive_count",
                    "negative_count",
                    "positive_ratio",
                    "positive_coverage",
                ]
            ]
        )
    )
    lines.extend(
        [
            "",
            "说明：",
            "",
            "- `positive_ratio` = 该规则区间内，正样本占比。",
            "- `positive_coverage` = 该规则区间覆盖了全部正样本中的多少比例。",
            "- 这两个值需要一起看，不能只追求命中率，也不能只追求覆盖率。",
            "",
            "## 2. 精确分数组合 Top 10",
            "",
        ]
    )
    lines.extend(
        table_lines(
            exact_df.head(10)[
                [
                    "background_score",
                    "trigger_score",
                    "sample_count",
                    "positive_count",
                    "negative_count",
                    "positive_ratio",
                ]
            ]
        )
    )
    lines.extend(
        [
            "",
            "## 3. 当前建议保留的候选规则区间",
            "",
        ]
    )
    lines.extend(table_lines(recommended_df))

    lines.extend(
        [
            "",
            "## 4. 各候选区间对应日期",
            "",
        ]
    )
    for band_name, band_df in band_dates_df.groupby("band_name"):
        lines.extend([f"### {band_name}", ""])
        lines.extend(table_lines(band_df))
        lines.append("")

    lines.extend(
        [
            "## 5. 当前收口建议",
            "",
            "1. `候选A-严格`：`背景分 >= 3` 且 `触发分 >= 2`",
            "   - 当前样本 `8` 个，正样本占比 `100%`，覆盖全部正样本约 `61.5%`。",
            "   - 这是当前最适合作为“第一版候选规则”的区间，因为样本数不算太小，且解释清晰。",
            "",
            "2. `候选B-平衡`：`背景分 >= 3` 且 `触发分 >= 1`",
            "   - 当前样本 `10` 个，正样本占比 `90%`，覆盖全部正样本约 `69.2%`。",
            "   - 这是更宽松的备选方案，适合后续拿来和候选A做对照。",
            "",
            "3. `候选C-触发优先`：`背景分 >= 1` 且 `触发分 >= 6`",
            "   - 当前样本 `5` 个，正样本占比 `100%`，但覆盖率更低。",
            "   - 它更像一个“强触发但不一定要求特别强背景”的特殊带，不适合作为第一主规则。",
            "",
            "## 6. 下一步如何闭环",
            "",
            "这一步之后，不建议继续无限迭代评分模型。建议按下面顺序收口：",
            "",
            "1. 先冻结 `候选A-严格` 作为第一版候选规则区间。",
            "2. 再用真实日期回看这些事件日，确认它们图形上是否真的符合你的低吸直觉。",
            "3. 然后再把 `候选A-严格` 翻译成第一版显式策略规则。",
            "4. 最后只保留 `候选A` 与 `候选B` 两条路线做验证，不再继续发散更多候选。",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TRACKED_REPORT.parent.mkdir(parents=True, exist_ok=True)
    TRACKED_SUMMARY.parent.mkdir(parents=True, exist_ok=True)

    print("Analyzing n5_r3 candidate rule bands")
    df = prepare_scored_dataset()
    summary_df = build_cumulative_band_summary(df)
    exact_df = build_exact_combo_summary(df)
    recommended_df = select_recommended_rows(summary_df)
    band_dates_df = build_band_dates(df, recommended_df)
    report_text = build_report(summary_df, exact_df, recommended_df, band_dates_df)

    summary_df.to_csv(OUTPUT_DIR / "cumulative_band_summary.csv", index=False, encoding="utf-8-sig")
    exact_df.to_csv(OUTPUT_DIR / "exact_combo_summary.csv", index=False, encoding="utf-8-sig")
    recommended_df.to_csv(OUTPUT_DIR / "recommended_bands.csv", index=False, encoding="utf-8-sig")
    band_dates_df.to_csv(OUTPUT_DIR / "recommended_band_dates.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / "n5_r3候选规则区间分析.md").write_text(report_text, encoding="utf-8")

    recommended_df.to_csv(TRACKED_SUMMARY, index=False, encoding="utf-8-sig")
    TRACKED_REPORT.write_text(report_text, encoding="utf-8")

    print("outputs:")
    print(" -", OUTPUT_DIR / "recommended_bands.csv")
    print(" -", TRACKED_SUMMARY)
    print(" -", TRACKED_REPORT)


if __name__ == "__main__":
    main()
