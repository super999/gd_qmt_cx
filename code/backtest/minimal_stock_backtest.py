#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd

from xtquant import xtdata


# Stable backtest policy flags.
PRICE_ADJUSTMENT = "front"
DATA_SOURCE = "local"
USE_FINANCIAL_DATA = False
INDICATOR_MODE = "python"

# Development-default instrument from the strategy plan.
STOCK_LIST = ["510300.SH"]
BENCHMARK_SECTOR = "沪深ETF"
DAILY_PERIOD = "1d"
INTRADAY_PERIOD = "30m"
START_DATE = "20240101"
END_DATE = "20260423"

INITIAL_CASH = 100000.0
LOT_SIZE = 100
MAX_HOLD_DAYS = 7
STOP_LOSS_PCT = 0.03
TAKE_PROFIT_PCT = 0.04

MA_SHORT = 5
MA_LONG = 20
RSI_PERIOD = 6
VOLUME_MA_PERIOD = 5

# Candidate parameters for the development version.
# They are intentionally marked as DEV_* to show they are not final truths.
DEV_PULLBACK_SCORE_MIN = 4
DEV_DRAWDOWN_LOOKBACK = 10
DEV_DRAWDOWN_FROM_HIGH = 0.008
DEV_TREND_FLOOR = 0.980
DEV_INTRADAY_REBOUND_FROM_LOW = 0.004
DEV_INTRADAY_CLOSE_IN_RANGE = 0.60
DEV_INTRADAY_HOLD_FROM_LOW = 0.002
DEV_INTRADAY_SCORE_MIN = 4

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "minimal_stock_backtest"


def ensure_history_download(stock_list, period, start_date, end_date):
    for stock in stock_list:
        xtdata.download_history_data(
            stock,
            period=period,
            start_time=start_date,
            end_time=end_date,
            incrementally=False,
        )


def load_price_frame(stock, period, start_date, end_date):
    frame = None

    if period == DAILY_PERIOD:
        data = xtdata.get_local_data(
            field_list=[],
            stock_list=[stock],
            period=period,
            start_time=start_date,
            end_time=end_date,
            count=-1,
            dividend_type=PRICE_ADJUSTMENT,
            fill_data=True,
        )
        frame = data.get(stock)
    else:
        data = xtdata.get_market_data_ex(
            field_list=[],
            stock_list=[stock],
            period=period,
            start_time=start_date,
            end_time=end_date,
            count=-1,
            dividend_type=PRICE_ADJUSTMENT,
            fill_data=True,
        )
        frame = data.get(stock)

    if frame is None or frame.empty:
        raise RuntimeError("no local history data returned for {}".format(stock))

    frame = frame.copy()
    frame.index = frame.index.astype(str)
    frame.index.name = "bar_time"
    return frame.sort_index()


