#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd

from xtquant import xtdata


# Backtest data policy: keep these explicit and stable.
PRICE_ADJUSTMENT = "front"
DATA_SOURCE = "local"
USE_FINANCIAL_DATA = False
INDICATOR_MODE = "python"

STOCK_LIST = ["600519.SH"]
BENCHMARK_SECTOR = "上证A股"
PERIOD = "1d"
START_DATE = "20240101"
END_DATE = "20260422"

INITIAL_CASH = 100000.0
LOT_SIZE = 100

MA_SHORT = 5
MA_LONG = 20
RSI_PERIOD = 14
VOLUME_MA_PERIOD = 5
BUY_RSI_THRESHOLD = 55.0
SELL_RSI_THRESHOLD = 45.0
MAX_HOLD_DAYS = 10

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
    if frame is None or frame.empty:
        raise RuntimeError("no local history data returned for {}".format(stock))

    frame = frame.copy()
    frame.index = frame.index.astype(str)
    frame.index.name = "trade_date"
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


def enrich_indicators(frame):
    enriched = frame.copy()
    enriched["ma_short"] = enriched["close"].rolling(MA_SHORT, min_periods=MA_SHORT).mean()
    enriched["ma_long"] = enriched["close"].rolling(MA_LONG, min_periods=MA_LONG).mean()
    enriched["rsi"] = compute_rsi(enriched["close"], RSI_PERIOD)
    enriched["volume_ma"] = enriched["volume"].rolling(
        VOLUME_MA_PERIOD, min_periods=VOLUME_MA_PERIOD
    ).mean()
    return enriched


def can_buy(row):
    if pd.isna(row["ma_short"]) or pd.isna(row["ma_long"]) or pd.isna(row["volume_ma"]):
        return False, "指标窗口不足"
    if row["close"] <= row["ma_short"]:
        return False, "收盘价未站上短均线"
    if row["ma_short"] <= row["ma_long"]:
        return False, "短均线未站上长均线"
    if row["rsi"] < BUY_RSI_THRESHOLD:
        return False, "RSI 未超过买入阈值"
    if row["volume"] <= row["volume_ma"]:
        return False, "成交量未高于量均线"
    return True, "价格强于均线且量能确认"


def should_sell(row, holding_days):
    if row["close"] < row["ma_short"]:
        return True, "收盘价跌破短均线"
    if row["rsi"] < SELL_RSI_THRESHOLD:
        return True, "RSI 跌破卖出阈值"
    if holding_days >= MAX_HOLD_DAYS:
        return True, "持有天数达到上限"
    return False, ""


def run_backtest(stock, frame, instrument_detail):
    cash = INITIAL_CASH
    shares = 0
    entry_price = 0.0
    entry_date = None

    trades = []
    daily = []

    for trade_date, row in frame.iterrows():
        close_price = float(row["close"])
        action = "hold"
        reason = "无信号"

        if shares > 0:
            holding_days = frame.index.get_loc(trade_date) - frame.index.get_loc(entry_date) + 1
            sell_flag, sell_reason = should_sell(row, holding_days)
            if sell_flag:
                proceeds = shares * close_price
                cash += proceeds
                trades.append(
                    {
                        "trade_date": trade_date,
                        "stock": stock,
                        "action": "sell",
                        "price": round(close_price, 4),
                        "shares": shares,
                        "amount": round(proceeds, 2),
                        "reason": sell_reason,
                    }
                )
                action = "sell"
                reason = sell_reason
                shares = 0
                entry_price = 0.0
                entry_date = None
            else:
                reason = "继续持有"

        if shares == 0:
            buy_flag, buy_reason = can_buy(row)
            if buy_flag:
                max_shares = int(cash / close_price / LOT_SIZE) * LOT_SIZE
                if max_shares > 0:
                    cost = max_shares * close_price
                    cash -= cost
                    shares = max_shares
                    entry_price = close_price
                    entry_date = trade_date
                    trades.append(
                        {
                            "trade_date": trade_date,
                            "stock": stock,
                            "action": "buy",
                            "price": round(close_price, 4),
                            "shares": max_shares,
                            "amount": round(cost, 2),
                            "reason": buy_reason,
                        }
                    )
                    action = "buy"
                    reason = buy_reason
                else:
                    reason = "现金不足以买入一手"

        market_value = shares * close_price
        total_equity = cash + market_value

        daily.append(
            {
                "trade_date": trade_date,
                "stock": stock,
                "close": round(close_price, 4),
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
                "action": action,
                "reason": reason,
            }
        )

    daily_df = pd.DataFrame(daily)
    trades_df = pd.DataFrame(trades)

    final_equity = float(daily_df.iloc[-1]["total_equity"])
    total_return = (final_equity / INITIAL_CASH) - 1

    summary = {
        "stock": stock,
        "instrument_name": instrument_detail.get("InstrumentName", ""),
        "price_adjustment": PRICE_ADJUSTMENT,
        "data_source": DATA_SOURCE,
        "indicator_mode": INDICATOR_MODE,
        "use_financial_data": USE_FINANCIAL_DATA,
        "period": PERIOD,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "initial_cash": INITIAL_CASH,
        "final_equity": round(final_equity, 2),
        "total_return": round(total_return, 6),
        "trade_count": int(len(trades_df)),
        "benchmark_sector": BENCHMARK_SECTOR,
    }
    return summary, trades_df, daily_df


def save_outputs(summary, trades_df, daily_df):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summary_path = OUTPUT_DIR / "summary.json"
    trades_path = OUTPUT_DIR / "trades.csv"
    equity_path = OUTPUT_DIR / "daily_equity.csv"

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    trades_df.to_csv(trades_path, index=False, encoding="utf-8-sig")
    daily_df.to_csv(equity_path, index=False, encoding="utf-8-sig")

    return summary_path, trades_path, equity_path


def main():
    stock = STOCK_LIST[0]
    print("Running minimal stock backtest")
    print("python policy: PRICE_ADJUSTMENT={}, DATA_SOURCE={}, INDICATOR_MODE={}, USE_FINANCIAL_DATA={}".format(
        PRICE_ADJUSTMENT, DATA_SOURCE, INDICATOR_MODE, USE_FINANCIAL_DATA
    ))
    print("stock={}, period={}, start={}, end={}".format(stock, PERIOD, START_DATE, END_DATE))

    ensure_history_download(STOCK_LIST, PERIOD, START_DATE, END_DATE)
    frame = load_price_frame(stock, PERIOD, START_DATE, END_DATE)
    frame = enrich_indicators(frame)
    instrument_detail = xtdata.get_instrument_detail(stock, iscomplete=False)

    summary, trades_df, daily_df = run_backtest(stock, frame, instrument_detail)
    summary_path, trades_path, equity_path = save_outputs(summary, trades_df, daily_df)

    print("instrument:", instrument_detail.get("InstrumentName", stock))
    print("rows:", len(frame), "trades:", len(trades_df))
    print("final_equity:", summary["final_equity"], "total_return:", summary["total_return"])
    print("outputs:")
    print(" -", summary_path)
    print(" -", trades_path)
    print(" -", equity_path)


if __name__ == "__main__":
    main()
