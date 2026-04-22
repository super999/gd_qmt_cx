# MiniQMT / xtquant API 可用性清单

## 官方参考

- xtdata 官方文档首页：<https://dict.thinktrader.net/nativeApi/xtdata.html?id=AiEOst>
- 建议用法：先看本清单确认“当前环境是否可用”，再回到官方文档核对参数细节和边界行为

## 适用范围

- 测试环境：MiniQMT / 当前账户 / 当前 Python 环境
- 结论性质：仅代表当前环境下的实测与已知反馈，不代表官方全量能力
- 记录原则：只写已验证事实，不把“脚本里出现过”直接等同于“接口可用”
- 本次实测执行环境：`d:\python_envs\gd_qmt_py36\python.exe`，测试日期 `2026-04-22`

## 面向后续 Agent 的阅读方式

如果后续要写回测代码或策略代码，建议按下面顺序读取本文件：

1. 先看“当前结论摘要”，确认当前环境下哪些接口能直接用
2. 再看各分组表格里的“现象说明”和“备注/下一步”
3. 写代码前优先参考“回测/策略开发推荐接口”
4. 需要核对参数时，再跳转到本文件顶部的官方文档链接

本文件的目标不是替代官方文档，而是补充三件官方文档没有告诉新会话的信息：

- 在你当前 MiniQMT 客户端里，哪些 API 实际能跑
- 哪些 API 会阻塞、报错或客户端不支持
- 已实测 API 的返回值大致是什么格式

## 状态定义

- `可用`：调用完成并返回符合预期的结果
- `不可用`：明确报错、明确无权限、明确不支持
- `阻塞`：调用长时间不返回，需人工中断或超时处理
- `未测试`：仓库里出现过 API 名称，或用户提到过，但当前没有可靠运行结果

## 回测/策略开发推荐接口

如果下一步是写“股票回测代码/策略代码”，当前优先使用这些已实测接口：

| 用途 | 建议优先接口 | 原因 |
| --- | --- | --- |
| 历史行情读取 | `xtdata.get_local_data` | 更适合回测，直接走本地数据，不依赖实时订阅 |
| 历史行情读取 | `xtdata.get_market_data_ex` | 返回 `DataFrame`，便于策略计算和调试 |
| 基础行情读取 | `xtdata.get_market_data` | 可用，但返回结构相对更原始，回测里不一定优先 |
| 股票池/板块 | `xtdata.get_sector_list`、`xtdata.get_stock_list_in_sector` | 可直接拿板块目录和成分股 |
| 合约基础属性 | `xtdata.get_instrument_detail` | 可拿证券名称、上市日、涨跌停价等 |
| 盘中订阅 | `xtdata.subscribe_quote` + `xtdata.run` | 后续若要做盘中策略，这条链路已经实测打通 |

当前不建议把这些作为回测主依赖：

- `xtdata.download_financial_data`
原因：已实测阻塞
- `xtdata.get_trading_calendar`
原因：当前客户端不支持
- `xtdata.get_period_list`
原因：当前客户端不支持

## 已实测返回值格式摘要

这部分是给后续 agent / 新会话直接抄用的高价值摘要，重点是“拿回来大概长什么样”。

| API 名称 | 当前状态 | 返回值格式摘要 |
| --- | --- | --- |
| `xtdata.download_history_data` | `可用` | 返回 `None`，更像执行型接口，不是取数接口 |
| `xtdata.get_market_data` | `可用` | 返回 `dict`，键包括 `time/open/high/low/close/volume/amount/preClose/...`，值看起来是按字段组织的数据表 |
| `xtdata.get_market_data_ex` | `可用` | 返回 `dict`，键是证券代码，值是 `pandas.DataFrame` |
| `xtdata.get_local_data` | `可用` | 返回 `dict`，键是证券代码，值是 `pandas.DataFrame` |
| `xtdata.subscribe_quote` | `可用` | 返回订阅号，本次实测为字符串形式的 `"1"` |
| `xtdata.run` | `可用` | 本身是阻塞事件循环入口；配合回调后，回调参数是 `dict`，键为证券代码 |
| `xtdata.get_sector_list` | `可用` | 返回 `list[str]`，本次实测共 854 个板块名 |
| `xtdata.get_stock_list_in_sector` | `可用` | 返回 `list[str]`，本次实测“上证A股”共 2312 个合约 |
| `xtdata.get_instrument_detail` | `可用` | 返回 `dict`，字段包括 `InstrumentName`、`OpenDate`、`UpStopPrice`、`DownStopPrice` 等 |
| `xtdata.get_holidays` | `可用` | 返回 `list`，当前结果为空列表 |
| `xtdata.get_divid_factors` | `可用` | 返回 `pandas.DataFrame`，当前测试结果为空表 |
| `xtdata.get_trading_calendar` | `不可用` | 当前客户端直接抛 `RuntimeError`，提示 `function not realize` |
| `xtdata.get_period_list` | `不可用` | 当前客户端直接抛 `RuntimeError`，提示 `function not realize` |
| `xtdata.download_financial_data` | `阻塞` | 25 秒超时内无返回 |

