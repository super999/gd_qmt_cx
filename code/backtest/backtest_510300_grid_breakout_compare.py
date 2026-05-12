#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd
from xtquant import xtdata

import minimal_stock_backtest as base
from analyze_best_interval_entry_signals import END_DATE, START_DATE, STOCK, load_daily_frame


PRICE_ADJUSTMENT = "front"
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "backtest_510300_grid_breakout_compare"
REPORT_PATH = Path("报告/研究结论/当前主线/510300网格与右侧突破对比回测.md")

GRID_LOOKBACK_DAYS = 20
GRID_LEVEL_COUNT = 4
GRID_MIN_RANGE_PCT = 0.025
GRID_MAX_RANGE_PCT = 0.12
GRID_MAX_MA20_SLOPE_ABS = 0.025
GRID_MAX_HOLD_DAYS = 3

BREAKOUT_LOOKBACK_DAYS = 20
BREAKOUT_NEAR_HIGH_PCT = 0.985
BREAKOUT_MORNING_BARS = 12
BREAKOUT_VOLUME_LOOKBACK = 8
BREAKOUT_VOLUME_RATIO_MIN = 1.2
BREAKOUT_MAX_HOLD_DAYS = 5
BREAKOUT_STOP_LOSS_PCT = 0.02
BREAKOUT_TAKE_PROFIT_PCT = 0.035
BREAKOUT_FAIL_TOLERANCE = 0.005


def ensure_history():
    for period in ["1d", "5m"]:
        try:
            xtdata.download_history_data(
                STOCK,
                period=period,
                start_time="20240101",
                end_time=END_DATE,
                incrementally=False,
            )
        except Exception as exc:
            print("history download skipped period={} reason={}".format(period, exc))


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
    frame.index.name = None
    frame["bar_time"] = frame.index
    frame["trade_date"] = frame["bar_time"].str[:8]
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["open", "high", "low", "close"]).sort_values("bar_time").reset_index(drop=True)


def build_daily_context():
    try:
        raw = load_daily_frame()
    except Exception:
        raw = base.load_price_frame(STOCK, base.DAILY_PERIOD, "20240101", END_DATE).copy()
        raw.index = raw.index.astype(str)
        raw.index.name = None
        raw["trade_date"] = raw.index.str[:8]
        for col in ["open", "high", "low", "close", "volume"]:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")
        raw = raw.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    raw = raw.sort_values("trade_date").reset_index(drop=True)
    close = raw["close"].astype(float)
    high = raw["high"].astype(float)
    low = raw["low"].astype(float)
    ma20 = close.rolling(20, min_periods=20).mean()
    ctx = pd.DataFrame(
        {
            "trade_date": raw["trade_date"],
            "prev_close": close.shift(1),
            "prev_high": high.shift(1),
            "prev_20d_high": high.rolling(GRID_LOOKBACK_DAYS, min_periods=GRID_LOOKBACK_DAYS).max().shift(1),
            "prev_20d_low": low.rolling(GRID_LOOKBACK_DAYS, min_periods=GRID_LOOKBACK_DAYS).min().shift(1),
            "prev_ma20": ma20.shift(1),
            "prev_ma20_5d_ago": ma20.shift(6),
        }
    )
    ctx["prev_20d_range_pct"] = ctx["prev_20d_high"] / ctx["prev_20d_low"] - 1.0
    ctx["prev_ma20_slope_5"] = ctx["prev_ma20"] / ctx["prev_ma20_5d_ago"] - 1.0
    ctx = ctx[(ctx["trade_date"] >= START_DATE) & (ctx["trade_date"] <= END_DATE)].copy()
    return ctx.reset_index(drop=True)


def split_5m_by_date(data_5m):
    return {date: group.copy().reset_index(drop=True) for date, group in data_5m.groupby("trade_date")}


def trading_dates(daily_ctx):
    return daily_ctx["trade_date"].astype(str).tolist()


def last_bar_index_for_exit(data_5m, dates, entry_date, hold_days):
    if entry_date not in dates:
        return None
    target_pos = min(dates.index(entry_date) + int(hold_days) - 1, len(dates) - 1)
    target_date = dates[target_pos]
    candidates = data_5m.index[data_5m["trade_date"].astype(str) == target_date].tolist()
    return candidates[-1] if candidates else None


