#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd
from scipy.stats import mannwhitneyu
from sklearn.metrics import roc_auc_score

import minimal_stock_backtest as base
from feature_labels import feature_label
from analyze_510300_v_reversal_multiframe import extract_v_features, load_intraday, split_by_trade_date


STOCK = "510300.SH"
START_DATE = "20250425"
END_DATE = "20260424"

EVENT_PATH = Path("报告/研究结论/数据摘要/510300近一年日线月度可交易区间.csv")
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "analyze_best_interval_entry_signals"

DAILY_PRE_FEATURES = [
    "pre_ret_1d",
    "pre_ret_3d",
    "pre_ret_5d",
    "pre_drawdown_from_high_5",
    "pre_drawdown_from_high_10",
    "pre_drawdown_from_high_20",
    "pre_close_vs_ma5",
    "pre_close_vs_ma10",
    "pre_close_vs_ma20",
    "pre_low_vs_ma20",
    "pre_rsi6",
    "pre_down_days_last3",
    "pre_down_days_last5",
    "pre_vol_ratio_5",
    "pre_volatility_5",
    "pre_volatility_10",
]

DAILY_DAY_FEATURES = [
    "day_gap_from_prev_close",
    "day_return",
    "day_intraday_range",
    "day_low_vs_prev_close",
    "day_close_in_range",
    "day_lower_shadow_ratio",
    "day_upper_shadow_ratio",
    "day_volume_ratio_5",
]


def load_event_dates():
    events = pd.read_csv(EVENT_PATH, dtype={"entry_date": str, "exit_date": str})
    return events, set(events["entry_date"].astype(str).tolist())


def load_daily_frame():
    base.ensure_history_download([STOCK], base.DAILY_PERIOD, "20240101", END_DATE)
    frame = base.load_price_frame(STOCK, base.DAILY_PERIOD, "20240101", END_DATE).copy()
    frame.index = frame.index.astype(str)
    frame.index.name = None
    frame["trade_date"] = frame.index.str[:8]
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"]).sort_values("trade_date")
    return frame.reset_index(drop=True)


def add_daily_features(frame):
    df = frame.copy()
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    volume = df["volume"].astype(float)

    ma5 = close.rolling(5, min_periods=5).mean()
    ma10 = close.rolling(10, min_periods=10).mean()
    ma20 = close.rolling(20, min_periods=20).mean()
    daily_return = close.pct_change()
    down_day = (close < open_).astype(int)
    volume_ma5 = volume.rolling(5, min_periods=5).mean()
    day_range = (high - low).replace(0.0, pd.NA)

    current = pd.DataFrame(
        {
            "trade_date": df["trade_date"],
            "ret_1d": close.pct_change(1),
            "ret_3d": close.pct_change(3),
            "ret_5d": close.pct_change(5),
            "drawdown_from_high_5": low / high.rolling(5, min_periods=5).max() - 1.0,
            "drawdown_from_high_10": low / high.rolling(10, min_periods=10).max() - 1.0,
            "drawdown_from_high_20": low / high.rolling(20, min_periods=20).max() - 1.0,
            "close_vs_ma5": close / ma5 - 1.0,
            "close_vs_ma10": close / ma10 - 1.0,
            "close_vs_ma20": close / ma20 - 1.0,
            "low_vs_ma20": low / ma20 - 1.0,
            "rsi6": base.compute_rsi(close, 6),
            "down_days_last3": down_day.rolling(3, min_periods=3).sum(),
            "down_days_last5": down_day.rolling(5, min_periods=5).sum(),
            "vol_ratio_5": volume / volume_ma5 - 1.0,
            "volatility_5": daily_return.rolling(5, min_periods=5).std(),
            "volatility_10": daily_return.rolling(10, min_periods=10).std(),
            "day_gap_from_prev_close": open_ / close.shift(1) - 1.0,
            "day_return": close / close.shift(1) - 1.0,
            "day_intraday_range": high / low - 1.0,
            "day_low_vs_prev_close": low / close.shift(1) - 1.0,
            "day_close_in_range": (close - low) / day_range,
            "day_lower_shadow_ratio": (pd.concat([open_, close], axis=1).min(axis=1) - low) / day_range,
            "day_upper_shadow_ratio": (high - pd.concat([open_, close], axis=1).max(axis=1)) / day_range,
            "day_volume_ratio_5": volume / volume_ma5 - 1.0,
        }
    )

    pre_cols = [
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
    ]
    for col in pre_cols:
        current["pre_" + col] = current[col].shift(1)

    return current[
        ["trade_date"]
        + DAILY_PRE_FEATURES
        + DAILY_DAY_FEATURES
    ].copy()


