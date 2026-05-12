# miniqmt_tools 目录说明

本目录保存外部 Python 直接连接 MiniQMT 的工具程序。

## 边界约定

- `code/run_qmt/`：QMT 内运行的策略脚本，需要复制或导入到 QMT 策略环境中，再在 QMT 里手动启动。
- `code/miniqmt_tools/`：外部 Python 工具脚本，通过 `xtquant.xtdata` 与已打开的 MiniQMT 客户端交互，不需要把脚本复制到 QMT 里，也不需要在 QMT 策略界面手动点启动。
- `code/run_xtquant/`：保留为 `xtquant` API 冒烟测试、接口矩阵测试和订阅链路验证脚本目录，不再新增面向业务查询的 MiniQMT 工具程序。

## 当前脚本

- `find_stocks_by_price_range.py`
  - 按指定日期、代码前缀、市场后缀和价格区间筛选股票。
  - 默认会先对目标股票池补下载目标日期历史行情，再读取和筛选。
- `check_missing_market_data.py`
  - 检查目标股票池在指定日期范围内的本地行情缺失情况。
  - 默认只检查、不下载，输出缺失清单，便于手动补全后再次检查。

## 推荐运行环境

```powershell
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/find_stocks_by_price_range.py
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/check_missing_market_data.py
```

运行前请确认 MiniQMT 已启动，且行情连接正常。
