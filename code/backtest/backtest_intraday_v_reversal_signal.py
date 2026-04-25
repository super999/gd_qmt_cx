#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd

import minimal_stock_backtest as base
from xtquant import xtdata


STOCK = "510300.SH"
START_DATE = "20240101"
END_DATE = "20260424"
PRICE_ADJUSTMENT = "front"
DAILY_GATE_MODE = "intraday_estimated_daily_gate"
TIME_GATE_MODE = "dynamic_low_repair_gate"

INITIAL_CASH = 100000.0
LOT_SIZE = 100
EXPECTED_5M_BARS = 48

STOP_LOSS_PCT = 0.03
TAKE_PROFIT_PCT = 0.03
LOW_BREAK_TOLERANCE = 0.001
MAX_HOLD_DAYS = 5
TRADE_SETTLEMENT = "T+1"

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
    ("m5_rebound_to_est_close", "higher", 0.009317),
    ("m5_up_close_streak_after_low", "higher", 4),
    ("m5_up_bar_ratio_after_low", "higher", 0.5),
    ("m5_est_close_in_range", "higher", 0.722222),
    ("m1_up_close_streak_after_low", "higher", 5),
]

BANDS = [
    {
        "name": "candidate_a_strict",
        "label": "候选A-严格",
        "background_min": 3,
        "trigger_min": 2,
    },
    {
        "name": "candidate_b_balanced",
        "label": "候选B-平衡",
        "background_min": 3,
        "trigger_min": 1,
    },
]

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "backtest_intraday_v_reversal_signal"


def ensure_history():
    for period in ["1d", "1m", "5m"]:
        xtdata.download_history_data(
            STOCK,
            period=period,
            start_time=START_DATE,
            end_time=END_DATE,
            incrementally=False,
        )


def load_price_frame(period):
    data = xtdata.get_local_data(
        field_list=[],
        stock_list=[STOCK],
        period=period,
        start_time=START_DATE,
        end_time=END_DATE,
        count=-1,
        dividend_type=PRICE_ADJUSTMENT,
        fill_data=True,
    ).get(STOCK)
    if data is None or data.empty:
        raise RuntimeError("no {} data for {}".format(period, STOCK))
    frame = data.copy()
    frame.index = frame.index.astype(str)
    frame["bar_time"] = frame.index.astype(str)
    frame.index.name = None
    frame["trade_date"] = frame["bar_time"].str[:8]
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.sort_values("bar_time").reset_index(drop=True)


def consecutive_positive_closes(values):
    max_streak = 0
    streak = 0
    prev = None
    for value in values:
        if prev is not None and value > prev:
            streak += 1
        else:
            streak = 0
        max_streak = max(max_streak, streak)
        prev = value
    return int(max_streak)


def score_rules(features, rules):
    score = 0
    hits = {}
    for feature, direction, threshold in rules:
        value = features.get(feature)
        hit = False
        if pd.notna(value):
            if isinstance(value, bool):
                numeric_value = int(value)
            else:
                numeric_value = float(value)
            hit = numeric_value >= threshold if direction == "higher" else numeric_value <= threshold
        hits[feature + "_hit"] = int(hit)
        score += int(hit)
    return score, hits


def build_intraday_maps(frame):
    return {date: group.copy().reset_index(drop=True) for date, group in frame.groupby("trade_date")}


def build_daily_context(daily_frame):
    daily = daily_frame.copy().sort_values("trade_date").reset_index(drop=True)
    daily["daily_return"] = daily["close"].pct_change()
    return daily


def estimate_daily_features(prev_daily, partial_5m):
    if len(prev_daily) < 20 or partial_5m.empty:
        return None

    current_price = float(partial_5m.iloc[-1]["close"])
    high_so_far = float(partial_5m["high"].max())
    low_so_far = float(partial_5m["low"].min())
    prev_close = float(prev_daily.iloc[-1]["close"])

    high_10 = max(float(prev_daily["high"].tail(9).max()), high_so_far)
    high_20 = max(float(prev_daily["high"].tail(19).max()), high_so_far)
    ma20_est = (float(prev_daily["close"].tail(19).sum()) + current_price) / 20.0
    current_return = current_price / prev_close - 1.0 if prev_close else 0.0
    recent_returns = prev_daily["daily_return"].dropna().tail(4).tolist() + [current_return]

    return {
        "estimated_close": current_price,
        "drawdown_from_high_10": low_so_far / high_10 - 1.0 if high_10 else 0.0,
        "drawdown_from_high_20": low_so_far / high_20 - 1.0 if high_20 else 0.0,
        "low_vs_ma20": low_so_far / ma20_est - 1.0 if ma20_est else 0.0,
        "volatility_5": float(pd.Series(recent_returns).std()) if len(recent_returns) >= 2 else 0.0,
        "ret_5d": current_price / float(prev_daily["close"].iloc[-5]) - 1.0,
    }