def build_intraday_feature_frame(dates):
    data_1m = split_by_trade_date(load_intraday("1m"))
    data_5m = split_by_trade_date(load_intraday("5m"))

    rows = []
    for trade_date in dates:
        item = {"trade_date": trade_date}
        if trade_date in data_1m:
            item.update(extract_v_features(data_1m[trade_date], "m1"))
        if trade_date in data_5m:
            item.update(extract_v_features(data_5m[trade_date], "m5"))
        rows.append(item)
    return pd.DataFrame(rows)


def standardized_diff(pos, neg):
    pos = pd.to_numeric(pos, errors="coerce").dropna()
    neg = pd.to_numeric(neg, errors="coerce").dropna()
    if len(pos) < 2 or len(neg) < 2:
        return None
    pooled = (((len(pos) - 1) * pos.var()) + ((len(neg) - 1) * neg.var())) / (len(pos) + len(neg) - 2)
    if pooled <= 0 or pd.isna(pooled):
        return None
    return float((pos.mean() - neg.mean()) / (pooled**0.5))


def single_feature_auc(values, target):
    working = pd.DataFrame({"value": values, "target": target}).dropna()
    if working["target"].nunique() < 2 or len(working) < 5:
        return None
    value = working["value"].astype(float)
    y = working["target"].astype(int)
    auc = roc_auc_score(y, value)
    return float(max(auc, 1.0 - auc))


def mann_whitney_p(pos, neg):
    pos = pd.to_numeric(pos, errors="coerce").dropna()
    neg = pd.to_numeric(neg, errors="coerce").dropna()
    if len(pos) < 3 or len(neg) < 3:
        return None
    try:
        return float(mannwhitneyu(pos, neg, alternative="two-sided").pvalue)
    except ValueError:
        return None


def compare_feature_group(df, feature_cols, group_name):
    pos = df[df["target"] == 1]
    neg = df[df["target"] == 0]
    rows = []

    for col in feature_cols:
        if col not in df.columns:
            continue
        series = df[col].astype(int) if df[col].dtype == bool else pd.to_numeric(df[col], errors="coerce")
        pos_s = series[df["target"] == 1]
        neg_s = series[df["target"] == 0]
        std_diff = standardized_diff(pos_s, neg_s)
        auc = single_feature_auc(series, df["target"])
        p_value = mann_whitney_p(pos_s, neg_s)
        label_key = col
        if col.startswith("pre_"):
            label_key = col.replace("pre_", "", 1)
        elif col.startswith("day_"):
            label_key = col.replace("day_", "", 1)
        rows.append(
            {
                "group": group_name,
                "feature": col,
                "feature_cn": feature_label(label_key),
                "positive_count": int(pos_s.notna().sum()),
                "control_count": int(neg_s.notna().sum()),
                "positive_mean": round(float(pos_s.mean()), 6) if pos_s.notna().any() else None,
                "control_mean": round(float(neg_s.mean()), 6) if neg_s.notna().any() else None,
                "positive_median": round(float(pos_s.median()), 6) if pos_s.notna().any() else None,
                "control_median": round(float(neg_s.median()), 6) if neg_s.notna().any() else None,
                "mean_gap": round(float(pos_s.mean() - neg_s.mean()), 6)
                if pos_s.notna().any() and neg_s.notna().any()
                else None,
                "std_diff": round(std_diff, 6) if std_diff is not None else None,
                "abs_std_diff": round(abs(std_diff), 6) if std_diff is not None else None,
                "single_feature_auc": round(auc, 6) if auc is not None else None,
                "mann_whitney_p": round(p_value, 6) if p_value is not None else None,
            }
        )

    result = pd.DataFrame(rows)
    return result.sort_values(
        ["abs_std_diff", "single_feature_auc"],
        ascending=[False, False],
    ).reset_index(drop=True)


def markdown_table(frame):
    columns = frame.columns.tolist()
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in columns) + " |")
    return "\n".join(lines)


