# backtest 目录说明

## 目的

本目录保存回测、事件研究、特征分析、评分建模相关脚本。  
这里以“脚本入口”组织，不以“报告结论”组织。

如果你想看结论，请优先去：

- `报告/研究结论/当前主线/`
- `报告/研究结论/历史探索/`

如果你想看代码入口，再回到这里。

## 当前脚本分组

### 当前主研究脚本

- `find_best_trade_intervals.py`
  - 当前三步法第 1 步：只用日线扫描近一年最优交易日期区间
  - 不使用候选A/B，不使用分钟级盘中信号，不调整策略参数
- `analyze_best_interval_entry_signals.py`
  - 当前三步法第 2 步：分析优质交易区间买入日的日线背景与盘中特征
  - 使用正样本/对照样本的标准化差异、单特征AUC、Mann-Whitney检验排序
- `backtest_statistical_entry_rules.py`
  - 当前三步法第 3 步：将统计特征整理成候选买入规则并做回测验证
  - 不沿用旧强 V 规则；输出逐信号观察和非重叠策略回测两类结果
- `backtest_intraday_statistical_warning.py`
  - 将表现较稳的日线弱势低位规则改写为盘中5分钟预警历史回放
  - 只使用前一日已确认日线背景和当前已完成5分钟K线
- `backtest_intraday_entry_offsets.py`
  - 当前五步路线第 4 步：验证盘中预警后第 1/2/3 根 5m K线开盘买入
  - 用于决定盘中真实买入口径是否可进入卖点设计
- `backtest_intraday_exit_rules.py`
  - 当前五步路线第 5 步：在已确认买入口径基础上单独验证卖点
  - 固定买点为 `盘中弱势低位-量能修复`，只比较时间退出、固定止盈止损、移动保护等卖点
- `scan_510300_rebound_events.py`
  - 扫描 `510300` 的低吸反弹事件候选样本
- `analyze_510300_rebound_features.py`
  - 分析事件日前静态特征
- `analyze_510300_event_profiles.py`
  - 分析事件日前窗口剖面
- `analyze_510300_v_reversal_multiframe.py`
  - 分析 `1m/5m/30m` 多时间框架 V 字结构
- `build_n5_r3_vscore_model.py`
  - 构建 `n5_r3` 首版 V 字结构评分
- `build_n5_r3_bg_trigger_scores.py`
  - 拆分背景分与触发分
- `feature_labels.py`
  - 特征字段英文到中文的统一映射

### 当前实验策略 / 回测脚本

- `minimal_stock_backtest.py`
  - 当前实验版单标的回测脚本
- `backtest_n5_r3_candidate_rules.py`
  - 当前 `n5_r3` 候选A/候选B 显式规则回测脚本
  - 候选A：`background_score >= 3` 且 `trigger_score >= 2`
  - 候选B：`background_score >= 3` 且 `trigger_score >= 1`
  - 该脚本用于第一轮闭环验证，不等于最终 QMT 实盘策略
- `backtest_intraday_v_reversal_signal.py`
  - 盘中即时 V 型反转信号历史回放脚本
  - 按已完成 `1m/5m` K 线逐步推进
  - 用当前盘中价格估算当日收盘相关字段
  - 使用动态时间门控，避免低点刚出现时过早触发
  - 用于验证“盘中触发即提示”是否可行
- `build_intraday_signal_review_report.py`
  - 将盘中信号与模拟交易合并成逐笔复盘报告
  - 展开每次触发时的日线背景条件、盘中触发条件和动态时间门控

注意：

- 这个脚本记录的是某一阶段的实验实现
- 它不等于当前最终策略结论
- 使用前应先读 `报告/策略设计/当前实际运行策略卡片.md`

### 历史探索脚本

- `compare_etf_backtests.py`
  - 历史横向对比，不再是当前主线
- `sweep_etf_dip_buy_params.py`
  - 历史参数扫描，不再是当前主线
- `analyze_510300_research.py`
  - 较早期的单标研究总结脚本

## outputs 目录说明

`outputs/` 目录保存脚本运行生成的原始输出：

- 中间数据集
- 原始 CSV
- 调试日志
- 临时结果

默认约定：

- `outputs/` 属于可重跑产物
- 默认不提交 Git
- 如果其中有需要长期保留的结论，应提炼后移动到 `报告/研究结论/`

## 阅读顺序建议

如果你想继续当前主研究，建议按下面顺序看：

1. `scan_510300_rebound_events.py`
2. `analyze_510300_rebound_features.py`
3. `analyze_510300_event_profiles.py`
4. `analyze_510300_v_reversal_multiframe.py`
5. `build_n5_r3_vscore_model.py`
6. `build_n5_r3_bg_trigger_scores.py`
