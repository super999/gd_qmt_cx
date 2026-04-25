#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd

import analyze_510300_v_reversal_multiframe as mf
import minimal_stock_backtest as base
from feature_labels import feature_label
from xtquant import xtdata


STOCK = "510300.SH"
START_DATE = "20240101"
END_DATE = "20260424"
PRICE_ADJUSTMENT = "front"

INITIAL_CASH = 100000.0
LOT_SIZE = 100
MAX_HOLD_DAYS = 5
STOP_LOSS_PCT = 0.03
TAKE_PROFIT_PCT = 0.03
LOW_BREAK_TOLERANCE = 0.001

CANDIDATE_A = {
    "name": "candidate_a_strict",
    "label": "候选A-严格",
    "background_min": 3,
    "trigger_min": 2,
}
CANDIDATE_B = {
    "name": "candidate_b_balanced",
    "label": "候选B-平衡",
    "background_min": 3,
    "trigger_min": 1,
}

BACKGROUND_RULES = [
    ("drawdown_from_high_10", "lower", -0.044051),
    ("drawdown_from_high_20", "lower", -0.050221),
    ("low_vs_ma20", "lower", -0.024041),
    ("volatility_5", "higher", 0.010574),
    ("ret_5d", "lower", -0.014103),
]

TRIGGER_RULES = [
    ("m5_low_before_last_quarter", "higher", 1),
    ("m5_low_pos_ratio", "lower", 0.319149),
    ("m5_rebound_to_close", "higher", 0.009317),
    ("m5_up_close_streak_after_low", "higher", 4),
    ("m5_up_bar_ratio_after_low", "higher", 0.5),
    ("m5_close_in_range", "higher", 0.722222),
    ("m1_up_close_streak_after_low", "higher", 5),
]

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "backtest_n5_r3_candidate_rules"
CACHED_DAILY_PATH = (
    Path(__file__).resolve().parent / "outputs" / "scan_510300_rebound_events" / "candidate_days.csv"
)
CACHED_SCORE_PATH = (
    Path(__file__).resolve().parent
    / "outputs"
    / "build_n5_r3_bg_trigger_scores"
    / "bg_trigger_scored_dataset.csv"
)


def load_daily_frame():
    base.ensure_history_download([STOCK], base.DAILY_PERIOD, START_DATE, END_DATE)
    frame = base.load_price_frame(STOCK, base.DAILY_PERIOD, START_DATE, END_DATE).copy()
    frame["trade_date"] = frame.index.astype(str).str[:8]

    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    volume = frame["volume"].astype(float)

    frame["ma20"] = close.rolling(20, min_periods=20).mean()
    frame["ret_5d"] = close.pct_change(5)
    frame["drawdown_from_high_10"] = low / high.rolling(10, min_periods=10).max() - 1.0
    frame["drawdown_from_high_20"] = low / high.rolling(20, min_periods=20).max() - 1.0
    frame["low_vs_ma20"] = low / frame["ma20"] - 1.0
    frame["daily_return"] = close.pct_change()
    frame["volatility_5"] = frame["daily_return"].rolling(5, min_periods=5).std()
    frame["volume_ma5"] = volume.rolling(5, min_periods=5).mean()
    return frame


def load_intraday_features():
    data_1m = mf.split_by_trade_date(mf.load_intraday("1m"))
    data_5m = mf.split_by_trade_date(mf.load_intraday("5m"))

    rows = []
    shared_dates = sorted(set(data_1m) & set(data_5m))
    for trade_date in shared_dates:
        item = {"trade_date": trade_date}
        item.update(mf.extract_v_features(data_1m[trade_date], "m1"))
        item.update(mf.extract_v_features(data_5m[trade_date], "m5"))
        rows.append(item)

    if not rows:
        raise RuntimeError("no shared 1m/5m intraday data for {}".format(STOCK))
    return pd.DataFrame(rows)


def rule_pass(value, direction, threshold):
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        numeric_value = int(value)
    else:
        numeric_value = float(value)
    if direction == "higher":
        return numeric_value >= threshold
    return numeric_value <= threshold


def apply_rule_score(df, rules, prefix):
    working = df.copy()
    point_cols = []
    label_cols = []
    for feature, direction, threshold in rules:
        point_col = prefix + "_" + feature + "_point"
        label_col = prefix + "_" + feature + "_label"
        working[point_col] = working[feature].apply(lambda value: int(rule_pass(value, direction, threshold)))
        working[label_col] = working[point_col].apply(
            lambda point: "{}{}".format(feature_label(feature), "命中" if point else "未命中")
        )
        point_cols.append(point_col)
        label_cols.append(label_col)

    working[prefix + "_score"] = working[point_cols].sum(axis=1)
    working[prefix + "_hit_labels"] = working[label_cols].agg(" | ".join, axis=1)
    return working, point_cols


