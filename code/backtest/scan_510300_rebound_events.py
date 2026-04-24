#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd

import minimal_stock_backtest as base


STOCK = "510300.SH"
START_DATE = "20240101"
END_DATE = "20260424"
LOCAL_LOW_LOOKBACK = 5
LOW_TOLERANCE = 0.001
LABEL_CONFIGS = [
    {"window_days": 5, "target_return": 0.03},
    {"window_days": 5, "target_return": 0.05},
    {"window_days": 10, "target_return": 0.03},
    {"window_days": 10, "target_return": 0.05},
]
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "scan_510300_rebound_events"


def make_label_name(window_days, target_return):
    return "n{}_r{}".format(window_days, int(target_return * 100))


def prepare_daily_frame():
    base.ensure_history_download([STOCK], base.DAILY_PERIOD, START_DATE, END_DATE)
    daily_frame = base.load_price_frame(STOCK, base.DAILY_PERIOD, START_DATE, END_DATE)
    frame = daily_frame.copy()
    frame["trade_date"] = frame.index.astype(str).str[:8]
    frame["rolling_low_lookback"] = (
        frame["low"].rolling(LOCAL_LOW_LOOKBACK, min_periods=LOCAL_LOW_LOOKBACK).min()
    )
    frame["is_local_low_candidate"] = frame["low"] <= frame["rolling_low_lookback"]
    return frame


def build_candidate_table(frame):
    rows = []
    trade_dates = list(frame["trade_date"])

    for idx, (_, row) in enumerate(frame.iterrows()):
        candidate = {
            "trade_date": row["trade_date"],
            "open": round(float(row["open"]), 4),
            "high": round(float(row["high"]), 4),
            "low": round(float(row["low"]), 4),
            "close": round(float(row["close"]), 4),
            "is_local_low_candidate": bool(row["is_local_low_candidate"])
            if pd.notna(row["is_local_low_candidate"])
            else False,
        }

        if not candidate["is_local_low_candidate"]:
            rows.append(candidate)
            continue

        event_low = float(row["low"])
        for cfg in LABEL_CONFIGS:
            window_days = cfg["window_days"]
            target_return = cfg["target_return"]
            label_name = make_label_name(window_days, target_return)

            future_slice = frame.iloc[idx : idx + window_days].copy()
            if len(future_slice) < window_days:
                candidate[label_name + "_enough_window"] = False
                candidate[label_name + "_valid_event"] = False
                continue

            future_low = float(future_slice["low"].min())
            future_high = float(future_slice["high"].max())
            future_end_close = float(future_slice.iloc[-1]["close"])
            hold_low = future_low >= event_low * (1 - LOW_TOLERANCE)
            reaches_target = future_high >= event_low * (1 + target_return)
            valid_event = hold_low and reaches_target

            first_hit_row = future_slice[future_slice["high"] >= event_low * (1 + target_return)]
            first_hit_date = ""
            if not first_hit_row.empty:
                first_hit_date = str(first_hit_row.iloc[0]["trade_date"])

            candidate[label_name + "_enough_window"] = True
            candidate[label_name + "_window_days"] = window_days
            candidate[label_name + "_target_return"] = target_return
            candidate[label_name + "_future_low"] = round(future_low, 4)
            candidate[label_name + "_future_high"] = round(future_high, 4)
            candidate[label_name + "_future_end_close"] = round(future_end_close, 4)
            candidate[label_name + "_future_low_return"] = round(future_low / event_low - 1.0, 6)
            candidate[label_name + "_future_high_return"] = round(future_high / event_low - 1.0, 6)
            candidate[label_name + "_hold_low"] = hold_low
            candidate[label_name + "_reaches_target"] = reaches_target
            candidate[label_name + "_valid_event"] = valid_event
            candidate[label_name + "_first_hit_date"] = first_hit_date
            candidate[label_name + "_window_end_date"] = str(future_slice.iloc[-1]["trade_date"])

        rows.append(candidate)

    return pd.DataFrame(rows)


def build_positive_events(candidate_df):
    rows = []
    base_cols = ["trade_date", "open", "high", "low", "close"]

    for cfg in LABEL_CONFIGS:
        label_name = make_label_name(cfg["window_days"], cfg["target_return"])
        valid_col = label_name + "_valid_event"
        if valid_col not in candidate_df.columns:
            continue

        subset = candidate_df[
            candidate_df["is_local_low_candidate"].fillna(False) & candidate_df[valid_col].fillna(False)
        ].copy()
        if subset.empty:
            continue

        keep_cols = base_cols + [
            label_name + "_window_days",
            label_name + "_target_return",
            label_name + "_future_low",
            label_name + "_future_high",
            label_name + "_future_end_close",
            label_name + "_future_low_return",
            label_name + "_future_high_return",
            label_name + "_hold_low",
            label_name + "_reaches_target",
            label_name + "_valid_event",
            label_name + "_first_hit_date",
            label_name + "_window_end_date",
        ]
        subset = subset[keep_cols].copy()
        subset.columns = [
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "window_days",
            "target_return",
            "future_low",
            "future_high",
            "future_end_close",
            "future_low_return",
            "future_high_return",
            "hold_low",
            "reaches_target",
            "valid_event",
            "first_hit_date",
            "window_end_date",
        ]
        subset["label_name"] = label_name
        rows.append(subset)

    if not rows:
        return pd.DataFrame()
    result = pd.concat(rows, ignore_index=True)
    return result.sort_values(["label_name", "trade_date"]).reset_index(drop=True)


