from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd

from config import StrategyConfig


class PortfolioSelector:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def select(self, pred_df: pd.DataFrame) -> pd.DataFrame:
        selections: List[pd.DataFrame] = []
        for _, day_df in pred_df.groupby("trade_date"):
            filtered = day_df[day_df["risk_score"] <= self.config.max_risk_score].copy()
            if filtered.empty:
                filtered = day_df.copy()
            selected = filtered.sort_values(["pred_return_5d", "pred_up_prob"], ascending=[False, False]).head(self.config.top_n).copy()
            selected["target_weight"] = 1.0 / len(selected) if len(selected) else 0.0
            selections.append(selected)
        return pd.concat(selections, ignore_index=True) if selections else pd.DataFrame()


class DailyRebalanceBacktester:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def run(self, selections: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        if selections.empty:
            return pd.DataFrame(), pd.DataFrame()
        nav = 1.0
        nav_no_cost = 1.0
        prev_weights: Dict[str, float] = {}
        daily_rows: List[Dict[str, Any]] = []
        trade_rows: List[Dict[str, Any]] = []
        for trade_date, day_df in selections.groupby("trade_date"):
            day_df = day_df.copy()
            current_weights = dict(zip(day_df["code"], day_df["target_weight"]))
            turnover = self._turnover(prev_weights, current_weights)
            cost = turnover * self.config.transaction_cost_rate
            gross_return = float((day_df["target_weight"] * day_df["realized_next_open_return"]).sum())
            net_return = gross_return - cost
            nav *= 1.0 + net_return
            nav_no_cost *= 1.0 + gross_return
            daily_rows.append(self._daily_row(trade_date, day_df, gross_return, turnover, cost, net_return, nav, nav_no_cost))
            trade_rows.extend(self._trade_rows(day_df))
            prev_weights = current_weights
        return pd.DataFrame(daily_rows), pd.DataFrame(trade_rows)

    def _turnover(self, prev_weights: Dict[str, float], current_weights: Dict[str, float]) -> float:
        all_codes = sorted(set(prev_weights) | set(current_weights))
        return sum(abs(current_weights.get(code, 0.0) - prev_weights.get(code, 0.0)) for code in all_codes)

    def _daily_row(
        self,
        trade_date: str,
        day_df: pd.DataFrame,
        gross_return: float,
        turnover: float,
        cost: float,
        net_return: float,
        nav: float,
        nav_no_cost: float,
    ) -> Dict[str, Any]:
        return {
            "trade_date": trade_date,
            "entry_date": day_df["entry_date"].iloc[0],
            "holding_count": len(day_df),
            "gross_return": gross_return,
            "turnover": turnover,
            "cost": cost,
            "net_return": net_return,
            "nav": nav,
            "nav_no_cost": nav_no_cost,
        }

    def _trade_rows(self, day_df: pd.DataFrame) -> List[Dict[str, Any]]:
        return [
            {
                "trade_date": row["trade_date"],
                "entry_date": row["entry_date"],
                "code": row["code"],
                "name": row["name"],
                "weight": row["target_weight"],
                "pred_return_5d": row["pred_return_5d"],
                "pred_up_prob": row["pred_up_prob"],
                "risk_score": row["risk_score"],
                "realized_next_open_return": row["realized_next_open_return"],
                "target_return_5d": row["target_return_5d"],
            }
            for _, row in day_df.iterrows()
        ]
