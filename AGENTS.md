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

- `D:\光大证券金阳光QMT实盘\python\多因子选股回测示例.py` 当前不是可直接阅读的普通源码。
- 该文件现状更像是封装后的内容，无法在不解封或不拿到原始源码的前提下做逐函数精确分析。
- 因此，任何对该策略的说明都必须先区分：
  - 已确认事实
  - 基于 QMT 常见策略结构的保守推断
  - 仍需源码或回测日志验证的部分

## Working Rules

- 优先最小改动，尽量沿用原函数名、原流程、原变量。
- 不臆造 QMT API；如果某个接口名不确定，优先沿用已有调用方式。
- 先验证事实，再提修改方案。
- 在没有拿到可读源码之前，不要输出“逐函数、逐变量、逐下单调用”的精确解释。
- 如果用户提供了回测日志、成交记录、持仓变化，也可以先基于运行结果反推策略行为。

## Roadmap

### Phase 1: Recover Inputs

- 获取 `多因子选股回测示例.py` 的可读源码，或至少获取回测日志、成交、持仓结果。
- 确认 QMT 当前究竟是如何加载该策略文件的。

### Phase 2: Understand the Example

- 说明策略的调仓频率、股票池、因子逻辑、买卖条件、资金分配方式。
- 区分“代码已实现”与“代码看起来打算实现但未完成”的部分。

### Phase 3: Make It Learnable

- 在不破坏原结构的前提下，补齐买入逻辑。
- 增加适合学习和调试的结构化日志。
- 让回测中哪天买、哪天卖、为什么调仓更容易追踪。

### Phase 4: Build a Custom Strategy

- 从示例策略中提炼可复用部分。
- 替换为用户自己的选股条件、买卖条件和风控规则。
- 在 QMT 中完成一次可解释、可验证、可复现的策略跑通。
