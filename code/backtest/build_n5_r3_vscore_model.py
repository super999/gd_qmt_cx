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
from feature_labels import feature_label


INPUT_DATASET = (
    Path(__file__).resolve().parent
    / "outputs"
    / "analyze_510300_v_reversal_multiframe"
    / "multiframe_dataset.csv"
)
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "build_n5_r3_vscore_model"
LABEL_NAME = "n5_r3"

# Chosen for interpretability, coverage, and repeated appearance in earlier analyses.
SELECTED_FEATURES = [
    "m5_low_before_last_quarter",
    "m5_low_pos_ratio",
    "m5_rebound_to_close",
    "m5_up_close_streak_after_low",
    "m5_up_bar_ratio_after_low",
    "m5_close_in_range",
    "m1_up_close_streak_after_low",
]

# Direction of "more like a valid V reversal".
FEATURE_DIRECTION = {
    "m5_low_before_last_quarter": "higher",
    "m5_low_pos_ratio": "lower",
    "m5_rebound_to_close": "higher",
    "m5_up_close_streak_after_low": "higher",
    "m5_up_bar_ratio_after_low": "higher",
    "m5_close_in_range": "higher",
    "m1_up_close_streak_after_low": "higher",
}


def load_dataset():
    df = pd.read_csv(INPUT_DATASET, dtype={"trade_date": str})
    enough_col = LABEL_NAME + "_enough_window"
    target_col = LABEL_NAME + "_target"
    df = df[df[enough_col].fillna(False)].copy()
    df["target"] = df[target_col].astype(int)
    return df


def build_thresholds(df):
    pos = df[df["target"] == 1].copy()
    thresholds = []
    for feature in SELECTED_FEATURES:
        series = pd.to_numeric(pos[feature], errors="coerce")
        threshold = float(series.median())
        thresholds.append(
            {
                "feature": feature,
                "feature_cn": feature_label(feature),
                "direction": FEATURE_DIRECTION[feature],
                "threshold": threshold,
                "positive_median": threshold,
                "positive_mean": float(series.mean()),
            }
        )
    return pd.DataFrame(thresholds)


def apply_score(df, thresholds_df):
    working = df.copy()
    score_cols = []
    for _, row in thresholds_df.iterrows():
        feature = row["feature"]
        direction = row["direction"]
        threshold = row["threshold"]
        score_col = feature + "_point"
        values = pd.to_numeric(working[feature], errors="coerce")
        if direction == "higher":
            working[score_col] = (values >= threshold).astype(int)
        else:
            working[score_col] = (values <= threshold).astype(int)
        score_cols.append(score_col)

    working["vscore"] = working[score_cols].sum(axis=1)
    return working, score_cols


