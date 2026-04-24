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
