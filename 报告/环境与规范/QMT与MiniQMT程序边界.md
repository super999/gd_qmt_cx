# QMT 与 MiniQMT 程序边界

## 当前项目约定

本项目后续区分三类脚本，避免把外部工具误放进 QMT 内策略目录，或把 QMT 内策略误当作本地 Python 工具运行。

## 1. QMT 内策略脚本

- 目录：`code/run_qmt/`
- 运行方式：需要复制或导入到 QMT 策略环境中，然后在 QMT 里手动启动。
- 典型对象：使用 `ContextInfo`、`init(ContextInfo)`、`handlebar(ContextInfo)`、`ContextInfo.subscribe_quote` 的脚本。
- 适用场景：QMT 内置策略运行、QMT 内回调、QMT 策略面板启动。

## 2. MiniQMT 外部 Python 工具

- 目录：`code/miniqmt_tools/`
- 运行方式：直接用本地 Python 运行，不需要把脚本复制到 QMT，也不需要在 QMT 策略界面手动点启动。
- 依赖方式：通过 `xtquant.xtdata` 连接已经打开并正常联网的 MiniQMT 客户端。
- 典型对象：行情筛选、行情缺失检查、批量取数、外部研究工具。
- 当前推荐 Python：`d:\python_envs\gd_qmt_env\python.exe`

## 3. xtquant 接口验证脚本

- 目录：`code/run_xtquant/`
- 运行方式：本地 Python 运行。
- 用途：保留为 `xtquant` / `xtdata` 冒烟测试、API 矩阵测试、订阅链路验证。
- 约束：后续业务型 MiniQMT 工具不再新增到该目录，优先放到 `code/miniqmt_tools/`。

## 关键提醒

- MiniQMT 外部工具虽然也会 `import xtquant.xtdata`，但它们不是 QMT 内策略脚本。
- 外部工具的前提是 MiniQMT 客户端已打开、行情连接正常，并且本地历史行情数据已下载或可被读取。
- 查历史行情前应先检查本地数据是否缺失；不要默认认为 `get_market_data_ex` 会自动补齐全部股票的历史行情。
