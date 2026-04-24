# QMT Project Agent Guide

## Project Goal

这个工作区用于整理和推进一个光大 QMT 学习项目。目标不是重写整套框架，而是沿着 QMT 自带示例策略逐步学习、验证、修改，最终写出一个可以在 QMT 中成功跑通的自定义买卖策略。

当前优先级：

1. 建立 QMT 环境与 Python 环境的稳定认知
2. 理解示例策略在做什么
3. 在示例策略上做最小改动，补强买卖逻辑与调试输出
4. 逐步过渡到用户自己的策略

## Local Environment

- QMT 安装目录：`D:\光大证券金阳光QMT实盘`
- QMT 示例策略目录：`D:\光大证券金阳光QMT实盘\python`
- 当前工作区：`D:\codex_n_workspace\gd_qmt_cx`

### Python Environments

- `d:\python_envs\gd_qmt_env`
  - Python `3.12`
  - 已安装 `numpy/pandas/scipy/scikit-learn/statsmodels`
  - 已确认可 `import xtquant`，并已跑通大部分冒烟测试与 API 测试
  - 这是当前推荐的首选开发环境

- `d:\python_envs\gd_qmt_py36`
  - Python `3.6.13`
  - 已安装兼容版本的 `numpy/pandas/scipy/scikit-learn/statsmodels`
  - 与 QMT 安装目录中的 `python36.dll` 匹配
  - 可作为兼容性备用环境，不再默认视为首选开发环境

## Environment Decision Update

- 早期判断曾偏向 `d:\python_envs\gd_qmt_py36`，主要依据是 QMT 安装目录中的 `python36.dll`。
- 后续实测已确认：`d:\python_envs\gd_qmt_env`（Python `3.12`）可以正常导入 `xtquant`，并可跑通大部分冒烟测试与 `xtdata` API 测试。
- 因此，当前项目默认应优先使用 `d:\python_envs\gd_qmt_env` 进行外部 Python 开发与测试。
- 只有在遇到明确的版本兼容问题时，才回退到 `d:\python_envs\gd_qmt_py36` 做交叉验证。

## Current Facts

- 工作区中的 `D:\codex_n_workspace\gd_qmt_cx\python\多因子选股回测示例.py` 当前已是可读源码。
- 当前进行分析、修改、加日志、做对比时，应优先以工作区副本为主：
  - `D:\codex_n_workspace\gd_qmt_cx\python\多因子选股回测示例.py`
- 但这不自动等于 QMT 实际运行时加载的文件版本，因此凡是涉及“最终在 QMT 中是否生效”的判断，都还需要确认它与 QMT 实际加载文件是否一致。
- 因此，项目当前阶段不再是“先获取可读源码”，而是：
  - 基于该可读源码理解策略结构
  - 确认它与 QMT 实际加载版本是否一致
  - 在此基础上做最小改动、日志增强和回测验证

## Persistent Project Docs

以下文档应作为后续会话和 agent 的优先参考资料：

- 文档权威级别与使用顺序：
  `报告/文档使用说明.md`
- API 实测与返回值摘要：
  `报告/MiniQMT_API可用性清单.md`
- 回测开发总体边界：
  `报告/回测策略开发约束.md`
- 回测中的复权、指标、财务数据处理口径：
  `报告/回测数据口径与指标处理约束.md`
- 当前动态任务与优先顺序：
  `报告/当前任务与阶段计划.md`
- 当前代码实际运行策略事实：
  `报告/当前实际运行策略卡片.md`
- 510300 单标的数据分析与研究结论：
  `code/backtest/outputs/analyze_510300_research/510300单标的数据分析与研究结论.md`
- ETF 低吸反弹主路线执行计划：
  `报告/低吸反弹主路线执行计划.md`
- 策略说明书 v1：
  `报告/策略说明书_v1_ETF低吸反弹.md`
- 示例策略源码分析：
  `报告/多因子选股回测示例-源码分析.md`

如果任务涉及外部 Python 回测、行情取数、复权、技术指标或财务指标，优先先读这些文档，再决定实现方式。

## Working Rules

- 优先最小改动，尽量沿用原函数名、原流程、原变量。
- 不臆造 QMT API；如果某个接口名不确定，优先沿用已有调用方式。
- 先验证事实，再提修改方案。
- 现在已经拿到工作区中的可读源码，可以基于该文件做逐函数、逐变量、逐流程分析；但仍要把“源码可读事实”和“QMT 实际运行行为验证”区分开。
- 如果用户提供了回测日志、成交记录、持仓变化，也可以先基于运行结果反推策略行为。
- 如果任务涉及外部 Python 策略或回测逻辑变更，应先查看：
  - `报告/当前实际运行策略卡片.md`
  - `报告/当前任务与阶段计划.md`
- 若后续要修改买点、卖点、持有天数、核心参数或默认标的，应先更新文档并等待用户确认，再改代码。
