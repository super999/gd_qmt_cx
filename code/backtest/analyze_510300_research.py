#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd

import minimal_stock_backtest as base


OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "analyze_510300_research"
SWEEP_PATH = (
    Path(__file__).resolve().parent
    / "outputs"
    / "etf_dip_buy_param_sweep"
    / "sweep_results.csv"
)
STOCK = "510300.SH"


def compute_max_drawdown(series):
    rolling_high = series.cummax()
    drawdown = series / rolling_high - 1.0
    return float(drawdown.min()) if not drawdown.empty else 0.0


def market_year_summary(daily_frame):
    frame = daily_frame.copy()
    frame["trade_date"] = frame.index.astype(str).str[:8]
    frame["year"] = frame["trade_date"].str[:4]

    rows = []
    for year, year_df in frame.groupby("year"):
        close = year_df["close"].astype(float)
        daily_ret = close.pct_change().dropna()
        rows.append(
            {
                "year": year,
                "start_close": round(float(close.iloc[0]), 4),
                "end_close": round(float(close.iloc[-1]), 4),
                "market_return": round(float(close.iloc[-1] / close.iloc[0] - 1.0), 6),
                "market_max_drawdown": round(compute_max_drawdown(close), 6),
                "up_day_ratio": round(float((year_df["close"] > year_df["open"]).mean()), 6),
                "daily_volatility": round(float(daily_ret.std()), 6) if not daily_ret.empty else 0.0,
                "rows": int(len(year_df)),
            }
        )
    return pd.DataFrame(rows)


def slice_intraday_signals(intraday_signals, year):
    return {k: v for k, v in intraday_signals.items() if str(k).startswith(year)}


def strategy_year_summary(daily_frame, intraday_signals, instrument_detail):
    working = daily_frame.copy()
    working["trade_date"] = working.index.astype(str).str[:8]
    working["year"] = working["trade_date"].str[:4]

    rows = []
    for year, year_df in working.groupby("year"):
        year_intraday = slice_intraday_signals(intraday_signals, year)
        summary, trades_df, daily_df = base.run_backtest(
            STOCK, year_df.copy(), year_intraday, instrument_detail
        )
        rows.append(
            {
                "year": year,
                "total_return": summary["total_return"],
                "win_rate": summary["win_rate"],
                "max_drawdown": summary["max_drawdown"],
                "closed_trade_count": summary["closed_trade_count"],
                "trade_count": summary["trade_count"],
                "avg_holding_days": summary["avg_holding_days"],
                "intraday_signal_days": int(len(year_intraday)),
                "pullback_ok_days": int(daily_df["pullback_ok"].fillna(False).astype(bool).sum()),
                "intraday_confirmed_days": int(
                    daily_df["intraday_confirmed"].fillna(False).astype(bool).sum()
                ),
                "prepare_buy_days": int((daily_df["next_signal"] == "prepare_buy").sum()),
            }
        )
    return pd.DataFrame(rows)


def current_trade_table():
    trades_path = (
        Path(__file__).resolve().parent
        / "outputs"
        / "minimal_stock_backtest"
        / "trades.csv"
    )
    if not trades_path.exists():
        return pd.DataFrame()
    trades_df = pd.read_csv(trades_path, dtype={"trade_date": str, "signal_date": str})
    sells = trades_df[trades_df["action"] == "sell"].copy()
    if sells.empty:
        return sells
    keep_cols = ["signal_date", "trade_date", "price", "holding_days", "reason", "pnl"]
    return sells[keep_cols].copy()