def compute_rsi(close, period):
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.rolling(period, min_periods=period).mean()
    avg_loss = loss.rolling(period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(0.0)


def enrich_daily_indicators(frame):
    enriched = frame.copy()
    enriched["ma_short"] = enriched["close"].rolling(MA_SHORT, min_periods=MA_SHORT).mean()
    enriched["ma_long"] = enriched["close"].rolling(MA_LONG, min_periods=MA_LONG).mean()
    enriched["rsi"] = compute_rsi(enriched["close"], RSI_PERIOD)
    enriched["volume_ma"] = enriched["volume"].rolling(
        VOLUME_MA_PERIOD, min_periods=VOLUME_MA_PERIOD
    ).mean()
    enriched["daily_return"] = enriched["close"].pct_change()
    enriched["five_day_return"] = enriched["close"].pct_change(5)
    enriched["recent_high"] = enriched["high"].rolling(
        DEV_DRAWDOWN_LOOKBACK, min_periods=DEV_DRAWDOWN_LOOKBACK
    ).max()
    enriched["drawdown_from_recent_high"] = (
        enriched["close"] / enriched["recent_high"] - 1.0
    )
    enriched["down_day"] = (enriched["close"] < enriched["open"]).astype(int)
    enriched["down_days_last3"] = enriched["down_day"].rolling(3, min_periods=3).sum()
    enriched["trade_date"] = enriched.index.astype(str).str[:8]
    return enriched


def build_intraday_signal_map(intraday_frame):
    signal_map = {}
    grouped = intraday_frame.groupby(intraday_frame.index.astype(str).str[:8])

    for trade_date, day_frame in grouped:
        bars = day_frame.copy()
        if len(bars) < 4:
            signal_map[trade_date] = {
                "confirmed": False,
                "score": 0,
                "labels": ["30m_bar不足"],
                "metrics": {},
            }
            continue

        day_low = float(bars["low"].min())
        day_high = float(bars["high"].max())
        first_open = float(bars.iloc[0]["open"])
        last_close = float(bars.iloc[-1]["close"])
        low_pos = int(bars["low"].astype(float).idxmin()[-4:] if False else bars["low"].astype(float).argmin())
        low_before_last_two = low_pos <= len(bars) - 3
        rebound_from_low = (last_close / day_low - 1.0) if day_low else 0.0
        close_in_range = (
            (last_close - day_low) / (day_high - day_low) if day_high > day_low else 0.0
        )
        last_two_closes = bars["close"].tail(2).astype(float)
        hold_from_low = (float(last_two_closes.min()) / day_low - 1.0) if day_low else 0.0
        close_above_open = last_close > first_open

        labels = []
        score = 0

        if low_before_last_two:
            labels.append("低点出现得不算太晚")
            score += 1
        else:
            labels.append("低点出现在尾盘过晚")

        if rebound_from_low >= DEV_INTRADAY_REBOUND_FROM_LOW:
            labels.append("盘中低点后出现明显反抽")
            score += 1
        else:
            labels.append("盘中低点反抽幅度不足")

        if close_in_range >= DEV_INTRADAY_CLOSE_IN_RANGE:
            labels.append("收盘位置处于当日波动区间上半部")
            score += 1
        else:
            labels.append("收盘位置仍偏弱")

        if hold_from_low >= DEV_INTRADAY_HOLD_FROM_LOW:
            labels.append("尾盘未重新贴近当日低点")
            score += 1
        else:
            labels.append("尾盘反抽不稳")

        if close_above_open:
            labels.append("当日30m结构整体转强")
            score += 1
        else:
            labels.append("当日30m结构未明显转强")

        # Development v1.1: intraday confirmation must look like a real rebound,
        # not just a weak bounce at the end of the session.
        hard_pass = (
            low_before_last_two
            and rebound_from_low >= DEV_INTRADAY_REBOUND_FROM_LOW
            and close_in_range >= DEV_INTRADAY_CLOSE_IN_RANGE
            and close_above_open
        )

        signal_map[trade_date] = {
            "confirmed": hard_pass and score >= DEV_INTRADAY_SCORE_MIN,
            "score": score,
            "labels": labels,
            "metrics": {
                "day_low": round(day_low, 4),
                "day_high": round(day_high, 4),
                "last_close": round(last_close, 4),
                "rebound_from_low": round(rebound_from_low, 6),
                "close_in_range": round(close_in_range, 6),
                "hold_from_low": round(hold_from_low, 6),
            },
        }

    return signal_map


def evaluate_pullback_background(row):
    labels = []
    score = 0

    if pd.isna(row["ma_short"]) or pd.isna(row["ma_long"]) or pd.isna(row["recent_high"]):
        return False, 0, ["日线指标窗口不足"]

    if row["down_days_last3"] >= 2:
        labels.append("近3日内至少2根阴线")
        score += 1
    else:
        labels.append("近3日连跌痕迹不足")

    if row["close"] < row["ma_short"]:
        labels.append("收盘价位于5日均线下方")
        score += 1
    else:
        labels.append("收盘价未落到5日均线下方")

    if row["drawdown_from_recent_high"] <= -DEV_DRAWDOWN_FROM_HIGH:
        labels.append("已从近10日高点出现可见回撤")
        score += 1
    else:
        labels.append("相对近10日高点回撤不明显")

    if row["five_day_return"] < 0:
        labels.append("5日方向偏弱")
        score += 1
    else:
        labels.append("5日方向未转弱")

    trend_not_broken = row["close"] >= row["ma_long"] * DEV_TREND_FLOOR
    if not trend_not_broken:
        labels.append("疑似已进入大级别破位")
        return False, score, labels

    labels.append("尚未出现明显大级别破位")
    hard_pass = (
        row["down_days_last3"] >= 2
        and row["drawdown_from_recent_high"] <= -DEV_DRAWDOWN_FROM_HIGH
        and row["five_day_return"] < 0
        and row["close"] < row["ma_short"]
    )
    return hard_pass and score >= DEV_PULLBACK_SCORE_MIN, score, labels


def evaluate_exit(row, entry_price, holding_days):
    reasons = []
    if row["close"] <= entry_price * (1 - STOP_LOSS_PCT):
        reasons.append("触发固定止损")
    if pd.notna(row["ma_short"]) and row["close"] < row["ma_short"]:
        reasons.append("收盘价跌回5日均线下方")
    if row["close"] >= entry_price * (1 + TAKE_PROFIT_PCT):
        reasons.append("触发固定止盈")
    if holding_days >= MAX_HOLD_DAYS:
        reasons.append("持有天数达到上限")
    return len(reasons) > 0, reasons


def compute_max_drawdown(equity_series):
    rolling_high = equity_series.cummax()
    drawdown = equity_series / rolling_high - 1.0
    return float(drawdown.min()) if not drawdown.empty else 0.0


def compute_consecutive_losses(trades_df):
    if trades_df.empty or "pnl" not in trades_df.columns:
        return 0
    loss_streak = 0
    max_loss_streak = 0
    for pnl in trades_df["pnl"]:
        if pnl < 0:
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            loss_streak = 0
    return int(max_loss_streak)


def run_backtest(stock, daily_frame, intraday_signals, instrument_detail):
    cash = INITIAL_CASH
    shares = 0
    entry_price = None
    entry_exec_date = None
    pending_entry = None
    pending_exit = None

    trades = []
    daily_records = []
    closed_trades = []

    dates = list(daily_frame.index)

    for idx, trade_date in enumerate(dates):
        row = daily_frame.loc[trade_date]
        executed_action = "hold"
        executed_reason = ""
        current_open = float(row["open"])
        current_close = float(row["close"])
        pullback_ok = False
        pullback_score = 0
        pullback_labels = ["未评估"]
        intraday_info = intraday_signals.get(
            row["trade_date"],
            {"confirmed": False, "score": 0, "labels": ["无30m信号数据"], "metrics": {}},
        )

        # Execute pending orders at today's open.
        if pending_exit and shares > 0:
            proceeds = shares * current_open
            pnl = (current_open - entry_price) * shares
            cash += proceeds
            trades.append(
                {
                    "signal_date": pending_exit["signal_date"],
                    "trade_date": trade_date,
                    "stock": stock,
                    "action": "sell",
                    "price": round(current_open, 4),
                    "shares": shares,
                    "amount": round(proceeds, 2),
                    "holding_days": pending_exit["holding_days"],
                    "reason": " | ".join(pending_exit["reasons"]),
                    "pnl": round(pnl, 2),
                }
            )
            closed_trades.append(
                {
                    "trade_date": trade_date,
                    "action": "sell",
                    "pnl": round(pnl, 2),
                    "holding_days": pending_exit["holding_days"],
                }
            )
            shares = 0
            entry_price = None
            entry_exec_date = None
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
                trades.append(
                    {
                        "signal_date": pending_entry["signal_date"],
                        "trade_date": trade_date,
                        "stock": stock,
                        "action": "buy",
                        "price": round(current_open, 4),
                        "shares": max_shares,
                        "amount": round(cost, 2),
                        "holding_days": 0,
                        "reason": " | ".join(pending_entry["reasons"]),
                        "pnl": 0.0,
                    }
                )
                executed_action = "buy"
                executed_reason = " | ".join(pending_entry["reasons"])
            else:
                executed_action = "skip_buy"
                executed_reason = "现金不足以买入一手"
            pending_entry = None

        market_value = shares * current_close
        total_equity = cash + market_value

        next_signal = "none"
        next_signal_reason = []

        # Build next-day signals using today's fully known bar data.
        if idx < len(dates) - 1:
            pullback_ok, pullback_score, pullback_labels = evaluate_pullback_background(row)
            if shares > 0:
                holding_days = dates.index(trade_date) - dates.index(entry_exec_date) + 1
                exit_flag, exit_reasons = evaluate_exit(row, entry_price, holding_days)
                if exit_flag:
                    pending_exit = {
                        "signal_date": trade_date,
                        "holding_days": holding_days,
                        "reasons": exit_reasons,
                    }
                    next_signal = "prepare_sell"
                    next_signal_reason = exit_reasons
            else:
                if pullback_ok and intraday_info["confirmed"]:
                    signal_reasons = [
                        "日线回撤背景成立(score={})".format(pullback_score),
                        "30m止跌确认成立(score={})".format(intraday_info["score"]),
                    ]
                    signal_reasons.extend(pullback_labels)
                    signal_reasons.extend(intraday_info["labels"])
                    pending_entry = {
                        "signal_date": trade_date,
                        "reasons": signal_reasons,
                    }
                    next_signal = "prepare_buy"
                    next_signal_reason = signal_reasons
                else:
                    next_signal = "watch"
                    next_signal_reason = pullback_labels + intraday_info["labels"]

        daily_records.append(
            {
                "trade_date": trade_date,
                "stock": stock,
                "open": round(current_open, 4),
                "close": round(current_close, 4),
                "cash": round(cash, 2),
                "shares": shares,
                "market_value": round(market_value, 2),
                "total_equity": round(total_equity, 2),
                "ma_short": round(float(row["ma_short"]), 4) if pd.notna(row["ma_short"]) else None,
                "ma_long": round(float(row["ma_long"]), 4) if pd.notna(row["ma_long"]) else None,
                "rsi": round(float(row["rsi"]), 4) if pd.notna(row["rsi"]) else None,
                "volume": int(row["volume"]) if pd.notna(row["volume"]) else None,
                "volume_ma": round(float(row["volume_ma"]), 4)
                if pd.notna(row["volume_ma"])
                else None,
                "pullback_ok": pullback_ok,
                "pullback_score": pullback_score,
                "pullback_labels": " | ".join(pullback_labels[:8]),
                "intraday_confirmed": bool(intraday_info["confirmed"]),
                "intraday_score": int(intraday_info["score"]),
                "intraday_labels": " | ".join(intraday_info["labels"][:8]),
                "intraday_rebound_from_low": intraday_info["metrics"].get("rebound_from_low"),
                "intraday_close_in_range": intraday_info["metrics"].get("close_in_range"),
                "intraday_hold_from_low": intraday_info["metrics"].get("hold_from_low"),
                "executed_action": executed_action,
                "executed_reason": executed_reason,
                "next_signal": next_signal,
                "next_signal_reason": " | ".join(next_signal_reason[:8]),
            }
        )

    daily_df = pd.DataFrame(daily_records)
    trades_df = pd.DataFrame(trades)
    closed_trades_df = pd.DataFrame(closed_trades)

    final_equity = float(daily_df.iloc[-1]["total_equity"])
    total_return = final_equity / INITIAL_CASH - 1.0
    max_drawdown = compute_max_drawdown(daily_df["total_equity"])
    win_rate = 0.0
    avg_holding_days = 0.0

    if not closed_trades_df.empty:
        win_rate = float((closed_trades_df["pnl"] > 0).mean())
        avg_holding_days = float(closed_trades_df["holding_days"].mean())

    summary = {
        "stock": stock,
        "instrument_name": instrument_detail.get("InstrumentName", ""),
        "price_adjustment": PRICE_ADJUSTMENT,
        "data_source": DATA_SOURCE,
        "indicator_mode": INDICATOR_MODE,
        "use_financial_data": USE_FINANCIAL_DATA,
        "daily_period": DAILY_PERIOD,
        "intraday_period": INTRADAY_PERIOD,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "initial_cash": INITIAL_CASH,
        "final_equity": round(final_equity, 2),
        "total_return": round(total_return, 6),
        "trade_count": int(len(trades_df)),
        "closed_trade_count": int(len(closed_trades_df)),
        "win_rate": round(win_rate, 6),
        "max_drawdown": round(max_drawdown, 6),
        "avg_holding_days": round(avg_holding_days, 4),
        "max_consecutive_losses": compute_consecutive_losses(closed_trades_df),
        "benchmark_sector": BENCHMARK_SECTOR,
        "dev_params": {
            "pullback_score_min": DEV_PULLBACK_SCORE_MIN,
            "drawdown_lookback": DEV_DRAWDOWN_LOOKBACK,
            "drawdown_from_high": DEV_DRAWDOWN_FROM_HIGH,
            "trend_floor": DEV_TREND_FLOOR,
            "intraday_rebound_from_low": DEV_INTRADAY_REBOUND_FROM_LOW,
            "intraday_close_in_range": DEV_INTRADAY_CLOSE_IN_RANGE,
            "intraday_hold_from_low": DEV_INTRADAY_HOLD_FROM_LOW,
            "intraday_score_min": DEV_INTRADAY_SCORE_MIN,
        },
    }
    return summary, trades_df, daily_df


def save_outputs(summary, trades_df, daily_df):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summary_path = OUTPUT_DIR / "summary.json"
    trades_path = OUTPUT_DIR / "trades.csv"
    equity_path = OUTPUT_DIR / "daily_equity.csv"
    signal_review_path = OUTPUT_DIR / "signal_review.csv"

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    trades_df.to_csv(trades_path, index=False, encoding="utf-8-sig")
    daily_df.to_csv(equity_path, index=False, encoding="utf-8-sig")
    signal_review_df = daily_df[
        [
            "trade_date",
            "stock",
            "close",
            "ma_short",
            "ma_long",
            "rsi",
            "pullback_ok",
            "pullback_score",
            "pullback_labels",
            "intraday_confirmed",
            "intraday_score",
            "intraday_labels",
            "intraday_rebound_from_low",
            "intraday_close_in_range",
            "intraday_hold_from_low",
            "next_signal",
            "next_signal_reason",
        ]
    ].copy()
    signal_review_df.to_csv(signal_review_path, index=False, encoding="utf-8-sig")

    return summary_path, trades_path, equity_path, signal_review_path


def main():
    stock = STOCK_LIST[0]
    print("Running ETF dip-buy development backtest")
    print(
        "python policy: PRICE_ADJUSTMENT={}, DATA_SOURCE={}, INDICATOR_MODE={}, USE_FINANCIAL_DATA={}".format(
            PRICE_ADJUSTMENT, DATA_SOURCE, INDICATOR_MODE, USE_FINANCIAL_DATA
        )
    )
    print(
        "stock={}, daily_period={}, intraday_period={}, start={}, end={}".format(
            stock, DAILY_PERIOD, INTRADAY_PERIOD, START_DATE, END_DATE
        )
    )

    ensure_history_download(STOCK_LIST, DAILY_PERIOD, START_DATE, END_DATE)
    ensure_history_download(STOCK_LIST, INTRADAY_PERIOD, START_DATE, END_DATE)

    daily_frame = load_price_frame(stock, DAILY_PERIOD, START_DATE, END_DATE)
    intraday_frame = load_price_frame(stock, INTRADAY_PERIOD, START_DATE, END_DATE)

    daily_frame = enrich_daily_indicators(daily_frame)
    intraday_signals = build_intraday_signal_map(intraday_frame)
    instrument_detail = xtdata.get_instrument_detail(stock, iscomplete=False)

    summary, trades_df, daily_df = run_backtest(
        stock, daily_frame, intraday_signals, instrument_detail
    )
    summary_path, trades_path, equity_path, signal_review_path = save_outputs(
        summary, trades_df, daily_df
    )

    print("instrument:", instrument_detail.get("InstrumentName", stock))
    print("rows(daily):", len(daily_frame), "rows(30m):", len(intraday_frame))
    print(
        "trade_count:",
        summary["trade_count"],
        "win_rate:",
        summary["win_rate"],
        "max_drawdown:",
        summary["max_drawdown"],
    )
    print("final_equity:", summary["final_equity"], "total_return:", summary["total_return"])
    print("outputs:")
    print(" -", summary_path)
    print(" -", trades_path)
    print(" -", equity_path)
    print(" -", signal_review_path)


if __name__ == "__main__":
    main()
