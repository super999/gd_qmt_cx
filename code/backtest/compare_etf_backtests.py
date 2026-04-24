#!/usr/bin/env python3
# coding: utf-8

import json
from pathlib import Path

import pandas as pd

import minimal_stock_backtest as base


ETF_LIST = [
    "510300.SH",
    "510500.SH",
    "159915.SZ",
]

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "compare_etf_backtests"


def build_markdown_report(summary_df):
    lines = [
        "# ETF 低吸反弹横向对比",
        "",
        "## 说明",
        "",
        "- 本报告使用同一套回测规则横向比较多只 ETF。",
        "- 目的不是选出历史最好的一只，而是看策略是否只对单一标的有效。",
        "- 如果结果只在单一 ETF 上成立，说明当前参数可能仍然偏拟合。",
        "",
        "## 汇总结果",
        "",
    ]

    display_cols = [
        "stock",
        "instrument_name",
        "total_return",
        "win_rate",
        "max_drawdown",
        "closed_trade_count",
        "avg_holding_days",
        "max_consecutive_losses",
        "status",
    ]
    table_df = summary_df[display_cols].copy()
    header = "| " + " | ".join(table_df.columns) + " |"
    separator = "| " + " | ".join(["---"] * len(table_df.columns)) + " |"
    lines.append(header)
    lines.append(separator)
    for _, row in table_df.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in table_df.columns) + " |")

    ok_df = summary_df[summary_df["status"] == "ok"].copy()
    if not ok_df.empty:
        lines.extend(
            [
                "",
                "## 观察",
                "",
                "- 正收益标的数：`{}`".format(int((ok_df["total_return"] > 0).sum())),
                "- 低于 `-3%` 最大回撤的标的数：`{}`".format(
                    int((ok_df["max_drawdown"] < -0.03).sum())
                ),
                "- 闭合交易数最少的标的：`{}`".format(
                    ok_df.sort_values("closed_trade_count").iloc[0]["stock"]
                ),
                "- 总收益最高的标的：`{}`".format(
                    ok_df.sort_values("total_return", ascending=False).iloc[0]["stock"]
                ),
                "",
                "## 判断建议",
                "",
            ]
        )

        positive_count = int((ok_df["total_return"] > 0).sum())
        if positive_count >= 2:
            lines.append("- 这套规则已经具备一定跨标的稳定性，可以继续做小步优化。")
        else:
            lines.append("- 这套规则跨标的稳定性仍然不够，下一步更应该优化结构，而不是继续细调参数。")

        if int((ok_df["closed_trade_count"] < 3).sum()) > 0:
            lines.append("- 至少有一只 ETF 交易样本偏少，当前结论仍要谨慎，不能直接上实盘。")

        if int((ok_df["max_drawdown"] < -0.03).sum()) > 0:
            lines.append("- 至少有一只 ETF 回撤超过 3%，需要优先检查趋势过滤和出场规则。")

    return "\n".join(lines) + "\n"


def save_case_outputs(stock, summary, trades_df, daily_df):
    case_dir = OUTPUT_DIR / stock.replace(".", "_")
    case_dir.mkdir(parents=True, exist_ok=True)

    summary_path = case_dir / "summary.json"
    trades_path = case_dir / "trades.csv"
    equity_path = case_dir / "daily_equity.csv"
    signal_path = case_dir / "signal_review.csv"

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
    signal_review_df.to_csv(signal_path, index=False, encoding="utf-8-sig")


def resample_5m_to_30m(frame_5m):
    working = frame_5m.copy()
    working.index = pd.to_datetime(working.index.astype(str), format="%Y%m%d%H%M%S")
    working["trade_date"] = working.index.strftime("%Y%m%d")

    resampled_parts = []
    for _, day_frame in working.groupby("trade_date"):
        day_frame = day_frame.sort_index()
        part = day_frame.resample("30min", label="right", closed="right").agg(
            {
                "time": "last",
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "amount": "sum",
                "settelementPrice": "last",
                "openInterest": "last",
                "preClose": "last",
                "suspendFlag": "max",
            }
        )
        part = part.dropna(subset=["open", "high", "low", "close"])
        resampled_parts.append(part)

    if not resampled_parts:
        return pd.DataFrame()

    result = pd.concat(resampled_parts).sort_index()
    result.index = result.index.strftime("%Y%m%d%H%M%S")
    result.index.name = "bar_time"
    return result