def sweep_analysis():
    if not SWEEP_PATH.exists():
        return pd.DataFrame(), pd.DataFrame()

    sweep_df = pd.read_csv(SWEEP_PATH)
    top_df = sweep_df.sort_values(
        ["rank_score", "total_return", "win_rate"], ascending=[False, False, False]
    ).head(30)

    param_cols = [
        "DEV_DRAWDOWN_FROM_HIGH",
        "DEV_TREND_FLOOR",
        "DEV_INTRADAY_REBOUND_FROM_LOW",
        "DEV_INTRADAY_CLOSE_IN_RANGE",
        "DEV_INTRADAY_SCORE_MIN",
        "MAX_HOLD_DAYS",
        "STOP_LOSS_PCT",
        "TAKE_PROFIT_PCT",
    ]

    rows = []
    for col in param_cols:
        grouped = (
            sweep_df.groupby(col)
            .agg(
                case_count=("case_id", "count"),
                mean_total_return=("total_return", "mean"),
                median_total_return=("total_return", "median"),
                mean_max_drawdown=("max_drawdown", "mean"),
                mean_rank_score=("rank_score", "mean"),
            )
            .reset_index()
        )
        grouped["parameter"] = col
        grouped = grouped.rename(columns={col: "value"})
        rows.append(grouped)

    param_effect_df = pd.concat(rows, ignore_index=True)
    top_counts = []
    for col in param_cols:
        counts = top_df[col].value_counts().sort_index().reset_index()
        counts.columns = ["value", "top30_count"]
        counts["parameter"] = col
        top_counts.append(counts)
    top_count_df = pd.concat(top_counts, ignore_index=True)

    merged = param_effect_df.merge(top_count_df, on=["parameter", "value"], how="left")
    merged["top30_count"] = merged["top30_count"].fillna(0).astype(int)
    return top_df, merged


def build_report(market_df, strategy_df, trade_df, top_df, param_effect_df, intraday_frame):
    lines = [
        "# 510300 单标的数据分析与研究结论",
        "",
        "## 结论先行",
        "",
        "- 当前更合适的研究对象已经明确：`510300.SH`。",
        "- 当前更合适的研究目标不是跨标的通用化，而是为 `510300.SH` 找到可滚动校准的参数规则。",
        "- 现有实验版回测可以跑出正收益，但样本仍然很少，暂时不能把这组参数视为长期有效答案。",
        "",
        "## 数据限制",
        "",
        "- 当前本地 `30m` 历史覆盖起点：`{}`".format(intraday_frame.index[0]),
        "- 这意味着 `2024` 年以及 `2025` 年前几个月的盘中确认统计并不完整。",
        "- 因此凡是涉及 `2024` 的策略表现结论，都只能视为“数据覆盖不足下的结果”，不能直接解释成策略无效。",
        "",
        "## 1. 标的本身的阶段特征",
        "",
    ]

    market_cols = [
        "year",
        "market_return",
        "market_max_drawdown",
        "up_day_ratio",
        "daily_volatility",
        "rows",
    ]
    lines.extend(table_lines(market_df[market_cols]))

    lines.extend(
        [
            "",
            "观察：",
            "- `510300` 在不同年份的方向和回撤并不一致，因此参数不太可能长期完全不变。",
            "- 这支持“结构稳定、参数小调”的研究方向。",
            "",
            "## 2. 当前实验版按年份拆分后的策略表现",
            "",
        ]
    )

    strategy_cols = [
        "year",
        "total_return",
        "win_rate",
        "max_drawdown",
        "closed_trade_count",
        "intraday_signal_days",
        "prepare_buy_days",
        "pullback_ok_days",
        "intraday_confirmed_days",
    ]
    lines.extend(table_lines(strategy_df[strategy_cols]))

    lines.extend(
        [
            "",
            "观察：",
            "- 如果某一年几乎没有闭合交易，说明当前阈值对那一阶段过严，不能直接据此得出“策略稳定”的结论。",
            "- 如果某一年回撤明显放大，下一步更应该查环境过滤，而不是先调止盈止损。",
            "",
            "## 3. 当前实验版实际成交记录",
            "",
        ]
    )

    if trade_df.empty:
        lines.append("- 当前没有可用的已闭合交易记录。")
    else:
        lines.extend(table_lines(trade_df))

    lines.extend(
        [
            "",
            "## 4. 单标的参数扫描里真正有影响的部分",
            "",
            "当前参数扫描基于同一只标的 `510300.SH`，更适合回答：",
            "",
            "- 哪些参数方向大体有效",
            "- 哪些参数几乎不敏感",
            "- 哪些参数可以成为后续滚动校准候选",
            "",
            "### 4.1 当前排名前 10 的参数组",
            "",
        ]
    )

    top_cols = [
        "rank_score",
        "total_return",
        "max_drawdown",
        "DEV_DRAWDOWN_FROM_HIGH",
        "DEV_TREND_FLOOR",
        "DEV_INTRADAY_REBOUND_FROM_LOW",
        "DEV_INTRADAY_CLOSE_IN_RANGE",
        "DEV_INTRADAY_SCORE_MIN",
        "MAX_HOLD_DAYS",
        "STOP_LOSS_PCT",
        "TAKE_PROFIT_PCT",
    ]
    lines.extend(table_lines(top_df[top_cols].head(10)))

    lines.extend(
        [
            "",
            "### 4.2 参数影响摘要",
            "",
        ]
    )

    effect_view = param_effect_df[
        [
            "parameter",
            "value",
            "mean_total_return",
            "median_total_return",
            "mean_max_drawdown",
            "top30_count",
        ]
    ].copy()
    lines.extend(table_lines(effect_view))

    lines.extend(
        [
            "",
            "## 5. 当前更可靠的研究结论",
            "",
            "1. 当前样本太少，不能继续把“最高收益参数”当作目标。",
            "2. 当前更值得研究的是：",
            "   - 回撤阈值是否应随市场阶段调整",
            "   - 趋势环境过滤是否应更明确",
            "   - 持有上限是否应作为主要可调参数",
            "3. 目前看，止盈止损并不是最敏感的参数项；回撤背景和趋势约束更值得优先研究。",
            "",
            "## 6. 下一步建议",
            "",
            "1. 先做 `510300.SH` 的分阶段分析，不再优先横向换标的。",
            "2. 先设计滚动校准框架，不急着继续硬调单点参数。",
            "3. 滚动校准第一版建议只允许调整这 3 类：",
            "   - 回撤阈值",
            "   - 趋势环境阈值",
            "   - 持有上限",
            "4. 在你确认之前，不继续改策略逻辑，只基于这些分析结果再提具体方案。",
        ]
    )

    return "\n".join(lines) + "\n"


