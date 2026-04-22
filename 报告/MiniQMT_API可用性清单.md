# MiniQMT / xtquant API 可用性清单

## 适用范围

- 测试环境：MiniQMT / 当前账户 / 当前 Python 环境
- 结论性质：仅代表当前环境下的实测与已知反馈，不代表官方全量能力
- 记录原则：只写已验证事实，不把“脚本里出现过”直接等同于“接口可用”
- 本次实测执行环境：`d:\python_envs\gd_qmt_py36\python.exe`，测试日期 `2026-04-22`

## 状态定义

- `可用`：调用完成并返回符合预期的结果
- `不可用`：明确报错、明确无权限、明确不支持
- `阻塞`：调用长时间不返回，需人工中断或超时处理
- `未测试`：仓库里出现过 API 名称，或用户提到过，但当前没有可靠运行结果

## 后续补测记录规则

### 成功类场景

- API 在当前账户环境下完成调用
- 返回值非空，或行为符合预期
- 可附 1 段关键输出摘要写入“现象说明”

### 不可用类场景

- 明确异常
- 明确提示权限不足
- 明确返回不支持或关键字段缺失

### 阻塞类场景

- 调用后长时间无返回
- 需要人工 `Ctrl+C` 中断
- 后续建议统一补充超时包装，再决定是否从 `阻塞` 调整为 `不可用`

### 未测试类场景

- 代码里出现过 API
- 但没有可靠运行结果
- 或只有“脚本写了”，没有“用户确认跑过”的证据

## 库导入与基础连通

| API 名称 | 状态 | 证据来源 | 测试脚本 | 现象说明 | 备注/下一步 |
| --- | --- | --- | --- | --- | --- |
| `from xtquant import xtdata` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测导入成功，`xtdata` 模块可用，且存在 `get_market_data_ex` 属性。 | 旧脚本 `check_miniqmt.py` 的失败点是控制台 emoji 编码，不是 `xtquant` 导入失败。 |
| `from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测导入成功，可解析到 `XtQuantTrader` 和 `XtQuantTraderCallback` 类。 | 当前仅确认“可导入”，不等于交易链路已验证。 |

## 历史行情

| API 名称 | 状态 | 证据来源 | 测试脚本 | 现象说明 | 备注/下一步 |
| --- | --- | --- | --- | --- | --- |
| `xtdata.download_history_data` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测在 `600519.SH`、`1d`、`20260401-20260422` 条件下快速返回，返回值为 `None`。 | `None` 更像“成功执行但无显式返回值”，后续可配合本地数据文件时间戳进一步确认下载行为。 |
| `xtdata.get_market_data` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测返回 `time/open/high/low/close/volume/amount/preClose` 等字段，样本中 `600519.SH` 有 15 个交易日数据。 | 当前已可作为基础历史行情读取接口使用。 |
| `xtdata.get_market_data_ex` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测返回 `600519.SH` 对应 `DataFrame`，长度 15。 | 已确认是当前环境下稳定可用的行情读取接口。 |

## 实时订阅行情

| API 名称 | 状态 | 证据来源 | 测试脚本 | 现象说明 | 备注/下一步 |
| --- | --- | --- | --- | --- | --- |
| `xtdata.subscribe_quote` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测返回订阅号 `1`，等待 1.5 秒后可继续通过 `get_market_data_ex` 读取到 `600519.SH` 数据。 | 当前只验证了“订阅调用可成功返回”，未验证带回调模式。 |
| `xtdata.run` | `未测试` | `code/run_xtquant/check_miniqmt_office_market_data.py` | `code/run_xtquant/check_miniqmt_office_market_data.py` | 该接口设计上就是事件循环阻塞入口，本次未单独测它是否在回调模式下稳定工作。 | 若后续需要验证回调模式，再单独做 `subscribe_quote + callback + xtdata.run` 的组合测试。 |

## 板块数据

| API 名称 | 状态 | 证据来源 | 测试脚本 | 现象说明 | 备注/下一步 |
| --- | --- | --- | --- | --- | --- |
| `xtdata.get_sector_list` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测；`code/run_xtquant/check_sector_list.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py`；`code/run_xtquant/check_sector_list.py` | 2026-04-22 实测返回 854 个板块名，前 10 项包括“上期所、上证A股、上证B股”等。 | 当前环境下板块目录读取正常。 |
| `xtdata.get_stock_list_in_sector` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测；`code/run_xtquant/check_sector_list.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py`；`code/run_xtquant/check_sector_list.py` | 2026-04-22 实测对“上证A股”返回 2312 个合约，前 10 项包括 `600051.SH`、`605090.SH` 等。 | 当前环境下按板块取成分股正常。 |

## 财务数据

| API 名称 | 状态 | 证据来源 | 测试脚本 | 现象说明 | 备注/下一步 |
| --- | --- | --- | --- | --- | --- |
| `xtdata.download_financial_data` | `阻塞` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测；用户口头反馈 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测在 `600519.SH` 上调用后，25 秒超时内没有返回，已由子进程超时终止。 | 这已经从“用户反馈”升级为“本地复现确认”；后续应继续排查权限、数据源或接口本身限制。 |

## 交易接口

| API 名称 | 状态 | 证据来源 | 测试脚本 | 现象说明 | 备注/下一步 |
| --- | --- | --- | --- | --- | --- |
| `XtQuantTrader` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测可成功导入 `XtQuantTrader` 类。 | 当前只证明类可导入，不证明交易连接、下单和回调链路可用。 |
| `XtQuantTraderCallback` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测可成功导入 `XtQuantTraderCallback` 类。 | 当前只证明类可导入，不证明回调链路已被触发验证。 |

## 当前结论摘要

- 2026-04-22 我已在本地亲自跑通：`xtdata` 导入、`xttrader` 导入、历史行情下载、`get_market_data`、`get_market_data_ex`、`subscribe_quote`、`get_sector_list`、`get_stock_list_in_sector`。
- `xtdata.download_financial_data` 已在本地复现阻塞，25 秒超时保护内仍无返回。
- 当前“可用”主要表示接口在当前环境下可调用并返回，不代表交易接口的完整业务链路已验证。
- `xtdata.run` 与交易连接、下单、回调等深一步链路仍需要单独自检。

## 后续优先补测建议

1. 单独补 `subscribe_quote + callback + xtdata.run` 的组合测试，确认回调模式是否正常。
2. 为 `xtdata.download_financial_data` 继续做最小复现和分步排查，确认是否与权限、证券代码、数据目录状态有关。
3. 单独补一份交易接口自检，至少覆盖连接、账户查询、委托查询或资产查询三类证据。