def build_signal_frame():
    try:
        daily = load_daily_frame()
        intraday = load_intraday_features()
        signal = daily.merge(intraday, on="trade_date", how="left")
        signal, bg_point_cols = apply_rule_score(signal, BACKGROUND_RULES, "background")
        signal, tg_point_cols = apply_rule_score(signal, TRIGGER_RULES, "trigger")
        signal["data_mode"] = "live_xtdata"
    except Exception as exc:
        print("live xtdata load failed, falling back to cached outputs:", exc)
        signal, bg_point_cols, tg_point_cols = build_cached_signal_frame()

    signal["is_candidate_a"] = (
        (signal["background_score"] >= CANDIDATE_A["background_min"])
        & (signal["trigger_score"] >= CANDIDATE_A["trigger_min"])
    )
    signal["is_candidate_b"] = (
        (signal["background_score"] >= CANDIDATE_B["background_min"])
        & (signal["trigger_score"] >= CANDIDATE_B["trigger_min"])
    )
    return signal.sort_values("trade_date").reset_index(drop=True), bg_point_cols, tg_point_cols


def build_cached_signal_frame():
    if not CACHED_DAILY_PATH.exists() or not CACHED_SCORE_PATH.exists():
        raise RuntimeError("cached daily/score outputs are missing")

    daily = pd.read_csv(CACHED_DAILY_PATH, dtype={"trade_date": str})
    score = pd.read_csv(CACHED_SCORE_PATH, dtype={"trade_date": str})
    daily = daily[["trade_date", "open", "high", "low", "close"]].copy()

    point_cols = [col for col in score.columns if col.endswith("_point")]
    keep_cols = (
        ["trade_date", "background_score", "trigger_score"]
        + [feature for feature, _, _ in BACKGROUND_RULES]
        + [feature for feature, _, _ in TRIGGER_RULES]
        + point_cols
    )
    signal = daily.merge(score[keep_cols], on="trade_date", how="left")
    signal["background_score"] = signal["background_score"].fillna(0).astype(int)
    signal["trigger_score"] = signal["trigger_score"].fillna(0).astype(int)

    for col in point_cols:
        signal[col] = signal[col].fillna(0).astype(int)

    bg_point_cols = ["background_" + feature + "_point" for feature, _, _ in BACKGROUND_RULES]
    tg_point_cols = ["trigger_" + feature + "_point" for feature, _, _ in TRIGGER_RULES]
    signal["background_hit_labels"] = signal.apply(
        lambda row: build_cached_hit_labels(row, BACKGROUND_RULES, bg_point_cols), axis=1
    )
    signal["trigger_hit_labels"] = signal.apply(
        lambda row: build_cached_hit_labels(row, TRIGGER_RULES, tg_point_cols), axis=1
    )
    signal["data_mode"] = "cached_outputs"
    return signal, bg_point_cols, tg_point_cols


def build_cached_hit_labels(row, rules, point_cols):
    labels = []
    for (feature, _, _), point_col in zip(rules, point_cols):
        point = int(row.get(point_col, 0))
        labels.append("{}{}".format(feature_label(feature), "命中" if point else "未命中"))
    return " | ".join(labels)


def evaluate_exit(row, entry_price, entry_signal_low, holding_days):
    reasons = []
    if float(row["close"]) <= entry_price * (1 - STOP_LOSS_PCT):
        reasons.append("收盘触发固定止损")
    if float(row["low"]) <= entry_signal_low * (1 - LOW_BREAK_TOLERANCE):
        reasons.append("跌破信号日低点容忍线")
    if float(row["close"]) >= entry_price * (1 + TAKE_PROFIT_PCT):
        reasons.append("收盘触发目标收益")
    if holding_days >= MAX_HOLD_DAYS:
        reasons.append("持有天数达到n5窗口上限")
    return bool(reasons), reasons


