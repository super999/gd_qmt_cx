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

import minimal_stock_backtest as base


STOCK = "510300.SH"
START_DATE = "20240101"
END_DATE = "20260424"
EVENT_DIR = Path(__file__).resolve().parent / "outputs" / "scan_510300_rebound_events"
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "analyze_510300_rebound_features"
LABELS_TO_ANALYZE = ["n5_r3", "n10_r3", "n10_r5"]


FEATURE_COLUMNS = [
    "ret_1d",
    "ret_3d",
    "ret_5d",
    "drawdown_from_high_5",
    "drawdown_from_high_10",
    "drawdown_from_high_20",
    "close_vs_ma5",
    "close_vs_ma10",
    "close_vs_ma20",
    "low_vs_ma20",
    "rsi6",
    "down_days_last3",
    "down_days_last5",
    "vol_ratio_5",
    "volatility_5",
    "volatility_10",
    "intraday_range",
    "close_in_day_range",
    "lower_shadow_ratio",
    "upper_shadow_ratio",
    "gap_from_prev_close",
]


def compute_event_features():
    base.ensure_history_download([STOCK], base.DAILY_PERIOD, START_DATE, END_DATE)
    frame = base.load_price_frame(STOCK, base.DAILY_PERIOD, START_DATE, END_DATE).copy()
    frame["trade_date"] = frame.index.astype(str).str[:8]

    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    open_ = frame["open"].astype(float)
    volume = frame["volume"].astype(float)

    frame["ma5"] = close.rolling(5, min_periods=5).mean()
    frame["ma10"] = close.rolling(10, min_periods=10).mean()
    frame["ma20"] = close.rolling(20, min_periods=20).mean()
    frame["rsi6"] = base.compute_rsi(close, 6)

    frame["ret_1d"] = close.pct_change(1)
    frame["ret_3d"] = close.pct_change(3)
    frame["ret_5d"] = close.pct_change(5)

    frame["drawdown_from_high_5"] = low / high.rolling(5, min_periods=5).max() - 1.0
    frame["drawdown_from_high_10"] = low / high.rolling(10, min_periods=10).max() - 1.0
    frame["drawdown_from_high_20"] = low / high.rolling(20, min_periods=20).max() - 1.0

    frame["close_vs_ma5"] = close / frame["ma5"] - 1.0
    frame["close_vs_ma10"] = close / frame["ma10"] - 1.0
    frame["close_vs_ma20"] = close / frame["ma20"] - 1.0
    frame["low_vs_ma20"] = low / frame["ma20"] - 1.0

    frame["down_day"] = (close < open_).astype(int)
    frame["down_days_last3"] = frame["down_day"].rolling(3, min_periods=3).sum()
    frame["down_days_last5"] = frame["down_day"].rolling(5, min_periods=5).sum()

    frame["volume_ma5"] = volume.rolling(5, min_periods=5).mean()
    frame["vol_ratio_5"] = volume / frame["volume_ma5"] - 1.0

    frame["daily_return"] = close.pct_change()
    frame["volatility_5"] = frame["daily_return"].rolling(5, min_periods=5).std()
    frame["volatility_10"] = frame["daily_return"].rolling(10, min_periods=10).std()

    frame["intraday_range"] = high / low - 1.0
    day_range = (high - low).replace(0.0, pd.NA)
    frame["close_in_day_range"] = (close - low) / day_range
    frame["lower_shadow_ratio"] = (pd.concat([open_, close], axis=1).min(axis=1) - low) / day_range
    frame["upper_shadow_ratio"] = (high - pd.concat([open_, close], axis=1).max(axis=1)) / day_range
    frame["gap_from_prev_close"] = open_ / close.shift(1) - 1.0

    keep_cols = ["trade_date"] + FEATURE_COLUMNS
    return frame[keep_cols].copy()


def load_candidate_dataset():
    candidate_path = EVENT_DIR / "candidate_days.csv"
    df = pd.read_csv(candidate_path, dtype={"trade_date": str})
    return df


def build_labeled_dataset():
    features = compute_event_features()
    candidates = load_candidate_dataset()
    df = candidates.merge(features, on="trade_date", how="left")
    df = df[df["is_local_low_candidate"].fillna(False)].copy()
    return df


