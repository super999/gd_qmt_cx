from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

import pandas as pd

from config import StrategyConfig


class PortfolioSelector:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def select(self, pred_df: pd.DataFrame) -> pd.DataFrame:
        selections: List[pd.DataFrame] = []
        previous_codes: Set[str] = set()
        holding_days: Dict[str, int] = {}
        last_rebalance_week: str = ""
        for trade_date, day_df in pred_df.groupby("trade_date", sort=True):
            ranked = self._rank_candidates(day_df)
            available = self._rank_all(day_df)
            current_week = pd.Timestamp(str(trade_date)).strftime("%G-%V")
            should_rebalance = self._should_rebalance(str(trade_date), selections, current_week, last_rebalance_week)
            if should_rebalance:
                selected = self._select_rebalance_day(ranked, available, previous_codes, holding_days)
                last_rebalance_week = current_week
            else:
                selected = self._select_hold_day(available, previous_codes)
            selected = selected.copy()
            selected["target_weight"] = 1.0 / len(selected) if len(selected) else 0.0
            selections.append(selected)
            current_codes = set(selected["code"]) if not selected.empty else set()
            holding_days = {code: holding_days.get(code, 0) + 1 for code in current_codes}
            previous_codes = current_codes
        return pd.concat(selections, ignore_index=True) if selections else pd.DataFrame()

    def _rank_candidates(self, day_df: pd.DataFrame) -> pd.DataFrame:
        filtered = day_df[day_df["risk_score"] <= self.config.max_risk_score].copy()
        if filtered.empty:
            filtered = day_df.copy()
        ranked = filtered.sort_values(["pred_return_5d", "pred_up_prob"], ascending=[False, False]).copy()
        ranked["selection_rank"] = range(1, len(ranked) + 1)
        return ranked

    def _rank_all(self, day_df: pd.DataFrame) -> pd.DataFrame:
        ranked = day_df.sort_values(["pred_return_5d", "pred_up_prob"], ascending=[False, False]).copy()
        ranked["selection_rank"] = range(1, len(ranked) + 1)
        return ranked

    def _should_rebalance(self, trade_date: str, selections: List[pd.DataFrame], current_week: str, last_rebalance_week: str) -> bool:
        if not selections:
            return True
        if self.config.rebalance_frequency == "daily":
            return True
        if self.config.rebalance_frequency == "weekly":
            return current_week != last_rebalance_week
        raise ValueError("不支持的调仓频率: {}".format(self.config.rebalance_frequency))

    def _select_rebalance_day(
        self,
        ranked: pd.DataFrame,
        available: pd.DataFrame,
        previous_codes: Set[str],
        holding_days: Dict[str, int],
    ) -> pd.DataFrame:
        keep_codes: List[str] = []
        if previous_codes:
            available_previous = available[available["code"].isin(previous_codes)]
            forced = available_previous[available_previous["code"].map(lambda code: holding_days.get(code, 0) < self.config.min_holding_days)]
            keep_codes.extend(forced["code"].tolist())
            if self.config.hold_rank_buffer > 0:
                buffered = available_previous[available_previous["selection_rank"] <= self.config.hold_rank_buffer]
                keep_codes.extend(buffered["code"].tolist())
        keep_codes = list(dict.fromkeys(keep_codes))
        kept = available[available["code"].isin(keep_codes)].head(self.config.top_n)
        fill_count = self.config.top_n - len(kept)
        if fill_count <= 0:
            return kept.copy()
        filler = ranked[~ranked["code"].isin(set(kept["code"]))].head(fill_count)
        return pd.concat([kept, filler], ignore_index=True)

    def _select_hold_day(self, ranked: pd.DataFrame, previous_codes: Set[str]) -> pd.DataFrame:
        held = ranked[ranked["code"].isin(previous_codes)].copy()
        return held.head(self.config.top_n)


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
            retained_count = len(set(prev_weights) & set(current_weights))
            added_count = len(set(current_weights) - set(prev_weights))
            removed_count = len(set(prev_weights) - set(current_weights))
            cost = turnover * self.config.transaction_cost_rate
            gross_return = float((day_df["target_weight"] * day_df["realized_next_open_return"]).sum())
            net_return = gross_return - cost
            nav *= 1.0 + net_return
            nav_no_cost *= 1.0 + gross_return
            daily_rows.append(
                self._daily_row(
                    trade_date,
                    day_df,
                    gross_return,
                    turnover,
                    cost,
                    net_return,
                    nav,
                    nav_no_cost,
                    retained_count,
                    added_count,
                    removed_count,
                )
            )
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
        retained_count: int,
        added_count: int,
        removed_count: int,
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
            "retained_count": retained_count,
            "added_count": added_count,
            "removed_count": removed_count,
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
                "selection_rank": row.get("selection_rank"),
                "realized_next_open_return": row["realized_next_open_return"],
                "target_return_5d": row["target_return_5d"],
            }
            for _, row in day_df.iterrows()
        ]