def execute_next_bar(data_5m, trigger_idx):
    entry_idx = int(trigger_idx) + 1
    if entry_idx >= len(data_5m):
        return None
    row = data_5m.iloc[entry_idx]
    return entry_idx, float(row["open"]), str(row["bar_time"]), str(row["trade_date"])


def trade_window_metrics(data_5m, entry_idx, exit_idx, entry_price):
    window = data_5m.iloc[int(entry_idx) : int(exit_idx) + 1]
    return {
        "mae_pct": round(float(window["low"].min()) / entry_price - 1.0, 6),
        "mfe_pct": round(float(window["high"].max()) / entry_price - 1.0, 6),
    }


def holding_trade_days(dates, entry_date, exit_date):
    if entry_date not in dates or exit_date not in dates:
        return None
    return dates.index(exit_date) - dates.index(entry_date) + 1


def grid_signal_level(close, prev_close, low_bound, high_bound):
    step = (high_bound - low_bound) / GRID_LEVEL_COUNT
    for level_no in range(1, GRID_LEVEL_COUNT):
        level = high_bound - step * level_no
        if prev_close > level and close <= level:
            return level_no, level, min(level + step, high_bound), low_bound - step * 0.25
    return None


def simulate_grid(data_5m, daily_ctx):
    daily_map = {str(row["trade_date"]): row for _, row in daily_ctx.iterrows()}
    dates = trading_dates(daily_ctx)
    rows = []
    available_after_idx = -1

    for idx in range(1, len(data_5m) - 1):
        if idx <= available_after_idx:
            continue
        row = data_5m.iloc[idx]
        prev_bar = data_5m.iloc[idx - 1]
        trade_date = str(row["trade_date"])
        ctx = daily_map.get(trade_date)
        if ctx is None:
            continue

        high_bound = ctx["prev_20d_high"]
        low_bound = ctx["prev_20d_low"]
        range_pct = ctx["prev_20d_range_pct"]
        ma20_slope = ctx["prev_ma20_slope_5"]
        if pd.isna(high_bound) or pd.isna(low_bound) or high_bound <= low_bound:
            continue
        if pd.isna(range_pct) or range_pct < GRID_MIN_RANGE_PCT or range_pct > GRID_MAX_RANGE_PCT:
            continue
        if pd.isna(ma20_slope) or abs(float(ma20_slope)) > GRID_MAX_MA20_SLOPE_ABS:
            continue

        level = grid_signal_level(float(row["close"]), float(prev_bar["close"]), float(low_bound), float(high_bound))
        if level is None:
            continue
        grid_level_no, signal_level, target_price, stop_price = level
        entry = execute_next_bar(data_5m, idx)
        if entry is None:
            continue
        entry_idx, entry_price, entry_time, entry_date = entry
        max_exit_idx = last_bar_index_for_exit(data_5m, dates, entry_date, GRID_MAX_HOLD_DAYS)
        if max_exit_idx is None:
            continue

        exit_idx = max_exit_idx
        exit_price = float(data_5m.iloc[exit_idx]["close"])
        exit_time = str(data_5m.iloc[exit_idx]["bar_time"])
        exit_date = str(data_5m.iloc[exit_idx]["trade_date"])
        exit_reason = "持有天数达到上限"
        for pos in range(entry_idx + 1, max_exit_idx + 1):
            bar = data_5m.iloc[pos]
            close = float(bar["close"])
            if close >= target_price:
                exit_idx = pos + 1 if pos + 1 < len(data_5m) else pos
                exit_bar = data_5m.iloc[exit_idx]
                exit_price = float(exit_bar["open"]) if exit_idx != pos else close
                exit_time = str(exit_bar["bar_time"])
                exit_date = str(exit_bar["trade_date"])
                exit_reason = "到达上一档网格"
                break
            if close <= stop_price:
                exit_idx = pos + 1 if pos + 1 < len(data_5m) else pos
                exit_bar = data_5m.iloc[exit_idx]
                exit_price = float(exit_bar["open"]) if exit_idx != pos else close
                exit_time = str(exit_bar["bar_time"])
                exit_date = str(exit_bar["trade_date"])
                exit_reason = "跌破区间下沿"
                break

        metrics = trade_window_metrics(data_5m, entry_idx, exit_idx, entry_price)
        rows.append(
            {
                "strategy_name": "grid",
                "strategy_label": "网格交易v1",
                "signal_time": str(row["bar_time"]),
                "signal_date": trade_date,
                "entry_time": entry_time,
                "entry_date": entry_date,
                "entry_price": round(entry_price, 4),
                "exit_time": exit_time,
                "exit_date": exit_date,
                "exit_price": round(exit_price, 4),
                "exit_reason": exit_reason,
                "return_pct": round(exit_price / entry_price - 1.0, 6),
                "holding_trade_days": holding_trade_days(dates, entry_date, exit_date),
                "grid_level_no": int(grid_level_no),
                "grid_signal_level": round(float(signal_level), 4),
                "grid_target_price": round(float(target_price), 4),
                "grid_stop_price": round(float(stop_price), 4),
                "prev_20d_range_pct": round(float(range_pct), 6),
                "prev_ma20_slope_5": round(float(ma20_slope), 6),
                **metrics,
            }
        )
        available_after_idx = int(exit_idx)

    return pd.DataFrame(rows)


