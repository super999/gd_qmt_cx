from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

try:
    import lightgbm as lgb
except Exception as exc:  # pragma: no cover - explicit runtime guard
    raise RuntimeError(
        "LightGBM 未安装或无法导入。请先运行："
        "d:\\python_envs\\gd_qmt_env\\python.exe -m pip install lightgbm"
    ) from exc

from config import StrategyConfig
from data import DailyMarketDataLoader, StockUniverseService
from dataset import FactorDatasetBuilder
from modeling import WalkForwardModeler
from portfolio import DailyRebalanceBacktester, PortfolioSelector
from reporting import ResultWriter


@dataclass
class ResearchArtifacts:
    codes: List[str]
    dataset: pd.DataFrame
    predictions: pd.DataFrame
    selections: pd.DataFrame
    daily_nav: pd.DataFrame
    trades: pd.DataFrame
    train_logs: List[Dict]
    feature_importance: Optional[pd.DataFrame]
    output_paths: Dict[str, Path]


class ResearchPipeline:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.universe_service = StockUniverseService(config)
        self.market_data_loader = DailyMarketDataLoader(config)
        self.dataset_builder = FactorDatasetBuilder(config)
        self.modeler = WalkForwardModeler(config)
        self.selector = PortfolioSelector(config)
        self.backtester = DailyRebalanceBacktester(config)
        self.result_writer = ResultWriter(config, lgb.__version__)

    def run(self, max_stocks: Optional[int]) -> ResearchArtifacts:
        codes, details = self.universe_service.load(max_stocks)
        print("股票池数量: {}".format(len(codes)))
        data_by_code = self.market_data_loader.load(codes)
        dataset = self.dataset_builder.build(data_by_code, details)
        if dataset.empty:
            raise RuntimeError("未生成数据集，请检查本地行情数据。")
        predictions, train_logs, feature_importance = self.modeler.predict(dataset)
        if predictions.empty:
            raise RuntimeError("未生成预测结果，请降低 min_train_samples 或扩大日期/股票范围。")
        selections = self.selector.select(predictions)
        daily_nav, trades = self.backtester.run(selections)
        output_paths = self.result_writer.write_all(
            codes,
            dataset,
            predictions,
            selections,
            daily_nav,
            trades,
            train_logs,
            feature_importance,
            max_stocks,
        )
        return ResearchArtifacts(
            codes=codes,
            dataset=dataset,
            predictions=predictions,
            selections=selections,
            daily_nav=daily_nav,
            trades=trades,
            train_logs=train_logs,
            feature_importance=feature_importance,
            output_paths=output_paths,
        )
