#!/usr/bin/env python3
# coding: utf-8

"""
510300 盘中低位承接模拟监控程序。

当前实现两种模式：
- replay 历史回放模式
- live 实时监控模式

replay：
- 按指定日期区间读取本地日线和 5m 数据
- 逐日重放已确认的盘中弱势低位-量能修复信号
- 输出预警、候选买入、模拟买入、退出提醒日志

live：
- 订阅 510300.SH 的 5m 行情
- 定时拉取最新 5m K 线
- 输出买入预警、候选买入提示、模拟买入、退出提示
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from xtquant import xtdata


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKTEST_DIR = ROOT_DIR / "code" / "backtest"
if str(BACKTEST_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTEST_DIR))

import minimal_stock_backtest as base  # noqa: E402
from analyze_best_interval_entry_signals import END_DATE, START_DATE, STOCK, add_daily_features  # noqa: E402
from backtest_intraday_entry_offsets import build_bar_index  # noqa: E402
from backtest_intraday_statistical_warning import (  # noqa: E402
    RULES,
    build_daily_dataset,
    ensure_history,
    load_5m_frame,
    partial_intraday_features,
    replay_intraday_signals,
    rule_passes,
)


ENTRY_RULE_NAME = "intraday_weak_volume_repair"
ENTRY_RULE_LABEL = "盘中弱势低位-量能修复"
WATCH_ENTRY_OFFSETS = [2, 3]
PRIMARY_ENTRY_OFFSET = 3
EXIT_HOLD_DAYS = 3

OUTPUT_DIR = ROOT_DIR / "code" / "run_qmt" / "outputs" / "intraday_low_absorb_monitor"
REPORT_PATH = ROOT_DIR / "报告" / "研究结论" / "当前主线" / "510300盘中模拟监控历史回放.md"
SUMMARY_CSV = ROOT_DIR / "报告" / "研究结论" / "数据摘要" / "510300盘中模拟监控历史回放摘要.csv"
EVENT_CSV = ROOT_DIR / "报告" / "研究结论" / "数据摘要" / "510300盘中模拟监控历史回放事件日志.csv"
LIVE_EVENT_CSV = OUTPUT_DIR / "live_event_log.csv"
LIVE_STATE_PATH = OUTPUT_DIR / "live_state.json"
LIVE_EXIT_TIME = "145500"
LIVE_BAR_COUNT = 160


def parse_args():
    parser = argparse.ArgumentParser(description="510300 盘中低位承接模拟监控程序")
    parser.add_argument("--mode", choices=["replay", "live"], default="replay")
    parser.add_argument("--stock", default=STOCK)
    parser.add_argument("--start-date", default=START_DATE)
    parser.add_argument("--end-date", default=END_DATE)
    parser.add_argument("--primary-entry-offset", type=int, default=PRIMARY_ENTRY_OFFSET)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--max-loops", type=int, default=0, help="live 模式循环次数；0 表示一直运行")
    parser.add_argument("--live-date", default="", help="live 调试用交易日，默认按最新 5m K 线日期")
    parser.add_argument("--state-file", default=str(LIVE_STATE_PATH))
    parser.add_argument("--reset-state", action="store_true")
    return parser.parse_args()


def last_bar_for_date(data_5m, trade_date):
    matched = data_5m[data_5m["trade_date"].astype(str) == str(trade_date)]
    if matched.empty:
        return None
    return matched.iloc[-1]


def last_bar_idx_for_date(data_5m, trade_date):
    matched = data_5m[data_5m["trade_date"].astype(str) == str(trade_date)]
    if matched.empty:
        return None
    return int(matched.index.max())


def trading_exit_date(daily_dates, entry_date, hold_days):
    entry_date = str(entry_date)
    if entry_date not in daily_dates:
        return None
    entry_pos = daily_dates.index(entry_date)
    exit_pos = entry_pos + int(hold_days) - 1
    if exit_pos >= len(daily_dates):
        return None
    return daily_dates[exit_pos]


def fmt_pct(value):
    if value is None or pd.isna(value):
        return ""
    return "{:.2%}".format(float(value))


def add_event(rows, **kwargs):
    defaults = {
        "event_time": "",
        "trade_date": "",
        "event_type": "",
        "event_label": "",
        "stock": STOCK,
        "price": None,
        "entry_offset_bars": None,
        "position_id": None,
        "status": "",
        "message": "",
    }
    defaults.update(kwargs)
    rows.append(defaults)


def condition_message(signal):
    parts = [
        "前10日回撤={}".format(fmt_pct(signal.get("pre_drawdown_from_high_10"))),
        "昨收偏离MA10={}".format(fmt_pct(signal.get("pre_close_vs_ma10"))),
        "前日RSI6={:.2f}".format(float(signal.get("pre_rsi6"))) if pd.notna(signal.get("pre_rsi6")) else "前日RSI6=",
        "估算日内位置={}".format(fmt_pct(signal.get("est_day_close_in_range"))),
        "估算当日涨跌={}".format(fmt_pct(signal.get("est_day_return"))),
        "低点位置={}".format(fmt_pct(signal.get("m5_low_pos_ratio"))),
        "低点后量能修复={}".format(fmt_pct(signal.get("m5_volume_ratio_after_low"))),
    ]
    return "；".join(parts)


def build_replay_events(daily, data_5m, start_date, end_date, primary_entry_offset):
    signals = replay_intraday_signals(daily, data_5m)
    signals = signals[
        (signals["rule_name"] == ENTRY_RULE_NAME)
        & (signals["trade_date"].astype(str) >= str(start_date))
        & (signals["trade_date"].astype(str) <= str(end_date))
    ].copy()
    signals = signals.sort_values("signal_time").reset_index(drop=True)

    bar_index = build_bar_index(data_5m)
    daily_dates = daily["trade_date"].astype(str).tolist()
    events = []
    trades = []
    available_after_idx = -1
    position_id = 0

    for _, signal in signals.iterrows():
        signal_time = str(signal["signal_time"])
        signal_date = str(signal["trade_date"])
        signal_bar_idx = bar_index.get(signal_time)
        if signal_bar_idx is None:
            continue

        add_event(
            events,
            event_time=signal_time,
            trade_date=signal_date,
            event_type="BUY_WARNING",
            event_label="买入预警",
            price=round(float(signal["signal_price"]), 4),
            status="已触发",
            message="{}：{}".format(ENTRY_RULE_LABEL, condition_message(signal)),
        )

        primary_entry = None
        for offset in WATCH_ENTRY_OFFSETS:
            entry_idx = signal_bar_idx + offset
            if entry_idx >= len(data_5m):
                continue
            entry_bar = data_5m.iloc[entry_idx]
            entry_date = str(entry_bar["trade_date"])
            if entry_date != signal_date:
                add_event(
                    events,
                    event_time=signal_time,
                    trade_date=signal_date,
                    event_type="SKIP_ENTRY",
                    event_label="候选买入跳过",
                    entry_offset_bars=offset,
                    status="当天剩余K线不足",
                    message="信号后第 {} 根 5m K 线已跨交易日，不买。".format(offset),
                )
                continue

            add_event(
                events,
                event_time=str(entry_bar["bar_time"]),
                trade_date=entry_date,
                event_type="ENTRY_CANDIDATE",
                event_label="候选买入提示",
                price=round(float(entry_bar["open"]), 4),
                entry_offset_bars=offset,
                status="可人工观察",
                message="信号后第 {} 根 5m K 线开盘，候选买入价={}。".format(
                    offset,
                    round(float(entry_bar["open"]), 4),
                ),
            )

            if offset == primary_entry_offset:
                primary_entry = (entry_idx, entry_bar)

        if primary_entry is None:
            continue

        entry_idx, entry_bar = primary_entry
        entry_date = str(entry_bar["trade_date"])
        entry_time = str(entry_bar["bar_time"])
        entry_price = float(entry_bar["open"])
        exit_date = trading_exit_date(daily_dates, entry_date, EXIT_HOLD_DAYS)
        if exit_date is None:
            continue
        exit_bar = last_bar_for_date(data_5m, exit_date)
        exit_idx = last_bar_idx_for_date(data_5m, exit_date)
        if exit_bar is None or exit_idx is None:
            continue

        if entry_idx <= available_after_idx:
            add_event(
                events,
                event_time=entry_time,
                trade_date=entry_date,
                event_type="SKIP_BUY",
                event_label="模拟买入跳过",
                price=round(entry_price, 4),
                entry_offset_bars=primary_entry_offset,
                status="已有模拟持仓",
                message="当前已有模拟持仓未退出，本次只记录预警，不重复开仓。",
            )
            continue

        position_id += 1
        exit_time = str(exit_bar["bar_time"])
        exit_price = float(exit_bar["close"])
        return_pct = exit_price / entry_price - 1.0

        add_event(
            events,
            event_time=entry_time,
            trade_date=entry_date,
            event_type="SIM_BUY",
            event_label="模拟买入",
            price=round(entry_price, 4),
            entry_offset_bars=primary_entry_offset,
            position_id=position_id,
            status="开仓",
            message="按主口径：信号后第 {} 根 5m K 线开盘模拟买入；计划第 {} 个交易日尾盘退出。".format(
                primary_entry_offset,
                EXIT_HOLD_DAYS,
            ),
        )
        add_event(
            events,
            event_time=exit_time,
            trade_date=exit_date,
            event_type="EXIT_REMINDER",
            event_label="退出提示",
            price=round(exit_price, 4),
            position_id=position_id,
            status="时间退出",
            message="第 {} 个交易日尾盘退出提示；模拟收益={}。".format(
                EXIT_HOLD_DAYS,
                fmt_pct(return_pct),
            ),
        )

        window = data_5m.iloc[entry_idx : exit_idx + 1]
        trades.append(
            {
                "position_id": position_id,
                "signal_time": signal_time,
                "entry_time": entry_time,
                "entry_date": entry_date,
                "entry_price": round(entry_price, 4),
                "exit_time": exit_time,
                "exit_date": exit_date,
                "exit_price": round(exit_price, 4),
                "return_pct": round(return_pct, 6),
                "mae_pct": round(float(window["low"].min()) / entry_price - 1.0, 6),
                "mfe_pct": round(float(window["high"].max()) / entry_price - 1.0, 6),
            }
        )
        available_after_idx = exit_idx

    event_df = pd.DataFrame(events).sort_values(["event_time", "event_type"]).reset_index(drop=True)
    trade_df = pd.DataFrame(trades)
    return signals, event_df, trade_df


def summarize_trade_df(trades):
    if trades.empty:
        return pd.DataFrame(
            [
                {
                    "trade_count": 0,
                    "month_count": 0,
                    "win_rate": 0.0,
                    "avg_return": 0.0,
                    "median_return": 0.0,
                    "compounded_return": 0.0,
                    "min_return": 0.0,
                    "avg_mae": 0.0,
                    "worst_mae": 0.0,
                    "avg_mfe": 0.0,
                }
            ]
        )
    returns = pd.to_numeric(trades["return_pct"], errors="coerce")
    maes = pd.to_numeric(trades["mae_pct"], errors="coerce")
    mfes = pd.to_numeric(trades["mfe_pct"], errors="coerce")
    return pd.DataFrame(
        [
            {
                "trade_count": int(len(trades)),
                "month_count": int(trades["signal_time"].astype(str).str[:6].nunique()),
                "win_rate": round(float((returns > 0).mean()), 6),
                "avg_return": round(float(returns.mean()), 6),
                "median_return": round(float(returns.median()), 6),
                "compounded_return": round(float((1.0 + returns).prod() - 1.0), 6),
                "min_return": round(float(returns.min()), 6),
                "avg_mae": round(float(maes.mean()), 6),
                "worst_mae": round(float(maes.min()), 6),
                "avg_mfe": round(float(mfes.mean()), 6),
            }
        ]
    )


def pct_table(frame):
    table = frame.copy()
    for col in [
        "win_rate",
        "avg_return",
        "median_return",
        "compounded_return",
        "min_return",
        "avg_mae",
        "worst_mae",
        "avg_mfe",
    ]:
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


def write_report(args, signals, events, trades, summary):
    event_counts = events.groupby(["event_type", "event_label"]).size().reset_index(name="count")
    sample_cols = [
        "event_time",
        "event_label",
        "price",
        "entry_offset_bars",
        "position_id",
        "status",
        "message",
    ]
    sample_events = events[sample_cols].head(30).copy()
    lines = [
        "# 510300 盘中模拟监控历史回放",
        "",
        "## 路线位置",
        "",
        "当前完整路线是：1 日线优质区间扫描、2 统计共同信号、3 候选买点规则回测、4 盘中真实买入口径验证、5 卖点设计；五步研究已初版闭环，本报告属于下一阶段的盘中模拟监控程序 replay 验证。",
        "",
        "## 运行口径",
        "",
        "- 模式：历史回放 replay。",
        "- 标的：`{}`。".format(args.stock),
        "- 日期：`{}` 至 `{}`。".format(args.start_date, args.end_date),
        "- 买入预警：`{}`。".format(ENTRY_RULE_LABEL),
        "- 候选买入提示：信号后第 2 和第 3 根 5m K 线开盘。",
        "- 模拟持仓主口径：信号后第 {} 根 5m K 线开盘买。".format(args.primary_entry_offset),
        "- 退出提示：第 {} 个交易日尾盘时间退出。".format(EXIT_HOLD_DAYS),
        "- 本程序只输出日志和模拟提示，不自动下单。",
        "",
        "## 事件数量",
        "",
        markdown_table(event_counts),
        "",
        "## 模拟持仓摘要",
        "",
        markdown_table(pct_table(summary)),
        "",
        "## 前 30 条事件日志样例",
        "",
        markdown_table(sample_events),
        "",
        "## 使用边界",
        "",
        "- replay 模式用于验证监控程序在历史行情中是否会按正确时间提示，不代表真实盘中延迟和成交可得性。",
        "- 当前只固定第 3 根 5m K 线为模拟开仓主口径，第 2 根 5m K 线先作为人工观察提示保留。",
        "- 下一步可在本程序基础上补 live 模式，接 MiniQMT 实时 5m K 线或定时拉取最新 K 线。",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def run_replay(args):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not args.skip_download:
        ensure_history()

    daily = build_daily_dataset()
    data_5m = load_5m_frame()
    signals, events, trades = build_replay_events(
        daily=daily,
        data_5m=data_5m,
        start_date=args.start_date,
        end_date=args.end_date,
        primary_entry_offset=args.primary_entry_offset,
    )
    summary = summarize_trade_df(trades)

    events.to_csv(OUTPUT_DIR / "replay_event_log.csv", index=False, encoding="utf-8-sig")
    signals.to_csv(OUTPUT_DIR / "replay_warning_signals.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(OUTPUT_DIR / "replay_simulated_trades.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(OUTPUT_DIR / "replay_summary.csv", index=False, encoding="utf-8-sig")

    EVENT_CSV.parent.mkdir(parents=True, exist_ok=True)
    events.to_csv(EVENT_CSV, index=False, encoding="utf-8-sig")
    trades.to_csv(
        ROOT_DIR / "报告" / "研究结论" / "数据摘要" / "510300盘中模拟监控历史回放模拟持仓.csv",
        index=False,
        encoding="utf-8-sig",
    )
    summary.to_csv(SUMMARY_CSV, index=False, encoding="utf-8-sig")
    write_report(args, signals, events, trades, summary)

    meta = {
        "mode": "replay",
        "stock": args.stock,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "entry_rule_name": ENTRY_RULE_NAME,
        "watch_entry_offsets": WATCH_ENTRY_OFFSETS,
        "primary_entry_offset": args.primary_entry_offset,
        "exit_hold_days": EXIT_HOLD_DAYS,
        "signal_count": int(len(signals)),
        "event_count": int(len(events)),
        "simulated_trade_count": int(len(trades)),
    }
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(pct_table(summary).to_string(index=False))


def normalize_price_frame(frame):
    if frame is None or frame.empty:
        return pd.DataFrame()
    working = frame.copy()
    working.index = working.index.astype(str)
    working["bar_time"] = working.index.astype(str)
    working["trade_date"] = working["bar_time"].str[:8]
    for col in ["open", "high", "low", "close", "volume"]:
        if col in working.columns:
            working[col] = pd.to_numeric(working[col], errors="coerce")
    return working.dropna(subset=["open", "high", "low", "close"]).sort_values("bar_time").reset_index(drop=True)


def load_live_daily_context(stock, current_date):
    base.ensure_history_download([stock], base.DAILY_PERIOD, "20240101", current_date)
    raw = base.load_price_frame(stock, base.DAILY_PERIOD, "20240101", current_date).copy()
    raw.index = raw.index.astype(str)
    raw.index.name = None
    raw["trade_date"] = raw.index.str[:8]
    for col in ["open", "high", "low", "close", "volume"]:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw = raw.dropna(subset=["open", "high", "low", "close"]).sort_values("trade_date").reset_index(drop=True)
    if raw.empty:
        raise RuntimeError("no daily data for {}".format(stock))

    current_date = str(current_date)
    if current_date not in set(raw["trade_date"].astype(str)):
        prev = raw.iloc[-1].copy()
        dummy = prev.copy()
        dummy["trade_date"] = current_date
        dummy["open"] = prev["close"]
        dummy["high"] = prev["close"]
        dummy["low"] = prev["close"]
        dummy["close"] = prev["close"]
        dummy["volume"] = 0
        raw = pd.concat([raw, pd.DataFrame([dummy])], ignore_index=True)

    features = add_daily_features(raw)
    dataset = raw.merge(features, on="trade_date", how="left")
    dataset = dataset.sort_values("trade_date").reset_index(drop=True)
    return dataset


def load_live_5m_frame(stock, count=LIVE_BAR_COUNT):
    data = xtdata.get_market_data_ex(
        [],
        [stock],
        period="5m",
        count=count,
        dividend_type="front",
        fill_data=True,
    )
    frame = data.get(stock)
    normalized = normalize_price_frame(frame)
    if normalized.empty:
        raise RuntimeError("no live 5m data for {}".format(stock))
    return normalized


def selected_entry_rule():
    for rule in RULES:
        if rule["name"] == ENTRY_RULE_NAME:
            return rule
    raise RuntimeError("entry rule not found: {}".format(ENTRY_RULE_NAME))


def latest_live_signal(daily, data_5m, trade_date):
    trade_date = str(trade_date)
    bars = data_5m[data_5m["trade_date"].astype(str) == trade_date].copy().reset_index(drop=True)
    if bars.empty:
        return None, "当天暂无 5m K 线"

    daily = daily.sort_values("trade_date").reset_index(drop=True)
    date_to_idx = {str(row["trade_date"]): idx for idx, row in daily.iterrows()}
    current_idx = date_to_idx.get(trade_date)
    if current_idx is None or current_idx == 0:
        return None, "缺少前一交易日日线背景"

    day = daily.iloc[current_idx]
    prev_close = float(daily.iloc[current_idx - 1]["close"])
    pos = len(bars) - 1
    intraday_features = partial_intraday_features(bars, prev_close, pos)
    features = {
        "rule_name": ENTRY_RULE_NAME,
        "rule_label": ENTRY_RULE_LABEL,
        "trade_date": trade_date,
        "signal_time": str(bars.iloc[pos]["bar_time"]),
        "pre_drawdown_from_high_10": day["pre_drawdown_from_high_10"],
        "pre_close_vs_ma10": day["pre_close_vs_ma10"],
        "pre_rsi6": day["pre_rsi6"],
    }
    features.update(intraday_features)
    if rule_passes(selected_entry_rule(), features):
        return features, "已触发"
    return features, "未触发"


def load_state(path):
    state_path = Path(path)
    if not state_path.exists():
        return {
            "warning_signal_time": "",
            "warning_bar_time": "",
            "entry_candidates_emitted": [],
            "position": None,
            "exit_reminder_emitted": False,
            "last_status_time": "",
        }
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "warning_signal_time": "",
            "warning_bar_time": "",
            "entry_candidates_emitted": [],
            "position": None,
            "exit_reminder_emitted": False,
            "last_status_time": "",
        }


def save_state(path, state):
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def append_live_event(event):
    LIVE_EVENT_CSV.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([event])
    header = not LIVE_EVENT_CSV.exists()
    frame.to_csv(LIVE_EVENT_CSV, mode="a", header=header, index=False, encoding="utf-8-sig")


def emit_live_event(**kwargs):
    rows = []
    add_event(rows, **kwargs)
    event = rows[0]
    event["local_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    append_live_event(event)
    print("[{}] {} {} price={} status={} {}".format(
        event["local_time"],
        event["event_time"],
        event["event_label"],
        event["price"],
        event["status"],
        event["message"],
    ))
    return event


def trade_dates_seen(daily, data_5m):
    dates = set(daily["trade_date"].astype(str).tolist())
    dates.update(data_5m["trade_date"].astype(str).tolist())
    return sorted(dates)


def maybe_emit_live_events(args, state, daily, data_5m, trade_date):
    trade_date = str(trade_date)
    bars = data_5m[data_5m["trade_date"].astype(str) == trade_date].copy().reset_index(drop=True)
    if bars.empty:
        return state

    latest_bar = bars.iloc[-1]
    latest_time = str(latest_bar["bar_time"])
    signal, status = latest_live_signal(daily, data_5m, trade_date)

    if signal is not None and status == "已触发" and not state.get("warning_signal_time"):
        state["warning_signal_time"] = str(signal["signal_time"])
        state["warning_bar_time"] = str(signal["signal_time"])
        state["entry_candidates_emitted"] = []
        emit_live_event(
            event_time=str(signal["signal_time"]),
            trade_date=trade_date,
            event_type="BUY_WARNING",
            event_label="买入预警",
            price=round(float(signal["signal_price"]), 4),
            status="已触发",
            message="{}：{}".format(ENTRY_RULE_LABEL, condition_message(signal)),
        )

    warning_time = state.get("warning_bar_time") or ""
    if warning_time:
        bar_index = {str(row["bar_time"]): idx for idx, row in bars.iterrows()}
        signal_idx = bar_index.get(warning_time)
        current_idx = len(bars) - 1
        emitted = set(str(x) for x in state.get("entry_candidates_emitted", []))
        if signal_idx is not None:
            for offset in WATCH_ENTRY_OFFSETS:
                key = str(offset)
                if key in emitted:
                    continue
                entry_idx = signal_idx + offset
                if entry_idx < len(bars):
                    entry_bar = bars.iloc[entry_idx]
                    entry_price = float(entry_bar["open"])
                    emit_live_event(
                        event_time=str(entry_bar["bar_time"]),
                        trade_date=trade_date,
                        event_type="ENTRY_CANDIDATE",
                        event_label="候选买入提示",
                        price=round(entry_price, 4),
                        entry_offset_bars=offset,
                        status="可人工观察",
                        message="信号后第 {} 根 5m K 线开盘，候选买入价={}。".format(offset, round(entry_price, 4)),
                    )
                    state.setdefault("entry_candidates_emitted", []).append(offset)
                    if offset == args.primary_entry_offset and state.get("position") is None:
                        state["position"] = {
                            "position_id": 1,
                            "entry_time": str(entry_bar["bar_time"]),
                            "entry_date": trade_date,
                            "entry_price": round(entry_price, 4),
                        }
                        state["exit_reminder_emitted"] = False
                        emit_live_event(
                            event_time=str(entry_bar["bar_time"]),
                            trade_date=trade_date,
                            event_type="SIM_BUY",
                            event_label="模拟买入",
                            price=round(entry_price, 4),
                            entry_offset_bars=offset,
                            position_id=1,
                            status="开仓",
                            message="按主口径：信号后第 {} 根 5m K 线开盘模拟买入；计划第 {} 个交易日尾盘退出。".format(
                                offset,
                                EXIT_HOLD_DAYS,
                            ),
                        )
                elif latest_time.endswith("150000") or latest_time[-6:] >= "150000":
                    emit_live_event(
                        event_time=latest_time,
                        trade_date=trade_date,
                        event_type="SKIP_ENTRY",
                        event_label="候选买入跳过",
                        entry_offset_bars=offset,
                        status="当天剩余K线不足",
                        message="信号后第 {} 根 5m K 线尚未出现且已到收盘，不买。".format(offset),
                    )
                    state.setdefault("entry_candidates_emitted", []).append(offset)
        elif current_idx >= 0 and latest_time[-6:] >= "150000":
            state["warning_bar_time"] = ""

    position = state.get("position")
    if position and not state.get("exit_reminder_emitted"):
        dates = trade_dates_seen(daily, data_5m)
        entry_date = str(position["entry_date"])
        holding_dates = [date for date in dates if entry_date <= date <= trade_date]
        if len(holding_dates) >= EXIT_HOLD_DAYS and latest_time[-6:] >= LIVE_EXIT_TIME:
            entry_price = float(position["entry_price"])
            exit_price = float(latest_bar["close"])
            return_pct = exit_price / entry_price - 1.0
            emit_live_event(
                event_time=latest_time,
                trade_date=trade_date,
                event_type="EXIT_REMINDER",
                event_label="退出提示",
                price=round(exit_price, 4),
                position_id=position.get("position_id", 1),
                status="时间退出",
                message="第 {} 个交易日尾盘退出提示；模拟收益={}。".format(EXIT_HOLD_DAYS, fmt_pct(return_pct)),
            )
            state["exit_reminder_emitted"] = True
            state["position"] = None

    if latest_time != state.get("last_status_time"):
        print("[{}] live heartbeat latest_bar={} status={} close={}".format(
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            latest_time,
            status,
            round(float(latest_bar["close"]), 4),
        ))
        state["last_status_time"] = latest_time
    return state


def run_live(args):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state_path = Path(args.state_file)
    if args.reset_state and state_path.exists():
        state_path.unlink()
    state = load_state(state_path)

    print("live 模式启动：stock={} poll_seconds={} state_file={}".format(args.stock, args.poll_seconds, state_path))
    print("提示：本程序只输出模拟监控日志，不自动下单。按 Ctrl+C 退出。")
    xtdata.subscribe_quote(args.stock, period="5m", count=-1)
    time.sleep(1)

    loop = 0
    while True:
        loop += 1
        try:
            data_5m = load_live_5m_frame(args.stock)
            trade_date = args.live_date or str(data_5m.iloc[-1]["trade_date"])
            daily = load_live_daily_context(args.stock, trade_date)
            state = maybe_emit_live_events(args, state, daily, data_5m, trade_date)
            save_state(state_path, state)
        except KeyboardInterrupt:
            save_state(state_path, state)
            print("live 模式已退出，状态已保存：{}".format(state_path))
            return
        except Exception as exc:
            print("[{}] live error: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), exc))
            save_state(state_path, state)

        if args.max_loops and loop >= args.max_loops:
            print("live 模式达到 max_loops={}，退出。".format(args.max_loops))
            return
        time.sleep(max(int(args.poll_seconds), 1))


def main():
    args = parse_args()
    if args.stock != STOCK:
        raise SystemExit("当前版本固定验证 {}，暂不支持 --stock {}".format(STOCK, args.stock))
    if args.mode == "live":
        run_live(args)
        return
    run_replay(args)


if __name__ == "__main__":
    main()