def simulate_breakout(data_5m, daily_ctx):
    daily_map = {str(row["trade_date"]): row for _, row in daily_ctx.iterrows()}
    dates = trading_dates(daily_ctx)
    day_maps = split_5m_by_date(data_5m)
    rows = []
    available_after_idx = -1

    for trade_date, bars in day_maps.items():
        ctx = daily_map.get(str(trade_date))
        if ctx is None:
            continue
        prev_close = ctx["prev_close"]
        prev_high = ctx["prev_high"]
        prev_20d_high = ctx["prev_20d_high"]
        if pd.isna(prev_close) or pd.isna(prev_high) or pd.isna(prev_20d_high):
            continue
        if float(prev_close) / float(prev_20d_high) < BREAKOUT_NEAR_HIGH_PCT:
            continue
        if len(bars) <= BREAKOUT_MORNING_BARS + 1:
            continue

        morning_high = float(bars.iloc[:BREAKOUT_MORNING_BARS]["high"].max())
        trigger_level = max(morning_high, float(prev_high))
        global_indices = data_5m.index[data_5m["trade_date"].astype(str) == str(trade_date)].tolist()

        for local_pos in range(BREAKOUT_MORNING_BARS, len(bars) - 1):
            global_idx = int(global_indices[local_pos])
            if global_idx <= available_after_idx:
                continue
            bar = bars.iloc[local_pos]
            prev_bar = bars.iloc[local_pos - 1]
            if float(prev_bar["close"]) > trigger_level or float(bar["close"]) <= trigger_level:
                continue
            volume_window = bars.iloc[max(0, local_pos - BREAKOUT_VOLUME_LOOKBACK) : local_pos]
            avg_volume = float(volume_window["volume"].mean()) if not volume_window.empty else 0.0
            volume_ratio = float(bar["volume"]) / avg_volume if avg_volume else 0.0
            if volume_ratio < BREAKOUT_VOLUME_RATIO_MIN:
                continue

            entry = execute_next_bar(data_5m, global_idx)
            if entry is None:
                continue
            entry_idx, entry_price, entry_time, entry_date = entry
            max_exit_idx = last_bar_index_for_exit(data_5m, dates, entry_date, BREAKOUT_MAX_HOLD_DAYS)
            if max_exit_idx is None:
                continue

            exit_idx = max_exit_idx
            exit_price = float(data_5m.iloc[exit_idx]["close"])
            exit_time = str(data_5m.iloc[exit_idx]["bar_time"])
            exit_date = str(data_5m.iloc[exit_idx]["trade_date"])
            exit_reason = "持有天数达到上限"
            fail_price = trigger_level * (1.0 - BREAKOUT_FAIL_TOLERANCE)
            stop_price = entry_price * (1.0 - BREAKOUT_STOP_LOSS_PCT)
            take_profit_price = entry_price * (1.0 + BREAKOUT_TAKE_PROFIT_PCT)
            for pos in range(entry_idx + 1, max_exit_idx + 1):
                test_bar = data_5m.iloc[pos]
                close = float(test_bar["close"])
                if close <= fail_price:
                    exit_reason = "跌回突破位"
                elif close <= stop_price:
                    exit_reason = "固定止损"
                elif close >= take_profit_price:
                    exit_reason = "固定止盈"
                else:
                    continue
                exit_idx = pos + 1 if pos + 1 < len(data_5m) else pos
                exit_bar = data_5m.iloc[exit_idx]
                exit_price = float(exit_bar["open"]) if exit_idx != pos else close
                exit_time = str(exit_bar["bar_time"])
                exit_date = str(exit_bar["trade_date"])
                break

            metrics = trade_window_metrics(data_5m, entry_idx, exit_idx, entry_price)
            rows.append(
                {
                    "strategy_name": "breakout",
                    "strategy_label": "右侧突破v1",
                    "signal_time": str(bar["bar_time"]),
                    "signal_date": str(trade_date),
                    "entry_time": entry_time,
                    "entry_date": entry_date,
                    "entry_price": round(entry_price, 4),
                    "exit_time": exit_time,
                    "exit_date": exit_date,
                    "exit_price": round(exit_price, 4),
                    "exit_reason": exit_reason,
                    "return_pct": round(exit_price / entry_price - 1.0, 6),
                    "holding_trade_days": holding_trade_days(dates, entry_date, exit_date),
                    "breakout_level": round(float(trigger_level), 4),
                    "morning_high": round(float(morning_high), 4),
                    "prev_high": round(float(prev_high), 4),
                    "prev_20d_high": round(float(prev_20d_high), 4),
                    "volume_ratio": round(float(volume_ratio), 6),
                    **metrics,
                }
            )
            available_after_idx = int(exit_idx)
            break

    return pd.DataFrame(rows)


