# ml_stock_selection 目录说明

本目录保存外部 Python 离线机器学习选股研究脚本。它是独立于当前 `510300.SH` 低吸反弹主线的新分支，不接 QMT 内策略，不做真实下单。

## 当前脚本

- `lightgbm_multi_factor_stock_selection.py`
  - A股全市场 LightGBM 多因子选股 v1。
  - 这是一个薄入口文件，只负责命令行参数和启动流程。
  - 股票池：`上证A股` + `深证A股`。
  - 数据：`xtdata.get_local_data` 日线，默认前复权 `front`，`fill_data=False`。
  - 特征：纯行情因子，不使用财务数据。
  - 标签：未来 5 日 open-to-open 收益、未来 5 日涨跌方向、未来 5 日大幅回撤风险。
  - 回测：每日收盘后打分，次日开盘 Top20 等权调仓。
  - 成本：主结果计入单边万分之3，即 `TRANSACTION_COST_RATE = 0.0003`。

## 代码结构

建议按这个顺序阅读：

1. `pipeline.py`：先看完整主流程。
2. `config.py`：再看所有参数、特征名和默认值。
3. `data.py`：股票池和日线行情读取。
4. `dataset.py`：单股清洗、特征计算、标签构建。
5. `modeling.py`：LightGBM 训练和 walk-forward 预测。
6. `portfolio.py`：TopN 选股和每日调仓回测。
7. `reporting.py`：指标、摘要和报告输出。

这样拆分后，入口文件只负责启动，不再承载具体业务逻辑。

## 推荐运行

先跑小样本冒烟：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/ml_stock_selection/lightgbm_multi_factor_stock_selection.py --max-stocks 50 --start-date 20250101 --end-date 20260511 --min-train-samples 200
```

全量运行：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/ml_stock_selection/lightgbm_multi_factor_stock_selection.py
```

## 输出

输出目录：

```text
code/ml_stock_selection/outputs/lightgbm_multi_factor_stock_selection/<run_id>/
```

每次运行都会生成新的 `run_id` 子目录，不再覆盖上一次实验。`run_id` 会包含运行时间、数据区间、最早预测日和股票池限制，例如：

```text
20260515_113000_start20250101_end20260511_pred20260101_all
```

主要文件：

- `factor_dataset.csv`：特征与标签数据集。
- `predictions.csv`：每日全市场预测分数。
- `selected_portfolio.csv`：每日 Top20 组合。
- `daily_nav.csv`：每日净值、换手和成本。
- `trades.csv`：逐日持仓明细。
- `feature_importance.csv`：LightGBM 特征重要性。
- `summary.json`：机器可读摘要。
- `report.md`：人读报告。

报告会额外打印：

- 配置的最早预测日
- 实际预测区间
- 实际组合信号区间
- 实际成交区间
- 股票池限制（全市场或前 N 只）

## 注意事项

- 运行前请确认 MiniQMT 已启动，且本地日线行情已下载。
- 第一版不使用财务数据，避免财务下载阻塞和披露日未来函数问题。
- 当前 ST 过滤基于 `get_instrument_detail` 返回的当前名称，不能代表完整历史 ST 状态。
- 交易成本按换手扣除，买入和卖出单边均使用 `0.0003`。
