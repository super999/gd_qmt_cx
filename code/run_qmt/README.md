# run_qmt 目录说明

## 目的

本目录保存 QMT 内运行脚本、实时行情验证脚本、盘中监控程序。

## 当前脚本

- `subscribe_quote_510300.py`
  - QMT 内单只标的行情订阅验证脚本
  - 验证 `ContextInfo.subscribe_quote`
- `subscribe_whole_quote.py`
  - QMT 内全推行情订阅验证脚本
  - 验证 `ContextInfo.subscribe_whole_quote`
- `intraday_low_absorb_monitor.py`
  - 510300 盘中低位承接模拟监控程序
  - 当前已实现 `replay` 历史回放模式
  - 固定使用 `盘中弱势低位-量能修复` 预警
  - 输出信号后第 2/3 根 5m K 线候选买入提示
  - 当前模拟持仓主口径为信号后第 3 根 5m K 线开盘买，第 3 个交易日尾盘退出

## replay 示例

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'd:\python_envs\gd_qmt_env\python.exe' 'code\run_qmt\intraday_low_absorb_monitor.py' --mode replay
```

指定区间：

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'd:\python_envs\gd_qmt_env\python.exe' 'code\run_qmt\intraday_low_absorb_monitor.py' --mode replay --start-date 20260101 --end-date 20260424
```

## 输出

- `code/run_qmt/outputs/intraday_low_absorb_monitor/replay_event_log.csv`
- `code/run_qmt/outputs/intraday_low_absorb_monitor/replay_simulated_trades.csv`
- `报告/研究结论/当前主线/510300盘中模拟监控历史回放.md`
- `报告/研究结论/数据摘要/510300盘中模拟监控历史回放事件日志.csv`
- `报告/研究结论/数据摘要/510300盘中模拟监控历史回放模拟持仓.csv`
- `报告/研究结论/数据摘要/510300盘中模拟监控历史回放摘要.csv`

## 当前边界

- `replay` 用历史数据验证日志时点和状态流转。
- `live` 尚未实现，下一步应在本脚本上接 MiniQMT 实时 5m K 线或定时拉取最新 K 线。
- 当前程序只输出提示和模拟日志，不自动下单。