def estimate_trigger_features(partial_5m, partial_1m):
    if len(partial_5m) < 4 or len(partial_1m) < 10:
        return None

    low_pos = int(partial_5m["low"].values.argmin())
    current_pos = len(partial_5m) - 1
    low_price = float(partial_5m.iloc[low_pos]["low"])
    high_so_far = float(partial_5m["high"].max())
    est_close = float(partial_5m.iloc[-1]["close"])
    day_range = high_so_far - low_price
    bars_after_low = partial_5m.iloc[low_pos + 1 :].copy()

    if bars_after_low.empty:
        m5_up_streak = 0
        m5_up_ratio = 0.0
    else:
        m5_up_streak = consecutive_positive_closes(bars_after_low["close"].astype(float).tolist())
        m5_up_ratio = float((bars_after_low["close"] > bars_after_low["open"]).mean())

    low_1m_pos = int(partial_1m["low"].values.argmin())
    bars_1m_after_low = partial_1m.iloc[low_1m_pos + 1 :].copy()
    m1_up_streak = (
        consecutive_positive_closes(bars_1m_after_low["close"].astype(float).tolist())
        if not bars_1m_after_low.empty
        else 0
    )

    low_before_last_quarter = low_pos <= int(EXPECTED_5M_BARS * 0.75) and low_pos <= len(partial_5m) - 3
    dynamic_wait_bars = compute_dynamic_wait_bars(low_pos)
    dynamic_min_signal_pos = low_pos + dynamic_wait_bars
    time_gate_pass = current_pos >= dynamic_min_signal_pos

    return {
        "time_gate_mode": TIME_GATE_MODE,
        "m5_current_pos": current_pos,
        "m5_low_pos": low_pos,
        "m5_dynamic_wait_bars": dynamic_wait_bars,
        "m5_dynamic_min_signal_pos": dynamic_min_signal_pos,
        "m5_time_gate_pass": int(time_gate_pass),
        "m5_low_before_last_quarter": int(low_before_last_quarter),
        "m5_low_pos_ratio": low_pos / max(EXPECTED_5M_BARS - 1, 1),
        "m5_rebound_to_est_close": est_close / low_price - 1.0 if low_price else 0.0,
        "m5_up_close_streak_after_low": m5_up_streak,
        "m5_up_bar_ratio_after_low": m5_up_ratio,
        "m5_est_close_in_range": (est_close - low_price) / day_range if day_range > 0 else 0.0,
        "m1_up_close_streak_after_low": m1_up_streak,
        "signal_low": low_price,
    }


def compute_dynamic_wait_bars(low_pos):
    remaining_bars = max(EXPECTED_5M_BARS - low_pos - 1, 1)
    wait_bars = int(round(remaining_bars * 0.18))
    return max(2, min(6, wait_bars))


