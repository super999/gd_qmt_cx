#!/usr/bin/env python3
# coding: utf-8

"""
MiniQMT xttrader trade access self-check.

Default mode is query-only and will not submit orders.  A real order is sent
only when both --place-test-order and --i-understand-this-may-send-a-real-order
are provided.

Official reference:
https://dict.thinktrader.net/nativeApi/start_now.html?id=AiEOst
"""

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path


DEFAULT_QMT_USERDATA = r"D:\光大证券金阳光QMT实盘\userdata_mini"


ACCOUNT_STATUS_TEXT = {
    -1: "invalid",
    0: "ok",
    1: "waiting_login",
    2: "logging_in",
    3: "failed",
    4: "initializing",
}


def now_session_id():
    return int(time.time()) % 1000000


def mask_account(value):
    if value is None:
        return None
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def get_any_attr(obj, names, default=None):
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj.get(name)
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def safe_scalar(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def object_to_dict(obj, mask_sensitive=True, depth=0):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if depth > 6:
        return repr(obj)
    if isinstance(obj, (list, tuple)):
        return [object_to_dict(item, mask_sensitive, depth + 1) for item in obj]
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            result[str(key)] = object_to_dict(value, mask_sensitive, depth + 1)
        return result

    attrs = {}
    if hasattr(obj, "__dict__"):
        attrs.update(obj.__dict__)
    else:
        for name in dir(obj):
            if name.startswith("_"):
                continue
            try:
                value = getattr(obj, name)
            except Exception:
                continue
            if callable(value):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                attrs[name] = value

    result = {}
    for key, value in attrs.items():
        if mask_sensitive and key.lower() in {"account_id", "m_straccountid", "accountid"}:
            result[key] = mask_account(value)
        else:
            result[key] = object_to_dict(value, mask_sensitive, depth + 1)
    if result:
        result["_type"] = type(obj).__name__
        return result
    return repr(obj)


def compact_list(items, max_items=8):
    if not items:
        return []
    return [object_to_dict(item) for item in list(items)[:max_items]]


class StepLogger:
    def __init__(self):
        self.results = []

    def add(self, name, status, detail=None):
        payload = {
            "step": name,
            "status": status,
            "detail": detail or {},
        }
        self.results.append(payload)
        print("[{}] {}".format(status.upper(), name))
        if detail:
            text = json.dumps(detail, ensure_ascii=False, default=str)
            if len(text) > 1200:
                text = text[:1200] + "...(truncated)"
            print("  {}".format(text))

    def ok(self, name, detail=None):
        self.add(name, "ok", detail)

    def warn(self, name, detail=None):
        self.add(name, "warn", detail)

    def error(self, name, detail=None):
        self.add(name, "error", detail)


def find_account_id(account_infos, account_type):
    candidates = []
    for info in account_infos or []:
        acc_id = get_any_attr(info, ["account_id", "m_strAccountID", "accountId", "id"])
        acc_type = get_any_attr(info, ["account_type", "m_nAccountType", "accountType", "type"])
        if not acc_id:
            continue
        if account_type.upper() == "STOCK":
            if acc_type in (None, 2, "2", "STOCK", "stock"):
                candidates.append(str(acc_id))
        else:
            candidates.append(str(acc_id))
    unique = []
    for item in candidates:
        if item not in unique:
            unique.append(item)
    return unique


def query_call(log, name, func):
    started = time.time()
    try:
        data = func()
        elapsed = round(time.time() - started, 3)
        log.ok(name, {"elapsed_sec": elapsed, "result": object_to_dict(data)})
        return data
    except Exception as exc:
        elapsed = round(time.time() - started, 3)
        log.error(
            name,
            {
                "elapsed_sec": elapsed,
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
        )
        return None


def build_parser():
    parser = argparse.ArgumentParser(
        description="Check whether current MiniQMT can connect to xttrader and query a stock trading account.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--qmt-userdata", default=DEFAULT_QMT_USERDATA, help="MiniQMT userdata path.")
    parser.add_argument("--session-id", type=int, default=now_session_id(), help="Unique xttrader session id.")
    parser.add_argument("--account-id", default="", help="Fund account id. If omitted, script tries query_account_infos.")
    parser.add_argument("--account-type", default="STOCK", help="Account type passed to StockAccount, usually STOCK.")
    parser.add_argument("--api-timeout", type=int, default=10, help="xttrader API timeout value passed to set_timeout.")
    parser.add_argument("--json-output", default="", help="Optional result JSON path.")

    parser.add_argument("--place-test-order", action="store_true", help="Submit a real test order.")
    parser.add_argument(
        "--i-understand-this-may-send-a-real-order",
        action="store_true",
        help="Required together with --place-test-order.",
    )
    parser.add_argument("--test-stock", default="510300.SH", help="Stock code for optional test order.")
    parser.add_argument("--test-side", choices=["buy", "sell"], default="buy", help="Side for optional test order.")
    parser.add_argument("--test-volume", type=int, default=100, help="Volume for optional test order.")
    parser.add_argument("--test-price", type=float, default=0.01, help="Limit price for optional test order.")
    parser.add_argument("--skip-cancel", action="store_true", help="Do not cancel optional test order after submission.")
    parser.add_argument("--show-sensitive", action="store_true", help="Print unmasked account fields in JSON output.")
    return parser


def main():
    args = build_parser().parse_args()
    log = StepLogger()
    final = {
        "config": {
            "qmt_userdata": args.qmt_userdata,
            "session_id": args.session_id,
            "account_id": mask_account(args.account_id) if args.account_id else "",
            "account_type": args.account_type,
            "place_test_order": args.place_test_order,
        },
        "steps": log.results,
        "summary": {},
    }

    if args.place_test_order and not args.i_understand_this_may_send_a_real_order:
        log.error(
            "safety_check",
            {"message": "Refusing to call order_stock without the explicit risk confirmation flag."},
        )
        final["summary"] = {"trade_access_check": "not_run", "reason": "missing_risk_confirmation"}
        write_json_if_needed(args, final)
        return 2

    userdata = Path(args.qmt_userdata)
    if not userdata.exists():
        log.error("check_userdata_path", {"path": str(userdata), "message": "Path does not exist."})
        final["summary"] = {"trade_access_check": "failed", "reason": "userdata_path_not_found"}
        write_json_if_needed(args, final)
        return 1
    log.ok("check_userdata_path", {"path": str(userdata)})

    try:
        from xtquant import xtconstant
        from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
        from xtquant.xttype import StockAccount
    except Exception as exc:
        log.error(
            "import_xttrader",
            {
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        final["summary"] = {"trade_access_check": "failed", "reason": "import_failed"}
        write_json_if_needed(args, final)
        return 1

    log.ok(
        "import_xttrader",
        {
            "python": sys.executable,
            "constants": {
                "STOCK_BUY": xtconstant.STOCK_BUY,
                "STOCK_SELL": xtconstant.STOCK_SELL,
                "FIX_PRICE": xtconstant.FIX_PRICE,
                "LATEST_PRICE": xtconstant.LATEST_PRICE,
            },
        },
    )

    callback_events = []

    class TradeCheckCallback(XtQuantTraderCallback):
        def _record(self, name, payload=None):
            event = {"event": name, "payload": object_to_dict(payload)}
            callback_events.append(event)
            print("[CALLBACK] {} {}".format(name, json.dumps(event["payload"], ensure_ascii=False, default=str)))

        def on_connected(self):
            self._record("on_connected")

        def on_disconnected(self):
            self._record("on_disconnected")

        def on_account_status(self, status):
            self._record("on_account_status", status)

        def on_stock_asset(self, asset):
            self._record("on_stock_asset", asset)

        def on_stock_order(self, order):
            self._record("on_stock_order", order)

        def on_stock_trade(self, trade):
            self._record("on_stock_trade", trade)

        def on_stock_position(self, position):
            self._record("on_stock_position", position)

        def on_order_error(self, order_error):
            self._record("on_order_error", order_error)

        def on_cancel_error(self, cancel_error):
            self._record("on_cancel_error", cancel_error)

        def on_order_stock_async_response(self, response):
            self._record("on_order_stock_async_response", response)

        def on_cancel_order_stock_async_response(self, response):
            self._record("on_cancel_order_stock_async_response", response)

    trader = None
    exit_code = 0
    try:
        trader = XtQuantTrader(str(userdata), args.session_id, TradeCheckCallback())
        trader.start()
        if hasattr(trader, "set_timeout"):
            trader.set_timeout(args.api_timeout)
        log.ok("start_trader", {"session_id": args.session_id, "api_timeout": args.api_timeout})

        connect_result = trader.connect()
        if connect_result != 0:
            log.error("connect", {"return_code": connect_result, "expected_success_code": 0})
            final["summary"] = {"trade_access_check": "failed", "reason": "connect_failed"}
            exit_code = 1
            return exit_code
        log.ok("connect", {"return_code": connect_result})

        account_infos = query_call(log, "query_account_infos", trader.query_account_infos)
        account_status = query_call(log, "query_account_status", trader.query_account_status)
        if account_status:
            status_rows = object_to_dict(account_status, mask_sensitive=not args.show_sensitive)
            log.ok(
                "account_status_text",
                {
                    "status_map": ACCOUNT_STATUS_TEXT,
                    "raw": status_rows,
                },
            )

        account_id = args.account_id.strip()
        if not account_id:
            candidates = find_account_id(account_infos, args.account_type)
            masked = [mask_account(item) for item in candidates]
            if len(candidates) == 1:
                account_id = candidates[0]
                log.ok("select_account", {"selected": mask_account(account_id), "source": "query_account_infos"})
            elif len(candidates) > 1:
                log.error(
                    "select_account",
                    {
                        "message": "Multiple accounts found. Re-run with --account-id.",
                        "candidates": masked,
                    },
                )
                final["summary"] = {"trade_access_check": "failed", "reason": "multiple_accounts_need_account_id"}
                exit_code = 1
                return exit_code
            else:
                log.error(
                    "select_account",
                    {"message": "No account id detected. Re-run with --account-id <fund_account_id>."},
                )
                final["summary"] = {"trade_access_check": "failed", "reason": "account_id_required"}
                exit_code = 1
                return exit_code

        account = StockAccount(account_id, args.account_type)
        log.ok("create_stock_account", {"account_id": mask_account(account_id), "account_type": args.account_type})

        subscribe_result = query_call(log, "subscribe_account", lambda: trader.subscribe(account))
        asset = query_call(log, "query_stock_asset", lambda: trader.query_stock_asset(account))
        orders = query_call(log, "query_stock_orders", lambda: trader.query_stock_orders(account, False))
        cancelable_orders = query_call(log, "query_cancelable_stock_orders", lambda: trader.query_stock_orders(account, True))
        trades = query_call(log, "query_stock_trades", lambda: trader.query_stock_trades(account))
        positions = query_call(log, "query_stock_positions", lambda: trader.query_stock_positions(account))

        order_result = None
        cancel_result = None
        if args.place_test_order:
            order_type = xtconstant.STOCK_BUY if args.test_side == "buy" else xtconstant.STOCK_SELL
            log.warn(
                "place_test_order",
                {
                    "message": "Calling order_stock now. This may submit a real order.",
                    "stock": args.test_stock,
                    "side": args.test_side,
                    "volume": args.test_volume,
                    "price_type": "FIX_PRICE",
                    "price": args.test_price,
                },
            )
            order_id = trader.order_stock(
                account,
                args.test_stock,
                order_type,
                args.test_volume,
                xtconstant.FIX_PRICE,
                args.test_price,
                "xttrader_trade_access_check",
                "manual_test_order",
            )
            order_result = {"order_id": order_id}
            log.ok("order_stock", order_result)

            time.sleep(1)
            query_call(log, "query_order_after_submit", lambda: trader.query_stock_order(account, order_id))

            if not args.skip_cancel and order_id and int(order_id) > 0:
                cancel_result = trader.cancel_order_stock(account, order_id)
                log.ok("cancel_order_stock", {"order_id": order_id, "cancel_result": cancel_result})
                time.sleep(1)
                query_call(log, "query_order_after_cancel", lambda: trader.query_stock_order(account, order_id))
        else:
            log.ok(
                "order_stock",
                {"skipped": True, "message": "Default query-only mode. No order was submitted."},
            )

        final["summary"] = {
            "trade_access_check": "ok",
            "connected": connect_result == 0,
            "account_subscribe_return": safe_scalar(subscribe_result),
            "asset_query_ok": asset is not None,
            "orders_count": len(orders) if orders is not None else None,
            "cancelable_orders_count": len(cancelable_orders) if cancelable_orders is not None else None,
            "trades_count": len(trades) if trades is not None else None,
            "positions_count": len(positions) if positions is not None else None,
            "order_test": object_to_dict(order_result),
            "cancel_test": object_to_dict(cancel_result),
            "callback_events_count": len(callback_events),
        }
    except Exception as exc:
        log.error(
            "unexpected_failure",
            {
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        final["summary"] = {"trade_access_check": "failed", "reason": "unexpected_failure"}
        exit_code = 1
    finally:
        final["steps"] = log.results
        final["callbacks"] = callback_events
        if trader is not None:
            try:
                trader.stop()
                log.ok("stop_trader")
            except Exception as exc:
                log.warn("stop_trader", {"error_type": type(exc).__name__, "message": str(exc)})
        write_json_if_needed(args, final)

    return exit_code


def write_json_if_needed(args, payload):
    if not args.json_output:
        return
    out_path = Path(args.json_output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not args.show_sensitive:
        payload = object_to_dict(payload, mask_sensitive=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("[OK] write_json_output")
    print("  {}".format(out_path))


if __name__ == "__main__":
    raise SystemExit(main())
