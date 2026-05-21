from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


FEATURE_LABELS: Dict[str, str] = {
    "ret_1": "近1日涨跌幅",
    "ret_3": "近3日涨跌幅",
    "ret_5": "近5日涨跌幅",
    "ret_10": "近10日涨跌幅",
    "ret_20": "近20日涨跌幅",
    "open_gap": "开盘跳空幅度",
    "ma5_dev": "收盘相对5日均线偏离",
    "ma10_dev": "收盘相对10日均线偏离",
    "ma20_dev": "收盘相对20日均线偏离",
    "volatility_5": "近5日收益波动率",
    "volatility_10": "近10日收益波动率",
    "volatility_20": "近20日收益波动率",
    "drawdown_20": "相对20日最高收盘回撤",
    "amplitude": "当日振幅",
    "amplitude_5_mean": "近5日平均振幅",
    "amount_5_mean": "近5日平均成交额",
    "amount_20_mean": "近20日平均成交额",
    "amount_ratio_5_20": "近5日成交额相对20日比例",
    "volume_ratio_5_20": "近5日成交量相对20日比例",
    "close_position_20": "收盘在20日高低区间位置",
}


FINANCIAL_FEATURE_LABELS: Dict[str, str] = {
    "fin_inc_revenue_rate": "主营收入同比增长",
    "fin_du_profit_rate": "净利润同比增长",
    "fin_inc_net_profit_rate": "归母净利润同比增长",
    "fin_du_return_on_equity": "净资产收益率",
    "fin_gross_profit": "毛利率",
    "fin_net_profit": "净利率",
    "fin_sales_cash_flow": "销售现金流/营业收入",
    "fin_gear_ratio": "资产负债率",
    "fin_inventory_turnover": "存货周转率",
}


@dataclass(frozen=True)
class StrategyConfig:
    start_date: str = "20230101"
    end_date: str = "20260511"
    target_sectors: List[str] = field(default_factory=lambda: ["上证A股", "深证A股"])
    market_suffixes: List[str] = field(default_factory=lambda: [".SH", ".SZ"])
    period: str = "1d"
    price_adjustment: str = "front"
    fill_data: bool = False
    batch_size: int = 300
    hold_days_for_label: int = 5
    top_n: int = 20
    rebalance_frequency: str = "daily"
    hold_rank_buffer: int = 0
    min_holding_days: int = 1
    transaction_cost_rate: float = 0.0003
    min_listing_days: int = 60
    min_avg_amount_20: int = 20_000_000
    risk_drawdown_threshold: float = -0.05
    max_risk_score: float = 70.0
    min_train_samples: int = 3000
    retrain_every_n_days: int = 20
    min_prediction_date: str = "20240101"
    random_state: int = 20260514
    output_dir: Path = Path(__file__).resolve().parent / "outputs" / "lightgbm_multi_factor_stock_selection"
    financial_cache_dir: Path = Path(__file__).resolve().parent / "outputs" / "financial_cache"
    use_financial_factors: bool = False
    feature_labels: Dict[str, str] = field(default_factory=lambda: FEATURE_LABELS.copy())
    financial_feature_labels: Dict[str, str] = field(default_factory=lambda: FINANCIAL_FEATURE_LABELS.copy())
    open_date_special_values: set[str] = field(
        default_factory=lambda: {"19700101", "19700102", "19700103", "19700104", "19700105", "19700106"}
    )

    @property
    def market_feature_cols(self) -> List[str]:
        return list(self.feature_labels.keys())

    @property
    def active_feature_labels(self) -> Dict[str, str]:
        labels = self.feature_labels.copy()
        if self.use_financial_factors:
            labels.update(self.financial_feature_labels)
        return labels

    @property
    def financial_feature_cols(self) -> List[str]:
        return list(self.financial_feature_labels.keys()) if self.use_financial_factors else []

    @property
    def feature_cols(self) -> List[str]:
        return list(self.active_feature_labels.keys())
