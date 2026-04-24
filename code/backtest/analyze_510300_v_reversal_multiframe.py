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

from xtquant import xtdata
from feature_labels import feature_label


STOCK = "510300.SH"
START_DATE = "20240101"
END_DATE = "20260424"
LABELS_TO_ANALYZE = ["n5_r3", "n10_r3"]
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "analyze_510300_v_reversal_multiframe"
EVENT_CANDIDATE_PATH = (
    Path(__file__).resolve().parent / "outputs" / "scan_510300_rebound_events" / "candidate_days.csv"
)


def load_intraday(period):
    xtdata.download_history_data(
        STOCK,
        period=period,
        start_time=START_DATE,
        end_time=END_DATE,
        incrementally=False,
    )
    data = xtdata.get_local_data(
        field_list=[],
        stock_list=[STOCK],
        period=period,
        start_time=START_DATE,
        end_time=END_DATE,
        count=-1,
        dividend_type="front",
        fill_data=True,
    ).get(STOCK)
    if data is None or data.empty:
        raise RuntimeError("no {} data for {}".format(period, STOCK))
    frame = data.copy()
    frame.index = frame.index.astype(str)
    frame.index.name = "bar_time"
    return frame.sort_index()


def split_by_trade_date(frame):
    mapping = {}
    for trade_date, day_frame in frame.groupby(frame.index.astype(str).str[:8]):
        mapping[str(trade_date)] = day_frame.copy()
    return mapping


def consecutive_positive_closes(series):
    max_streak = 0
    streak = 0
    prev = None
    for value in series:
        if prev is not None and value > prev:
            streak += 1
        else:
            streak = 0
        max_streak = max(max_streak, streak)
        prev = value
    return int(max_streak)


def extract_v_features(day_frame, prefix):
    bars = day_frame.copy()
    if len(bars) < 4:
        return {
            prefix + "_bar_count": int(len(bars)),
            prefix + "_enough_bars": False,
        }

    bars["open"] = pd.to_numeric(bars["open"], errors="coerce")
    bars["high"] = pd.to_numeric(bars["high"], errors="coerce")
    bars["low"] = pd.to_numeric(bars["low"], errors="coerce")
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    bars["volume"] = pd.to_numeric(bars["volume"], errors="coerce")
    bars = bars.dropna(subset=["open", "high", "low", "close"])
    if len(bars) < 4:
        return {
            prefix + "_bar_count": int(len(bars)),
            prefix + "_enough_bars": False,
        }

    low_pos = int(bars["low"].values.argmin())
    low_price = float(bars.iloc[low_pos]["low"])
    high_price = float(bars["high"].max())
    first_open = float(bars.iloc[0]["open"])
    last_close = float(bars.iloc[-1]["close"])
    day_range = high_price - low_price
    bars_after_low = bars.iloc[low_pos + 1 :].copy()
    highs_after_low = bars.iloc[low_pos:]["high"].astype(float)
    lows_after_low = bars.iloc[low_pos:]["low"].astype(float)

    result = {
        prefix + "_bar_count": int(len(bars)),
        prefix + "_enough_bars": True,
        prefix + "_low_pos": low_pos,
        prefix + "_low_pos_ratio": round(low_pos / max(len(bars) - 1, 1), 6),
        prefix + "_low_before_last_quarter": low_pos <= int(len(bars) * 0.75),
        prefix + "_rebound_to_close": round(last_close / low_price - 1.0, 6),
        prefix + "_rebound_to_high_after_low": round(float(highs_after_low.max()) / low_price - 1.0, 6),
        prefix + "_post_low_min_return": round(float(lows_after_low.min()) / low_price - 1.0, 6),
        prefix + "_close_in_range": round((last_close - low_price) / day_range, 6) if day_range > 0 else 0.0,
        prefix + "_day_range": round(high_price / low_price - 1.0, 6),
        prefix + "_close_vs_open": round(last_close / first_open - 1.0, 6),
    }

    if not bars_after_low.empty:
        close_series = bars_after_low["close"].astype(float).tolist()
        result[prefix + "_up_close_streak_after_low"] = consecutive_positive_closes(close_series)
        positive_bars = (bars_after_low["close"].astype(float) > bars_after_low["open"].astype(float)).mean()
        result[prefix + "_up_bar_ratio_after_low"] = round(float(positive_bars), 6)
        avg_before = float(bars.iloc[: low_pos + 1]["volume"].mean())
        avg_after = float(bars_after_low["volume"].mean())
        result[prefix + "_volume_ratio_after_low"] = round(avg_after / avg_before - 1.0, 6) if avg_before else 0.0
    else:
        result[prefix + "_up_close_streak_after_low"] = 0
        result[prefix + "_up_bar_ratio_after_low"] = 0.0
        result[prefix + "_volume_ratio_after_low"] = 0.0

    return result


