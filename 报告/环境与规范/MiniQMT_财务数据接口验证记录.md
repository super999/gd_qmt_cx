# MiniQMT 财务数据接口验证记录

## 结论

截至 `2026-05-16`，在已开通 Level2 后，当前 MiniQMT 环境中的财务数据链路已重新可用：

- `xtdata.download_financial_data`
- `xtdata.get_financial_data(..., report_type="report_time")`
- `xtdata.get_financial_data(..., report_type="announce_time")`

这说明 `2026-04-22` 时“财务下载接口阻塞、暂不应依赖”的旧结论已经过期，后续文档与策略设计应以本记录为准。

## 官方口径

官方文档明确说明：

- 原生 Python 使用 `xtdata.get_financial_data(stock_list, table_list=[], start_time='', end_time='', report_type='report_time')`
- `table_list` 可使用 `Balance`、`Income`、`CashFlow`
- `report_time` 按报告期取数，`announce_time` 按披露日期取数
- 财务数据读取前，应先补充或下载本地财务数据

对于回测，`announce_time` 更接近“当时真实可见的数据口径”；如果直接把 `report_time` 数据并到交易日上，容易产生未来函数。

## 验证环境

- 日期：`2026-05-16`
- Python：`d:\python_envs\gd_qmt_env\python.exe`
- MiniQMT：已启动并可正常连接
- 验证脚本：`code/miniqmt_tools/财务接口/verify_financial_data_api.py`
- 输出摘要：`code/miniqmt_tools/财务接口/outputs/financial_api_summary_20260516_154727.csv`

## 常规样例结果

验证股票：

- `000001.SZ`
- `600519.SH`

验证财务表：

- `Balance`
- `Income`
- `CashFlow`

结果：

| 接口 | 结果 |
| --- | --- |
| `download_financial_data` | 常规样例全部正常 |
| `get_financial_data(report_time)` | 常规样例全部正常 |
| `get_financial_data(announce_time)` | 常规样例全部正常 |

样例行数：

| 股票 | 表 | `report_time` | `announce_time` |
| --- | --- | ---: | ---: |
| `000001.SZ` | `Balance` | 26 | 27 |
| `000001.SZ` | `Income` | 29 | 30 |
| `000001.SZ` | `CashFlow` | 33 | 34 |
| `600519.SH` | `Balance` | 26 | 27 |
| `600519.SH` | `Income` | 26 | 29 |
| `600519.SH` | `CashFlow` | 25 | 26 |

## 已知边界

`2026-05-16` 单独排查时发现：

- `002422.SZ` 的 `Balance`、`CashFlow` 下载可以快速返回
- `002422.SZ` 的 `Income` 下载会长时间阻塞

因此，当前正确结论不是“财务接口全面不可用”，而是：

> 财务接口整体已可用，但个别“股票 + 表”组合仍可能异常，正式接入策略前需要做批量覆盖检查和超时保护。

## 对回测与机器学习的影响

### 可以开始做的事

- 设计财务因子原型
- 使用 `announce_time` 做点时可见数据拼接
- 对 `Balance`、`Income`、`CashFlow` 中的少量核心字段做第一批因子

### 仍然不能省略的事

- 不能把 `report_time` 直接当作回测时点可见数据
- 不能默认所有股票所有财务表都能稳定下载
- 不能一次性把全部字段塞进模型，应先做覆盖率、缺失率、延迟、异常值检查
- 批量下载时需要对子任务加超时与失败清单

## 推荐后续动作

1. 写一个全市场财务覆盖率检查程序，统计每只股票各表最近若干年的可用性。
2. 建立公告日可见的财务因子宽表，不先接模型。
3. 先从少量基础因子开始：
   - 净利润同比
   - 营业收入同比
   - ROE / 净资产收益率
   - 资产负债率
   - 经营现金流质量
4. 等财务数据质量报告稳定后，再把因子接入 LightGBM。
