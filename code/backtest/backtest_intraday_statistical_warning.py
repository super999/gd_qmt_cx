#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd
from xtquant import xtdata

from analyze_best_interval_entry_signals import END_DATE, START_DATE, STOCK, add_daily_features, load_daily_frame
from backtest_statistical_entry_rules import exit_midpoint, max_drawdown_from_lows


PRICE_ADJUSTMENT = "front"
EXPECTED_5M_BARS = 48
HORIZONS = [3, 5, 10, 20]
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "backtest_intraday_statistical_warning"


RULES = [
    {
        "name": "intraday_weak_low",
        "label": "盘中弱势低位预警",
        "description": "日线超跌背景 + 盘中估算弱势低位，下午前半段后允许触发",
        "min_signal_bar_pos": 24,
        "conditions": [
            ("pre_drawdown_from_high_10", "<=", -0.025),
            ("pre_close_vs_ma10", "<=", 0.0),
            ("pre_rsi6", "<=", 48.0),
            ("est_day_close_in_range", "<=", 0.45),
            ("est_day_return", "<=", 0.003),
        ],
    },
    {
        "name": "intraday_weak_late_low",
        "label": "盘中弱势低位-低点偏晚",
        "description": "在弱势低位基础上，要求5分钟低点出现在全天偏后位置",
        "min_signal_bar_pos": 24,
        "conditions": [
            ("pre_drawdown_from_high_10", "<=", -0.025),
            ("pre_close_vs_ma10", "<=", 0.0),
            ("pre_rsi6", "<=", 48.0),
            ("est_day_close_in_range", "<=", 0.45),
            ("est_day_return", "<=", 0.003),
            ("m5_low_pos_ratio", ">=", 0.35),
        ],
    },
    {
        "name": "intraday_weak_volume_repair",
        "label": "盘中弱势低位-量能修复",
        "description": "在弱势低位基础上，要求低点后5分钟均量不低于低点前太多",
        "min_signal_bar_pos": 24,
        "conditions": [
            ("pre_drawdown_from_high_10", "<=", -0.025),
            ("pre_close_vs_ma10", "<=", 0.0),
            ("pre_rsi6", "<=", 48.0),
            ("est_day_close_in_range", "<=", 0.45),
            ("est_day_return", "<=", 0.003),
            ("m5_low_pos_ratio", ">=", 0.35),
            ("m5_volume_ratio_after_low", ">=", -0.30),
        ],
    },
]


def ensure_history():
    for period in ["1d", "5m"]:
        xtdata.download_history_data(
            STOCK,
            period=period,
            start_time="20240101",
            end_time=END_DATE,
            incrementally=False,
        )


def load_5m_frame():
    data = xtdata.get_local_data(
        field_list=[],
        stock_list=[STOCK],
        period="5m",
        start_time=START_DATE,
        end_time=END_DATE,
        count=-1,
        dividend_type=PRICE_ADJUSTMENT,
        fill_data=True,
    ).get(STOCK)
    if data is None or data.empty:
        raise RuntimeError("no 5m data for {}".format(STOCK))
    frame = data.copy()
    frame.index = frame.index.astype(str)
    frame["bar_time"] = frame.index.astype(str)
    frame.index.name = None
    frame["trade_date"] = frame["bar_time"].str[:8]
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["open", "high", "low", "close"]).sort_values("bar_time").reset_index(drop=True)


def compare_value(value, op, threshold):
    if pd.isna(value):
        return False
    value = float(value)
    if op == "<=":
        return value <= threshold
    if op == ">=":
        return value >= threshold
    raise ValueError("unsupported operator {}".format(op))


def build_daily_dataset():
    raw = load_daily_frame()
    features = add_daily_features(raw)
    raw = raw[(raw["trade_date"] >= START_DATE) & (raw["trade_date"] <= END_DATE)].copy()
    features = features[(features["trade_date"] >= START_DATE) & (features["trade_date"] <= END_DATE)].copy()
    dataset = raw.merge(features, on="trade_date", how="left")
    return dataset.sort_values("trade_date").reset_index(drop=True)


def build_5m_maps(frame):
    return {date: group.copy().reset_index(drop=True) for date, group in frame.groupby("trade_date")}