def max_drawdown_from_returns(returns):
    if returns.empty:
        return None
    equity = (1.0 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def summarize_window(trades, strategy_name, window_label, start_date, end_date):
    label = "网格交易v1" if strategy_name == "grid" else "右侧突破v1"
    if trades.empty or "strategy_name" not in trades.columns:
        return {
            "window": window_label,
            "start_date": start_date,
            "end_date": end_date,
            "strategy_name": strategy_name,
            "strategy_label": label,
            "trade_count": 0,
            "win_rate": None,
            "compounded_return": 0.0,
            "max_drawdown": None,
            "avg_holding_trade_days": None,
            "avg_mae": None,
            "avg_mfe": None,
        }
    selected = trades[
        (trades["strategy_name"] == strategy_name)
        & (trades["entry_date"].astype(str) >= start_date)
        & (trades["entry_date"].astype(str) <= end_date)
    ].copy()
    if selected.empty:
        return {
            "window": window_label,
            "start_date": start_date,
            "end_date": end_date,
            "strategy_name": strategy_name,
            "strategy_label": label,
            "trade_count": 0,
            "win_rate": None,
            "compounded_return": 0.0,
            "max_drawdown": None,
            "avg_holding_trade_days": None,
            "avg_mae": None,
            "avg_mfe": None,
        }
    returns = pd.to_numeric(selected["return_pct"], errors="coerce")
    return {
        "window": window_label,
        "start_date": start_date,
        "end_date": end_date,
        "strategy_name": strategy_name,
        "strategy_label": label,
        "trade_count": int(len(selected)),
        "win_rate": round(float((returns > 0).mean()), 6),
        "compounded_return": round(float((1.0 + returns).prod() - 1.0), 6),
        "max_drawdown": round(max_drawdown_from_returns(returns), 6),
        "avg_holding_trade_days": round(float(pd.to_numeric(selected["holding_trade_days"], errors="coerce").mean()), 3),
        "avg_mae": round(float(pd.to_numeric(selected["mae_pct"], errors="coerce").mean()), 6),
        "avg_mfe": round(float(pd.to_numeric(selected["mfe_pct"], errors="coerce").mean()), 6),
    }


def build_summary(all_trades, daily_ctx):
    end_date = daily_ctx["trade_date"].astype(str).max()
    unique_dates = daily_ctx["trade_date"].astype(str).tolist()
    windows = [
        ("最近3个月", unique_dates[max(0, len(unique_dates) - 63)], end_date),
        ("最近6个月", unique_dates[max(0, len(unique_dates) - 126)], end_date),
        ("近一年", START_DATE, end_date),
    ]
    rows = []
    for window_label, start_date, window_end in windows:
        for strategy_name in ["grid", "breakout"]:
            rows.append(summarize_window(all_trades, strategy_name, window_label, start_date, window_end))
    return pd.DataFrame(rows)


def choose_recent_bias(summary):
    recent = summary[summary["window"] == "最近3个月"].copy()
    if recent.empty:
        return "两者都不适合，继续观察"
    valid = recent[(recent["trade_count"] >= 2) & (recent["compounded_return"] > 0) & (recent["max_drawdown"].fillna(-1.0) >= -0.04)]
    if valid.empty:
        return "两者都不适合，继续观察"
    ranked = valid.sort_values(["compounded_return", "win_rate"], ascending=[False, False])
    best = str(ranked.iloc[0]["strategy_name"])
    if best == "grid":
        return "近期偏网格"
    if best == "breakout":
        return "近期偏突破"
    return "两者都不适合，继续观察"


def pct_table(frame):
    table = frame.copy()
    for col in ["win_rate", "compounded_return", "max_drawdown", "avg_mae", "avg_mfe"]:
        if col in table.columns:
            table[col] = table[col].map(lambda value: "" if pd.isna(value) else "{:.2%}".format(float(value)))
    return table


def markdown_table(frame):
    if frame.empty:
        return "_无数据_"
    columns = frame.columns.tolist()
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in columns) + " |")
    return "\n".join(lines)


