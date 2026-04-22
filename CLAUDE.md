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

- `d:\python_envs\gd_qmt_py36` (Python 3.6.13) - **Recommended for QMT**
  - Contains: numpy 1.19.5, pandas 1.1.5, scipy 1.5.4, scikit-learn 0.24.2, statsmodels 0.12.2
  - Matches the `python36.dll` in QMT installation directory
  - This is the primary environment for QMT native interface access

- `d:\python_envs\gd_qmt_env` (Python 3.12) - General analysis only
  - Not recommended for QMT native interface

## Code Structure

- `python/` - Original QMT example strategies (encoded/obfuscated)
  - `多因子选股回测示例.py` - Multi-factor stock selection backtest example
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
4. **Conservative approach**: Without readable source code, only provide conservative inferences based on common QMT strategy patterns

## Current Project Status

The project has completed API availability verification:

- **Available APIs**: `xtdata` import, `xttrader` import, historical data download/query, real-time subscription, sector data
- **Blocked APIs**: `xtdata.download_financial_data` (hangs on call)
- **Untested**: `xtdata.run` event loop, trading connection/callback chains

See `报告/MiniQMT_API可用性清单.md` for detailed API test results.

## Roadmap

1. **Phase 1**: Obtain readable source code or backtest logs from example strategies
2. **Phase 2**: Analyze the example strategy's rebalancing frequency, stock pool, factor logic, buy/sell conditions
3. **Phase 3**: Supplement buy/sell logic and add structured debug logging
4. **Phase 4**: Create a custom strategy with user's own conditions and complete a verified backtest

## Common Commands

```bash
# Activate QMT Python environment
d:\python_envs\gd_qmt_py36\python.exe

# Run API test suite
python code/run_xtquant/test_xtquant_api_matrix.py

# Test sector data
python code/run_xtquant/check_sector_list.py
```