def build_report(events, summary, daily_pre, daily_day, intraday):
    show_cols = [
        "feature",
        "feature_cn",
        "positive_mean",
        "control_mean",
        "positive_median",
        "control_median",
        "std_diff",
        "single_feature_auc",
        "mann_whitney_p",
    ]
    lines = [
        "# 510300 优质区间买入日信号统计分析（第2步）",
        "",
        "## 分析边界",
        "",
        "- 本报告只做统计分析，不改策略规则。",
        "- 正样本来自第1步“满足月度交易期望的区间”的买入日。",
        "- 对照样本为近一年其它交易日。",
        "- 由于正样本只有 `{}` 个，结论只作为候选信号，不作为最终规则。".format(summary["positive_count"]),
        "",
        "## 样本概况",
        "",
        "- 正样本买入日数：`{}`".format(summary["positive_count"]),
        "- 对照交易日数：`{}`".format(summary["control_count"]),
        "- 分析起止：`{}` 至 `{}`".format(START_DATE, END_DATE),
        "",
        "## 结论先行",
        "",
        "- 优质买入日前一天通常已经明显走弱：近10日高点回撤更深、收盘低于5/10日均线、RSI(6)更低。",
        "- 优质买入日当天并不是强势确认日：收盘在日内区间位置更低，当日收益更弱，上影线比例更高。",
        "- 盘中特征也不支持“强 V 后再买”的旧假设：优质样本的日内低点更晚出现，低点后反弹到收盘的幅度反而更小。",
        "- 但低点后成交量相对低点前有明显改善，说明“低位换手/放量修复”可能比“价格已经强反弹”更有统计价值。",
        "- 第3步应优先研究“日线超跌背景 + 当日弱势低位承接/放量”的规则，而不是直接延续之前的强 V 反转规则。",
        "",
        "## 正样本买入日",
        "",
        markdown_table(events[["entry_date", "entry_price", "exit_date", "exit_price", "return_pct", "mae_pct"]]),
        "",
        "## 日线前置背景差异最大的特征",
        "",
        markdown_table(daily_pre[show_cols].head(12)),
        "",
        "## 买入日当天日线形态差异最大的特征",
        "",
        markdown_table(daily_day[show_cols].head(12)),
        "",
        "## 买入日盘中特征差异最大的特征",
        "",
        markdown_table(intraday[show_cols].head(18)),
        "",
        "## 初步结论",
        "",
        "- 先看 `std_diff` 和 `single_feature_auc`，不要只看 p 值；当前样本太少，p 值容易不稳定。",
        "- 第3步只能使用本报告中同时具备业务含义和统计区分度的少数特征。",
        "- 下一步应把这些候选特征整理成少量可解释规则，再回测验证，而不是继续盲目扩因子。",
    ]
    Path("报告/研究结论/当前主线/510300优质区间买入日信号统计分析.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    events, event_dates = load_event_dates()
    daily = add_daily_features(load_daily_frame())
    daily = daily[(daily["trade_date"] >= START_DATE) & (daily["trade_date"] <= END_DATE)].copy()
    daily["target"] = daily["trade_date"].isin(event_dates).astype(int)

    intraday_dates = daily["trade_date"].tolist()
    intraday = build_intraday_feature_frame(intraday_dates)
    dataset = daily.merge(intraday, on="trade_date", how="left")

    intraday_cols = [
        col
        for col in dataset.columns
        if (col.startswith("m1_") or col.startswith("m5_"))
        and not col.endswith("_enough_bars")
        and not col.endswith("_bar_count")
    ]

    daily_pre_compare = compare_feature_group(dataset, DAILY_PRE_FEATURES, "daily_pre")
    daily_day_compare = compare_feature_group(dataset, DAILY_DAY_FEATURES, "daily_day")
    intraday_compare = compare_feature_group(dataset, intraday_cols, "intraday")

    summary = {
        "stock": STOCK,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "positive_count": int(dataset["target"].sum()),
        "control_count": int((dataset["target"] == 0).sum()),
        "daily_pre_feature_count": int(len(DAILY_PRE_FEATURES)),
        "daily_day_feature_count": int(len(DAILY_DAY_FEATURES)),
        "intraday_feature_count": int(len(intraday_cols)),
    }

    dataset.to_csv(OUTPUT_DIR / "entry_signal_dataset.csv", index=False, encoding="utf-8-sig")
    daily_pre_compare.to_csv(OUTPUT_DIR / "daily_pre_feature_compare.csv", index=False, encoding="utf-8-sig")
    daily_day_compare.to_csv(OUTPUT_DIR / "daily_day_feature_compare.csv", index=False, encoding="utf-8-sig")
    intraday_compare.to_csv(OUTPUT_DIR / "intraday_feature_compare.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    daily_pre_compare.to_csv(
        "报告/研究结论/数据摘要/510300优质区间买入日前置日线特征.csv",
        index=False,
        encoding="utf-8-sig",
    )
    daily_day_compare.to_csv(
        "报告/研究结论/数据摘要/510300优质区间买入日当天日线特征.csv",
        index=False,
        encoding="utf-8-sig",
    )
    intraday_compare.to_csv(
        "报告/研究结论/数据摘要/510300优质区间买入日盘中特征.csv",
        index=False,
        encoding="utf-8-sig",
    )
    build_report(events, summary, daily_pre_compare, daily_day_compare, intraday_compare)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("daily pre")
    print(daily_pre_compare.head(8).to_string(index=False))
    print("daily day")
    print(daily_day_compare.head(8).to_string(index=False))
    print("intraday")
    print(intraday_compare.head(12).to_string(index=False))


if __name__ == "__main__":
    main()