def build_intraday_signals(daily, data_1m, data_5m):
    map_1m = build_intraday_maps(data_1m)
    rows = []
    daily_dates = daily["trade_date"].tolist()

    for trade_date, bars_5m in data_5m.groupby("trade_date"):
        if trade_date not in map_1m or trade_date not in daily_dates:
            continue
        daily_idx = daily_dates.index(trade_date)
        prev_daily = daily.iloc[:daily_idx].copy()
        if len(prev_daily) < 20:
            continue

        bars_5m = bars_5m.copy().reset_index(drop=True)
        bars_1m = map_1m[trade_date]
        first_hit = {band["name"]: False for band in BANDS}

        for pos in range(len(bars_5m)):
            current_bar = bars_5m.iloc[pos]
            current_time = str(current_bar["bar_time"])
            partial_5m = bars_5m.iloc[: pos + 1].copy()
            partial_1m = bars_1m[bars_1m["bar_time"] <= current_time].copy()

            daily_features = estimate_daily_features(prev_daily, partial_5m)
            trigger_features = estimate_trigger_features(partial_5m, partial_1m)
            if daily_features is None or trigger_features is None:
                continue

            background_score, bg_hits = score_rules(daily_features, BACKGROUND_RULES)
            trigger_score, trigger_hits = score_rules(trigger_features, TRIGGER_RULES)
            if not trigger_features["m5_time_gate_pass"]:
                continue

            for band in BANDS:
                if first_hit[band["name"]]:
                    continue
                is_signal = (
                    background_score >= band["background_min"]
                    and trigger_score >= band["trigger_min"]
                )
                if not is_signal:
                    continue

                first_hit[band["name"]] = True
                item = {
                    "band": band["label"],
                    "band_name": band["name"],
                    "daily_gate_mode": DAILY_GATE_MODE,
                    "time_gate_mode": TIME_GATE_MODE,
                    "trade_date": trade_date,
                    "signal_time": current_time,
                    "signal_bar_pos": pos,
                    "signal_price": round(float(current_bar["close"]), 4),
                    "signal_low": round(float(trigger_features["signal_low"]), 4),
                    "background_score": background_score,
                    "trigger_score": trigger_score,
                }
                item.update({k: round(float(v), 6) for k, v in daily_features.items()})
                item.update(
                    {
                        k: round(float(v), 6)
                        for k, v in trigger_features.items()
                        if k not in {"signal_low", "time_gate_mode"}
                    }
                )
                item.update(bg_hits)
                item.update(trigger_hits)
                rows.append(item)

    return pd.DataFrame(rows).sort_values(["band", "signal_time"]).reset_index(drop=True)


def trade_dates_between(all_dates, start_date, end_date):
    return [date for date in all_dates if start_date <= date <= end_date]


def simulate_exit(all_5m, entry_idx, entry_price, signal_low, all_dates):
    entry_date = str(all_5m.iloc[entry_idx]["trade_date"])
    date_pos = all_dates.index(entry_date)
    max_date = all_dates[min(date_pos + MAX_HOLD_DAYS - 1, len(all_dates) - 1)]
    last_idx = int(all_5m[all_5m["trade_date"] <= max_date].index.max())

    for idx in range(entry_idx + 1, last_idx + 1):
        row = all_5m.iloc[idx]
        current_date = str(row["trade_date"])
        if current_date == entry_date:
            continue

        reasons = []
        close = float(row["close"])
        low = float(row["low"])

        if close <= entry_price * (1 - STOP_LOSS_PCT):
            reasons.append("盘中5m收盘触发固定止损")
        if low <= signal_low * (1 - LOW_BREAK_TOLERANCE):
            reasons.append("盘中跌破信号低点容忍线")
        if close >= entry_price * (1 + TAKE_PROFIT_PCT):
            reasons.append("盘中5m收盘触发目标收益")

        is_last_bar = idx >= last_idx
        if is_last_bar:
            reasons.append("持有天数达到上限")

        if reasons:
            exit_idx = idx + 1 if idx + 1 < len(all_5m) else idx
            exit_row = all_5m.iloc[exit_idx]
            if str(exit_row["trade_date"]) == entry_date:
                continue
            exit_price = float(exit_row["open"]) if exit_idx != idx else close
            return {
                "exit_idx": int(exit_idx),
                "exit_time": str(exit_row["bar_time"]),
                "exit_date": str(exit_row["trade_date"]),
                "exit_price": exit_price,
                "exit_reason": " | ".join(reasons),
                "holding_trade_days": len(
                    trade_dates_between(all_dates, entry_date, str(row["trade_date"]))
                ),
            }

    exit_row = all_5m.iloc[last_idx]
    return {
        "exit_idx": int(last_idx),
        "exit_time": str(exit_row["bar_time"]),
        "exit_date": str(exit_row["trade_date"]),
        "exit_price": float(exit_row["close"]),
        "exit_reason": "持有天数达到上限",
        "holding_trade_days": MAX_HOLD_DAYS,
    }


