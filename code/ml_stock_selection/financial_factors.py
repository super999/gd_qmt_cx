from __future__ import annotations

import time
from typing import Dict, List

import pandas as pd

from config import StrategyConfig
from financial_data import FinancialCacheLoader


PERSHARE_INDEX_FIELD_MAP: Dict[str, str] = {
    "inc_revenue_rate": "fin_inc_revenue_rate",
    "du_profit_rate": "fin_du_profit_rate",
    "inc_net_profit_rate": "fin_inc_net_profit_rate",
    "du_return_on_equity": "fin_du_return_on_equity",
    "gross_profit": "fin_gross_profit",
    "net_profit": "fin_net_profit",
    "sales_cash_flow": "fin_sales_cash_flow",
    "gear_ratio": "fin_gear_ratio",
    "inventory_turnover": "fin_inventory_turnover",
}


class FinancialFactorBuilder:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.cache_loader = FinancialCacheLoader(config)

    def build_for_dataset(self, dataset: pd.DataFrame) -> pd.DataFrame:
        if dataset.empty:
            return pd.DataFrame()

        started = time.perf_counter()
        source = self.cache_loader.load_pershare_index()
        print("财务因子: 读取 PershareIndex 缓存 {} 行".format(len(source)))
        source = self._prepare_source(source)
        print("财务因子: 标准化后可用公告记录 {} 行，用时 {:.1f}s".format(len(source), time.perf_counter() - started))
        if source.empty:
            return self._empty_result(dataset)

        frames: List[pd.DataFrame] = []
        trade_dates = dataset[["code", "trade_date"]].drop_duplicates().copy()
        trade_dates["trade_dt"] = pd.to_datetime(trade_dates["trade_date"], format="%Y%m%d", errors="coerce")
        trade_dates = trade_dates.dropna(subset=["trade_dt"])
        total_codes = int(trade_dates["code"].nunique())
        total_rows = len(trade_dates)
        source_by_code = {
            code: frame.sort_values(["announce_dt", "report_dt"]).drop_duplicates("announce_dt", keep="last")
            for code, frame in source.groupby("code", sort=False)
        }
        print("财务因子: 待合并股票 {} 只，交易日样本 {} 行".format(total_codes, total_rows))

        matched_codes = 0
        matched_rows = 0
        for index, (code, left) in enumerate(trade_dates.groupby("code", sort=False), start=1):
            right = source_by_code.get(code)
            if right is None or right.empty:
                frames.append(self._empty_code_result(left))
            else:
                matched_codes += 1
                merged = pd.merge_asof(
                    left.sort_values("trade_dt"),
                    right.sort_values("announce_dt"),
                    left_on="trade_dt",
                    right_on="announce_dt",
                    direction="backward",
                    allow_exact_matches=False,
                )
                merged["code"] = code
                matched_rows += int(merged["fin_source_m_anntime"].notna().sum()) if "fin_source_m_anntime" in merged.columns else 0
                frames.append(merged)
            if index % 500 == 0 or index == total_codes:
                print(
                    "财务因子: 合并进度 {}/{}，有财务缓存股票 {}，已匹配样本 {}，累计 {:.1f}s".format(
                        index,
                        total_codes,
                        matched_codes,
                        matched_rows,
                        time.perf_counter() - started,
                    )
                )

        if not frames:
            return self._empty_result(dataset)
        result = pd.concat(frames, ignore_index=True)
        keep_cols = ["code", "trade_date", "fin_source_m_anntime", "fin_source_m_timetag"] + self.config.financial_feature_cols
        print("财务因子: 合并完成，输出 {} 行，总用时 {:.1f}s".format(len(result), time.perf_counter() - started))
        return result[keep_cols]

    def _prepare_source(self, source: pd.DataFrame) -> pd.DataFrame:
        required = {"code", "m_anntime", "m_timetag"}
        if not required.issubset(source.columns):
            missing = sorted(required - set(source.columns))
            raise RuntimeError("PershareIndex 财务缓存缺少必要字段: {}".format(", ".join(missing)))

        available_fields = [field for field in PERSHARE_INDEX_FIELD_MAP if field in source.columns]
        if not available_fields:
            raise RuntimeError("PershareIndex 财务缓存中没有找到第一批财务因子字段。")

        df = source[["code", "m_anntime", "m_timetag"] + available_fields].copy()
        df["announce_dt"] = pd.to_datetime(df["m_anntime"], format="%Y%m%d", errors="coerce")
        df["report_dt"] = pd.to_datetime(df["m_timetag"], format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["announce_dt"])
        df = df.sort_values(["code", "announce_dt", "report_dt"])
        df = df.rename(columns=PERSHARE_INDEX_FIELD_MAP)
        for column in self.config.financial_feature_cols:
            if column not in df.columns:
                df[column] = pd.NA
            df[column] = pd.to_numeric(df[column], errors="coerce")
        df["fin_source_m_anntime"] = df["m_anntime"]
        df["fin_source_m_timetag"] = df["m_timetag"]
        return df

    def _empty_result(self, dataset: pd.DataFrame) -> pd.DataFrame:
        result = dataset[["code", "trade_date"]].drop_duplicates().copy()
        result["fin_source_m_anntime"] = pd.NA
        result["fin_source_m_timetag"] = pd.NA
        for column in self.config.financial_feature_cols:
            result[column] = pd.NA
        return result

    def _empty_code_result(self, left: pd.DataFrame) -> pd.DataFrame:
        result = left[["code", "trade_date"]].copy()
        result["fin_source_m_anntime"] = pd.NA
        result["fin_source_m_timetag"] = pd.NA
        for column in self.config.financial_feature_cols:
            result[column] = pd.NA
        return result