def standardized_diff(pos, neg):
    pos = pos.dropna()
    neg = neg.dropna()
    if len(pos) < 2 or len(neg) < 2:
        return 0.0
    pooled = (((len(pos) - 1) * pos.var()) + ((len(neg) - 1) * neg.var())) / (len(pos) + len(neg) - 2)
    if pooled <= 0 or pd.isna(pooled):
        return 0.0
    return float((pos.mean() - neg.mean()) / (pooled**0.5))


def build_feature_compare(df, label_name):
    valid_col = label_name + "_valid_event"
    enough_col = label_name + "_enough_window"
    subset = df[df[enough_col].fillna(False)].copy()
    subset["target"] = subset[valid_col].fillna(False).astype(int)
    pos = subset[subset["target"] == 1]
    neg = subset[subset["target"] == 0]

    rows = []
    for col in FEATURE_COLUMNS:
        pos_s = pd.to_numeric(pos[col], errors="coerce")
        neg_s = pd.to_numeric(neg[col], errors="coerce")
        rows.append(
            {
                "feature": col,
                "positive_count": int(pos_s.notna().sum()),
                "negative_count": int(neg_s.notna().sum()),
                "positive_mean": round(float(pos_s.mean()), 6) if pos_s.notna().any() else None,
                "negative_mean": round(float(neg_s.mean()), 6) if neg_s.notna().any() else None,
                "positive_median": round(float(pos_s.median()), 6) if pos_s.notna().any() else None,
                "negative_median": round(float(neg_s.median()), 6) if neg_s.notna().any() else None,
                "mean_gap": round(float(pos_s.mean() - neg_s.mean()), 6)
                if pos_s.notna().any() and neg_s.notna().any()
                else None,
                "std_diff": round(standardized_diff(pos_s, neg_s), 6),
            }
        )
    result = pd.DataFrame(rows)
    result["abs_std_diff"] = result["std_diff"].abs()
    return subset, result.sort_values("abs_std_diff", ascending=False).reset_index(drop=True)


def fit_logit(subset, label_name):
    working = subset[["trade_date", "target"] + FEATURE_COLUMNS].copy()
    for col in FEATURE_COLUMNS:
        working[col] = pd.to_numeric(working[col], errors="coerce")

    feature_df = working[FEATURE_COLUMNS].copy()
    missing_ratio = feature_df.isna().mean()
    use_cols = [col for col in FEATURE_COLUMNS if missing_ratio[col] <= 0.10]
    feature_df = feature_df[use_cols].fillna(feature_df[use_cols].median())
    target = working["target"].astype(int)

    if target.nunique() < 2 or len(use_cols) < 3:
        return {}, pd.DataFrame()

    pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("logit", LogisticRegression(max_iter=2000, random_state=42)),
        ]
    )
    pipeline.fit(feature_df, target)
    probs = pipeline.predict_proba(feature_df)[:, 1]
    in_sample_auc = float(roc_auc_score(target, probs))

    positive_count = int(target.sum())
    negative_count = int((target == 0).sum())
    cv_auc = None
    n_splits = min(5, positive_count, negative_count)
    if n_splits >= 3:
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        cv_auc = float(cross_val_score(pipeline, feature_df, target, cv=cv, scoring="roc_auc").mean())

    coef = pipeline.named_steps["logit"].coef_[0]
    coef_df = pd.DataFrame({"feature": use_cols, "coefficient": coef})
    coef_df["abs_coefficient"] = coef_df["coefficient"].abs()
    coef_df = coef_df.sort_values("abs_coefficient", ascending=False).reset_index(drop=True)

    meta = {
        "label_name": label_name,
        "sample_count": int(len(feature_df)),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "feature_count": int(len(use_cols)),
        "in_sample_auc": round(in_sample_auc, 6),
        "cv_auc": round(cv_auc, 6) if cv_auc is not None else None,
    }
    return meta, coef_df


def build_label_summary(df):
    rows = []
    for label_name in LABELS_TO_ANALYZE:
        enough_col = label_name + "_enough_window"
        valid_col = label_name + "_valid_event"
        subset = df[df[enough_col].fillna(False)].copy()
        positives = int(subset[valid_col].fillna(False).sum())
        negatives = int(len(subset) - positives)
        rows.append(
            {
                "label_name": label_name,
                "candidate_count": int(len(subset)),
                "positive_count": positives,
                "negative_count": negatives,
                "positive_ratio": round(float(positives / len(subset)), 6) if len(subset) else 0.0,
            }
        )
    return pd.DataFrame(rows)


