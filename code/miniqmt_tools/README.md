# miniqmt_tools 目录说明

本目录保存外部 Python 直接连接 MiniQMT 的工具程序。

## 边界约定

- `code/run_qmt/`：QMT 内运行的策略脚本，需要复制或导入到 QMT 策略环境中，再在 QMT 里手动启动。
- `code/miniqmt_tools/`：外部 Python 工具脚本，通过 `xtquant.xtdata` 与已打开的 MiniQMT 客户端交互，不需要把脚本复制到 QMT 里，也不需要在 QMT 策略界面手动点启动。
- `code/run_xtquant/`：保留为 `xtquant` API 冒烟测试、接口矩阵测试和订阅链路验证脚本目录，不再新增面向业务查询的 MiniQMT 工具程序。

## 当前脚本

- `check_xttrader_trade_access.py`
  - 外部 Python 交易接口自检脚本，用于验证当前 MiniQMT 是否能通过 `xtquant.xttrader` 连接交易服务、识别账号、订阅账号、查询资产、委托、成交和持仓。
  - 默认是只查询模式，不会调用 `order_stock`，不会自动下单。
  - 如果要测试真实委托链路，必须同时传入 `--place-test-order` 和 `--i-understand-this-may-send-a-real-order`；脚本会按参数提交一笔限价测试委托，并默认尝试撤单。
- `find_stocks_by_price_range.py`
  - 按指定日期、代码前缀、市场后缀和价格区间筛选股票。
  - 默认会先对目标股票池补下载目标日期历史行情，再读取和筛选。
- `check_missing_market_data.py`
  - 全市场 Local-First 行情缺失检查，默认扫描 A 股全市场（`上证A股` + `深证A股`）。
  - 默认无参数即可运行：从项目历史基准 `20200101` 到当前可识别最新交易日，自动生成交易日清单，检查并补下载缺失日线。
  - 默认先用 `get_local_data` 检查本地行情；发现缺失后调用 `download_history_data` 补下载，再复查。
  - 每只股票会用 `get_instrument_detail` 的 `OpenDate` 作为自己的实际检查起点，上市前日期不会被误判为缺失。
  - 已确认完整的 `股票 + 交易日` 会写入本地 SQLite 检查缓存：`code/miniqmt_tools/outputs/market_data_check_cache.sqlite`；下次运行自动跳过同一 `股票 + 交易日`，避免反复检查。
  - 如果补下载后仍缺失，也会以 `missing_after_download` 写入缓存；这代表“已诊断过但本地数据源仍无此日线”，下次不重复下载同一缺口。
  - 如需完全重查，可加 `--reset-cache`；如需临时绕过缓存，可加 `--no-cache`。
  - 自动交易日历会优先尝试 `xtdata.get_trading_calendar`，不可用时再尝试 `akshare`、`tushare`，最后回退到参考标的本地日线，默认参考标的是 `510300.SH`。当前 `d:\python_envs\gd_qmt_env` 已安装 `akshare/tushare`；Tushare 如需使用需设置 `TUSHARE_TOKEN`。
  - `fill_data=False` 是缺失检查的固定口径；`fill_data=True` 会用前一条数据填充缺失 K 线，可能掩盖真实缺口。
  - 有行情行时会额外读取 `suspendFlag`；`suspendFlag` 非 0 的股票单独输出为“停牌标记”，不混入缺失清单。
  - 会调用 `get_instrument_detail` 读取 `OpenDate`、`ExpireDate`、`InstrumentStatus`、`IsTrading`，用于识别目标日尚未上市、目标日已退市/到期、当前合约停牌状态。
  - 合约状态输出会按官方特殊值做中文解释：`OpenDate=19700101..19700106`、`ExpireDate=0/99999999`、`InstrumentStatus<=0/>=1`、`ProductType`。
  - 输出初始缺失清单、停牌标记清单和合约状态清单；如果触发下载，再输出下载后仍缺失清单和下载后停牌标记清单。
- `verify_suspend_flag_field.py`
  - 单独验证 `get_market_data_ex/get_local_data` 是否能返回 `suspendFlag` 字段。
  - 同时打印样本股票的 `get_instrument_detail` 状态，用来区分“字段不可用”和“目标日没有行情行”。
  - 验证结论已沉淀到 `报告/环境与规范/MiniQMT_suspendFlag字段验证记录.md`。
- `example_download_then_get_market_data_ex.py`
  - 单只股票示例：先直接 `get_market_data_ex`，再 `download_history_data`，再 `get_market_data_ex`。
  - 用下载前后对比说明“取不到历史行情是否因为未下载”。
- `example_get_local_data.py`
  - 单只股票示例：先 `download_history_data`，再用 `get_local_data` 从本地行情库读取。
  - 同时用 `get_market_data_ex` 做对照，便于理解两个接口的返回结构。
- `财务接口/verify_financial_data_api.py`
  - 逐标的、逐财务表验证 `download_financial_data` 与 `get_financial_data`。
  - 每个子任务独立加超时，避免一个异常组合拖死整次检查。
  - 当前验证结论已沉淀到 `报告/环境与规范/MiniQMT_财务数据接口验证记录.md`。

## 推荐运行环境

```powershell
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/check_xttrader_trade_access.py --account-id 你的资金账号
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/find_stocks_by_price_range.py
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/check_missing_market_data.py
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/verify_suspend_flag_field.py
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/example_download_then_get_market_data_ex.py
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/example_get_local_data.py
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/财务接口/verify_financial_data_api.py
```

运行前请确认 MiniQMT 已启动，且行情连接正常。

## 交易接口自检示例

只做连接和账号查询，不下单：

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'd:\python_envs\gd_qmt_env\python.exe' 'code\miniqmt_tools\check_xttrader_trade_access.py' --account-id '你的资金账号'
```

如果不确定资金账号，可以先不传 `--account-id`，脚本会尝试调用 `query_account_infos` 自动发现账号；若发现多个账号，会提示你重新指定：

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'd:\python_envs\gd_qmt_env\python.exe' 'code\miniqmt_tools\check_xttrader_trade_access.py'
```

保存 JSON 结果：

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'd:\python_envs\gd_qmt_env\python.exe' 'code\miniqmt_tools\check_xttrader_trade_access.py' --account-id '你的资金账号' --json-output 'code\miniqmt_tools\outputs\trade_access_check.json'
```

真实委托链路测试，默认会尝试撤单。只有确认 MiniQMT 当前账户、交易权限、测试标的、数量、价格都符合你的预期后再运行：

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'd:\python_envs\gd_qmt_env\python.exe' 'code\miniqmt_tools\check_xttrader_trade_access.py' --account-id '你的资金账号' --place-test-order --i-understand-this-may-send-a-real-order --test-stock 510300.SH --test-side buy --test-volume 100 --test-price 0.01
```

判断口径：

- `connect` 返回 `0`：外部 Python 到 MiniQMT 交易服务连接成功。
- `subscribe_account`、`query_stock_asset`、`query_stock_orders`、`query_stock_trades`、`query_stock_positions` 都成功：说明账号查询链路基本可用。
- 只有启用真实委托测试且 `order_stock` 返回有效委托编号，才能说明自动报单链路已实际触达交易系统。
