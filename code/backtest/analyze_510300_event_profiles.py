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


OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "analyze_510300_event_profiles"
LABELS_TO_ANALYZE = ["n5_r3", "n10_r3"]
PROFILE_OFFSETS = [10, 5, 3, 1, 0]
PROFILE_FEATURES = [
    "drawdown_from_high_5",
    "drawdown_from_high_10",
    "drawdown_from_high_20",
    "down_days_last3",
    "down_days_last5",
    "volatility_5",
    "volatility_10",
    "ret_1d",
    "ret_3d",
    "ret_5d",
    "close_vs_ma5",
    "close_vs_ma20",
    "vol_ratio_5",
    "intraday_range",
    "close_in_day_range",
]


def build_event_profile_dataset():
    labeled_df = featmod.build_labeled_dataset().copy()
    features_df = featmod.compute_event_features().copy()
    features_df = features_df.reset_index(drop=True)
    features_df["row_idx"] = range(len(features_df))

    merged = labeled_df.merge(features_df[["trade_date", "row_idx"]], on="trade_date", how="left")
    return merged, features_df


def extract_profiles(labeled_df, features_df, label_name):
    enough_col = label_name + "_enough_window"
    valid_col = label_name + "_valid_event"
    subset = labeled_df[labeled_df[enough_col].fillna(False)].copy()
    subset["target"] = subset[valid_col].fillna(False).astype(int)

    rows = []
    for _, event_row in subset.iterrows():
        event_idx = int(event_row["row_idx"])
        for offset in PROFILE_OFFSETS:
            idx = event_idx - offset
            if idx < 0:
                continue
            profile_row = {
                "label_name": label_name,
                "trade_date": event_row["trade_date"],
                "target": int(event_row["target"]),
                "offset_days": offset,
            }
            feature_slice = features_df.iloc[idx]
            profile_row["feature_trade_date"] = feature_slice["trade_date"]
            for feature in PROFILE_FEATURES:
                profile_row[feature] = feature_slice.get(feature)
            rows.append(profile_row)

    return pd.DataFrame(rows)


def summarize_profiles(profile_df):
    rows = []
    for feature in PROFILE_FEATURES:
        grouped = (
            profile_df.groupby(["label_name", "offset_days", "target"])[feature]
            .agg(["mean", "median", "count"])
            .reset_index()
        )
        pivot_mean = grouped.pivot_table(
            index=["label_name", "offset_days"], columns="target", values="mean"
        ).reset_index()
        pivot_median = grouped.pivot_table(
            index=["label_name", "offset_days"], columns="target", values="median"
        ).reset_index()
        pivot_count = grouped.pivot_table(
            index=["label_name", "offset_days"], columns="target", values="count"
        ).reset_index()

        pivot_mean.columns = ["label_name", "offset_days", "negative_mean", "positive_mean"]
        pivot_median.columns = ["label_name", "offset_days", "negative_median", "positive_median"]
        pivot_count.columns = ["label_name", "offset_days", "negative_count", "positive_count"]

        summary = (
            pivot_mean.merge(pivot_median, on=["label_name", "offset_days"], how="outer")
            .merge(pivot_count, on=["label_name", "offset_days"], how="outer")
            .sort_values(["label_name", "offset_days"], ascending=[True, False])
        )
        summary["feature"] = feature
        summary["mean_gap"] = summary["positive_mean"] - summary["negative_mean"]
        rows.append(summary)

    result = pd.concat(rows, ignore_index=True)
    return result[
        [
            "label_name",
            "feature",
            "offset_days",
            "positive_mean",
            "negative_mean",
            "mean_gap",
            "positive_median",
            "negative_median",
            "positive_count",
            "negative_count",
        ]
    ].copy()


def build_window_features(summary_df):
    rows = []
    for label_name in LABELS_TO_ANALYZE:
        subset = summary_df[summary_df["label_name"] == label_name].copy()
        for feature in PROFILE_FEATURES:
            feature_df = subset[subset["feature"] == feature].copy().sort_values("offset_days")
            if feature_df.empty:
                continue
            row = {"label_name": label_name, "feature": feature}
            for _, item in feature_df.iterrows():
                row[f"gap_t_minus_{int(item['offset_days'])}"] = round(float(item["mean_gap"]), 6)
            if "gap_t_minus_10" in row and "gap_t_minus_0" in row:
                row["gap_change_10_to_0"] = round(row["gap_t_minus_0"] - row["gap_t_minus_10"], 6)
            rows.append(row)
    return pd.DataFrame(rows)


def build_model_dataset(profile_df, label_name):
    subset = profile_df[profile_df["label_name"] == label_name].copy()
    pivot_rows = []
    for trade_date, event_df in subset.groupby("trade_date"):
        row = {"trade_date": trade_date, "target": int(event_df["target"].iloc[0])}
        for _, item in event_df.iterrows():
            offset = int(item["offset_days"])
            for feature in PROFILE_FEATURES:
                row[f"{feature}_t_minus_{offset}"] = item.get(feature)
        pivot_rows.append(row)
    return pd.DataFrame(pivot_rows)