def write_report(summary, grid_trades, breakout_trades, bias):
    lines = [
        "# 510300 网格与右侧突破对比回测",
        "",
        "## 验证边界",
        "",
        "- 本报告是独立实验分支，不替换当前低吸反弹主线策略。",
        "- 标的固定为 `510300.SH`，只做多，单仓位，不补仓，不真实下单。",
        "- 数据口径沿用本项目外部 Python 回测约束：前复权、本地行情、Python 自算指标。",
        "- 近期策略倾向只来自本地历史回测结果，不构成投资建议。",
        "",
        "## 规则摘要",
        "",
        "- 网格交易 v1：以前 20 个交易日高低点定义区间，切成 4 档；仅在区间振幅适中且 MA20 斜率不明显单边时启用，跌入下方网格后下一根 5m 开盘模拟买入。",
        "- 右侧突破 v1：前一日收盘接近 20 日高点时启用，5m 放量突破早盘高点或前一日高点后，下一根 5m 开盘模拟买入。",
        "",
        "## 分段对比摘要",
        "",
        markdown_table(pct_table(summary)),
        "",
        "## 近期倾向",
        "",
        "- `{}`。".format(bias),
        "- 判断规则：最近 3 个月内，交易次数至少 2 次、复合收益为正、最大回撤不低于 -4%；满足条件后按复合收益和胜率排序。",
        "",
        "## 交易数量",
        "",
        "- 网格交易逐笔数：`{}`。".format(len(grid_trades)),
        "- 右侧突破逐笔数：`{}`。".format(len(breakout_trades)),
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_history()
    try:
        daily_ctx = build_daily_context()
        data_5m = load_5m_frame()
    except Exception as exc:
        raise SystemExit("无法读取 xtquant 行情数据，请先开启 QMT 后重试：{}".format(exc))
    grid_trades = simulate_grid(data_5m, daily_ctx)
    breakout_trades = simulate_breakout(data_5m, daily_ctx)
    all_trades = pd.concat([grid_trades, breakout_trades], ignore_index=True)
    summary = build_summary(all_trades, daily_ctx)
    bias = choose_recent_bias(summary)
    meta = {
        "stock": STOCK,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "price_adjustment": PRICE_ADJUSTMENT,
        "grid_trade_count": int(len(grid_trades)),
        "breakout_trade_count": int(len(breakout_trades)),
        "recent_bias": bias,
    }

    grid_trades.to_csv(OUTPUT_DIR / "grid_trades.csv", index=False, encoding="utf-8-sig")
    breakout_trades.to_csv(OUTPUT_DIR / "breakout_trades.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(OUTPUT_DIR / "strategy_compare_summary.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(summary, grid_trades, breakout_trades, bias)

    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(pct_table(summary).to_string(index=False))


if __name__ == "__main__":
    main()
