# QMT 项目仓库导航

## 目的

本文件是当前仓库的顶层入口说明。  
它不负责记录所有细节，而是回答 3 个问题：

1. 这个仓库现在在做什么
2. 新会话或新 agent 应该先看哪些文件
3. 各目录分别放什么，哪些是当前主线，哪些只是历史或临时产物

## 当前项目状态

这个仓库当前不是“从零写一套量化框架”，而是并行推进两条线：

1. 理解并验证 QMT 示例策略
2. 基于 `xtquant` / `xtdata` 做外部 Python 回测与事件研究

当前研究主线已经明确：

- 主标的：`510300.SH`
- 主目标：从数据出发研究低吸反弹窗口
- 当前方法：先做事件定义、样本提取、特征分析、评分建模，再回到策略

## 当前环境事实

- 工作区：`D:\codex_n_workspace\gd_qmt_cx`
- QMT 安装目录：`D:\光大证券金阳光QMT实盘`
- QMT 示例策略目录：`D:\光大证券金阳光QMT实盘\python`

### Python 环境

- 首选环境：`d:\python_envs\gd_qmt_env`
  - Python `3.12`
  - 已确认可导入 `xtquant`
  - 已跑通大部分冒烟测试和 `xtdata` API 测试

- 备用环境：`d:\python_envs\gd_qmt_py36`
  - Python `3.6.13`
  - 主要用于兼容性回查

## 新会话建议阅读顺序

如果是新会话第一次进入本仓库，建议按这个顺序读：

1. [AGENTS.md](/d:/codex_n_workspace/gd_qmt_cx/AGENTS.md)
2. [报告/README.md](/d:/codex_n_workspace/gd_qmt_cx/报告/README.md)
3. [报告/项目管理/文档使用说明.md](/d:/codex_n_workspace/gd_qmt_cx/报告/项目管理/文档使用说明.md)
4. [报告/项目管理/当前任务与阶段计划.md](/d:/codex_n_workspace/gd_qmt_cx/报告/项目管理/当前任务与阶段计划.md)

如果任务偏环境/API：

5. [报告/环境与规范/MiniQMT_API可用性清单.md](/d:/codex_n_workspace/gd_qmt_cx/报告/环境与规范/MiniQMT_API可用性清单.md)
6. [报告/环境与规范/回测策略开发约束.md](/d:/codex_n_workspace/gd_qmt_cx/报告/环境与规范/回测策略开发约束.md)
7. [报告/环境与规范/回测数据口径与指标处理约束.md](/d:/codex_n_workspace/gd_qmt_cx/报告/环境与规范/回测数据口径与指标处理约束.md)

如果任务偏当前策略/研究主线：

5. [报告/策略设计/当前实际运行策略卡片.md](/d:/codex_n_workspace/gd_qmt_cx/报告/策略设计/当前实际运行策略卡片.md)
6. [报告/研究结论/当前主线/](/d:/codex_n_workspace/gd_qmt_cx/报告/研究结论/当前主线/)

## 顶层目录说明

### `报告/`

当前最重要的文档都在这里。  
目录分工见：

- [报告/README.md](/d:/codex_n_workspace/gd_qmt_cx/报告/README.md)

你可以简单理解为：

- `项目管理/`：现在做什么
- `环境与规范/`：哪些 API 能用、开发边界是什么
- `策略设计/`：当前策略说明和当前代码事实
- `研究计划/`：研究方案和标签定义
- `研究结论/当前主线/`：当前有效结论
- `研究结论/历史探索/`：旧探索，不是当前主线
- `归档/`：历史文稿，不是当前事实

### `code/`

代码入口目录。  
当前和回测/研究最相关的是：

- [code/backtest/README.md](/d:/codex_n_workspace/gd_qmt_cx/code/backtest/README.md)

这里区分了：

- 当前主研究脚本
- 当前实验回测脚本
- 历史探索脚本
- `outputs/` 临时输出

### `python/`

QMT 相关策略文件与示例源码副本。

当前重要事实：

- 工作区中的 [多因子选股回测示例.py](/d:/codex_n_workspace/gd_qmt_cx/python/多因子选股回测示例.py) 已是可读源码副本
- 但这不自动等于 QMT 实际运行时加载的版本

### `归档/`

放历史材料、旧计划、旧产物。

入口见：

- [归档/README.md](/d:/codex_n_workspace/gd_qmt_cx/归档/README.md)

### `参考资料/`

放辅助理解项目的参考稿，不是当前权威事实。

入口见：

- [参考资料/README.md](/d:/codex_n_workspace/gd_qmt_cx/参考资料/README.md)

## 哪些文件应长期保留

建议长期保留并提交 Git：

- `报告/环境与规范/`
- `报告/策略设计/`
- `报告/研究计划/`
- `报告/研究结论/当前主线/`
- `报告/研究结论/数据摘要/`
- `code/backtest/*.py`

通常不必提交 Git：

- `code/backtest/outputs/` 下可重跑的原始输出
- 大体量中间数据
- 临时测试日志

## 当前最重要的几个入口

- 当前任务：  
  [当前任务与阶段计划.md](/d:/codex_n_workspace/gd_qmt_cx/报告/项目管理/当前任务与阶段计划.md)

- 当前代码实际在跑什么：  
  [当前实际运行策略卡片.md](/d:/codex_n_workspace/gd_qmt_cx/报告/策略设计/当前实际运行策略卡片.md)

- 当前主研究结论：  
  [报告/研究结论/当前主线/](/d:/codex_n_workspace/gd_qmt_cx/报告/研究结论/当前主线/)

- API 可用性：  
  [MiniQMT_API可用性清单.md](/d:/codex_n_workspace/gd_qmt_cx/报告/环境与规范/MiniQMT_API可用性清单.md)

- 回测脚本入口：  
  [code/backtest/README.md](/d:/codex_n_workspace/gd_qmt_cx/code/backtest/README.md)
