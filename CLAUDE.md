# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a quantitative trading project for 光大证券 QMT (MiniQMT). The goal is to learn from QMT's example strategies, modify them, and eventually create a custom trading strategy that runs successfully within QMT.

## Key Paths

- **Workspace**: `D:\codex_n_workspace\gd_qmt_cx`
- **QMT Installation**: `D:\光大证券金阳光QMT实盘`
- **QMT Python Examples**: `D:\光大证券金阳光QMT实盘\python`

## Python Environments

Two environments are configured:

- `d:\python_envs\gd_qmt_env` (Python 3.12) - **Recommended for active development**
  - Confirmed to import `xtquant`
  - Confirmed to pass smoke tests and most `xtdata` API tests
  - This is the preferred environment for new strategy/backtest code

- `d:\python_envs\gd_qmt_py36` (Python 3.6.13) - Compatibility fallback
  - Contains: numpy 1.19.5, pandas 1.1.5, scipy 1.5.4, scikit-learn 0.24.2, statsmodels 0.12.2
  - Matches the `python36.dll` in QMT installation directory
  - Use it only when a version-compatibility issue needs cross-checking

## Code Structure

- `python/` - QMT strategy files and workspace copies
  - `多因子选股回测示例.py` - Readable workspace copy of the multi-factor example
  - `沪深300ETF回测策略.py` - CSI 300 ETF backtest strategy
  - `交易实时主推示例.py` - Real-time trading push example

- `code/run_xtquant/` - MiniQMT/xtquant API testing scripts
  - `test_xtquant_api_matrix.py` - Main API test suite
  - `check_sector_list.py` - Sector data verification
  - `check_miniqmt.py` / `check_miniqmt_office_market_data.py` - Market data tests

## Working Rules

1. **Minimal changes**: Preserve original function names, variables, and call styles
2. **No fabricated APIs**: If an interface name is uncertain, use the existing call pattern from the source file
3. **Verify before modifying**: Confirm facts before proposing changes
4. **Separate source vs runtime**: The workspace copy is readable, but still does not automatically prove that QMT loads exactly the same file at runtime

## Current Project Status

The project has completed API availability verification:

- **Available APIs**: `xtdata` import, `xttrader` import, historical data download/query, local data query, real-time subscription, callback event loop, sector data, instrument detail
- **Blocked APIs**: `xtdata.download_financial_data` (hangs on call)
- **Unavailable in current client**: `xtdata.get_trading_calendar`, `xtdata.get_period_list`
- **Untested**: trading connection/account/order chains

See:

- `报告/README.md`
- `报告/项目管理/文档使用说明.md`
- `报告/环境与规范/MiniQMT_API可用性清单.md`

## Current Focus

Do not rely on a hard-coded roadmap here. Prefer:

- `报告/项目管理/当前任务与阶段计划.md` for current work
- `报告/策略设计/当前实际运行策略卡片.md` for the current experimental strategy facts
- `报告/研究结论/当前主线/` for current research conclusions

## Common Commands

```bash
# Preferred Python environment
d:\python_envs\gd_qmt_env\python.exe

# Run API test suite
python code/run_xtquant/test_xtquant_api_matrix.py

# Test sector data
python code/run_xtquant/check_sector_list.py
```
