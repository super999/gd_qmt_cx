from __future__ import annotations

from pathlib import Path

import pandas as pd

from config import StrategyConfig
from utils import normalize_date


class FinancialCacheLoader:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def load_pershare_index(self) -> pd.DataFrame:
        return self.load_table("PershareIndex")

    def load_table(self, table: str) -> pd.DataFrame:
        path = self.config.financial_cache_dir / "raw_{}.csv".format(table)
        if not path.exists():
            raise FileNotFoundError(
                "未找到财务缓存 {}。请先运行 prepare_financial_data.py 生成缓存。".format(path)
            )
        frame = pd.read_csv(path, encoding="utf-8-sig")
        if frame.empty:
            raise RuntimeError("财务缓存为空: {}".format(path))
        return self._normalize_dates(frame)

    def _normalize_dates(self, frame: pd.DataFrame) -> pd.DataFrame:
        df = frame.copy()
        for column in ("m_anntime", "m_timetag", "declareDate", "endDate"):
            if column in df.columns:
                df[column] = df[column].map(normalize_date)
        if "code" in df.columns:
            df["code"] = df["code"].astype(str)
        return df


def financial_cache_path(cache_dir: Path, table: str) -> Path:
    return cache_dir / "raw_{}.csv".format(table)