def table_lines(df):
    header = "| " + " | ".join(df.columns.astype(str)) + " |"
    separator = "| " + " | ".join(["---"] * len(df.columns)) + " |"
    lines = [header, separator]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in df.columns) + " |")
    return lines


def build_report(summary_df, feature_reports, model_reports):
    lines = [
        "# 510300 低吸反弹事件前特征分析",
        "",
        "## 结论先行",
        "",
        "- 当前分析对象不是策略信号，而是已经定义好的低吸反弹事件样本。",
        "- 本轮重点是看：有效反弹事件发生前，到底有哪些统计上更常见的特征。",
        "- 这一步的产出是“特征候选”，不是最终策略条件。",
        "",
        "## 1. 标签样本概况",
        "",
    ]
    lines.extend(table_lines(summary_df))

    for label_name, feature_df in feature_reports.items():
        lines.extend(
            [
                "",
                "## 2. `{}` 正负样本差异最大的前 10 个特征".format(label_name),
                "",
            ]
        )
        show_df = feature_df[
            ["feature", "positive_mean", "negative_mean", "mean_gap", "std_diff", "abs_std_diff"]
        ].head(10)
        lines.extend(table_lines(show_df))

    for label_name, meta in model_reports["meta"].items():
        coef_df = model_reports["coef"].get(label_name)
        lines.extend(
            [
                "",
                "## 3. `{}` 简单逻辑回归结果".format(label_name),
                "",
                "- 样本数：`{}`".format(meta.get("sample_count")),
                "- 正样本数：`{}`".format(meta.get("positive_count")),
                "- 负样本数：`{}`".format(meta.get("negative_count")),
                "- 特征数：`{}`".format(meta.get("feature_count")),
                "- 样本内 AUC：`{}`".format(meta.get("in_sample_auc")),
                "- 交叉验证 AUC：`{}`".format(meta.get("cv_auc")),
                "",
            ]
        )
        if coef_df is not None and not coef_df.empty:
            lines.extend(table_lines(coef_df[["feature", "coefficient", "abs_coefficient"]].head(10)))

    lines.extend(
        [
            "",
            "## 4. 当前能落下来的研究结论",
            "",
            "1. 哪些特征在正样本里更常见，只能说明它们值得继续研究，不能直接当作交易规则。",
            "2. 如果不同标签（例如 `n5_r3` 与 `n10_r3`）反复指向相似特征，这些特征更值得进入下一轮建模。",
            "3. 下一步更合适的是：",
            "   - 先选 1 到 2 组主标签",
            "   - 再围绕这些标签做更细的事件前窗口分析",
            "   - 然后再考虑把高价值特征翻译成策略条件。",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Analyzing 510300 pre-event features")

    labeled_df = build_labeled_dataset()
    summary_df = build_label_summary(labeled_df)
    summary_df.to_csv(OUTPUT_DIR / "label_sample_summary.csv", index=False, encoding="utf-8-sig")

    feature_reports = {}
    model_meta = {}
    model_coef = {}

    for label_name in LABELS_TO_ANALYZE:
        subset, compare_df = build_feature_compare(labeled_df, label_name)
        compare_path = OUTPUT_DIR / f"feature_compare_{label_name}.csv"
        compare_df.to_csv(compare_path, index=False, encoding="utf-8-sig")
        feature_reports[label_name] = compare_df

        meta, coef_df = fit_logit(subset, label_name)
        model_meta[label_name] = meta
        model_coef[label_name] = coef_df
        if not coef_df.empty:
            coef_df.to_csv(
                OUTPUT_DIR / f"logit_coefficients_{label_name}.csv",
                index=False,
                encoding="utf-8-sig",
            )

    report_path = OUTPUT_DIR / "510300低吸反弹事件前特征分析.md"
    report_path.write_text(
        build_report(summary_df, feature_reports, {"meta": model_meta, "coef": model_coef}),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "model_meta.json").write_text(
        json.dumps(model_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("outputs:")
    print(" -", OUTPUT_DIR / "label_sample_summary.csv")
    print(" -", report_path)


if __name__ == "__main__":
    main()