def build_dataset():
    candidate_df = pd.read_csv(EVENT_CANDIDATE_PATH, dtype={"trade_date": str})
    candidate_df = candidate_df[candidate_df["is_local_low_candidate"].fillna(False)].copy()

    data_1m = split_by_trade_date(load_intraday("1m"))
    data_5m = split_by_trade_date(load_intraday("5m"))
    data_30m = split_by_trade_date(load_intraday("30m"))

    first_covered_date = min(min(data_1m), min(data_5m), min(data_30m))
    working = candidate_df[candidate_df["trade_date"] >= first_covered_date].copy()

    rows = []
    for _, row in working.iterrows():
        trade_date = row["trade_date"]
        if trade_date not in data_1m or trade_date not in data_5m or trade_date not in data_30m:
            continue
        item = {
            "trade_date": trade_date,
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
        }
        for label_name in LABELS_TO_ANALYZE:
            item[label_name + "_target"] = int(bool(row.get(label_name + "_valid_event", False)))
            item[label_name + "_enough_window"] = bool(row.get(label_name + "_enough_window", False))

        item.update(extract_v_features(data_1m[trade_date], "m1"))
        item.update(extract_v_features(data_5m[trade_date], "m5"))
        item.update(extract_v_features(data_30m[trade_date], "m30"))
        rows.append(item)

    return pd.DataFrame(rows), first_covered_date


def compare_features(df, label_name):
    subset = df[df[label_name + "_enough_window"].fillna(False)].copy()
    subset["target"] = subset[label_name + "_target"].astype(int)
    feature_cols = [
        col
        for col in subset.columns
        if col.startswith("m1_") or col.startswith("m5_") or col.startswith("m30_")
    ]
    rows = []
    for col in feature_cols:
        if subset[col].dtype == bool:
            series = subset[col].astype(int)
        else:
            series = pd.to_numeric(subset[col], errors="coerce")
        pos = series[subset["target"] == 1]
        neg = series[subset["target"] == 0]
        rows.append(
            {
                "feature": col,
                "positive_mean": round(float(pos.mean()), 6) if pos.notna().any() else None,
                "negative_mean": round(float(neg.mean()), 6) if neg.notna().any() else None,
                "mean_gap": round(float(pos.mean() - neg.mean()), 6)
                if pos.notna().any() and neg.notna().any()
                else None,
            }
        )
    result = pd.DataFrame(rows)
    result["feature_cn"] = result["feature"].map(feature_label)
    result["abs_mean_gap"] = result["mean_gap"].abs()
    return subset, result.sort_values("abs_mean_gap", ascending=False).reset_index(drop=True)


