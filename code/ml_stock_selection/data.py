from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from xtquant import xtdata

from config import StrategyConfig
from utils import chunked


class StockUniverseService:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def load(self, max_stocks: Optional[int]) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
        sector_list = set(xtdata.get_sector_list())
        codes: List[str] = []
        for sector in self.config.target_sectors:
            if sector not in sector_list:
                print("WARN: 未找到板块 {}".format(sector))
                continue
            for code in xtdata.get_stock_list_in_sector(sector):
                if self.config.market_suffixes and not any(code.endswith(suffix) for suffix in self.config.market_suffixes):
                    continue
                codes.append(code)

        codes = sorted(set(codes))
        if max_stocks:
            codes = codes[:max_stocks]

        details: Dict[str, Dict[str, Any]] = {}
        for index, code in enumerate(codes, start=1):
            detail = xtdata.get_instrument_detail(code)
            if isinstance(detail, dict):
                details[code] = detail
            if index % 500 == 0:
                print("读取合约信息: {}/{}".format(index, len(codes)))
        return codes, details


class DailyMarketDataLoader:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def load(self, codes: List[str]) -> Dict[str, pd.DataFrame]:
        all_data: Dict[str, pd.DataFrame] = {}
        for batch_index, batch_codes in enumerate(chunked(codes, self.config.batch_size), start=1):
            print("读取日线行情批次 {}: {} 个标的".format(batch_index, len(batch_codes)))
            data = xtdata.get_local_data(
                field_list=[],
                stock_list=batch_codes,
                period=self.config.period,
                start_time=self.config.start_date,
                end_time=self.config.end_date,
                count=-1,
                dividend_type=self.config.price_adjustment,
                fill_data=self.config.fill_data,
            )
            all_data.update(data)
        return all_data