## 回测代码编写前建议补充的最小背景文档

如果你接下来要写回测/策略代码，建议再至少补 1 份短文档，名称可以叫：

`报告/回测策略开发约束.md`

建议写进去的内容：

- 当前推荐 Python 环境：`d:\python_envs\gd_qmt_py36`
- 当前已验证可用的 xtdata 接口清单
- 当前明确不可依赖的接口：`download_financial_data`、`get_trading_calendar`、`get_period_list`
- 回测优先数据来源：`get_local_data` / `get_market_data_ex`
- 当前交易日历替代方案：
  因 `get_trading_calendar` 不可用，短期内需要手工维护交易日，或引入外部交易日历
- 当前策略范围建议：
  先做纯行情驱动策略，不要把财务因子作为第一版依赖
- 当前回测代码风格建议：
  先写最小可运行版本，少接口、强日志、强可解释

如果不想多一个文件，也可以把这几条直接并入本清单底部。

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
| `xtdata.get_local_data` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 第二轮实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测在先下载历史数据后，可直接从本地取出 `600519.SH` 的 `DataFrame`，长度 15。 | 这是后续回测优先应使用的历史数据接口之一，因为它不依赖实时订阅。 |

## 实时订阅行情

| API 名称 | 状态 | 证据来源 | 测试脚本 | 现象说明 | 备注/下一步 |
| --- | --- | --- | --- | --- | --- |
| `xtdata.subscribe_quote` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测返回订阅号 `1`，等待 1.5 秒后可继续通过 `get_market_data_ex` 读取到 `600519.SH` 数据。 | 当前只验证了“订阅调用可成功返回”，未验证带回调模式。 |
| `xtdata.run` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 第二轮实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 通过独立子进程实测 `subscribe_quote + callback + xtdata.run`，成功观察到回调触发，返回标的键 `600519.SH`。 | 当前已确认回调链路能跑；若后续做盘中策略，再继续测多标的、多周期和长时间稳定性。 |

## 板块数据

| API 名称 | 状态 | 证据来源 | 测试脚本 | 现象说明 | 备注/下一步 |
| --- | --- | --- | --- | --- | --- |
| `xtdata.get_sector_list` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测；`code/run_xtquant/check_sector_list.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py`；`code/run_xtquant/check_sector_list.py` | 2026-04-22 实测返回 854 个板块名，前 10 项包括“上期所、上证A股、上证B股”等。 | 当前环境下板块目录读取正常。 |
| `xtdata.get_stock_list_in_sector` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测；`code/run_xtquant/check_sector_list.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py`；`code/run_xtquant/check_sector_list.py` | 2026-04-22 实测对“上证A股”返回 2312 个合约，前 10 项包括 `600051.SH`、`605090.SH` 等。 | 当前环境下按板块取成分股正常。 |
| `xtdata.get_instrument_detail` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 第二轮实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测返回 `600519.SH` 的基础信息，包括 `InstrumentName=贵州茅台`、`OpenDate=20080605`、涨跌停价等。 | 这个接口对策略过滤股票池、核对基础属性、做交易前校验很有用。 |

## 财务数据

