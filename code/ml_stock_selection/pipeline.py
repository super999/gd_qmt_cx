from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
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
        started = time.perf_counter()
        print("阶段 1/7: 读取股票池和合约信息", flush=True)
        codes, details = self.universe_service.load(max_stocks)
        print("股票池数量: {}".format(len(codes)))
        print("阶段 1/7 完成，用时 {:.1f}s".format(time.perf_counter() - started), flush=True)

        stage_started = time.perf_counter()
        print("阶段 2/7: 读取本地日线行情", flush=True)
        data_by_code = self.market_data_loader.load(codes)
        print("阶段 2/7 完成，读取标的 {} 个，用时 {:.1f}s".format(len(data_by_code), time.perf_counter() - stage_started), flush=True)

        stage_started = time.perf_counter()
        print("阶段 3/7: 构建行情特征、标签并合并财务因子", flush=True)
        dataset = self.dataset_builder.build(data_by_code, details)
        if dataset.empty:
            raise RuntimeError("未生成数据集，请检查本地行情数据。")
        print("阶段 3/7 完成，数据集 {} 行，用时 {:.1f}s".format(len(dataset), time.perf_counter() - stage_started), flush=True)

        stage_started = time.perf_counter()
        print("阶段 4/7: Walk-forward 训练并生成预测", flush=True)
        predictions, train_logs, feature_importance = self.modeler.predict(dataset)
        if predictions.empty:
            raise RuntimeError("未生成预测结果，请降低 min_train_samples 或扩大日期/股票范围。")
        print(
            "阶段 4/7 完成，预测 {} 行，训练轮数 {}，用时 {:.1f}s".format(
                len(predictions),
                len(train_logs),
                time.perf_counter() - stage_started,
            ),
            flush=True,
        )

        stage_started = time.perf_counter()
        print("阶段 5/7: 根据预测分数构造组合", flush=True)
        backtest_predictions = predictions.dropna(subset=["entry_date", "realized_next_open_return"]).copy()
        if len(backtest_predictions) < len(predictions):
            print(
                "阶段 5/7: {} 行最新预测缺少未来收益，仅用于实盘候选，不参与净值回测".format(
                    len(predictions) - len(backtest_predictions)
                ),
                flush=True,
            )
        selections = self.selector.select(backtest_predictions)
        print("阶段 5/7 完成，组合持仓记录 {} 行，用时 {:.1f}s".format(len(selections), time.perf_counter() - stage_started), flush=True)

        stage_started = time.perf_counter()
        print("阶段 6/7: 计算组合净值和交易明细", flush=True)
        daily_nav, trades = self.backtester.run(selections)
        print(
            "阶段 6/7 完成，净值 {} 行，交易明细 {} 行，用时 {:.1f}s".format(
                len(daily_nav),
                len(trades),
                time.perf_counter() - stage_started,
            ),
            flush=True,
        )

        stage_started = time.perf_counter()
        print("阶段 7/7: 写出 CSV、summary 和报告", flush=True)
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
        print("阶段 7/7 完成，用时 {:.1f}s；全流程 {:.1f}s".format(time.perf_counter() - stage_started, time.perf_counter() - started), flush=True)
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