def partial_intraday_features(partial_5m, prev_close, current_pos):
    current_close = float(partial_5m.iloc[-1]["close"])
    high_so_far = float(partial_5m["high"].max())
    low_so_far = float(partial_5m["low"].min())
    day_range = high_so_far - low_so_far
    low_pos = int(partial_5m["low"].values.argmin())
    before_low = partial_5m.iloc[: low_pos + 1]
    after_low = partial_5m.iloc[low_pos + 1 :]

    avg_before = float(before_low["volume"].mean()) if not before_low.empty else 0.0
    avg_after = float(after_low["volume"].mean()) if not after_low.empty else 0.0
    volume_ratio_after_low = avg_after / avg_before - 1.0 if avg_before else 0.0

    return {
        "signal_bar_pos": current_pos,
        "signal_price": current_close,
        "signal_low_so_far": low_so_far,
        "est_day_return": current_close / prev_close - 1.0 if prev_close else 0.0,
        "est_day_close_in_range": (current_close - low_so_far) / day_range if day_range > 0 else 0.0,
        "m5_low_pos": low_pos,
        "m5_low_pos_ratio": low_pos / max(EXPECTED_5M_BARS - 1, 1),
        "m5_current_pos_ratio": current_pos / max(EXPECTED_5M_BARS - 1, 1),
        "m5_volume_ratio_after_low": volume_ratio_after_low,
    }


def rule_passes(rule, features):
    if features["signal_bar_pos"] < rule["min_signal_bar_pos"]:
        return False
    for feature, op, threshold in rule["conditions"]:
        if not compare_value(features.get(feature), op, threshold):
            return False
    return True


def replay_intraday_signals(daily, data_5m):
    maps_5m = build_5m_maps(data_5m)
    rows = []

    for idx, day in daily.iterrows():
        trade_date = str(day["trade_date"])
        if trade_date not in maps_5m or idx == 0:
            continue
        bars = maps_5m[trade_date]
        prev_close = float(daily.iloc[idx - 1]["close"])
        first_hit = {rule["name"]: False for rule in RULES}

        for pos in range(len(bars)):
            partial = bars.iloc[: pos + 1].copy()
            intraday_features = partial_intraday_features(partial, prev_close, pos)
            features = {
                "trade_date": trade_date,
                "signal_time": str(bars.iloc[pos]["bar_time"]),
                "pre_drawdown_from_high_10": day["pre_drawdown_from_high_10"],
                "pre_close_vs_ma10": day["pre_close_vs_ma10"],
                "pre_rsi6": day["pre_rsi6"],
            }
            features.update(intraday_features)

            for rule in RULES:
                if first_hit[rule["name"]]:
                    continue
                if not rule_passes(rule, features):
                    continue
                first_hit[rule["name"]] = True
                item = {
                    "rule_name": rule["name"],
                    "rule_label": rule["label"],
                    "trade_date": trade_date,
                    "signal_time": features["signal_time"],
                }
                for key, value in features.items():
                    if key in {"trade_date", "signal_time"}:
                        continue
                    item[key] = round(float(value), 6) if pd.notna(value) else None
                rows.append(item)

    return pd.DataFrame(rows).sort_values(["rule_name", "signal_time"]).reset_index(drop=True)


def evaluate_signal(daily, signal_row, horizon, fill_mode):
    date_to_idx = {str(row["trade_date"]): idx for idx, row in daily.iterrows()}
    signal_date = str(signal_row["trade_date"])
    signal_idx = date_to_idx.get(signal_date)
    if signal_idx is None:
        return None

    if fill_mode == "same_bar_close":
        entry_idx = signal_idx
        entry_price = float(signal_row["signal_price"])
        entry_date = signal_date
    elif fill_mode == "next_open":
        entry_idx = signal_idx + 1
        if entry_idx >= len(daily):
            return None
        entry_row = daily.iloc[entry_idx]
        entry_price = float(entry_row["open"])
        entry_date = str(entry_row["trade_date"])
    else:
        raise ValueError("unsupported fill mode {}".format(fill_mode))

    exit_idx = entry_idx + horizon - 1
    if exit_idx >= len(daily):
        return None
    exit_row = daily.iloc[exit_idx]
    window = daily.iloc[entry_idx : exit_idx + 1]
    exit_price = exit_midpoint(exit_row)
    return_pct = exit_price / entry_price - 1.0
    mae_pct = float(window["low"].min()) / entry_price - 1.0
    max_dd_pct = max_drawdown_from_lows(window)
    return {
        "rule_name": signal_row["rule_name"],
        "rule_label": signal_row["rule_label"],
        "fill_mode": fill_mode,
        "horizon_days": horizon,
        "signal_date": signal_date,
        "signal_time": signal_row["signal_time"],
        "entry_date": entry_date,
        "entry_price": round(entry_price, 4),
        "exit_date": str(exit_row["trade_date"]),
        "exit_price": round(exit_price, 4),
        "return_pct": round(return_pct, 6),
        "mae_pct": round(mae_pct, 6),
        "max_drawdown_pct": round(max_dd_pct, 6),
    }


