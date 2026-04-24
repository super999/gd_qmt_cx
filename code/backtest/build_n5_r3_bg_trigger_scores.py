#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import analyze_510300_rebound_features as featmod
from feature_labels import feature_label


MULTIFRAME_DATASET = (
    Path(__file__).resolve().parent
    / "outputs"
    / "analyze_510300_v_reversal_multiframe"
    / "multiframe_dataset.csv"
)
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "build_n5_r3_bg_trigger_scores"
LABEL_NAME = "n5_r3"

BACKGROUND_FEATURES = [
    "drawdown_from_high_10",
    "drawdown_from_high_20",
    "low_vs_ma20",
    "volatility_5",
    "ret_5d",
]
BACKGROUND_DIRECTION = {
    "drawdown_from_high_10": "lower",
    "drawdown_from_high_20": "lower",
    "low_vs_ma20": "lower",
    "volatility_5": "higher",
    "ret_5d": "lower",
}

TRIGGER_FEATURES = [
    "m5_low_before_last_quarter",
    "m5_low_pos_ratio",
    "m5_rebound_to_close",
    "m5_up_close_streak_after_low",
    "m5_up_bar_ratio_after_low",
    "m5_close_in_range",
    "m1_up_close_streak_after_low",
]
TRIGGER_DIRECTION = {
    "m5_low_before_last_quarter": "higher",
    "m5_low_pos_ratio": "lower",
    "m5_rebound_to_close": "higher",
    "m5_up_close_streak_after_low": "higher",
    "m5_up_bar_ratio_after_low": "higher",
    "m5_close_in_range": "higher",
    "m1_up_close_streak_after_low": "higher",
}


def load_merged_dataset():
    daily_df = featmod.build_labeled_dataset().copy()
    mf_df = pd.read_csv(MULTIFRAME_DATASET, dtype={"trade_date": str})
    merged = mf_df.merge(daily_df, on="trade_date", how="left", suffixes=("", "_daily"))
    merged = merged[merged[LABEL_NAME + "_enough_window"].fillna(False)].copy()
    merged["target"] = merged[LABEL_NAME + "_target"].astype(int)
    return merged


def build_threshold_table(df, feature_list, direction_map, score_type):
    pos = df[df["target"] == 1].copy()
    rows = []
    for feature in feature_list:
        series = pd.to_numeric(pos[feature], errors="coerce")
        threshold = float(series.median())
        rows.append(
            {
                "score_type": score_type,
                "feature": feature,
                "feature_cn": feature_label(feature),
                "direction": direction_map[feature],
                "threshold": threshold,
                "positive_median": threshold,
                "positive_mean": float(series.mean()),
            }
        )
    return pd.DataFrame(rows)


def apply_score(df, thresholds_df, prefix):
    working = df.copy()
    point_cols = []
    for _, row in thresholds_df.iterrows():
        feature = row["feature"]
        direction = row["direction"]
        threshold = row["threshold"]
        point_col = prefix + "_" + feature + "_point"
        values = pd.to_numeric(working[feature], errors="coerce")
        if direction == "higher":
            working[point_col] = (values >= threshold).astype(int)
        else:
            working[point_col] = (values <= threshold).astype(int)
        point_cols.append(point_col)
    working[prefix + "_score"] = working[point_cols].sum(axis=1)
    return working, point_cols


def summarize_single_score(df, score_col):
    grouped = (
        df.groupby([score_col, "target"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
        .rename(columns={0: "negative_count", 1: "positive_count"})
    )
    grouped["positive_ratio"] = grouped["positive_count"] / (
        grouped["positive_count"] + grouped["negative_count"]
    )
    return grouped.sort_values(score_col).reset_index(drop=True)


def summarize_joint_score(df):
    grouped = (
        df.groupby(["background_score", "trigger_score", "target"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
        .rename(columns={0: "negative_count", 1: "positive_count"})
    )
    grouped["positive_ratio"] = grouped["positive_count"] / (
        grouped["positive_count"] + grouped["negative_count"]
    )
    return grouped.sort_values(["background_score", "trigger_score"]).reset_index(drop=True)


def fit_score_model(df):
    X = df[["background_score", "trigger_score"]].copy()
    y = df["target"].astype(int)
    pipe = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("logit", LogisticRegression(max_iter=2000, random_state=42)),
        ]
    )
    pipe.fit(X, y)
    probs = pipe.predict_proba(X)[:, 1]
    in_sample_auc = float(roc_auc_score(y, probs))

    positive_count = int(y.sum())
    negative_count = int((y == 0).sum())
    n_splits = min(5, positive_count, negative_count)
    cv_auc = None
    if n_splits >= 3:
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        cv_auc = float(cross_val_score(pipe, X, y, cv=cv, scoring="roc_auc").mean())

    coef = pipe.named_steps["logit"].coef_[0]
    coef_df = pd.DataFrame(
        {
            "feature": ["background_score", "trigger_score"],
            "feature_cn": ["背景分", "触发分"],
            "coefficient": coef,
        }
    )
    coef_df["abs_coefficient"] = coef_df["coefficient"].abs()

    meta = {
        "sample_count": int(len(X)),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "in_sample_auc": round(in_sample_auc, 6),
        "cv_auc": round(cv_auc, 6) if cv_auc is not None else None,
    }
    return meta, coef_df


def table_lines(df):
    header = "| " + " | ".join(df.columns.astype(str)) + " |"
    separator = "| " + " | ".join(["---"] * len(df.columns)) + " |"
    lines = [header, separator]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in df.columns) + " |")
    return lines