def fit_model(subset, label_name):
    feature_cols = [
        col
        for col in subset.columns
        if (col.startswith("m1_") or col.startswith("m5_") or col.startswith("m30_"))
        and not col.endswith("_enough_bars")
    ]
    X = subset[feature_cols].copy()
    for col in feature_cols:
        if X[col].dtype == bool:
            X[col] = X[col].astype(int)
        else:
            X[col] = pd.to_numeric(X[col], errors="coerce")
    X = X.fillna(X.median(numeric_only=True))
    y = subset["target"].astype(int)

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
    n_splits = min(5, positive_count, negative_count)
    cv_auc = None
    if n_splits >= 3:
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        cv_auc = float(cross_val_score(pipe, X, y, cv=cv, scoring="roc_auc").mean())

    coef = pipe.named_steps["logit"].coef_[0]
    coef_df = pd.DataFrame({"feature": feature_cols, "coefficient": coef})
    coef_df["feature_cn"] = coef_df["feature"].map(feature_label)
    coef_df["abs_coefficient"] = coef_df["coefficient"].abs()
    coef_df = coef_df.sort_values("abs_coefficient", ascending=False).reset_index(drop=True)

    meta = {
        "label_name": label_name,
        "sample_count": int(len(X)),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "feature_count": int(len(feature_cols)),
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


def build_report(summary_rows, compare_reports, model_meta, model_coef, first_covered_date):
    lines = [
        "# 510300 多时间框架 V 字结构分析",
        "",
        "## 数据限制",
        "",
        "- 当前 `1m/5m/30m` 本地历史覆盖起点：`{}`".format(first_covered_date),
        "- 因此本报告只分析 `2025-04-22` 之后的候选事件日。",
        "",
        "## 样本概况",
        "",
    ]
    summary_df = pd.DataFrame(summary_rows)
    lines.extend(table_lines(summary_df))

    for label_name in LABELS_TO_ANALYZE:
        lines.extend(
            [
                "",
                "## `{}` 多时间框架差异最大的前 15 个特征".format(label_name),
                "",
            ]
        )
        lines.extend(
            table_lines(
                compare_reports[label_name][
                    ["feature", "feature_cn", "positive_mean", "negative_mean", "mean_gap", "abs_mean_gap"]
                ].head(15)
            )
        )

    for label_name in LABELS_TO_ANALYZE:
        meta = model_meta[label_name]
        lines.extend(
            [
                "",
                "## `{}` 多时间框架逻辑回归结果".format(label_name),
                "",
                "- 样本数：`{}`".format(meta["sample_count"]),
                "- 正样本数：`{}`".format(meta["positive_count"]),
                "- 负样本数：`{}`".format(meta["negative_count"]),
                "- 特征数：`{}`".format(meta["feature_count"]),
                "- 样本内 AUC：`{}`".format(meta["in_sample_auc"]),
                "- 交叉验证 AUC：`{}`".format(meta["cv_auc"]),
                "",
            ]
        )
        lines.extend(
            table_lines(
                model_coef[label_name][["feature", "feature_cn", "coefficient", "abs_coefficient"]].head(12)
            )
        )

    lines.extend(
        [
            "",
            "## 当前能落下来的理解",
            "",
            "1. 如果 `1m/5m/30m` 都反复指向相似结构，这类特征更可能是真正有效的 V 字信号。",
            "2. `30m` 更适合看背景和低点位置，`5m` 更适合看成形过程，`1m` 更适合看微观修复速度。",
            "3. 下一步更合理的是：",
            "   - 先固定一个主标签",
            "   - 再把多时间框架里最稳定的 5 到 8 个 V 字特征抽出来",
            "   - 然后才考虑把它们翻译成策略触发条件。",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Analyzing 510300 multiframe V-structure")

    dataset, first_covered_date = build_dataset()
    dataset.to_csv(OUTPUT_DIR / "multiframe_dataset.csv", index=False, encoding="utf-8-sig")

    compare_reports = {}
    model_meta = {}
    model_coef = {}
    summary_rows = []

    for label_name in LABELS_TO_ANALYZE:
        subset, compare_df = compare_features(dataset, label_name)
        compare_reports[label_name] = compare_df
        compare_df.to_csv(
            OUTPUT_DIR / f"feature_compare_{label_name}.csv", index=False, encoding="utf-8-sig"
        )

        meta, coef_df = fit_model(subset, label_name)
        model_meta[label_name] = meta
        model_coef[label_name] = coef_df
        coef_df.to_csv(
            OUTPUT_DIR / f"logit_coefficients_{label_name}.csv", index=False, encoding="utf-8-sig"
        )
        summary_rows.append(meta)

    report_path = OUTPUT_DIR / "510300多时间框架V字结构分析.md"
    report_path.write_text(
        build_report(summary_rows, compare_reports, model_meta, model_coef, first_covered_date),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "model_meta.json").write_text(
        json.dumps(model_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("outputs:")
    print(" -", OUTPUT_DIR / "multiframe_dataset.csv")
    print(" -", report_path)


if __name__ == "__main__":
    main()