def run_band_backtest(signals, all_5m, band_name):
    band_signals = signals[signals["band_name"] == band_name].copy().sort_values("signal_time")
    if band_signals.empty:
        return {}, pd.DataFrame()

    bar_index = {str(row["bar_time"]): idx for idx, row in all_5m.iterrows()}
    all_dates = sorted(all_5m["trade_date"].unique().tolist())
    cash = INITIAL_CASH
    available_after_idx = -1
    trades = []

    for _, signal in band_signals.iterrows():
        signal_idx = bar_index.get(str(signal["signal_time"]))
        if signal_idx is None or signal_idx <= available_after_idx:
            continue
        entry_idx = signal_idx + 1
        if entry_idx >= len(all_5m):
            continue

        entry_row = all_5m.iloc[entry_idx]
        entry_price = float(entry_row["open"])
        shares = int(cash / entry_price / LOT_SIZE) * LOT_SIZE
        if shares <= 0:
            continue

        exit_info = simulate_exit(
            all_5m,
            entry_idx,
            entry_price,
            float(signal["signal_low"]),
            all_dates,
        )
        pnl = (exit_info["exit_price"] - entry_price) * shares
        cash += pnl
        available_after_idx = exit_info["exit_idx"]

        trades.append(
            {
                "band": signal["band"],
                "signal_time": signal["signal_time"],
                "signal_price": signal["signal_price"],
                "entry_time": str(entry_row["bar_time"]),
                "entry_price": round(entry_price, 4),
                "exit_time": exit_info["exit_time"],
                "exit_price": round(exit_info["exit_price"], 4),
                "shares": shares,
                "pnl": round(pnl, 2),
                "return_pct": round(exit_info["exit_price"] / entry_price - 1.0, 6),
                "holding_trade_days": exit_info["holding_trade_days"],
                "exit_reason": exit_info["exit_reason"],
                "background_score": int(signal["background_score"]),
                "trigger_score": int(signal["trigger_score"]),
            }
        )

    trades_df = pd.DataFrame(trades)
    summary = build_summary(band_signals, trades_df, cash)
    return summary, trades_df


def build_summary(band_signals, trades_df, final_cash):
    closed_count = int(len(trades_df))
    win_rate = 0.0 if trades_df.empty else float((trades_df["pnl"] > 0).mean())
    return {
        "band": str(band_signals.iloc[0]["band"]),
        "daily_gate_mode": DAILY_GATE_MODE,
        "time_gate_mode": TIME_GATE_MODE,
        "trade_settlement": TRADE_SETTLEMENT,
        "signal_count": int(len(band_signals)),
        "closed_trade_count": closed_count,
        "initial_cash": INITIAL_CASH,
        "final_cash": round(float(final_cash), 2),
        "total_return": round(float(final_cash / INITIAL_CASH - 1.0), 6),
        "win_rate": round(win_rate, 6),
        "avg_trade_return": round(float(trades_df["return_pct"].mean()), 6)
        if not trades_df.empty
        else 0.0,
        "avg_holding_trade_days": round(float(trades_df["holding_trade_days"].mean()), 4)
        if not trades_df.empty
        else 0.0,
    }


def save_outputs(signals, summaries, trades):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    signal_path = OUTPUT_DIR / "intraday_signals.csv"
    summary_path = OUTPUT_DIR / "summary.json"
    trades_path = OUTPUT_DIR / "trades.csv"

    signals.to_csv(signal_path, index=False, encoding="utf-8-sig")
    trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    summary_path.write_text(
        json.dumps({"runs": summaries}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return signal_path, summary_path, trades_path


def main():
    print("Running intraday V-reversal signal replay")
    print("stock={}, start={}, end={}".format(STOCK, START_DATE, END_DATE))
    print("daily gate mode:", DAILY_GATE_MODE)
    print("time gate mode:", TIME_GATE_MODE)
    ensure_history()

    daily = build_daily_context(load_price_frame("1d"))
    data_1m = load_price_frame("1m")
    data_5m = load_price_frame("5m")
    signals = build_intraday_signals(daily, data_1m, data_5m)

    summaries = []
    trade_frames = []
    for band in BANDS:
        summary, trades_df = run_band_backtest(signals, data_5m, band["name"])
        if summary:
            summaries.append(summary)
            trade_frames.append(trades_df)

    all_trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    paths = save_outputs(signals, summaries, all_trades)

    for summary in summaries:
        print(
            "{}: signals={}, trades={}, return={}, win_rate={}".format(
                summary["band"],
                summary["signal_count"],
                summary["closed_trade_count"],
                summary["total_return"],
                summary["win_rate"],
            )
        )
    print("outputs:")
    for path in paths:
        print(" -", path)


if __name__ == "__main__":
    main()