def load_intraday_frame_with_fallback(stock):
    try:
        frame_30m = base.load_price_frame(stock, base.INTRADAY_PERIOD, base.START_DATE, base.END_DATE)
        return frame_30m, "native_30m"
    except Exception:
        frame_5m = base.xtdata.get_local_data(
            field_list=[],
            stock_list=[stock],
            period="5m",
            start_time=base.START_DATE,
            end_time=base.END_DATE,
            count=-1,
            dividend_type=base.PRICE_ADJUSTMENT,
            fill_data=True,
        ).get(stock)
        if frame_5m is None or frame_5m.empty:
            raise RuntimeError("no intraday history data returned for {}".format(stock))
        frame_30m = resample_5m_to_30m(frame_5m)
        if frame_30m.empty:
            raise RuntimeError("failed to build 30m bars from 5m for {}".format(stock))
        return frame_30m, "resampled_from_5m"


def run_one(stock):
    daily_frame = base.load_price_frame(stock, base.DAILY_PERIOD, base.START_DATE, base.END_DATE)
    intraday_frame, intraday_source = load_intraday_frame_with_fallback(stock)
    daily_frame = base.enrich_daily_indicators(daily_frame)
    intraday_signals = base.build_intraday_signal_map(intraday_frame)
    instrument_detail = base.xtdata.get_instrument_detail(stock, iscomplete=False)
    summary, trades_df, daily_df = base.run_backtest(
        stock, daily_frame, intraday_signals, instrument_detail
    )
    save_case_outputs(stock, summary, trades_df, daily_df)
    summary["rows_daily"] = int(len(daily_frame))
    summary["rows_intraday"] = int(len(intraday_frame))
    summary["intraday_source"] = intraday_source
    summary["status"] = "ok"
    return summary


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Running ETF dip-buy cross-symbol comparison")
    print("stocks:", ", ".join(ETF_LIST))

    base.ensure_history_download(ETF_LIST, base.DAILY_PERIOD, base.START_DATE, base.END_DATE)
    base.ensure_history_download(ETF_LIST, base.INTRADAY_PERIOD, base.START_DATE, base.END_DATE)
    base.ensure_history_download(ETF_LIST, "5m", base.START_DATE, base.END_DATE)

    summary_rows = []
    for stock in ETF_LIST:
        try:
            print("testing:", stock)
            summary = run_one(stock)
            summary_rows.append(summary)
            print(
                " -> total_return={} win_rate={} max_drawdown={} closed_trades={}".format(
                    summary["total_return"],
                    summary["win_rate"],
                    summary["max_drawdown"],
                    summary["closed_trade_count"],
                )
            )
        except Exception as exc:
            summary_rows.append(
                {
                    "stock": stock,
                    "instrument_name": "",
                    "status": "error",
                    "error": str(exc),
                    "total_return": None,
                    "win_rate": None,
                    "max_drawdown": None,
                    "closed_trade_count": None,
                    "avg_holding_days": None,
                    "max_consecutive_losses": None,
                }
            )
            print(" -> error:", exc)

    summary_df = pd.DataFrame(summary_rows)
    summary_df = summary_df.sort_values(["status", "total_return"], ascending=[True, False])

    summary_csv = OUTPUT_DIR / "comparison_summary.csv"
    summary_json = OUTPUT_DIR / "comparison_summary.json"
    report_md = OUTPUT_DIR / "comparison_report.md"

    summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    summary_json.write_text(
        json.dumps(summary_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_md.write_text(build_markdown_report(summary_df), encoding="utf-8")

    print("outputs:")
    print(" -", summary_csv)
    print(" -", summary_json)
    print(" -", report_md)


if __name__ == "__main__":
    main()
