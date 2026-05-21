# ml_stock_selection 目录说明

本目录保存外部 Python 离线机器学习选股研究脚本。它是独立于当前 `510300.SH` 低吸反弹主线的新分支，不接 QMT 内策略，不做真实下单。

## 当前脚本

- `lightgbm_multi_factor_stock_selection.py`
  - A股全市场 LightGBM 多因子选股 v1。
  - 这是一个薄入口文件，只负责命令行参数和启动流程。
  - 股票池：`上证A股` + `深证A股`。
  - 数据：`xtdata.get_local_data` 日线，默认前复权 `front`，`fill_data=False`。
  - 特征：默认纯行情因子；可通过本地财务缓存启用公告日口径财务因子。
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
8. `prepare_financial_data.py`：财务数据下载、读取、缓存和覆盖率诊断。
9. `financial_factors.py`：把公告日口径财务数据转成点时可见因子。
10. `run_portfolio_experiments.py`：复用已有预测结果，批量测试 TopN、调仓频率和排名缓冲。
11. `analyze_portfolio_quality.py`：复用组合实验输出，分析月度收益、最差月份和收益回撤比。

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

组合参数也可以在主脚本中直接指定：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/ml_stock_selection/lightgbm_multi_factor_stock_selection.py --top-n 50 --rebalance-frequency weekly --hold-rank-buffer 100
```

## 财务因子

模型主流程不会自动下载财务数据。若要启用财务因子，先单独准备缓存：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/ml_stock_selection/prepare_financial_data.py --max-stocks 20 --tables PershareIndex,Balance,Income,CashFlow
```

`prepare_financial_data.py` 会批量请求财务接口，默认每批 `20` 只股票。若某个批次超时或异常，会自动拆成更小批次继续尝试，直到定位到单只失败标的。可以用下面参数调整批量大小：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/ml_stock_selection/prepare_financial_data.py --max-stocks 100 --stock-batch-size 20
```

默认下载方式是 `legacy`，即 `xtdata.download_financial_data`。实测它在当前环境里比 `download_financial_data2` 更稳定。若要单独测试披露日期范围下载，可以加：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/ml_stock_selection/prepare_financial_data.py --max-stocks 1 --download-method range
```

缓存目录：

```text
code/ml_stock_selection/outputs/financial_cache/
```

主要文件：

- `raw_PershareIndex.csv`：公告日口径原始主要指标缓存。
- `financial_coverage_report.csv`：逐股票、逐表覆盖率。
- `financial_schema_report.csv`：字段、类型和非空率。
- `financial_download_failures.csv`：下载、读取、超时失败清单。

准备完成后，再启用财务因子运行：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/ml_stock_selection/lightgbm_multi_factor_stock_selection.py --use-financial-factors --max-stocks 50 --start-date 20250101 --end-date 20260511 --min-train-samples 200
```

当前第一批财务因子来自 `PershareIndex`，包括营收同比、净利润同比、归母净利润同比、ROE、毛利率、净利率、销售现金流/营业收入、资产负债率和存货周转率。合并规则是 `m_anntime < trade_date`，即公告日当天不用于当日收盘打分，只从后续交易日开始可见。

如果准备脚本出现 `timeout`：

- 先确认命令输出里的 `下载方式`。建议默认使用 `legacy`。
- 可以先用 `--max-stocks 1` 做最小验证。
- 如果只想验证近期数据，可加 `--start-date 20250101` 缩短读取范围。
- 失败不会中断全量流程，失败明细会写入 `financial_download_failures.csv`。

## 组合实验矩阵

如果已经有某次运行的 `predictions.csv`，可以不重训模型，直接批量测试组合构造：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/ml_stock_selection/run_portfolio_experiments.py --run-dirs code/ml_stock_selection/outputs/lightgbm_multi_factor_stock_selection/20260521_171405_start20200101_end20260511_pred20250101_all code/ml_stock_selection/outputs/lightgbm_multi_factor_stock_selection/20260521_171836_start20200101_end20260511_pred20250101_all --labels market_only financial
```

默认矩阵：

- TopN：`20 / 50 / 100`
- 调仓频率：`daily / weekly`
- 排名缓冲：`0 / TopN*2`

也可以指定更细的矩阵：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/ml_stock_selection/run_portfolio_experiments.py --run-dirs <run_dir_1> <run_dir_2> --labels market_only financial --top-n-values 30,50,80,100,150 --rebalance-frequencies weekly --buffer-multipliers 0,1,1.5,2,3
```

输出目录：

```text
code/ml_stock_selection/outputs/portfolio_experiments/<run_id>/
```

主要输出：

- `experiment_summary.csv`：所有组合实验汇总。
- `experiment_report.md`：按含成本收益排序的人读报告。
- 每个实验子目录下会保存 `selected_portfolio.csv`、`daily_nav.csv`、`trades.csv`、`summary.json`。

收益质量分析：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/ml_stock_selection/analyze_portfolio_quality.py --experiment-dirs code/ml_stock_selection/outputs/portfolio_experiments/20260521_213609 code/ml_stock_selection/outputs/portfolio_experiments/20260521_210924
```

`buffer0` 表示没有排名缓冲：每次调仓时只看当期排名，旧持仓没有保留优先权。  
例如 `top150_weekly_buffer450` 表示每周调仓、目标持有 150 只；旧持仓只要当期排名仍在前 450，就优先保留，再用新高分股票补足 150 只。

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
- 财务因子只读取本地缓存；如果缓存不存在，需要先运行 `prepare_financial_data.py`。
- 财务因子允许缺失，LightGBM 会处理 `NaN`，报告会输出每个财务因子的覆盖率。
- 交易成本按换手扣除，买入和卖出单边均使用 `0.0003`。
