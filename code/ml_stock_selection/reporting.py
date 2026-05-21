from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from config import StrategyConfig


class MetricsCalculator:
    @staticmethod
    def max_drawdown(nav: pd.Series) -> float:
        if nav.empty:
            return 0.0
        drawdown = nav / nav.cummax() - 1.0
        return float(drawdown.min())

    @staticmethod
    def annualized_return(nav: pd.Series, periods_per_year: int = 244) -> float:
        if len(nav) <= 1:
            return 0.0
        total_return = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
        return (1.0 + total_return) ** (periods_per_year / len(nav)) - 1.0

    @staticmethod
    def evaluate_predictions(pred_df: pd.DataFrame) -> Dict[str, Any]:
        if pred_df.empty:
            return {}
        eval_df = pred_df.dropna(subset=["pred_return_5d", "target_return_5d", "target_up_5d", "target_risk_5d"]).copy()
        result: Dict[str, Any] = {"prediction_rows": len(eval_df)}
        if eval_df.empty:
            return result
        daily_ic = [
            day_df["pred_return_5d"].corr(day_df["target_return_5d"], method="spearman")
            for _, day_df in eval_df.groupby("trade_date")
            if len(day_df) >= 5 and day_df["pred_return_5d"].nunique() > 1 and day_df["target_return_5d"].nunique() > 1
        ]
        result["mean_daily_rank_ic"] = float(np.nanmean(daily_ic)) if daily_ic else np.nan
        result["median_daily_rank_ic"] = float(np.nanmedian(daily_ic)) if daily_ic else np.nan
        result["up_auc"] = (
            float(roc_auc_score(eval_df["target_up_5d"], eval_df["pred_up_prob"].fillna(0.5)))
            if eval_df["target_up_5d"].nunique() >= 2 and eval_df["pred_up_prob"].notna().any()
            else np.nan
        )
        result["risk_auc"] = (
            float(roc_auc_score(eval_df["target_risk_5d"], eval_df["risk_prob"].fillna(0.0)))
            if eval_df["target_risk_5d"].nunique() >= 2 and eval_df["risk_prob"].notna().any()
            else np.nan
        )
        return result


