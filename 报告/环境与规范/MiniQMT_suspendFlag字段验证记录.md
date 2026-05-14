# MiniQMT suspendFlag 字段验证记录

## 结论摘要

- `suspendFlag` 字段可用。
- `get_market_data_ex` 和 `get_local_data` 都可以返回 `suspendFlag` 列。
- 对正常有行情的股票，`suspendFlag=0`。
- 对当前停牌状态股票，如果 `fill_data=False`，目标日可能没有行情行，因此没有行可读取 `suspendFlag`。
- 对当前停牌状态股票，如果 `fill_data=True`，接口会返回填充行，`suspendFlag=1`，但 `open/high/low/close` 为 `nan`，`volume/amount` 为 0。
- 因此，缺失检查工具继续使用 `fill_data=False` 是正确的；停牌解释应同时看 `get_instrument_detail` 的 `InstrumentStatus`。

## 验证环境

- 验证日期：2026-05-14
- Python：`d:\python_envs\gd_qmt_env\python.exe`
- MiniQMT 数据路径：`D:\光大证券金阳光QMT实盘\userdata_mini\datadir`
- 验证脚本：`code/miniqmt_tools/verify_suspend_flag_field.py`
- 目标日期：`20260511`
- 周期：`1d`
- 字段：`open, high, low, close, volume, amount, suspendFlag`

运行命令：

```powershell
d:\python_envs\gd_qmt_env\python.exe code/miniqmt_tools/verify_suspend_flag_field.py
```

## 样本股票

| 股票 | 名称 | `InstrumentStatus` | 合约状态解释 |
| --- | --- | ---: | --- |
| `002422.SZ` | 科伦药业 | 0 | 正常交易 |
| `000004.SZ` | *ST国华 | 10 | 停牌状态/停牌10天 |
| `002731.SZ` | ST萃华 | 7 | 停牌状态/停牌7天 |
| `600193.SH` | *ST创兴 | 10 | 停牌状态/停牌10天 |

## 实测结果

### fill_data=False

`get_market_data_ex`：

| 股票 | 是否有目标日行情行 | `suspendFlag` |
| --- | --- | --- |
| `002422.SZ` | 有 | 0 |
| `000004.SZ` | 无 | 无行情行，无法读取 |
| `002731.SZ` | 无 | 无行情行，无法读取 |
| `600193.SH` | 无 | 无行情行，无法读取 |

`get_local_data`：

| 股票 | 是否有目标日行情行 | `suspendFlag` |
| --- | --- | --- |
| `002422.SZ` | 有 | 0 |
| `000004.SZ` | 无 | 无行情行，无法读取 |
| `002731.SZ` | 无 | 无行情行，无法读取 |
| `600193.SH` | 无 | 无行情行，无法读取 |

### fill_data=True

`get_market_data_ex` 和 `get_local_data` 都能返回停牌样本的填充行：

| 股票 | `open/high/low/close` | `volume/amount` | `suspendFlag` |
| --- | --- | --- | ---: |
| `000004.SZ` | `nan` | 0 | 1 |
| `002731.SZ` | `nan` | 0 | 1 |
| `600193.SH` | `nan` | 0 | 1 |

## 对缺失检查工具的影响

`check_missing_market_data.py` 的目标是检查真实本地行情缺口，所以必须使用：

```python
fill_data=False
```

原因：

- `fill_data=True` 会构造填充行，容易把真实缺失掩盖掉。
- 停牌股票在 `fill_data=False` 下可能没有行情行，这时不能从行情行读取 `suspendFlag`。
- 对“缺失股票中有多少是停牌”这个问题，应使用 `get_instrument_detail` 的 `InstrumentStatus` 与缺失清单做交叉统计。

2026-05-14 的全市场检查结果：

| 指标 | 数量 |
| --- | ---: |
| 全市场股票数 | 5206 |
| 缺失股票数 | 26 |
| 缺失股票中当前合约停牌状态数 | 18 |
| 缺失股票中目标日尚未上市数 | 0 |
| 缺失股票中目标日已退市/到期数 | 0 |
| 缺失股票中暂未被合约状态解释数 | 8 |

## 以后如何判断

- 想确认 `suspendFlag` 字段链路是否可用：运行 `verify_suspend_flag_field.py`。
- 想检查全市场本地行情是否缺失：运行 `check_missing_market_data.py`。
- 看到“未发现 suspendFlag 非 0 的行情行”时，不应直接判断接口坏了；这只表示在 `fill_data=False` 的真实行情行中，没有读到 `suspendFlag=1`。
- 对没有行情行的缺失股票，应查看主检查报告中的“缺失股票中当前合约停牌状态数”和合约状态 CSV。

## 官方字段口径

迅投股票数据字典中：

- `suspendFlag`：行情字段，`1` 表示停牌，`0` 表示不停牌。
- `InstrumentStatus`：合约停牌状态，`<=0` 表示正常交易或复牌，`>=1` 表示停牌天数。
- `OpenDate`：上市日期，特殊值包括 `19700101` 至 `19700106`。
- `ExpireDate`：退市日或到期日，`0` 或 `99999999` 表示暂无退市日或到期日。

参考：<https://dict.thinktrader.net/dictionary/stock.html?id=AiEOst>
