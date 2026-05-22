#!/usr/bin/env python3
# coding: utf-8
from __future__ import annotations

import csv
import json
import os
import queue
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_DIR = ROOT_DIR / "code" / "ml_stock_selection"
OUTPUT_DIR = MODULE_DIR / "outputs" / "live_execution_checklist"
FINANCIAL_CACHE = MODULE_DIR / "outputs" / "financial_cache" / "raw_PershareIndex.csv"
PREDICTION_ROOT = MODULE_DIR / "outputs" / "lightgbm_multi_factor_stock_selection"
SMALL_CAPITAL_ROOT = MODULE_DIR / "outputs" / "small_capital_experiments"
CHECKLIST_DOC = ROOT_DIR / "报告" / "策略设计" / "LightGBM小资金实盘执行清单.md"
STATE_JSON = OUTPUT_DIR / "checklist_state.json"
WEEKLY_LOG = OUTPUT_DIR / "weekly_checklist_log.csv"

DEFAULT_STRATEGY_ID = "bp_value_q70_max8_cash25_buffer24"
DEFAULT_STRATEGY_CN = "低PB防守过滤；最多持有8只；至少保留25%现金；每周调仓；旧持仓前24名保留"
REFERENCE_STOCK = "510300.SH"


def current_week_id() -> str:
    return datetime.now().strftime("%G-W%V")