| API 名称 | 状态 | 证据来源 | 测试脚本 | 现象说明 | 备注/下一步 |
| --- | --- | --- | --- | --- | --- |
| `xtdata.download_financial_data` | `阻塞` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测；用户口头反馈 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测在 `600519.SH` 上调用后，25 秒超时内没有返回，已由子进程超时终止。 | 这已经从“用户反馈”升级为“本地复现确认”；后续应继续排查权限、数据源或接口本身限制。 |
| `xtdata.get_divid_factors` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 第二轮实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测接口可调用，返回类型为 `DataFrame`，但在 `600519.SH`、`20200101-20260422` 条件下结果为空。 | 这说明接口本身没有报错；后续若你要做复权核验，需要换更多标的或时间区间继续验证数据覆盖情况。 |
| `xtdata.get_holidays` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 第二轮实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测接口可调用，但当前返回空列表。 | 空列表不等于接口不可用，更像当前客户端未预置或未下载节假日数据。 |
| `xtdata.get_trading_calendar` | `不可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 第二轮实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测直接抛出 `RuntimeError`，提示“当前客户端未支持此功能，请更新客户端或升级投研版”，底层错误为 `function not realize`。 | 对策略/回测是关键缺口。短期内可先用外部交易日历替代，或尝试升级客户端后再测。 |
| `xtdata.get_period_list` | `不可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 第二轮实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测直接抛出 `RuntimeError`，提示当前客户端未支持该功能。 | 这说明不能依赖它动态探测周期列表，短期内只能按文档和实测周期手工维护。 |

## 交易接口

| API 名称 | 状态 | 证据来源 | 测试脚本 | 现象说明 | 备注/下一步 |
| --- | --- | --- | --- | --- | --- |
| `XtQuantTrader` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测可成功导入 `XtQuantTrader` 类。 | 当前只证明类可导入，不证明交易连接、下单和回调链路可用。 |
| `XtQuantTraderCallback` | `可用` | `code/run_xtquant/test_xtquant_api_matrix.py` 实测 | `code/run_xtquant/test_xtquant_api_matrix.py` | 2026-04-22 实测可成功导入 `XtQuantTraderCallback` 类。 | 当前只证明类可导入，不证明回调链路已被触发验证。 |

## 暂不急测但已纳入观察

| API 名称 | 状态 | 为什么现在不急着测 | 后续什么情况下再测 |
| --- | --- | --- | --- |
| `xtdata.subscribe_whole_quote` | `未测试` | 这是全市场主推，适合做盘中批量扫描；你当前优先目标是先把单标的/小股票池策略和回测跑通。 | 当你开始做全市场条件选股、实时盯盘或板块轮动扫描时再测。 |
| `xtdata.get_full_kline` | `未测试` | 它偏“最新交易日全推 K 线”场景，当前已有 `get_market_data_ex` 和 `get_local_data` 能支撑回测和基础策略开发。 | 当你需要更强的盘中 K 线更新能力，或发现 `get_market_data_ex` 不够用时再测。 |
| `xtdata.get_cb_info` | `未测试` | 这是可转债专项信息接口；你现在的主线还是股票策略与回测。 | 当你转向可转债策略、正股-转债联动或可转债池筛选时再测。 |
| `xtdata.get_ipo_info` | `未测试` | 这是新股信息接口，对普通股票回测主流程不是必需项。 | 当你策略要过滤次新股、新股上市天数，或专门做新股相关研究时再测。 |
| `xtdata.download_etf_info` | `未测试` | 这是 ETF 申赎清单下载接口，和当前股票策略主线无直接关系。 | 当你转做 ETF 套利、ETF 申赎或 ETF 组合策略时再测。 |
| `xtdata.get_etf_info` | `未测试` | 只有在 ETF 研究或 ETF 策略里才是核心接口。 | 当你明确把标的范围转到 ETF 后再测。 |

## 当前结论摘要

- 2026-04-22 我已在本地亲自跑通：`xtdata` 导入、`xttrader` 导入、历史行情下载、`get_market_data`、`get_market_data_ex`、`get_local_data`、`subscribe_quote`、`xtdata.run` 回调链路、`get_sector_list`、`get_stock_list_in_sector`、`get_instrument_detail`、`get_holidays`、`get_divid_factors`。
- `xtdata.download_financial_data` 已在本地复现阻塞，25 秒超时保护内仍无返回。
- `xtdata.get_trading_calendar` 和 `xtdata.get_period_list` 在当前客户端上不是“没数据”，而是明确“不支持此功能”，错误信息为 `function not realize`。
- 当前“可用”主要表示接口在当前环境下可调用并返回，不代表交易接口的完整业务链路已验证。

## 后续优先补测建议

1. 为 `xtdata.download_financial_data` 继续做最小复现和分步排查，确认是否与权限、证券代码、数据目录状态有关。
2. 单独补一份交易接口自检，至少覆盖连接、账户查询、委托查询或资产查询三类证据。
3. 如果你后面必须依赖交易日历，尽快决定是升级客户端/投研版，还是在策略层引入外部交易日历替代方案。
