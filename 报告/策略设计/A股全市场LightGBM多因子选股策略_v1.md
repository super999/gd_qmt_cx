# A股全市场 LightGBM 多因子选股策略 v1

## 定位

这是一个独立的离线研究分支，不替换当前 `510300.SH` 低吸反弹主线，不接 QMT 内策略，也不做真实下单。

第一版目标是验证：

- A股全市场纯行情因子是否能形成可用的横截面排序能力。
- LightGBM 对未来 5 日收益、涨跌方向、回撤风险是否有可解释预测力。
- 每日 Top20 等权调仓在扣除单边万分之3成本后是否仍有研究价值。

## 当前实现

入口脚本：

```text
code/ml_stock_selection/lightgbm_multi_factor_stock_selection.py
```

运行环境：

```text
d:\python_envs\gd_qmt_env
```

核心口径：

- 股票池：`上证A股` + `深证A股`
- 数据接口：`xtdata.get_local_data`
- 周期：`1d`
- 复权：`front`
- 填充：`fill_data=False`
- 训练方式：时间序列 walk-forward，不随机打乱
- 交易：每日收盘后打分，次日开盘调仓
- 持仓：Top20 等权
- 成本：单边 `0.0003`

## 特征和标签

第一版只使用行情因子：

- 近 1/3/5/10/20 日收益
- 均线偏离
- 波动率
- 20 日回撤
- 当日及近 5 日振幅
- 成交额均值与成交额变化
- 成交量变化
- 收盘价在 20 日高低区间的位置

标签：

- 主标签：未来 5 日 open-to-open 收益
- 涨跌标签：未来 5 日收益是否大于 0
- 风险标签：未来 5 日内最大不利波动是否小于等于 `-5%`

## 运行方式

小样本冒烟：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/ml_stock_selection/lightgbm_multi_factor_stock_selection.py --max-stocks 50 --start-date 20250101 --end-date 20260511 --min-train-samples 200
```

全量：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/ml_stock_selection/lightgbm_multi_factor_stock_selection.py
```

## 已知限制

- 第一版不使用财务数据。
- 当前 ST 过滤基于当前合约名称，不是逐历史日期的 ST 状态。
- 交易成本按组合换手扣除，暂不模拟涨跌停无法成交、盘口冲击、成交量约束。
- LightGBM 是当前环境新增依赖，版本需要在报告中记录。
- 输出按每次运行单独的 `run_id` 子目录保存，避免不同参数实验互相覆盖。

## 后续验收重点

- 小样本冒烟能完整生成数据集、预测、组合、净值和报告。
- 全量运行时需要重点检查缺失行情、停牌、新股和低成交额过滤数量。
- 若平均日截面 Rank IC 接近 0 或为负，优先回到特征和标签定义，不直接调模型参数。
- 若含成本结果与不计成本结果差距过大，说明每日滚动 Top20 换手过高，应进一步研究低换手调仓约束。
