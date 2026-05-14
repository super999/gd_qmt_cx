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
  - 全市场单日 Local-First 行情缺失检查，默认扫描 `上证A股` + `深证A股`。
  - 默认先用 `get_local_data` 检查本地行情，不自动下载。
  - 如需只下载缺失股票并复查，把脚本顶部的 `DOWNLOAD_MISSING_AFTER_LOCAL_CHECK` 改为 `True`。
  - `fill_data=False` 是缺失检查的固定口径；`fill_data=True` 会用前一条数据填充缺失 K 线，可能掩盖真实缺口。
  - 有行情行时会额外读取 `suspendFlag`；`suspendFlag` 非 0 的股票单独输出为“停牌标记”，不混入缺失清单。
  - 输出初始缺失清单和停牌标记清单；如果触发下载，再输出下载后仍缺失清单和下载后停牌标记清单。
- `example_download_then_get_market_data_ex.py`
  - 单只股票示例：先直接 `get_market_data_ex`，再 `download_history_data`，再 `get_market_data_ex`。
  - 用下载前后对比说明“取不到历史行情是否因为未下载”。
- `example_get_local_data.py`
  - 单只股票示例：先 `download_history_data`，再用 `get_local_data` 从本地行情库读取。
  - 同时用 `get_market_data_ex` 做对照，便于理解两个接口的返回结构。

## 推荐运行环境

```powershell
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/find_stocks_by_price_range.py
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/check_missing_market_data.py
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/example_download_then_get_market_data_ex.py
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/example_get_local_data.py
```

运行前请确认 MiniQMT 已启动，且行情连接正常。