def dedupe_positive_events(positive_df):
    if positive_df.empty:
        return positive_df.copy()

    deduped_rows = []
    for label_name, label_df in positive_df.groupby("label_name"):
        label_df = label_df.sort_values("trade_date").copy()
        active_until = ""
        for _, row in label_df.iterrows():
            trade_date = str(row["trade_date"])
            if active_until and trade_date <= active_until:
                continue
            deduped_rows.append(row.to_dict())
            active_until = str(row["window_end_date"])

    if not deduped_rows:
        return pd.DataFrame(columns=positive_df.columns)
    return pd.DataFrame(deduped_rows).sort_values(["label_name", "trade_date"]).reset_index(drop=True)


def build_summary(candidate_df, positive_df, deduped_df):
    summary_rows = []
    candidate_count = int(candidate_df["is_local_low_candidate"].fillna(False).sum())

    for cfg in LABEL_CONFIGS:
        label_name = make_label_name(cfg["window_days"], cfg["target_return"])
        subset_raw = positive_df[positive_df["label_name"] == label_name].copy()
        subset_deduped = deduped_df[deduped_df["label_name"] == label_name].copy()
        summary_rows.append(
            {
                "label_name": label_name,
                "window_days": cfg["window_days"],
                "target_return": cfg["target_return"],
                "candidate_count": candidate_count,
                "positive_event_count_raw": int(len(subset_raw)),
                "positive_event_count_deduped": int(len(subset_deduped)),
                "first_event_date": ""
                if subset_deduped.empty
                else str(subset_deduped.iloc[0]["trade_date"]),
                "last_event_date": ""
                if subset_deduped.empty
                else str(subset_deduped.iloc[-1]["trade_date"]),
                "avg_future_high_return_raw": 0.0
                if subset_raw.empty
                else round(float(subset_raw["future_high_return"].mean()), 6),
            }
        )

    return pd.DataFrame(summary_rows)


def build_report(summary_df):
    lines = [
        "# 510300 低吸反弹事件扫描结果",
        "",
        "## 说明",
        "",
        "- 本结果基于 `报告/510300低吸反弹事件定义与研究计划.md` 的首版标签定义。",
        "- 当前不是策略回测结果，而是事件样本提取结果。",
        "- 目的不是直接生成交易规则，而是先找出历史上可能成立的低吸反弹窗口。",
        "",
        "## 标签摘要",
        "",
    ]

    header = "| " + " | ".join(summary_df.columns.astype(str)) + " |"
    separator = "| " + " | ".join(["---"] * len(summary_df.columns)) + " |"
    lines.append(header)
    lines.append(separator)
    for _, row in summary_df.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in summary_df.columns) + " |")

    lines.extend(
        [
            "",
            "## 下一步用途",
            "",
            "1. 以这些正样本事件作为研究窗口。",
            "2. 再回看事件发生前的价格、均线、波动率、成交量等特征。",
            "3. 再做正负样本对比和简单建模。",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Scanning 510300 rebound events")
    print(
        "stock={}, start={}, end={}, lookback={}, tolerance={}".format(
            STOCK, START_DATE, END_DATE, LOCAL_LOW_LOOKBACK, LOW_TOLERANCE
        )
    )

    daily_frame = prepare_daily_frame()
    candidate_df = build_candidate_table(daily_frame)
    positive_df = build_positive_events(candidate_df)
    deduped_df = dedupe_positive_events(positive_df)
    summary_df = build_summary(candidate_df, positive_df, deduped_df)

    candidate_path = OUTPUT_DIR / "candidate_days.csv"
    positive_path = OUTPUT_DIR / "positive_events_raw.csv"
    deduped_path = OUTPUT_DIR / "positive_events_deduped.csv"
    summary_csv_path = OUTPUT_DIR / "event_summary.csv"
    summary_json_path = OUTPUT_DIR / "event_summary.json"
    report_path = OUTPUT_DIR / "510300低吸反弹事件扫描结果.md"

    candidate_df.to_csv(candidate_path, index=False, encoding="utf-8-sig")
    positive_df.to_csv(positive_path, index=False, encoding="utf-8-sig")
    deduped_df.to_csv(deduped_path, index=False, encoding="utf-8-sig")
    summary_df.to_csv(summary_csv_path, index=False, encoding="utf-8-sig")
    summary_json_path.write_text(
        json.dumps(
            {
                "stock": STOCK,
                "lookback": LOCAL_LOW_LOOKBACK,
                "tolerance": LOW_TOLERANCE,
                "label_configs": LABEL_CONFIGS,
                "summary": summary_df.to_dict(orient="records"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report_path.write_text(build_report(summary_df), encoding="utf-8")

    print("candidate_local_lows:", int(candidate_df["is_local_low_candidate"].fillna(False).sum()))
    print("outputs:")
    print(" -", candidate_path)
    print(" -", positive_path)
    print(" -", deduped_path)
    print(" -", summary_csv_path)
    print(" -", report_path)


if __name__ == "__main__":
    main()