def today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_date(value) -> str:
    text = "" if value is None else str(value).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def latest_dir_with_file(root: Path, required_name: str) -> Path | None:
    if not root.exists():
        return None
    candidates = [path for path in root.iterdir() if path.is_dir() and (path / required_name).exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


class LiveExecutionChecklistGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("LightGBM 小资金实盘执行清单")
        self.root.geometry("1280x820")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        self.queue: queue.Queue = queue.Queue()
        self.pages: dict[str, ttk.Frame] = {}
        self.nav_buttons: dict[str, ttk.Button] = {}
        self.current_page = ""

        self.state = self._load_state()
        self.week_var = tk.StringVar(value=self.state.get("week_id") or current_week_id())
        self.expected_date_var = tk.StringVar(value=self.state.get("expected_latest_trade_date") or today_yyyymmdd())
        self.last_save_var = tk.StringVar(value=self.state.get("last_saved_at") or "未保存")
        self.overall_status_var = tk.StringVar(value="未检查")
        self.strategy_var = tk.StringVar(value=DEFAULT_STRATEGY_CN)

        self.manual_checks = {
            "account_recorded": tk.BooleanVar(value=bool(self._checks_state("manual").get("account_recorded", False))),
            "positions_recorded": tk.BooleanVar(value=bool(self._checks_state("manual").get("positions_recorded", False))),
            "no_pause_trigger": tk.BooleanVar(value=bool(self._checks_state("manual").get("no_pause_trigger", False))),
            "candidate_review_done": tk.BooleanVar(value=bool(self._checks_state("manual").get("candidate_review_done", False))),
            "weekly_new_limit_confirmed": tk.BooleanVar(value=bool(self._checks_state("manual").get("weekly_new_limit_confirmed", False))),
            "cash_reserve_confirmed": tk.BooleanVar(value=bool(self._checks_state("manual").get("cash_reserve_confirmed", False))),
        }
        self.auto_status_vars = {
            key: tk.StringVar(value=self._auto_state(key).get("status", "未检查"))
            for key in ("miniqmt", "daily_data", "financial_cache", "prediction_run", "small_capital")
        }
        self.auto_detail_vars = {
            key: tk.StringVar(value=self._auto_state(key).get("detail", "-"))
            for key in ("miniqmt", "daily_data", "financial_cache", "prediction_run", "small_capital")
        }
        self.auto_path_vars = {
            key: tk.StringVar(value=self._auto_state(key).get("path", ""))
            for key in ("prediction_run", "small_capital")
        }
        account = self.state.get("account", {})
        self.start_capital_var = tk.StringVar(value=str(account.get("start_capital", "400000")))
        self.high_watermark_var = tk.StringVar(value=str(account.get("high_watermark", "400000")))
        self.current_assets_var = tk.StringVar(value=str(account.get("current_assets", "")))
        self.available_cash_var = tk.StringVar(value=str(account.get("available_cash", "")))
        self.holding_count_var = tk.StringVar(value=str(account.get("holding_count", "")))
        self.drawdown_var = tk.StringVar(value="-")
        self.risk_action_var = tk.StringVar(value="请填写资产后计算")
        self.run_note_text: tk.Text | None = None
        self.action_note_text: tk.Text | None = None

        self._build_ui()
        self._restore_text_widgets()
        self._show_page("weekly")
        self._refresh_risk()
        self._refresh_overall_status()
        self.root.after(150, self._drain_queue)

    def _checks_state(self, group: str) -> dict:
        return self.state.get("checks", {}).get(group, {})

    def _auto_state(self, key: str) -> dict:
        return self._checks_state("auto").get(key, {})

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(outer)
        top.pack(fill=tk.X)
        ttk.Label(top, text="LightGBM 小资金实盘执行清单", font=("Microsoft YaHei UI", 15, "bold")).pack(side=tk.LEFT)
        ttk.Label(top, text="当前周：").pack(side=tk.LEFT, padx=(24, 4))
        ttk.Entry(top, textvariable=self.week_var, width=10).pack(side=tk.LEFT)
        ttk.Label(top, text="策略：").pack(side=tk.LEFT, padx=(24, 4))
        ttk.Label(top, textvariable=self.strategy_var).pack(side=tk.LEFT)
        ttk.Label(top, text="整体状态：").pack(side=tk.RIGHT, padx=(20, 4))
        ttk.Label(top, textvariable=self.overall_status_var, font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.RIGHT)
        ttk.Label(top, text="最近保存：").pack(side=tk.RIGHT, padx=(20, 4))
        ttk.Label(top, textvariable=self.last_save_var).pack(side=tk.RIGHT)

        main = ttk.Frame(outer)
        main.pack(fill=tk.BOTH, expand=True, pady=(10, 8))
        nav = ttk.LabelFrame(main, text="导航", width=190)
        nav.pack(side=tk.LEFT, fill=tk.Y)
        nav.pack_propagate(False)
        content = ttk.Frame(main)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))
        self.content = content

        for page_id, label in [
            ("weekly", "周度准备"),
            ("auto", "自动检查"),
            ("risk", "账户与风控"),
            ("review", "人工复核"),
            ("record", "执行记录"),
            ("files", "文件入口"),
        ]:
            button = ttk.Button(nav, text=label, command=lambda pid=page_id: self._show_page(pid))
            button.pack(fill=tk.X, padx=8, pady=(8 if not self.nav_buttons else 4, 0))
            self.nav_buttons[page_id] = button

        self._register_page("weekly", self._build_weekly_page())
        self._register_page("auto", self._build_auto_page())
        self._register_page("risk", self._build_risk_page())
        self._register_page("review", self._build_review_page())
        self._register_page("record", self._build_record_page())
        self._register_page("files", self._build_files_page())

        bottom = ttk.Frame(outer)
        bottom.pack(fill=tk.X)
        ttk.Button(bottom, text="保存本周状态", command=self.save_state).pack(side=tk.LEFT)
        ttk.Button(bottom, text="刷新自动检查", command=self.run_auto_checks).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bottom, text="打开输出目录", command=lambda: self.open_path(OUTPUT_DIR)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bottom, text="打开执行清单文档", command=lambda: self.open_path(CHECKLIST_DOC)).pack(side=tk.RIGHT)

    def _register_page(self, page_id: str, frame: ttk.Frame) -> None:
        frame.grid(row=0, column=0, sticky="nsew")
        self.pages[page_id] = frame

    def _show_page(self, page_id: str) -> None:
        self.current_page = page_id
        self.pages[page_id].tkraise()
        for key, button in self.nav_buttons.items():
            button.state(["pressed"] if key == page_id else ["!pressed"])

    def _page(self) -> ttk.Frame:
        frame = ttk.Frame(self.content)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        return frame

    def _build_weekly_page(self) -> ttk.Frame:
        page = self._page()
        ttk.Label(page, text="周度准备清单", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(page, text="每周生成候选前，先完成这些确认。自动检查项可在“自动检查”页一键刷新。").pack(anchor=tk.W, pady=(4, 12))

        box = ttk.LabelFrame(page, text="准备项")
        box.pack(fill=tk.X)
        rows = [
            ("MiniQMT 已启动并可连接", "miniqmt"),
            ("本地日线行情已更新到预期最新交易日", "daily_data"),
            ("财务缓存 raw_PershareIndex.csv 存在且非空", "financial_cache"),
            ("最近一次财务增强模型预测输出目录确认", "prediction_run"),
            ("当前账户总资产、可用资金、持仓已记录", "account_recorded"),
            ("本周没有触发暂停交易条件", "no_pause_trigger"),
        ]
        for index, (label, key) in enumerate(rows):
            row = ttk.Frame(box)
            row.pack(fill=tk.X, padx=8, pady=5)
            ttk.Label(row, text=label, width=42).pack(side=tk.LEFT)
            if key in self.auto_status_vars:
                ttk.Label(row, textvariable=self.auto_status_vars[key], width=10).pack(side=tk.LEFT)
                ttk.Label(row, textvariable=self.auto_detail_vars[key]).pack(side=tk.LEFT, fill=tk.X, expand=True)
            else:
                ttk.Checkbutton(row, text="已完成", variable=self.manual_checks[key], command=self._refresh_overall_status).pack(side=tk.LEFT)

        expected = ttk.LabelFrame(page, text="检查参数")
        expected.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(expected, text="预期最新交易日 YYYYMMDD").pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Entry(expected, textvariable=self.expected_date_var, width=12).pack(side=tk.LEFT)
        ttk.Button(expected, text="一键自动检查", command=self.run_auto_checks).pack(side=tk.LEFT, padx=(12, 0))
        return page

    def _build_auto_page(self) -> ttk.Frame:
        page = self._page()
        ttk.Label(page, text="自动检查", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(page, text="只读检查：不下载、不训练、不下单。").pack(anchor=tk.W, pady=(4, 12))
        columns = ("item", "status", "detail")
        self.auto_tree = ttk.Treeview(page, columns=columns, show="headings", height=10)
        self.auto_tree.heading("item", text="检查项")
        self.auto_tree.heading("status", text="状态")
        self.auto_tree.heading("detail", text="详情")
        self.auto_tree.column("item", width=180)
        self.auto_tree.column("status", width=90)
        self.auto_tree.column("detail", width=720)
        self.auto_tree.pack(fill=tk.X)
        self._reload_auto_tree()
        ttk.Button(page, text="刷新自动检查", command=self.run_auto_checks).pack(anchor=tk.W, pady=(10, 0))
        return page

    def _build_risk_page(self) -> ttk.Frame:
        page = self._page()
        ttk.Label(page, text="账户与风控", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W)
        form = ttk.LabelFrame(page, text="账户输入")
        form.pack(fill=tk.X, pady=(10, 0))
        rows = [
            ("策略起始资金", self.start_capital_var),
            ("策略最高权益", self.high_watermark_var),
            ("当前总资产", self.current_assets_var),
            ("当前可用资金", self.available_cash_var),
            ("当前持仓数量", self.holding_count_var),
        ]
        for label, var in rows:
            row = ttk.Frame(form)
            row.pack(fill=tk.X, padx=8, pady=5)
            ttk.Label(row, text=label, width=16).pack(side=tk.LEFT)
            entry = ttk.Entry(row, textvariable=var, width=18)
            entry.pack(side=tk.LEFT)
            entry.bind("<KeyRelease>", lambda _event: self._refresh_risk())
        result = ttk.LabelFrame(page, text="风控提示")
        result.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(result, text="当前回撤").grid(row=0, column=0, padx=8, pady=8, sticky=tk.W)
        ttk.Label(result, textvariable=self.drawdown_var, font=("Microsoft YaHei UI", 12, "bold")).grid(row=0, column=1, padx=8, pady=8, sticky=tk.W)
        ttk.Label(result, text="动作").grid(row=1, column=0, padx=8, pady=8, sticky=tk.W)
        ttk.Label(result, textvariable=self.risk_action_var, font=("Microsoft YaHei UI", 12, "bold")).grid(row=1, column=1, padx=8, pady=8, sticky=tk.W)
        return page

    def _build_review_page(self) -> ttk.Frame:
        page = self._page()
        ttk.Label(page, text="人工复核", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W)
        box = ttk.LabelFrame(page, text="候选股复核")
        box.pack(fill=tk.X, pady=(10, 0))
        items = [
            ("candidate_review_done", "候选股已逐只检查 ST / 停牌 / 重大利空 / 连续涨停"),
            ("weekly_new_limit_confirmed", "确认本周最多新增不超过 2-3 只"),
            ("cash_reserve_confirmed", "确认交易后现金比例不低于 25%"),
            ("positions_recorded", "当前持仓明细已记录"),
        ]
        for key, label in items:
            ttk.Checkbutton(box, text=label, variable=self.manual_checks[key], command=self._refresh_overall_status).pack(anchor=tk.W, padx=8, pady=6)
        ttk.Label(page, text="复核备注").pack(anchor=tk.W, pady=(12, 4))
        self.run_note_text = tk.Text(page, height=14, wrap=tk.WORD)
        self.run_note_text.pack(fill=tk.BOTH, expand=True)
        return page

    def _build_record_page(self) -> ttk.Frame:
        page = self._page()
        ttk.Label(page, text="执行记录", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(page, text="记录本周实际动作、跳过原因、异常情况。").pack(anchor=tk.W, pady=(4, 8))
        self.action_note_text = tk.Text(page, height=22, wrap=tk.WORD)
        self.action_note_text.pack(fill=tk.BOTH, expand=True)
        return page

    def _build_files_page(self) -> ttk.Frame:
        page = self._page()
        ttk.Label(page, text="文件入口", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W)
        entries = [
            ("打开输出目录", OUTPUT_DIR),
            ("打开财务缓存", FINANCIAL_CACHE),
            ("打开预测输出根目录", PREDICTION_ROOT),
            ("打开小资金实验根目录", SMALL_CAPITAL_ROOT),
            ("打开实盘执行清单文档", CHECKLIST_DOC),
        ]
        for label, path in entries:
            row = ttk.Frame(page)
            row.pack(fill=tk.X, pady=5)
            ttk.Button(row, text=label, width=22, command=lambda p=path: self.open_path(p)).pack(side=tk.LEFT)
            ttk.Label(row, text=str(path)).pack(side=tk.LEFT, padx=(8, 0))
        latest = ttk.LabelFrame(page, text="自动发现路径")
        latest.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(latest, text="最新预测目录").grid(row=0, column=0, padx=8, pady=8, sticky=tk.W)
        ttk.Entry(latest, textvariable=self.auto_path_vars["prediction_run"], width=100).grid(row=0, column=1, sticky=tk.W)
        ttk.Button(latest, text="打开", command=lambda: self.open_path(self.auto_path_vars["prediction_run"].get())).grid(row=0, column=2, padx=8)
        ttk.Label(latest, text="最新小资金报告").grid(row=1, column=0, padx=8, pady=8, sticky=tk.W)
        ttk.Entry(latest, textvariable=self.auto_path_vars["small_capital"], width=100).grid(row=1, column=1, sticky=tk.W)
        ttk.Button(latest, text="打开", command=lambda: self.open_path(self.auto_path_vars["small_capital"].get())).grid(row=1, column=2, padx=8)
        return page

    def _restore_text_widgets(self) -> None:
        notes = self.state.get("notes", {})
        if self.run_note_text is not None:
            self.run_note_text.insert("1.0", notes.get("review", ""))
        if self.action_note_text is not None:
            self.action_note_text.insert("1.0", notes.get("action", ""))

    def run_auto_checks(self) -> None:
        self.overall_status_var.set("检查中")
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self) -> None:
        results = {
            "miniqmt": self._check_miniqmt(),
            "daily_data": self._check_daily_data(),
            "financial_cache": self._check_financial_cache(),
            "prediction_run": self._check_prediction_run(),
            "small_capital": self._check_small_capital(),
        }
        self.queue.put(("auto_results", results))

    def _check_miniqmt(self) -> dict:
        try:
            from xtquant import xtdata

            sectors = xtdata.get_sector_list()
            if sectors:
                return {"status": "通过", "detail": "xtdata 可连接，板块数量 {}".format(len(sectors)), "path": ""}
            return {"status": "失败", "detail": "xtdata 返回空板块列表，请确认 MiniQMT 已启动。", "path": ""}
        except Exception as exc:
            return {"status": "失败", "detail": "{}: {}".format(type(exc).__name__, exc), "path": ""}

    def _check_daily_data(self) -> dict:
        expected = normalize_date(self.expected_date_var.get())
        if not expected:
            return {"status": "失败", "detail": "预期最新交易日为空或格式错误。", "path": ""}
        try:
            from xtquant import xtdata

            data = xtdata.get_local_data(
                field_list=[],
                stock_list=[REFERENCE_STOCK],
                period="1d",
                start_time=expected,
                end_time=expected,
                count=-1,
                dividend_type="front",
                fill_data=False,
            )
            frame = data.get(REFERENCE_STOCK) if isinstance(data, dict) else None
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                latest = max(normalize_date(idx) for idx in frame.index)
                return {"status": "通过", "detail": "{} 本地日线包含 {}".format(REFERENCE_STOCK, latest), "path": ""}
            return {"status": "失败", "detail": "{} 本地日线未读取到 {}。".format(REFERENCE_STOCK, expected), "path": ""}
        except Exception as exc:
            return {"status": "失败", "detail": "{}: {}".format(type(exc).__name__, exc), "path": ""}

    def _check_financial_cache(self) -> dict:
        if not FINANCIAL_CACHE.exists():
            return {"status": "失败", "detail": "未找到 {}".format(FINANCIAL_CACHE), "path": str(FINANCIAL_CACHE)}
        try:
            df = pd.read_csv(FINANCIAL_CACHE, encoding="utf-8-sig")
            if df.empty:
                return {"status": "失败", "detail": "财务缓存为空。", "path": str(FINANCIAL_CACHE)}
            ann = df["m_anntime"].map(normalize_date).max() if "m_anntime" in df.columns else ""
            tag = df["m_timetag"].map(normalize_date).max() if "m_timetag" in df.columns else ""
            return {
                "status": "通过",
                "detail": "行数 {}，最新公告日 {}，最新报告期 {}".format(len(df), ann or "-", tag or "-"),
                "path": str(FINANCIAL_CACHE),
            }
        except Exception as exc:
            return {"status": "失败", "detail": "{}: {}".format(type(exc).__name__, exc), "path": str(FINANCIAL_CACHE)}

    def _check_prediction_run(self) -> dict:
        run_dir = latest_dir_with_file(PREDICTION_ROOT, "summary.json")
        if run_dir is None:
            return {"status": "失败", "detail": "未找到模型输出目录。", "path": str(PREDICTION_ROOT)}
        predictions = run_dir / "predictions.csv"
        if not predictions.exists():
            return {"status": "失败", "detail": "最新目录缺少 predictions.csv: {}".format(run_dir), "path": str(run_dir)}
        try:
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            if not summary.get("use_financial_factors"):
                detail = "最新 run 不是财务增强版：{}".format(run_dir.name)
                return {"status": "失败", "detail": detail, "path": str(run_dir)}
            detail = "run={}，预测区间 {} 至 {}".format(
                run_dir.name,
                summary.get("actual_prediction_start_date", ""),
                summary.get("actual_prediction_end_date", ""),
            )
            return {"status": "通过", "detail": detail, "path": str(run_dir)}
        except Exception as exc:
            return {"status": "失败", "detail": "{}: {}".format(type(exc).__name__, exc), "path": str(run_dir)}

    def _check_small_capital(self) -> dict:
        run_dir = latest_dir_with_file(SMALL_CAPITAL_ROOT, "small_capital_report.md")
        if run_dir is None:
            return {"status": "未找到", "detail": "尚未生成小资金实验报告。", "path": str(SMALL_CAPITAL_ROOT)}
        report = run_dir / "small_capital_report.md"
        return {"status": "通过", "detail": "最新小资金报告 {}".format(run_dir.name), "path": str(report)}

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "auto_results":
                    self._apply_auto_results(item[1])
        except queue.Empty:
            pass
        self.root.after(150, self._drain_queue)

    def _apply_auto_results(self, results: dict[str, dict]) -> None:
        for key, result in results.items():
            self.auto_status_vars[key].set(result.get("status", "未知"))
            self.auto_detail_vars[key].set(result.get("detail", ""))
            if key in self.auto_path_vars:
                self.auto_path_vars[key].set(result.get("path", ""))
        self._reload_auto_tree()
        self._refresh_overall_status()

    def _reload_auto_tree(self) -> None:
        if not hasattr(self, "auto_tree"):
            return
        self.auto_tree.delete(*self.auto_tree.get_children())
        labels = {
            "miniqmt": "MiniQMT 连接",
            "daily_data": "本地日线行情",
            "financial_cache": "财务缓存",
            "prediction_run": "最新预测目录",
            "small_capital": "小资金报告",
        }
        for key, label in labels.items():
            self.auto_tree.insert("", tk.END, values=(label, self.auto_status_vars[key].get(), self.auto_detail_vars[key].get()))

    def _refresh_risk(self) -> None:
        try:
            high = float(self.high_watermark_var.get() or 0)
            current = float(self.current_assets_var.get() or 0)
        except ValueError:
            self.drawdown_var.set("-")
            self.risk_action_var.set("资产输入不是数字")
            return
        if high <= 0 or current <= 0:
            self.drawdown_var.set("-")
            self.risk_action_var.set("请填写策略最高权益和当前总资产")
            return
        drawdown = current / high - 1.0
        self.drawdown_var.set("{:.2%}".format(drawdown))
        if drawdown <= -0.10:
            action = "停止策略，重新复盘"
        elif drawdown <= -0.08:
            action = "股票仓位降到一半"
        elif drawdown <= -0.05:
            action = "暂停新增买入"
        else:
            action = "正常：未触发账户级暂停"
        self.risk_action_var.set(action)
        self._refresh_overall_status()

    def _refresh_overall_status(self) -> None:
        auto_required = ["miniqmt", "daily_data", "financial_cache", "prediction_run"]
        auto_ok = all(self.auto_status_vars[key].get() == "通过" for key in auto_required)
        manual_ok = all(var.get() for var in self.manual_checks.values())
        action = self.risk_action_var.get()
        risk_ok = action.startswith("正常") or action.startswith("请填写")
        if auto_ok and manual_ok and risk_ok:
            self.overall_status_var.set("可进入候选生成/人工复核")
        elif not auto_ok:
            self.overall_status_var.set("自动检查未通过")
        elif not manual_ok:
            self.overall_status_var.set("仍有手动项未完成")
        else:
            self.overall_status_var.set("触发风控提示")

    def save_state(self) -> None:
        data = self._collect_state()
        STATE_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._append_weekly_log(data)
        self.last_save_var.set(data["last_saved_at"])
        messagebox.showinfo("已保存", "已保存本周执行清单状态。")

    def _collect_state(self) -> dict:
        auto = {
            key: {
                "status": self.auto_status_vars[key].get(),
                "detail": self.auto_detail_vars[key].get(),
                "path": self.auto_path_vars[key].get() if key in self.auto_path_vars else "",
            }
            for key in self.auto_status_vars
        }
        manual = {key: var.get() for key, var in self.manual_checks.items()}
        return {
            "week_id": self.week_var.get().strip() or current_week_id(),
            "strategy_id": DEFAULT_STRATEGY_ID,
            "strategy_cn": DEFAULT_STRATEGY_CN,
            "expected_latest_trade_date": self.expected_date_var.get().strip(),
            "last_saved_at": now_text(),
            "overall_status": self.overall_status_var.get(),
            "checks": {"auto": auto, "manual": manual},
            "account": {
                "start_capital": self.start_capital_var.get().strip(),
                "high_watermark": self.high_watermark_var.get().strip(),
                "current_assets": self.current_assets_var.get().strip(),
                "available_cash": self.available_cash_var.get().strip(),
                "holding_count": self.holding_count_var.get().strip(),
                "drawdown": self.drawdown_var.get(),
                "risk_action": self.risk_action_var.get(),
            },
            "notes": {
                "review": self.run_note_text.get("1.0", tk.END).strip() if self.run_note_text is not None else "",
                "action": self.action_note_text.get("1.0", tk.END).strip() if self.action_note_text is not None else "",
            },
        }

    def _append_weekly_log(self, data: dict) -> None:
        exists = WEEKLY_LOG.exists()
        with WEEKLY_LOG.open("a", encoding="utf-8-sig", newline="") as file_obj:
            writer = csv.DictWriter(
                file_obj,
                fieldnames=[
                    "saved_at",
                    "week_id",
                    "overall_status",
                    "current_assets",
                    "available_cash",
                    "holding_count",
                    "drawdown",
                    "risk_action",
                    "auto_pass_count",
                    "manual_pass_count",
                ],
            )
            if not exists:
                writer.writeheader()
            auto = data["checks"]["auto"]
            manual = data["checks"]["manual"]
            writer.writerow(
                {
                    "saved_at": data["last_saved_at"],
                    "week_id": data["week_id"],
                    "overall_status": data["overall_status"],
                    "current_assets": data["account"]["current_assets"],
                    "available_cash": data["account"]["available_cash"],
                    "holding_count": data["account"]["holding_count"],
                    "drawdown": data["account"]["drawdown"],
                    "risk_action": data["account"]["risk_action"],
                    "auto_pass_count": sum(1 for item in auto.values() if item.get("status") == "通过"),
                    "manual_pass_count": sum(1 for value in manual.values() if value),
                }
            )

    def _load_state(self) -> dict:
        if not STATE_JSON.exists():
            return {}
        try:
            return json.loads(STATE_JSON.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def open_path(self, path_value) -> None:
        text = str(path_value).strip()
        if not text:
            messagebox.showinfo("路径为空", "还没有可打开的路径。")
            return
        path = Path(text)
        if not path.exists():
            messagebox.showinfo("文件不存在", "还没有生成：\n{}".format(path))
            return
        os.startfile(str(path))


def main() -> int:
    root = tk.Tk()
    LiveExecutionChecklistGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