class ResultWriter:
    def __init__(self, config: StrategyConfig, lightgbm_version: str) -> None:
        self.config = config
        self.lightgbm_version = lightgbm_version
        self.metrics = MetricsCalculator()

    def write_all(
        self,
        codes: List[str],
        dataset: pd.DataFrame,
        pred_df: pd.DataFrame,
        selections: pd.DataFrame,
        daily_df: pd.DataFrame,
        trades_df: pd.DataFrame,
        train_logs: List[Dict[str, Any]],
        feature_importance: Optional[pd.DataFrame],
        max_stocks: Optional[int],
    ) -> Dict[str, Path]:
        run_id = self._build_run_id(max_stocks)
        run_dir = self.config.output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        if feature_importance is None:
            feature_importance = pd.DataFrame(columns=["feature", "feature_cn", "importance"])
        output_paths = {
            "dataset": run_dir / "factor_dataset.csv",
            "predictions": run_dir / "predictions.csv",
            "selected_portfolio": run_dir / "selected_portfolio.csv",
            "daily_nav": run_dir / "daily_nav.csv",
            "trades": run_dir / "trades.csv",
            "train_logs": run_dir / "train_logs.csv",
            "feature_importance": run_dir / "feature_importance.csv",
            "summary": run_dir / "summary.json",
            "report": run_dir / "report.md",
        }
        dataset.to_csv(output_paths["dataset"], index=False, encoding="utf-8-sig")
        pred_df.to_csv(output_paths["predictions"], index=False, encoding="utf-8-sig")
        selections.to_csv(output_paths["selected_portfolio"], index=False, encoding="utf-8-sig")
        daily_df.to_csv(output_paths["daily_nav"], index=False, encoding="utf-8-sig")
        trades_df.to_csv(output_paths["trades"], index=False, encoding="utf-8-sig")
        pd.DataFrame(train_logs).to_csv(output_paths["train_logs"], index=False, encoding="utf-8-sig")
        feature_importance.to_csv(output_paths["feature_importance"], index=False, encoding="utf-8-sig")
        summary = self._build_summary(run_id, max_stocks, codes, dataset, pred_df, daily_df, train_logs)
        output_paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        output_paths["report"].write_text(self._build_report(summary, feature_importance, output_paths), encoding="utf-8")
        return output_paths

    def _build_run_id(self, max_stocks: Optional[int]) -> str:
        stock_scope = "all" if max_stocks is None else "max{}".format(max_stocks)
        return "{}_start{}_end{}_pred{}_{}".format(
            datetime.now().strftime("%Y%m%d_%H%M%S"),
            self.config.start_date,
            self.config.end_date,
            self.config.min_prediction_date,
            stock_scope,
        )

    def _build_summary(
        self,
        run_id: str,
        max_stocks: Optional[int],
        codes: List[str],
        dataset: pd.DataFrame,
        pred_df: pd.DataFrame,
        daily_df: pd.DataFrame,
        train_logs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        portfolio = {}
        if not daily_df.empty:
            portfolio = {
                "days": int(len(daily_df)),
                "trade_start_date": str(daily_df["trade_date"].min()),
                "trade_end_date": str(daily_df["trade_date"].max()),
                "entry_start_date": str(daily_df["entry_date"].min()),
                "entry_end_date": str(daily_df["entry_date"].max()),
                "total_return_with_cost": float(daily_df["nav"].iloc[-1] - 1.0),
                "total_return_no_cost": float(daily_df["nav_no_cost"].iloc[-1] - 1.0),
                "annualized_return_with_cost": float(self.metrics.annualized_return(daily_df["nav"])),
                "max_drawdown_with_cost": float(self.metrics.max_drawdown(daily_df["nav"])),
                "win_rate": float((daily_df["net_return"] > 0).mean()),
                "avg_turnover": float(daily_df["turnover"].mean()),
                "total_cost": float(daily_df["cost"].sum()),
                "avg_retained_count": float(daily_df["retained_count"].mean()) if "retained_count" in daily_df.columns else 0.0,
                "avg_added_count": float(daily_df["added_count"].mean()) if "added_count" in daily_df.columns else 0.0,
                "avg_removed_count": float(daily_df["removed_count"].mean()) if "removed_count" in daily_df.columns else 0.0,
            }
        return {
            "run_id": run_id,
            "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "python": sys.executable,
            "lightgbm_version": self.lightgbm_version,
            "start_date": self.config.start_date,
            "end_date": self.config.end_date,
            "configured_min_prediction_date": self.config.min_prediction_date,
            "actual_prediction_start_date": str(pred_df["trade_date"].min()) if not pred_df.empty else "",
            "actual_prediction_end_date": str(pred_df["trade_date"].max()) if not pred_df.empty else "",
            "max_stocks": max_stocks,
            "stock_scope": "all" if max_stocks is None else "first_{}".format(max_stocks),
            "stock_count": len(codes),
            "dataset_rows": int(len(dataset)),
            "eligible_rows": int(dataset["base_eligible"].sum()) if not dataset.empty else 0,
            "prediction_rows": int(len(pred_df)),
            "train_rounds": len(train_logs),
            "top_n": self.config.top_n,
            "rebalance_frequency": self.config.rebalance_frequency,
            "hold_rank_buffer": self.config.hold_rank_buffer,
            "min_holding_days": self.config.min_holding_days,
            "transaction_cost_rate": self.config.transaction_cost_rate,
            "use_financial_factors": self.config.use_financial_factors,
            "financial_cache_dir": str(self.config.financial_cache_dir),
            "financial_factor_coverage": self._financial_factor_coverage(dataset),
            "portfolio": portfolio,
            "model_metrics": self.metrics.evaluate_predictions(pred_df),
        }

    def _financial_factor_coverage(self, dataset: pd.DataFrame) -> Dict[str, Any]:
        if not self.config.use_financial_factors:
            return {"enabled": False}
        coverage: Dict[str, Any] = {
            "enabled": True,
            "rows": int(len(dataset)),
            "rows_with_any_financial_factor": 0,
            "features": {},
        }
        feature_cols = [col for col in self.config.financial_feature_cols if col in dataset.columns]
        if not feature_cols or dataset.empty:
            return coverage
        has_any = dataset[feature_cols].notna().any(axis=1)
        coverage["rows_with_any_financial_factor"] = int(has_any.sum())
        coverage["any_financial_factor_rate"] = float(has_any.mean())
        for column in feature_cols:
            notna = dataset[column].notna()
            coverage["features"][column] = {
                "feature_cn": self.config.financial_feature_labels.get(column, ""),
                "non_null_rows": int(notna.sum()),
                "non_null_rate": float(notna.mean()),
                "missing_rate": float(1.0 - notna.mean()),
            }
        return coverage

    def _build_report(self, summary: Dict[str, Any], feature_importance: pd.DataFrame, output_paths: Dict[str, Path]) -> str:
        portfolio = summary.get("portfolio") or {}
        metrics = summary.get("model_metrics") or {}
        financial_coverage = summary.get("financial_factor_coverage") or {}
        lines = [
            "# A股全市场 LightGBM 多因子选股 v1",
            "",
            "## 运行摘要",
            "",
            "- run_id：{}".format(summary["run_id"]),
            "- 运行时间：{}".format(summary["run_time"]),
            "- Python：{}".format(summary["python"]),
            "- LightGBM：{}".format(summary["lightgbm_version"]),
            "- 日期范围：{} 至 {}".format(summary["start_date"], summary["end_date"]),
            "- 配置的最早预测日：{}".format(summary["configured_min_prediction_date"]),
            "- 实际预测区间：{} 至 {}".format(summary["actual_prediction_start_date"], summary["actual_prediction_end_date"]),
            "- 股票池限制：{}".format(summary["stock_scope"]),
            "- 股票数：{}".format(summary["stock_count"]),
            "- 数据集行数：{}".format(summary["dataset_rows"]),
            "- 可训练/可预测基础合格行数：{}".format(summary["eligible_rows"]),
            "- 预测行数：{}".format(summary["prediction_rows"]),
            "- 训练轮数：{}".format(summary["train_rounds"]),
            "- TopN：{}".format(summary["top_n"]),
            "- 调仓频率：{}".format(summary["rebalance_frequency"]),
            "- 排名缓冲：{}".format(summary["hold_rank_buffer"]),
            "- 最少持有天数：{}".format(summary["min_holding_days"]),
            "- 单边交易成本：{}".format(summary["transaction_cost_rate"]),
            "- 财务因子：{}".format("启用" if summary["use_financial_factors"] else "未启用"),
            "",
            "## 组合结果",
            "",
        ]
        if portfolio:
            lines.extend(
                [
                    "- 回测天数：{}".format(portfolio["days"]),
                    "- 实际组合信号区间：{} 至 {}".format(portfolio["trade_start_date"], portfolio["trade_end_date"]),
                    "- 实际成交区间：{} 至 {}".format(portfolio["entry_start_date"], portfolio["entry_end_date"]),
                    "- 总收益（含成本）：{:.4%}".format(portfolio["total_return_with_cost"]),
                    "- 总收益（不计成本）：{:.4%}".format(portfolio["total_return_no_cost"]),
                    "- 年化收益（含成本）：{:.4%}".format(portfolio["annualized_return_with_cost"]),
                    "- 最大回撤（含成本）：{:.4%}".format(portfolio["max_drawdown_with_cost"]),
                    "- 日胜率：{:.2%}".format(portfolio["win_rate"]),
                    "- 平均换手：{:.4f}".format(portfolio["avg_turnover"]),
                    "- 总成本：{:.4%}".format(portfolio["total_cost"]),
                    "- 平均保留数：{:.2f}".format(portfolio["avg_retained_count"]),
                    "- 平均新增数：{:.2f}".format(portfolio["avg_added_count"]),
                    "- 平均移除数：{:.2f}".format(portfolio["avg_removed_count"]),
                ]
            )
        else:
            lines.append("- 未生成组合结果。")
        lines.extend(
            [
                "",
                "## 模型指标",
                "",
                "- 平均日截面 Rank IC：{}".format(metrics.get("mean_daily_rank_ic", "")),
                "- 中位数日截面 Rank IC：{}".format(metrics.get("median_daily_rank_ic", "")),
                "- 涨跌方向 AUC：{}".format(metrics.get("up_auc", "")),
                "- 风险模型 AUC：{}".format(metrics.get("risk_auc", "")),
                "",
                "## 财务因子覆盖率",
                "",
            ]
        )
        if financial_coverage.get("enabled"):
            lines.append("- 财务缓存目录：{}".format(summary["financial_cache_dir"]))
            lines.append("- 任一财务因子非空行数：{} / {}".format(
                financial_coverage.get("rows_with_any_financial_factor", 0),
                financial_coverage.get("rows", 0),
            ))
            lines.extend(["", "| feature | 中文含义 | 非空率 | 缺失率 |", "| --- | --- | ---: | ---: |"])
            for feature, item in financial_coverage.get("features", {}).items():
                lines.append(
                    "| `{}` | {} | {:.2%} | {:.2%} |".format(
                        feature,
                        item.get("feature_cn", ""),
                        item.get("non_null_rate", 0.0),
                        item.get("missing_rate", 0.0),
                    )
                )
        else:
            lines.append("- 未启用财务因子。")
        lines.extend(
            [
                "",
                "## Top 特征重要性",
                "",
            ]
        )
        if not feature_importance.empty:
            lines.extend(["| feature | 中文含义 | importance |", "| --- | --- | ---: |"])
            for _, row in feature_importance.head(20).iterrows():
                lines.append("| `{}` | {} | {} |".format(row["feature"], row["feature_cn"], row["importance"]))
        else:
            lines.append("- 未生成特征重要性。")
        lines.extend(["", "## 输出文件", ""])
        for name, path in output_paths.items():
            if name != "report":
                lines.append("- {}：{}".format(name, path))
        return "\n".join(lines) + "\n"
