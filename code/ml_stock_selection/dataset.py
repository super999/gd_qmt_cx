from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from config import StrategyConfig
from financial_factors import FinancialFactorBuilder
from utils import date_int, is_st_name, normalize_date, safe_divide


class FactorDatasetBuilder:
    REQUIRED_FIELDS = ["open", "high", "low", "close", "volume", "amount"]

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.financial_factor_builder = FinancialFactorBuilder(config) if config.use_financial_factors else None

    def build(self, data_by_code: Dict[str, pd.DataFrame], details: Dict[str, Dict[str, Any]]) -> pd.DataFrame:
        frames: List[pd.DataFrame] = []
        for index, (code, frame) in enumerate(data_by_code.items(), start=1):
            prepared = self._prepare_one_stock(code, frame, details.get(code, {}))
            if not prepared.empty:
                frames.append(prepared)
            if index % 500 == 0:
                print("计算特征: {}/{}".format(index, len(data_by_code)))
        if not frames:
            return pd.DataFrame()
        dataset = pd.concat(frames, ignore_index=True)
        dataset = dataset.replace([np.inf, -np.inf], np.nan)
        dataset["trade_date"] = dataset["trade_date"].astype(str)
        if self.financial_factor_builder is not None:
            dataset = self._merge_financial_factors(dataset)
        return dataset.sort_values(["trade_date", "code"]).reset_index(drop=True)

    def _merge_financial_factors(self, dataset: pd.DataFrame) -> pd.DataFrame:
        print("读取并合并公告日口径财务因子...")
        financial_factors = self.financial_factor_builder.build_for_dataset(dataset)
        if financial_factors.empty:
            raise RuntimeError("已启用财务因子，但未生成任何财务因子行。")
        merged = dataset.merge(financial_factors, on=["code", "trade_date"], how="left")
        return merged

    def _prepare_one_stock(self, code: str, frame: pd.DataFrame, detail: Dict[str, Any]) -> pd.DataFrame:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return pd.DataFrame()

        df = frame.copy()
        df.index = [normalize_date(index) for index in df.index]
        df = df[df.index.notna()]
        df.index.name = "trade_date"
        df = df.sort_index()
        if any(field not in df.columns for field in self.REQUIRED_FIELDS):
            return pd.DataFrame()

        self._normalize_base_columns(df)
        self._add_instrument_columns(df, code, detail)
        self._add_features(df)
        self._add_labels(df)
        self._add_eligibility(df)
        return df[self._keep_columns()].reset_index()

    def _normalize_base_columns(self, df: pd.DataFrame) -> None:
        for field in self.REQUIRED_FIELDS:
            df[field] = pd.to_numeric(df[field], errors="coerce")
        if "suspendFlag" in df.columns:
            df["suspendFlag"] = pd.to_numeric(df["suspendFlag"], errors="coerce").fillna(0)
        else:
            df["suspendFlag"] = 0

    def _add_instrument_columns(self, df: pd.DataFrame, code: str, detail: Dict[str, Any]) -> None:
        df["code"] = code
        df["name"] = detail.get("InstrumentName", "") if isinstance(detail, dict) else ""
        df["is_current_st"] = is_st_name(df["name"].iloc[0])
        open_date = date_int(detail.get("OpenDate"), self.config.open_date_special_values) if isinstance(detail, dict) else None
        df["listing_days"] = np.nan
        if open_date:
            open_dt = pd.to_datetime(str(open_date), format="%Y%m%d")
            trade_dt = pd.to_datetime(pd.Series(df.index, index=df.index), format="%Y%m%d")
            df["listing_days"] = (trade_dt - open_dt).dt.days.astype(float)

    def _add_features(self, df: pd.DataFrame) -> None:
        close = df["close"]
        open_price = df["open"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        amount = df["amount"]
        ret_1 = close.pct_change()
        prev_close = close.shift(1)

        df["ret_1"] = ret_1
        for window in (3, 5, 10, 20):
            df[f"ret_{window}"] = close.pct_change(window)
        df["open_gap"] = safe_divide(open_price, prev_close) - 1

        for window in (5, 10, 20):
            ma = close.rolling(window).mean()
            df[f"ma{window}_dev"] = safe_divide(close, ma) - 1
            df[f"volatility_{window}"] = ret_1.rolling(window).std()

        df["drawdown_20"] = safe_divide(close, close.rolling(20).max()) - 1
        df["amplitude"] = safe_divide(high - low, prev_close)
        df["amplitude_5_mean"] = df["amplitude"].rolling(5).mean()
        df["amount_5_mean"] = amount.rolling(5).mean()
        df["amount_20_mean"] = amount.rolling(20).mean()
        df["amount_ratio_5_20"] = safe_divide(df["amount_5_mean"], df["amount_20_mean"])
        df["volume_ratio_5_20"] = safe_divide(volume.rolling(5).mean(), volume.rolling(20).mean())
        rolling_low_20 = low.rolling(20).min()
        rolling_high_20 = high.rolling(20).max()
        df["close_position_20"] = safe_divide(close - rolling_low_20, rolling_high_20 - rolling_low_20)

    def _add_labels(self, df: pd.DataFrame) -> None:
        open_price = df["open"]
        low = df["low"]
        entry_open = open_price.shift(-1)
        exit_open = open_price.shift(-(self.config.hold_days_for_label + 1))
        next_exit_open = open_price.shift(-2)
        future_min_low = pd.concat(
            [low.shift(-i) for i in range(1, self.config.hold_days_for_label + 1)],
            axis=1,
        ).min(axis=1)
        df["entry_date"] = pd.Series(df.index, index=df.index).shift(-1)
        df["exit_date"] = pd.Series(df.index, index=df.index).shift(-(self.config.hold_days_for_label + 1))
        df["target_return_5d"] = safe_divide(exit_open, entry_open) - 1
        df["target_up_5d"] = (df["target_return_5d"] > 0).astype(int)
        df["future_max_drawdown_5d"] = safe_divide(future_min_low, entry_open) - 1
        df["target_risk_5d"] = (df["future_max_drawdown_5d"] <= self.config.risk_drawdown_threshold).astype(int)
        df["realized_next_open_return"] = safe_divide(next_exit_open, entry_open) - 1

    def _add_eligibility(self, df: pd.DataFrame) -> None:
        df["base_eligible"] = (
            (df["suspendFlag"] == 0)
            & (~df["is_current_st"])
            & (df["listing_days"].fillna(self.config.min_listing_days + 1) >= self.config.min_listing_days)
            & (df["amount_20_mean"] >= self.config.min_avg_amount_20)
            & df[self.REQUIRED_FIELDS].notna().all(axis=1)
        )

    def _keep_columns(self) -> List[str]:
        return [
            "code",
            "name",
            "entry_date",
            "exit_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "suspendFlag",
            "listing_days",
            "is_current_st",
            "base_eligible",
            "target_return_5d",
            "target_up_5d",
            "future_max_drawdown_5d",
            "target_risk_5d",
            "realized_next_open_return",
        ] + self.config.market_feature_cols