def summarize_score(df):
    grouped = (
        df.groupby(["vscore", "target"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
        .rename(columns={0: "negative_count", 1: "positive_count"})
    )
    grouped["positive_ratio"] = grouped["positive_count"] / (
        grouped["positive_count"] + grouped["negative_count"]
    )
    return grouped.sort_values("vscore").reset_index(drop=True)


def fit_selected_feature_logit(df):
    X = df[SELECTED_FEATURES].copy()
    for col in SELECTED_FEATURES:
        X[col] = pd.to_numeric(X[col], errors="coerce")
    X = X.fillna(X.median(numeric_only=True))
    y = df["target"].astype(int)

    pipe = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("logit", LogisticRegression(max_iter=3000, random_state=42)),
        ]
    )
    pipe.fit(X, y)
    probs = pipe.predict_proba(X)[:, 1]
    in_sample_auc = float(roc_auc_score(y, probs))

    positive_count = int(y.sum())
    negative_count = int((y == 0).sum())
    cv_auc = None
    n_splits = min(5, positive_count, negative_count)
    if n_splits >= 3:
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        cv_auc = float(cross_val_score(pipe, X, y, cv=cv, scoring="roc_auc").mean())

    coef = pipe.named_steps["logit"].coef_[0]
    coef_df = pd.DataFrame({"feature": SELECTED_FEATURES, "coefficient": coef})
    coef_df["feature_cn"] = coef_df["feature"].map(feature_label)
    coef_df["abs_coefficient"] = coef_df["coefficient"].abs()
    coef_df = coef_df.sort_values("abs_coefficient", ascending=False).reset_index(drop=True)

    meta = {
        "label_name": LABEL_NAME,
        "sample_count": int(len(X)),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "feature_count": int(len(SELECTED_FEATURES)),
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


def build_report(thresholds_df, score_summary_df, logit_meta, coef_df):
    lines = [
        "# n5_r3 首版 V 字结构评分模型",
        "",
        "## 模型定位",
        "",
        "- 本模型不是交易策略，只是 `n5_r3` 事件标签下的首版结构评分卡。",
        "- 目标是把多时间框架 V 字特征压成少量、可解释、可手工检查的结构分数。",
        "- 这一步的产出，是后续翻译成策略条件前的中间层。",
        "",
        "## 1. 当前选用的特征",
        "",
    ]
    lines.extend(
        table_lines(thresholds_df[["feature", "feature_cn", "direction", "threshold", "positive_mean"]])
    )
    lines.extend(
        [
            "",
            "解释：",
            "- `m5` 作为主分析层，承担绝大部分结构判断。",
            "- `m1_up_close_streak_after_low` 作为细节补充，用来观察低点后修复是否连贯。",
            "",
            "## 2. 评分规则",
            "",
            "- 每个特征满足阈值记 `1` 分，不满足记 `0` 分。",
            "- 总分 `vscore` 为所有单项分数之和。",
            "- 当前总分范围：`0` 到 `{}`。".format(len(SELECTED_FEATURES)),
            "",
        ]
    )
    lines.extend(table_lines(score_summary_df))
    lines.extend(
        [
            "",
            "## 3. 选定特征下的逻辑回归参考结果",
            "",
            "- 样本数：`{}`".format(logit_meta["sample_count"]),
            "- 正样本数：`{}`".format(logit_meta["positive_count"]),
            "- 负样本数：`{}`".format(logit_meta["negative_count"]),
            "- 特征数：`{}`".format(logit_meta["feature_count"]),
            "- 样本内 AUC：`{}`".format(logit_meta["in_sample_auc"]),
            "- 交叉验证 AUC：`{}`".format(logit_meta["cv_auc"]),
            "",
        ]
    )
    lines.extend(table_lines(coef_df[["feature", "feature_cn", "coefficient", "abs_coefficient"]]))
    lines.extend(
        [
            "",
            "## 4. 当前可落下来的理解",
            "",
            "1. 当前评分卡已经能把“更像有效 V 字结构”的事件日压成一个简单分数。",
            "2. 这个分数还不是交易信号，它只是说明结构更接近历史上的有效反弹样本。",
            "3. 下一步更合适的是：",
            "   - 观察 `vscore` 在不同分数段的正样本占比",
            "   - 再决定是否把分数拆成“背景分 + 触发分”",
            "   - 最后才讨论如何翻译成策略条件。",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Building n5_r3 V-structure scorecard")

    df = load_dataset()
    thresholds_df = build_thresholds(df)
    scored_df, score_cols = apply_score(df, thresholds_df)
    score_summary_df = summarize_score(scored_df)
    logit_meta, coef_df = fit_selected_feature_logit(df)

    thresholds_df.to_csv(OUTPUT_DIR / "selected_feature_thresholds.csv", index=False, encoding="utf-8-sig")
    scored_df[["trade_date", "target", "vscore"] + SELECTED_FEATURES + score_cols].to_csv(
        OUTPUT_DIR / "n5_r3_scored_dataset.csv", index=False, encoding="utf-8-sig"
    )
    score_summary_df.to_csv(OUTPUT_DIR / "score_summary.csv", index=False, encoding="utf-8-sig")
    coef_df.to_csv(OUTPUT_DIR / "selected_feature_logit_coefficients.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / "model_meta.json").write_text(
        json.dumps(logit_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT_DIR / "n5_r3首版V字结构评分模型.md").write_text(
        build_report(thresholds_df, score_summary_df, logit_meta, coef_df), encoding="utf-8"
    )

    print("outputs:")
    print(" -", OUTPUT_DIR / "selected_feature_thresholds.csv")
    print(" -", OUTPUT_DIR / "score_summary.csv")
    print(" -", OUTPUT_DIR / "n5_r3首版V字结构评分模型.md")


if __name__ == "__main__":
    main()