def table_lines(df):
    header = "| " + " | ".join(df.columns.astype(str)) + " |"
    separator = "| " + " | ".join(["---"] * len(df.columns)) + " |"
    lines = [header, separator]
    for _, row in df.iterrows():
        values = [str(row[col]) for col in df.columns]
        lines.append("| " + " | ".join(values) + " |")
    return lines


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Analyzing 510300 single-symbol research context")

    base.ensure_history_download([STOCK], base.DAILY_PERIOD, base.START_DATE, base.END_DATE)
    base.ensure_history_download([STOCK], base.INTRADAY_PERIOD, base.START_DATE, base.END_DATE)

    daily_frame = base.load_price_frame(STOCK, base.DAILY_PERIOD, base.START_DATE, base.END_DATE)
    intraday_frame = base.load_price_frame(
        STOCK, base.INTRADAY_PERIOD, base.START_DATE, base.END_DATE
    )
    daily_frame = base.enrich_daily_indicators(daily_frame)
    intraday_signals = base.build_intraday_signal_map(intraday_frame)
    instrument_detail = base.xtdata.get_instrument_detail(STOCK, iscomplete=False)

    market_df = market_year_summary(daily_frame)
    strategy_df = strategy_year_summary(daily_frame, intraday_signals, instrument_detail)
    trade_df = current_trade_table()
    top_df, param_effect_df = sweep_analysis()

    report = build_report(market_df, strategy_df, trade_df, top_df, param_effect_df, intraday_frame)

    market_df.to_csv(OUTPUT_DIR / "market_year_summary.csv", index=False, encoding="utf-8-sig")
    strategy_df.to_csv(OUTPUT_DIR / "strategy_year_summary.csv", index=False, encoding="utf-8-sig")
    if not trade_df.empty:
        trade_df.to_csv(OUTPUT_DIR / "current_closed_trades.csv", index=False, encoding="utf-8-sig")
    if not top_df.empty:
        top_df.to_csv(OUTPUT_DIR / "top_param_cases.csv", index=False, encoding="utf-8-sig")
        param_effect_df.to_csv(
            OUTPUT_DIR / "param_effect_summary.csv", index=False, encoding="utf-8-sig"
        )

    summary = {
        "stock": STOCK,
        "instrument_name": instrument_detail.get("InstrumentName", ""),
        "market_years": market_df.to_dict(orient="records"),
        "strategy_years": strategy_df.to_dict(orient="records"),
    }
    (OUTPUT_DIR / "analysis_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT_DIR / "510300单标的数据分析与研究结论.md").write_text(report, encoding="utf-8")

    print("outputs:")
    print(" -", OUTPUT_DIR / "market_year_summary.csv")
    print(" -", OUTPUT_DIR / "strategy_year_summary.csv")
    print(" -", OUTPUT_DIR / "510300单标的数据分析与研究结论.md")


if __name__ == "__main__":
    main()