def fit_profile_model(model_df, label_name):
    if model_df.empty:
        return {}, pd.DataFrame()

    feature_cols = [col for col in model_df.columns if col not in {"trade_date", "target"}]
    working = model_df.copy()
    for col in feature_cols:
        working[col] = pd.to_numeric(working[col], errors="coerce")

    valid_cols = [col for col in feature_cols if working[col].isna().mean() <= 0.15]
    X = working[valid_cols].fillna(working[valid_cols].median())
    y = working["target"].astype(int)

    if y.nunique() < 2 or len(valid_cols) < 5:
        return {}, pd.DataFrame()

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
    coef_df = pd.DataFrame({"feature": valid_cols, "coefficient": coef})
    coef_df["abs_coefficient"] = coef_df["coefficient"].abs()
    coef_df = coef_df.sort_values("abs_coefficient", ascending=False).reset_index(drop=True)

    meta = {
        "label_name": label_name,
        "sample_count": int(len(X)),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "feature_count": int(len(valid_cols)),
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


def build_report(summary_df, window_df, model_meta, model_coef):
    lines = [
        "# 510300 低吸反弹事件日前窗口剖面分析",
        "",
        "## 结论先行",
        "",
        "- 这一轮不再只看事件日单点特征，而是看事件日前 `10/5/3/1/0` 天的特征演化。",
        "- 重点分析回撤深度、连跌结构、波动率、量能与价格位置如何走到事件低点。",
        "- 这一步更接近后续建模和规则设计，因为它开始回答“有效反弹前通常是怎样一步步形成的”。",
        "",
    ]

    for label_name in LABELS_TO_ANALYZE:
        lines.extend(
            [
                f"## `{label_name}` 剖面差异最大的特征",
                "",
            ]
        )
        subset = window_df[window_df["label_name"] == label_name].copy()
        if subset.empty:
            lines.append("- 无可用结果。")
            continue
        show_df = subset.sort_values(
            ["gap_change_10_to_0", "gap_t_minus_0"], ascending=[False, False]
        ).head(10)
        lines.extend(table_lines(show_df))
        lines.append("")

    for label_name in LABELS_TO_ANALYZE:
        meta = model_meta.get(label_name, {})
        coef_df = model_coef.get(label_name, pd.DataFrame())
        if not meta:
            continue
        lines.extend(
            [
                f"## `{label_name}` 剖面逻辑回归结果",
                "",
                f"- 样本数：`{meta.get('sample_count')}`",
                f"- 正样本数：`{meta.get('positive_count')}`",
                f"- 负样本数：`{meta.get('negative_count')}`",
                f"- 特征数：`{meta.get('feature_count')}`",
                f"- 样本内 AUC：`{meta.get('in_sample_auc')}`",
                f"- 交叉验证 AUC：`{meta.get('cv_auc')}`",
                "",
            ]
        )
        if not coef_df.empty:
            lines.extend(table_lines(coef_df[["feature", "coefficient", "abs_coefficient"]].head(12)))
            lines.append("")

    lines.extend(
        [
            "## 当前可落下来的理解",
            "",
            "1. 如果某些特征在 `t-10 -> t` 的差异持续扩大，说明它们更像“形成事件的过程特征”，比单点值更值得用。",
            "2. 如果某些特征只在事件日当天才明显分化，它们更可能适合作为触发条件，而不是背景条件。",
            "3. 下一步更合理的是：",
            "   - 先挑 1 到 2 个主标签继续深入",
            "   - 再围绕最稳定的 5 到 8 个特征做组合建模",
            "   - 然后才讨论哪些特征该翻译成策略规则。",
        ]
    )

    return "\n".join(lines) + "\n"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Analyzing 510300 event-window profiles")

    labeled_df, features_df = build_event_profile_dataset()
    all_profiles = []
    for label_name in LABELS_TO_ANALYZE:
        all_profiles.append(extract_profiles(labeled_df, features_df, label_name))
    profile_df = pd.concat(all_profiles, ignore_index=True)
    summary_df = summarize_profiles(profile_df)
    summary_df.to_csv(OUTPUT_DIR / "profile_summary_long.csv", index=False, encoding="utf-8-sig")

    window_df = build_window_features(summary_df)
    window_df.to_csv(OUTPUT_DIR / "profile_gap_summary.csv", index=False, encoding="utf-8-sig")

    model_meta = {}
    model_coef = {}
    for label_name in LABELS_TO_ANALYZE:
        model_df = build_model_dataset(profile_df, label_name)
        model_df.to_csv(
            OUTPUT_DIR / f"profile_model_dataset_{label_name}.csv",
            index=False,
            encoding="utf-8-sig",
        )
        meta, coef_df = fit_profile_model(model_df, label_name)
        model_meta[label_name] = meta
        model_coef[label_name] = coef_df
        if not coef_df.empty:
            coef_df.to_csv(
                OUTPUT_DIR / f"profile_logit_coefficients_{label_name}.csv",
                index=False,
                encoding="utf-8-sig",
            )

    report_path = OUTPUT_DIR / "510300低吸反弹事件日前窗口剖面分析.md"
    report_path.write_text(build_report(summary_df, window_df, model_meta, model_coef), encoding="utf-8")
    (OUTPUT_DIR / "profile_model_meta.json").write_text(
        json.dumps(model_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("outputs:")
    print(" -", OUTPUT_DIR / "profile_summary_long.csv")
    print(" -", OUTPUT_DIR / "profile_gap_summary.csv")
    print(" -", report_path)


if __name__ == "__main__":
    main()
