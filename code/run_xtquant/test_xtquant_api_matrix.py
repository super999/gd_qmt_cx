#!/usr/bin/env python3
# coding: utf-8

import json
import multiprocessing as mp
import os
import subprocess
import sys
import tempfile
import time
import traceback


PYTHON_EXE = sys.executable
DEFAULT_TIMEOUT = 15
LONG_TIMEOUT = 25
STOCK_CODE = "600519.SH"
SECTOR_NAME = "上证A股"
MARKET = "SH"


def _safe_repr(value, limit=240):
    text = repr(value)
    if len(text) > limit:
        return text[:limit] + "...(truncated)"
    return text


def _worker_entry(name, target, result_queue):
    started = time.time()
    try:
        result = target()
        payload = {
            "name": name,
            "status": "ok",
            "elapsed_sec": round(time.time() - started, 3),
            "detail": result,
        }
    except Exception as exc:
        payload = {
            "name": name,
            "status": "error",
            "elapsed_sec": round(time.time() - started, 3),
            "detail": {
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        }
    result_queue.put(payload)


def _run_with_timeout(name, timeout, target):
    queue = mp.Queue()

    process = mp.Process(target=_worker_entry, args=(name, target, queue))
    process.start()
    process.join(timeout)

    if process.is_alive():
        process.terminate()
        process.join(5)
        return {
            "name": name,
            "status": "timeout",
            "elapsed_sec": timeout,
            "detail": {
                "message": "call did not finish before timeout",
            },
        }

    if queue.empty():
        return {
            "name": name,
            "status": "error",
            "elapsed_sec": None,
            "detail": {
                "message": "worker exited without returning a result",
            },
        }

    return queue.get()


def test_import_xtdata():
    from xtquant import xtdata

    return {
        "module": "xtdata",
        "has_get_market_data_ex": hasattr(xtdata, "get_market_data_ex"),
    }


def test_import_xttrader():
    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback

    return {
        "XtQuantTrader": str(XtQuantTrader),
        "XtQuantTraderCallback": str(XtQuantTraderCallback),
    }


def test_download_history_data():
    from xtquant import xtdata

    result = xtdata.download_history_data(
        STOCK_CODE,
        period="1d",
        start_time="20260401",
        end_time="20260422",
        incrementally=False,
    )
    return {
        "return_repr": _safe_repr(result),
    }


def test_get_market_data():
    from xtquant import xtdata

    data = xtdata.get_market_data(stock_list=[STOCK_CODE], period="1d")
    shape = {}
    for key, value in data.items():
        try:
            shape[key] = len(value)
        except Exception:
            shape[key] = "n/a"
    return {
        "keys": list(data.keys()),
        "shape": shape,
        "sample": _safe_repr(data),
    }


def test_get_market_data_ex():
    from xtquant import xtdata

    data = xtdata.get_market_data_ex([], [STOCK_CODE], period="1d", count=-1)
    sample = data.get(STOCK_CODE)
    sample_info = {
        "type": type(sample).__name__,
        "repr": _safe_repr(sample),
    }
    try:
        sample_info["len"] = len(sample)
    except Exception:
        sample_info["len"] = "n/a"
    return {
        "codes": list(data.keys()),
        "sample": sample_info,
    }


def test_subscribe_quote():
    from xtquant import xtdata

    seq = xtdata.subscribe_quote(STOCK_CODE, period="1d", count=-1)
    time.sleep(1.5)
    data = xtdata.get_market_data_ex([], [STOCK_CODE], period="1d")
    return {
        "subscribe_result": _safe_repr(seq),
        "has_code": STOCK_CODE in data,
        "sample": _safe_repr(data.get(STOCK_CODE)),
    }


def test_get_sector_list():
    from xtquant import xtdata

    data = xtdata.get_sector_list()
    return {
        "count": len(data),
        "first_items": data[:10],
    }


def test_get_stock_list_in_sector():
    from xtquant import xtdata

    data = xtdata.get_stock_list_in_sector(SECTOR_NAME)
    return {
        "sector": SECTOR_NAME,
        "count": len(data),
        "first_items": data[:10],
    }


def test_get_local_data():
    from xtquant import xtdata

    xtdata.download_history_data(
        STOCK_CODE,
        period="1d",
        start_time="20260401",
        end_time="20260422",
        incrementally=False,
    )
    data = xtdata.get_local_data(
        field_list=[],
        stock_list=[STOCK_CODE],
        period="1d",
        start_time="20260401",
        end_time="20260422",
        count=-1,
        dividend_type="none",
        fill_data=True,
    )
    sample = data.get(STOCK_CODE)
    sample_info = {
        "type": type(sample).__name__,
        "repr": _safe_repr(sample),
    }
    try:
        sample_info["len"] = len(sample)
    except Exception:
        sample_info["len"] = "n/a"
    return {
        "codes": list(data.keys()),
        "sample": sample_info,
    }


def test_get_instrument_detail():
    from xtquant import xtdata

    data = xtdata.get_instrument_detail(STOCK_CODE, iscomplete=False)
    subset = {}
    for key in [
        "InstrumentID",
        "InstrumentName",
        "ExchangeID",
        "ExchangeCode",
        "OpenDate",
        "UpStopPrice",
        "DownStopPrice",
        "ProductName",
    ]:
        if key in data:
            subset[key] = data[key]
    return {
        "keys": list(data.keys())[:15],
        "subset": subset,
    }


def test_get_holidays():
    from xtquant import xtdata

    data = xtdata.get_holidays()
    return {
        "count": len(data),
        "tail_items": data[-10:],
    }


def test_get_trading_calendar():
    from xtquant import xtdata

    data = xtdata.get_trading_calendar(MARKET, "20260401", "20260430")
    return {
        "market": MARKET,
        "count": len(data),
        "first_items": data[:5],
        "last_items": data[-5:],
    }


def test_get_divid_factors():
    from xtquant import xtdata

    data = xtdata.get_divid_factors(STOCK_CODE, start_time="20200101", end_time="20260422")
    return {
        "type": type(data).__name__,
        "len": len(data) if hasattr(data, "__len__") else "n/a",
        "sample": _safe_repr(data),
    }


def test_get_period_list():
    from xtquant import xtdata

    data = xtdata.get_period_list()
    return {
        "count": len(data),
        "items": list(data),
    }


def test_xtdata_run_callback():
    marker_dir = tempfile.mkdtemp(prefix="xtdata_callback_")
    marker_path = os.path.join(marker_dir, "callback.json")
    code = """
import json
import os
from xtquant import xtdata

MARKER = {marker}
STOCK = {stock}

def cb(data):
    with open(MARKER, 'w', encoding='utf-8') as f:
        json.dump({{'keys': list(data.keys()), 'size': len(data)}}, f, ensure_ascii=False)
    os._exit(0)

xtdata.subscribe_quote(STOCK, period='1d', count=-1, callback=cb)
xtdata.run()
""".format(marker=json.dumps(marker_path), stock=json.dumps(STOCK_CODE))

    proc = subprocess.Popen([PYTHON_EXE, "-c", code])
    deadline = time.time() + 12
    observed = None

    try:
        while time.time() < deadline:
            if os.path.exists(marker_path):
                with open(marker_path, "r", encoding="utf-8") as f:
                    observed = json.load(f)
                break
            if proc.poll() is not None:
                break
            time.sleep(0.3)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

    if observed is None:
        raise RuntimeError("callback marker not observed within timeout")

    return {
        "callback_observed": True,
        "detail": observed,
    }


def test_download_financial_data():
    from xtquant import xtdata

    result = xtdata.download_financial_data([STOCK_CODE])
    return {
        "return_repr": _safe_repr(result),
    }


def build_tests():
    return [
        {"name": "import_xtdata", "timeout": DEFAULT_TIMEOUT, "target": test_import_xtdata},
        {"name": "import_xttrader", "timeout": DEFAULT_TIMEOUT, "target": test_import_xttrader},
        {"name": "download_history_data", "timeout": LONG_TIMEOUT, "target": test_download_history_data},
        {"name": "get_market_data", "timeout": DEFAULT_TIMEOUT, "target": test_get_market_data},
        {"name": "get_market_data_ex", "timeout": DEFAULT_TIMEOUT, "target": test_get_market_data_ex},
        {"name": "subscribe_quote", "timeout": DEFAULT_TIMEOUT, "target": test_subscribe_quote},
        {"name": "get_sector_list", "timeout": DEFAULT_TIMEOUT, "target": test_get_sector_list},
        {"name": "get_stock_list_in_sector", "timeout": DEFAULT_TIMEOUT, "target": test_get_stock_list_in_sector},
        {"name": "get_local_data", "timeout": DEFAULT_TIMEOUT, "target": test_get_local_data},
        {"name": "get_instrument_detail", "timeout": DEFAULT_TIMEOUT, "target": test_get_instrument_detail},
        {"name": "get_holidays", "timeout": DEFAULT_TIMEOUT, "target": test_get_holidays},
        {"name": "get_trading_calendar", "timeout": DEFAULT_TIMEOUT, "target": test_get_trading_calendar},
        {"name": "get_divid_factors", "timeout": DEFAULT_TIMEOUT, "target": test_get_divid_factors},
        {"name": "get_period_list", "timeout": DEFAULT_TIMEOUT, "target": test_get_period_list},
        {"name": "xtdata_run_callback", "timeout": DEFAULT_TIMEOUT, "target": test_xtdata_run_callback},
        {"name": "download_financial_data", "timeout": LONG_TIMEOUT, "target": test_download_financial_data},
    ]


def main():
    mp.freeze_support()

    results = []
    print("Running xtquant API checks with python:", PYTHON_EXE)
    print("Process PID:", os.getpid())

    for item in build_tests():
        print("")
        print("=== {} ===".format(item["name"]))
        result = _run_with_timeout(item["name"], item["timeout"], item["target"])
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    print("")
    print("=== SUMMARY ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