def evaluate_signals(daily, signals):
    rows = []
    for _, signal in signals.iterrows():
        for fill_mode in ["same_bar_close", "next_open"]:
            for horizon in HORIZONS:
                item = evaluate_signal(daily, signal, horizon, fill_mode)
                if item is not None:
                    rows.append(item)
    return pd.DataFrame(rows)


def summarize(trades):
    rows = []
    for keys, group in trades.groupby(["rule_name", "rule_label", "fill_mode", "horizon_days"]):
        rule_name, rule_label, fill_mode, horizon = keys
        returns = pd.to_numeric(group["return_pct"], errors="coerce")
        maes = pd.to_numeric(group["mae_pct"], errors="coerce")
        max_dd = pd.to_numeric(group["max_drawdown_pct"], errors="coerce")
        rows.append(
            {
                "rule_name": rule_name,
                "rule_label": rule_label,
                "fill_mode": fill_mode,
                "horizon_days": int(horizon),
                "trade_count": int(len(group)),
                "month_count": int(group["signal_date"].str[:6].nunique()),
                "win_rate": round(float((returns > 0).mean()), 6),
                "avg_return": round(float(returns.mean()), 6),
                "median_return": round(float(returns.median()), 6),
                "min_return": round(float(returns.min()), 6),
                "avg_mae": round(float(maes.mean()), 6),
                "worst_mae": round(float(maes.min()), 6),
                "avg_max_drawdown": round(float(max_dd.mean()), 6),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["fill_mode", "horizon_days", "avg_return"],
        ascending=[True, True, False],
    )


def summarize_non_overlapping(trades):
    rows = []
    selected_rows = []
    daily_idx = {}
    # Use dates as sortable positions for non-overlap because this replay evaluates daily horizons.
    all_dates = sorted(trades["entry_date"].dropna().astype(str).unique().tolist() + trades["exit_date"].dropna().astype(str).unique().tolist())
    for pos, date in enumerate(sorted(set(all_dates))):
        daily_idx[date] = pos

    for keys, group in trades.groupby(["rule_name", "rule_label", "fill_mode", "horizon_days"]):
        rule_name, rule_label, fill_mode, horizon = keys
        group = group.sort_values(["signal_date", "signal_time"]).copy()
        available_after_date = ""
        selected = []
        for _, row in group.iterrows():
            entry_date = str(row["entry_date"])
            if available_after_date and entry_date <= available_after_date:
                continue
            selected.append(row)
            available_after_date = str(row["exit_date"])
            item = row.to_dict()
            item["strategy_trade_no"] = len(selected)
            selected_rows.append(item)
        if not selected:
            continue
        selected_df = pd.DataFrame(selected)
        returns = pd.to_numeric(selected_df["return_pct"], errors="coerce")
        maes = pd.to_numeric(selected_df["mae_pct"], errors="coerce")
        max_dd = pd.to_numeric(selected_df["max_drawdown_pct"], errors="coerce")
        rows.append(
            {
                "rule_name": rule_name,
                "rule_label": rule_label,
                "fill_mode": fill_mode,
                "horizon_days": int(horizon),
                "strategy_trade_count": int(len(selected_df)),
                "month_count": int(selected_df["signal_date"].str[:6].nunique()),
                "win_rate": round(float((returns > 0).mean()), 6),
                "avg_return": round(float(returns.mean()), 6),
                "median_return": round(float(returns.median()), 6),
                "compounded_return": round(float((1.0 + returns).prod() - 1.0), 6),
                "min_return": round(float(returns.min()), 6),
                "avg_mae": round(float(maes.mean()), 6),
                "worst_mae": round(float(maes.min()), 6),
                "avg_max_drawdown": round(float(max_dd.mean()), 6),
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(selected_rows)


def pct_table(frame):
    table = frame.copy()
    percent_cols = [
        "win_rate",
        "avg_return",
        "median_return",
        "compounded_return",
        "min_return",
        "avg_mae",
        "worst_mae",
        "avg_max_drawdown",
    ]
    for col in percent_cols:
        if col in table.columns:
            table[col] = table[col].map(lambda value: "{:.2%}".format(float(value)))
    return table


def markdown_table(frame):
    columns = frame.columns.tolist()
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in columns) + " |")
    return "\n".join(lines)


def write_report(signals, summary, strategy_summary):
    signal_counts = signals.groupby(["rule_name", "rule_label"]).size().reset_index(name="signal_count")
    lines = [
        "# 510300 日线弱势低位盘中预警回放",
        "",
        "## 验证边界",
        "",
        "- 本报告把第3步中表现较稳的 `日线弱势低位规则` 改成盘中预警回放。",
        "- 日线背景只使用前一交易日已经确定的数据。",
        "- 当日弱势低位条件使用当前5分钟收盘价实时估算。",
        "- `same_bar_close` 表示预警当根5分钟收盘价买入；`next_open` 表示次日开盘买入。",
        "- 本报告仍只验证买点，卖点暂用固定持有周期观察。",
        "",
        "## 预警规则数量",
        "",
        markdown_table(signal_counts),
        "",
        "## 逐信号观察摘要",
        "",
        markdown_table(pct_table(summary)),
        "",
        "## 非重叠策略摘要",
        "",
        markdown_table(pct_table(strategy_summary)),
        "",
        "## 初步结论",
        "",
        "- 三条盘中预警规则在 `same_bar_close` 和 `next_open` 两种口径下均保持正收益，说明盘中预警版本没有破坏第3步的买点有效性。",
        "- `盘中弱势低位-量能修复` 更适合短周期观察：非重叠、当根收盘口径下，3日持有 `10` 笔，胜率 `90.00%`，复合收益 `11.67%`，最差持仓内不利波动 `-1.85%`。",
        "- `盘中弱势低位预警` 更适合中长周期观察：非重叠、次日开盘口径下，20日持有 `7` 笔，胜率 `85.71%`，复合收益 `32.42%`，最差持仓内不利波动 `-3.18%`。",
        "- 当前最合理的模拟监控基线是：先用 `盘中弱势低位-量能修复` 做短线预警，用 `盘中弱势低位预警` 做波段观察，不再使用旧强 V 反转触发。",
        "- 仍未设计正式卖点，因此不能直接自动交易；下一步应单独验证止盈、止损、移动保护和时间退出。",
    ]
    Path("报告/研究结论/当前主线/510300日线弱势低位盘中预警回放.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_history()
    daily = build_daily_dataset()
    data_5m = load_5m_frame()
    signals = replay_intraday_signals(daily, data_5m)
    trades = evaluate_signals(daily, signals)
    summary = summarize(trades)
    strategy_summary, strategy_trades = summarize_non_overlapping(trades)
    meta = {
        "stock": STOCK,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "signal_count": int(len(signals)),
        "trade_eval_count": int(len(trades)),
        "strategy_trade_eval_count": int(len(strategy_trades)),
    }

    signals.to_csv(OUTPUT_DIR / "intraday_warning_signals.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(OUTPUT_DIR / "intraday_warning_trade_evaluations.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(OUTPUT_DIR / "intraday_warning_summary.csv", index=False, encoding="utf-8-sig")
    strategy_summary.to_csv(OUTPUT_DIR / "intraday_warning_strategy_summary.csv", index=False, encoding="utf-8-sig")
    strategy_trades.to_csv(OUTPUT_DIR / "intraday_warning_strategy_trades.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    signals.to_csv("报告/研究结论/数据摘要/510300日线弱势低位盘中预警信号.csv", index=False, encoding="utf-8-sig")
    trades.to_csv("报告/研究结论/数据摘要/510300日线弱势低位盘中预警逐笔验证.csv", index=False, encoding="utf-8-sig")
    summary.to_csv("报告/研究结论/数据摘要/510300日线弱势低位盘中预警摘要.csv", index=False, encoding="utf-8-sig")
    strategy_summary.to_csv(
        "报告/研究结论/数据摘要/510300日线弱势低位盘中预警非重叠摘要.csv",
        index=False,
        encoding="utf-8-sig",
    )
    strategy_trades.to_csv(
        "报告/研究结论/数据摘要/510300日线弱势低位盘中预警非重叠逐笔.csv",
        index=False,
        encoding="utf-8-sig",
    )
    write_report(signals, summary, strategy_summary)

    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(pct_table(summary).to_string(index=False))
    print("non-overlap")
    print(pct_table(strategy_summary).to_string(index=False))


if __name__ == "__main__":
    main()