def run_single_band(signal_df, band_config):
    cash = INITIAL_CASH
    shares = 0
    entry_price = None
    entry_exec_date = None
    entry_signal_low = None
    pending_entry = None
    pending_exit = None

    trades = []
    daily_records = []
    closed_trades = []
    dates = signal_df["trade_date"].tolist()

    signal_col = "is_candidate_a" if band_config["name"] == CANDIDATE_A["name"] else "is_candidate_b"

    for idx, row in signal_df.iterrows():
        trade_date = row["trade_date"]
        current_open = float(row["open"])
        current_close = float(row["close"])
        current_low = float(row["low"])
        executed_action = "hold"
        executed_reason = ""

        if pending_exit and shares > 0:
            proceeds = shares * current_open
            pnl = (current_open - entry_price) * shares
            cash += proceeds
            trades.append(
                {
                    "band": band_config["label"],
                    "signal_date": pending_exit["signal_date"],
                    "trade_date": trade_date,
                    "stock": STOCK,
                    "action": "sell",
                    "price": round(current_open, 4),
                    "shares": shares,
                    "amount": round(proceeds, 2),
                    "holding_days": pending_exit["holding_days"],
                    "reason": " | ".join(pending_exit["reasons"]),
                    "pnl": round(pnl, 2),
                }
            )
            closed_trades.append({"pnl": pnl, "holding_days": pending_exit["holding_days"]})
            shares = 0
            entry_price = None
            entry_exec_date = None
            entry_signal_low = None
            executed_action = "sell"
            executed_reason = " | ".join(pending_exit["reasons"])
            pending_exit = None

        elif pending_entry and shares == 0:
            max_shares = int(cash / current_open / LOT_SIZE) * LOT_SIZE
            if max_shares > 0:
                cost = max_shares * current_open
                cash -= cost
                shares = max_shares
                entry_price = current_open
                entry_exec_date = trade_date
                entry_signal_low = pending_entry["signal_low"]
                trades.append(
                    {
                        "band": band_config["label"],
                        "signal_date": pending_entry["signal_date"],
                        "trade_date": trade_date,
                        "stock": STOCK,
                        "action": "buy",
                        "price": round(current_open, 4),
                        "shares": max_shares,
                        "amount": round(cost, 2),
                        "holding_days": 0,
                        "reason": pending_entry["reason"],
                        "pnl": 0.0,
                    }
                )
                executed_action = "buy"
                executed_reason = pending_entry["reason"]
            else:
                executed_action = "skip_buy"
                executed_reason = "现金不足以买入一手"
            pending_entry = None

        market_value = shares * current_close
        total_equity = cash + market_value
        next_signal = "none"
        next_signal_reason = ""

        if idx < len(signal_df) - 1:
            if shares > 0:
                holding_days = dates.index(trade_date) - dates.index(entry_exec_date) + 1
                exit_flag, exit_reasons = evaluate_exit(row, entry_price, entry_signal_low, holding_days)
                if exit_flag:
                    pending_exit = {
                        "signal_date": trade_date,
                        "holding_days": holding_days,
                        "reasons": exit_reasons,
                    }
                    next_signal = "prepare_sell"
                    next_signal_reason = " | ".join(exit_reasons)
            elif bool(row[signal_col]):
                reason = "{}: background_score={} trigger_score={}".format(
                    band_config["label"], int(row["background_score"]), int(row["trigger_score"])
                )
                pending_entry = {
                    "signal_date": trade_date,
                    "signal_low": current_low,
                    "reason": reason,
                }
                next_signal = "prepare_buy"
                next_signal_reason = reason
            else:
                next_signal = "watch"
                next_signal_reason = "background_score={} trigger_score={}".format(
                    int(row["background_score"]), int(row["trigger_score"])
                )

        daily_records.append(
            {
                "band": band_config["label"],
                "trade_date": trade_date,
                "stock": STOCK,
                "open": round(current_open, 4),
                "high": round(float(row["high"]), 4),
                "low": round(current_low, 4),
                "close": round(current_close, 4),
                "cash": round(cash, 2),
                "shares": shares,
                "market_value": round(market_value, 2),
                "total_equity": round(total_equity, 2),
                "background_score": int(row["background_score"]),
                "trigger_score": int(row["trigger_score"]),
                "is_candidate_a": bool(row["is_candidate_a"]),
                "is_candidate_b": bool(row["is_candidate_b"]),
                "executed_action": executed_action,
                "executed_reason": executed_reason,
                "next_signal": next_signal,
                "next_signal_reason": next_signal_reason,
            }
        )

    trades_df = pd.DataFrame(trades)
    daily_df = pd.DataFrame(daily_records)
    closed_df = pd.DataFrame(closed_trades)
    return build_summary(band_config, daily_df, trades_df, closed_df), trades_df, daily_df


def compute_max_drawdown(equity_series):
    rolling_high = equity_series.cummax()
    drawdown = equity_series / rolling_high - 1.0
    return float(drawdown.min()) if not drawdown.empty else 0.0