def build_report(bg_thresholds, tg_thresholds, bg_summary, tg_summary, joint_summary, meta, coef_df):
    lines = [
        "# n5_r3 背景分与触发分拆分模型",
        "",
        "## 模型定位",
        "",
        "- 本模型把 `n5_r3` 拆成两层：背景分 + 触发分。",
        "- 背景分负责描述：这个低点前的回撤环境是否足够像有效反弹背景。",
        "- 触发分负责描述：事件日当日的 `1m/5m` V 字修复结构是否足够像有效触发。",
        "- 当前仍然不是交易策略，只是更接近策略结构的中间层。",
        "",
        "## 1. 背景分特征",
        "",
    ]
    lines.extend(
        table_lines(bg_thresholds[["feature", "feature_cn", "direction", "threshold", "positive_mean"]])
    )
    lines.extend(
        [
            "",
            "## 2. 触发分特征",
            "",
        ]
    )
    lines.extend(
        table_lines(tg_thresholds[["feature", "feature_cn", "direction", "threshold", "positive_mean"]])
    )
    lines.extend(
        [
            "",
            "## 3. 单独分数分布",
            "",
            "### 背景分",
            "",
        ]
    )
    lines.extend(table_lines(bg_summary))
    lines.extend(
        [
            "",
            "### 触发分",
            "",
        ]
    )
    lines.extend(table_lines(tg_summary))
    lines.extend(
        [
            "",
            "## 4. 背景分 + 触发分联合分布",
            "",
        ]
    )
    lines.extend(table_lines(joint_summary.head(20)))
    lines.extend(
        [
            "",
            "## 5. 两分模型的逻辑回归结果",
            "",
            "- 样本数：`{}`".format(meta["sample_count"]),
            "- 正样本数：`{}`".format(meta["positive_count"]),
            "- 负样本数：`{}`".format(meta["negative_count"]),
            "- 样本内 AUC：`{}`".format(meta["in_sample_auc"]),
            "- 交叉验证 AUC：`{}`".format(meta["cv_auc"]),
            "",
        ]
    )
    lines.extend(table_lines(coef_df[["feature", "feature_cn", "coefficient", "abs_coefficient"]]))
    lines.extend(
        [
            "",
            "## 6. 当前能落下来的理解",
            "",
            "1. 如果背景分高、触发分也高，这类样本更接近“可交易的有效反弹事件”。",
            "2. 如果背景分高但触发分低，更像“环境允许，但日内结构没确认”。",
            "3. 如果触发分高但背景分低，更像“当日有修复动作，但前置回撤环境不够标准”。",
            "4. 下一步更适合继续观察联合分布里哪些分数组合的正样本占比显著更高。",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Building n5_r3 background/trigger split scores")

    df = load_merged_dataset()
    bg_thresholds = build_threshold_table(df, BACKGROUND_FEATURES, BACKGROUND_DIRECTION, "background")
    tg_thresholds = build_threshold_table(df, TRIGGER_FEATURES, TRIGGER_DIRECTION, "trigger")

    df, bg_point_cols = apply_score(df, bg_thresholds, "background")
    df, tg_point_cols = apply_score(df, tg_thresholds, "trigger")

    bg_summary = summarize_single_score(df, "background_score")
    tg_summary = summarize_single_score(df, "trigger_score")
    joint_summary = summarize_joint_score(df)
    meta, coef_df = fit_score_model(df)

    bg_thresholds.to_csv(OUTPUT_DIR / "background_thresholds.csv", index=False, encoding="utf-8-sig")
    tg_thresholds.to_csv(OUTPUT_DIR / "trigger_thresholds.csv", index=False, encoding="utf-8-sig")
    df[
        ["trade_date", "target", "background_score", "trigger_score"]
        + BACKGROUND_FEATURES
        + TRIGGER_FEATURES
        + bg_point_cols
        + tg_point_cols
    ].to_csv(OUTPUT_DIR / "bg_trigger_scored_dataset.csv", index=False, encoding="utf-8-sig")
    bg_summary.to_csv(OUTPUT_DIR / "background_score_summary.csv", index=False, encoding="utf-8-sig")
    tg_summary.to_csv(OUTPUT_DIR / "trigger_score_summary.csv", index=False, encoding="utf-8-sig")
    joint_summary.to_csv(OUTPUT_DIR / "joint_score_summary.csv", index=False, encoding="utf-8-sig")
    coef_df.to_csv(OUTPUT_DIR / "bg_trigger_logit_coefficients.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / "model_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT_DIR / "n5_r3背景分与触发分拆分模型.md").write_text(
        build_report(bg_thresholds, tg_thresholds, bg_summary, tg_summary, joint_summary, meta, coef_df),
        encoding="utf-8",
    )

    print("outputs:")
    print(" -", OUTPUT_DIR / "background_score_summary.csv")
    print(" -", OUTPUT_DIR / "trigger_score_summary.csv")
    print(" -", OUTPUT_DIR / "n5_r3背景分与触发分拆分模型.md")


if __name__ == "__main__":
    main()
