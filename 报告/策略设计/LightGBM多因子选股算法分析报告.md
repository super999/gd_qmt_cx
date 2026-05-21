# LightGBM 多因子选股算法分析报告

**生成日期**: 2026-05-15

---

## 1. 数据来源

| 项目 | 说明 |
|------|------|
| **股票池** | 上证A股 + 深证A股（`.SH` / `.SZ`） |
| **数据源** | MiniQMT 本地日线行情（通过 `xtdata.get_local_data`） |
| **原始字段** | open, high, low, close, volume, amount |

---

## 2. 选取的特征（20个因子）

| 类别 | 因子 | 含义 |
|------|------|------|
| **动量** | ret_1 | 近1日涨跌幅 |
|  | ret_3 | 近3日涨跌幅 |
|  | ret_5 | 近5日涨跌幅 |
|  | ret_10 | 近10日涨跌幅 |
|  | ret_20 | 近20日涨跌幅 |
| **开盘** | open_gap | 开盘跳空幅度 |
| **均线偏离** | ma5_dev | 收盘相对5日均线偏离 |
|  | ma10_dev | 收盘相对10日均线偏离 |
|  | ma20_dev | 收盘相对20日均线偏离 |
| **波动率** | volatility_5 | 近5日收益波动率 |
|  | volatility_10 | 近10日收益波动率 |
|  | volatility_20 | 近20日收益波动率 |
| **回撤** | drawdown_20 | 相对20日最高收盘回撤 |
| **振幅** | amplitude | 当日振幅 |
|  | amplitude_5_mean | 近5日平均振幅 |
| **成交额/量** | amount_5_mean | 近5日平均成交额 |
|  | amount_20_mean | 近20日平均成交额 |
|  | amount_ratio_5_20 | 近5日成交额相对20日比例 |
|  | volume_ratio_5_20 | 近5日成交量相对20日比例 |
| **位置** | close_position_20 | 收盘在20日高低区间位置 |

---

## 3. 标签设计

| 标签 | 说明 |
|------|------|
| **target_return_5d** | 明日开盘买入 → 第6日开盘卖出 的收益率 |
| **target_up_5d** | 5日后是否上涨（二分类标签） |
| **target_risk_5d** | 未来5日是否出现超过5%的回撤（风险标签） |

---

## 4. 模型结构

采用 **Walk-Forward** 训练策略：每20天重新训练模型，使用历史数据。

### 三个 LightGBM 模型

| 模型 | 类型 | 用途 | 参数 |
|------|------|------|------|
| **return_model** | 回归 | 预测5日收益 | 160棵树, lr=0.05, 31叶子 |
| **up_model** | 二分类 | 预测上涨概率 | 120棵树, lr=0.05, 31叶子 |
| **risk_model** | 二分类 | 预测风险概率 | 120棵树, lr=0.05, 31叶子 |

---

## 5. 选股逻辑

1. **风险过滤**：risk_score ≤ 70 才保留
2. **排序**：按 `pred_return_5d` 和 `pred_up_prob` 降序排列
3. **持仓**：top_n = 20 只，等权重分配

---

## 6. 你的运行参数

```
--start-date 20250101
--end-date 20260511
--min-train-samples 200
--min-prediction-date 20260101
```

### 参数解读

| 参数 | 值 | 说明 |
|------|-----|------|
| start_date | 20250101 | 数据开始日期 |
| end_date | 20260511 | 数据结束日期 |
| min_train_samples | 200 | 最小训练样本数（默认3000，你设为200用于快速测试） |
| min_prediction_date | 20260101 | 预测开始日期 |

> **注意**：由于 `--min-prediction-date 20260101`，而 `--end-date 20260511`，实际预测期约 4.5 个月。如果本地日线数据只到 2025 年，可能没有实际预测结果。

---

## 7. 筛选条件（base_eligible）

| 条件 | 说明 |
|------|------|
| suspendFlag == 0 | 未停牌 |
| is_current_st == False | 非ST股 |
| listing_days ≥ 60 | 上市天数 ≥ 60天 |
| amount_20_mean ≥ 20,000,000 | 20日平均成交额 ≥ 2000万 |

---

## 8. 配置文件关键参数

详见 `config.py`：

```python
hold_days_for_label = 5           # 持有天数
top_n = 20                        # 持仓数量
transaction_cost_rate = 0.0003   # 单边��易成本
min_listing_days = 60             # 最小上市天数
min_avg_amount_20 = 20_000_000    # 最小成交额
max_risk_score = 70.0             # 最大风险分数
retrain_every_n_days = 20         # 每20天重新训练
random_state = 20260514           # 随机种子
```

---

## 9. 文件结构

```
code/ml_stock_selection/
├── lightgbm_multi_factor_stock_selection.py  # 入口
├── config.py                                   # 配置
├── data.py                                     # 数据读取
├── dataset.py                                  # 特征工程
├── modeling.py                                 # 模型训练
├── portfolio.py                                # 选股与回测
├── pipeline.py                                 # 主流程
└── reporting.py                                # 报告输出
```