def compute_consecutive_losses(trades_df):
    if trades_df.empty or "pnl" not in trades_df.columns:
        return 0
    max_streak = 0
    streak = 0
    for pnl in trades_df["pnl"]:
        if pnl < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return int(max_streak)


def build_summary(band_config, daily_df, trades_df, closed_df):
    final_equity = float(daily_df.iloc[-1]["total_equity"])
    total_return = final_equity / INITIAL_CASH - 1.0
    win_rate = 0.0 if closed_df.empty else float((closed_df["pnl"] > 0).mean())
    avg_holding_days = 0.0 if closed_df.empty else float(closed_df["holding_days"].mean())
    return {
        "band": band_config["label"],
        "stock": STOCK,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "price_adjustment": PRICE_ADJUSTMENT,
        "initial_cash": INITIAL_CASH,
        "final_equity": round(final_equity, 2),
        "total_return": round(total_return, 6),
        "trade_count": int(len(trades_df)),
        "closed_trade_count": int(len(closed_df)),
        "win_rate": round(win_rate, 6),
        "max_drawdown": round(compute_max_drawdown(daily_df["total_equity"]), 6),
        "avg_holding_days": round(avg_holding_days, 4),
        "max_consecutive_losses": compute_consecutive_losses(closed_df),
        "background_min": band_config["background_min"],
        "trigger_min": band_config["trigger_min"],
        "exit_policy": {
            "max_hold_days": MAX_HOLD_DAYS,
            "stop_loss_pct": STOP_LOSS_PCT,
            "take_profit_pct": TAKE_PROFIT_PCT,
            "low_break_tolerance": LOW_BREAK_TOLERANCE,
            "execution": "signals use full-day data; orders execute at next open",
        },
    }


def save_outputs(summary_rows, trades_df, daily_df, signal_df, bg_point_cols, tg_point_cols):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summary_path = OUTPUT_DIR / "summary.json"
    trades_path = OUTPUT_DIR / "trades.csv"
    equity_path = OUTPUT_DIR / "daily_equity.csv"
    signal_review_path = OUTPUT_DIR / "signal_review.csv"

    summary_path.write_text(
        json.dumps({"runs": summary_rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trades_df.to_csv(trades_path, index=False, encoding="utf-8-sig")
    daily_df.to_csv(equity_path, index=False, encoding="utf-8-sig")

    review_cols = [
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "background_score",
        "trigger_score",
        "is_candidate_a",
        "is_candidate_b",
        "background_hit_labels",
        "trigger_hit_labels",
    ]
    review_cols.extend(bg_point_cols)
    review_cols.extend(tg_point_cols)
    signal_df[review_cols].to_csv(signal_review_path, index=False, encoding="utf-8-sig")
    return summary_path, trades_path, equity_path, signal_review_path


def main():
    print("Running n5_r3 candidate-rule backtest")
    print("stock={}, start={}, end={}, adjustment={}".format(STOCK, START_DATE, END_DATE, PRICE_ADJUSTMENT))
    print("candidate A: background>=3 trigger>=2")
    print("candidate B: background>=3 trigger>=1")

    signal_df, bg_point_cols, tg_point_cols = build_signal_frame()
    try:
        instrument_detail = xtdata.get_instrument_detail(STOCK, iscomplete=False)
    except Exception:
        instrument_detail = {}

    summaries = []
    trades_list = []
    daily_list = []
    for band_config in [CANDIDATE_A, CANDIDATE_B]:
        summary, trades_df, daily_df = run_single_band(signal_df, band_config)
        summary["instrument_name"] = instrument_detail.get("InstrumentName", "")
        summaries.append(summary)
        trades_list.append(trades_df)
        daily_list.append(daily_df)

    all_trades = pd.concat(trades_list, ignore_index=True) if trades_list else pd.DataFrame()
    all_daily = pd.concat(daily_list, ignore_index=True) if daily_list else pd.DataFrame()
    paths = save_outputs(summaries, all_trades, all_daily, signal_df, bg_point_cols, tg_point_cols)

    for summary in summaries:
        print(
            "{}: trades={}, closed={}, return={}, win_rate={}, max_dd={}".format(
                summary["band"],
                summary["trade_count"],
                summary["closed_trade_count"],
                summary["total_return"],
                summary["win_rate"],
                summary["max_drawdown"],
            )
        )
    print("outputs:")
    for path in paths:
        print(" -", path)


if __name__ == "__main__":
    main()
