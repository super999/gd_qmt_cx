#!/usr/bin/env python3
# coding: utf-8
from __future__ import annotations

import csv
import json
import os
import queue
import threading
from datetime import datetime, timedelta
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
DAILY_DATA_READY_TIME = "15:30"
PREDICTION_REFRESH_COMMAND = (
    r"d:\python_envs\gd_qmt_env\python.exe "
    r"code\ml_stock_selection\lightgbm_multi_factor_stock_selection.py "
    r"--use-financial-factors --min-prediction-date 20260101 --recent-prediction-days 30"
)
SMALL_CAPITAL_REFRESH_COMMAND = (
    r"d:\python_envs\gd_qmt_env\python.exe "
    r"code\ml_stock_selection\run_small_capital_experiments.py "
    r"--run-dirs <最新预测目录> --labels financial "
    r"--filters none,growth_q50,bp_value_q70 "
    r"--capital 400000 --min-trade-amount 30000 "
    r"--max-holdings-values 5,8,10,12 --cash-reserve-rates 0.10,0.25"
)


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


def latest_file(root: Path, required_name: str) -> Path | None:
    run_dir = latest_dir_with_file(root, required_name)
    return run_dir / required_name if run_dir is not None else None


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
        self.next_step_var = tk.StringVar(value="下一步：先进入“自动检查”并刷新检查结果。")
        self.strategy_var = tk.StringVar(value=DEFAULT_STRATEGY_CN)
        self.next_page_id = "auto"
        settings = self.state.get("settings", {})
        self.close_auto_save_var = tk.BooleanVar(value=bool(settings.get("close_auto_save", True)))

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
        self.prediction_command_var = tk.StringVar(value=self._prediction_refresh_command())
        self.small_capital_command_var = tk.StringVar(value=self._small_capital_refresh_command())
        self.candidate_status_var = tk.StringVar(value="请先刷新候选股")
        self.candidate_path_var = tk.StringVar(value="")
        self.candidate_date_var = tk.StringVar(value="最新")
        self.candidate_date_combo: ttk.Combobox | None = None
        account = self.state.get("account", {})
        self.start_capital_var = tk.StringVar(value=str(account.get("start_capital", "400000")))
        self.high_watermark_var = tk.StringVar(value=str(account.get("high_watermark", "400000")))
        self.current_assets_var = tk.StringVar(value=str(account.get("current_assets", "")))
        self.available_cash_var = tk.StringVar(value=str(account.get("available_cash", "")))
        self.holding_count_var = tk.StringVar(value=str(account.get("holding_count", "")))
        self._saved_account_snapshot = {
            "start_capital": self.start_capital_var.get(),
            "high_watermark": self.high_watermark_var.get(),
            "current_assets": self.current_assets_var.get(),
            "available_cash": self.available_cash_var.get(),
            "holding_count": self.holding_count_var.get(),
        }
        self.drawdown_var = tk.StringVar(value="-")
        self.risk_action_var = tk.StringVar(value="请填写资产后计算")
        self.run_note_text: tk.Text | None = None
        self.action_note_text: tk.Text | None = None
        self.holding_note_text: tk.Text | None = None

        self._build_ui()
        self._restore_text_widgets()
        self._show_page("weekly")
        self._refresh_risk()
        self._refresh_overall_status()
        self.root.after(150, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Control-s>", self.save_state_shortcut)
        self.root.bind("<Control-S>", self.save_state_shortcut)

    def _checks_state(self, group: str) -> dict:
        return self.state.get("checks", {}).get(group, {})

    def _auto_state(self, key: str) -> dict:
        return self._checks_state("auto").get(key, {})

    def _prediction_refresh_command(self) -> str:
        return PREDICTION_REFRESH_COMMAND

    def _small_capital_refresh_command(self) -> str:
        prediction_dir = self.auto_path_vars["prediction_run"].get().strip() if hasattr(self, "auto_path_vars") else ""
        if not prediction_dir:
            prediction_dir = "<最新预测目录>"
        return SMALL_CAPITAL_REFRESH_COMMAND.replace("<最新预测目录>", '"{}"'.format(prediction_dir))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        title_bar = ttk.Frame(outer)
        title_bar.pack(fill=tk.X)
        ttk.Label(title_bar, text="LightGBM 小资金实盘执行清单", font=("Microsoft YaHei UI", 15, "bold")).pack(side=tk.LEFT)

        status_bar = ttk.LabelFrame(outer, text="当前状态")
        status_bar.pack(fill=tk.X, pady=(8, 0))
        for column, weight in enumerate([0, 2, 1, 1, 0]):
            status_bar.columnconfigure(column, weight=weight)

        week_cell = ttk.Frame(status_bar, padding=(8, 4))
        week_cell.grid(row=0, column=0, sticky="nsew")
        ttk.Label(week_cell, text="当前周").pack(anchor=tk.W)
        ttk.Entry(week_cell, textvariable=self.week_var, width=12).pack(anchor=tk.W, pady=(2, 0))

        strategy_cell = ttk.Frame(status_bar, padding=(8, 4))
        strategy_cell.grid(row=0, column=1, sticky="nsew")
        ttk.Label(strategy_cell, text="当前策略").pack(anchor=tk.W)
        ttk.Label(strategy_cell, textvariable=self.strategy_var, wraplength=520, justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

        saved_cell = ttk.Frame(status_bar, padding=(8, 4))
        saved_cell.grid(row=0, column=2, sticky="nsew")
        ttk.Label(saved_cell, text="最近保存").pack(anchor=tk.W)
        ttk.Label(saved_cell, textvariable=self.last_save_var, font=("Microsoft YaHei UI", 10, "bold")).pack(anchor=tk.W, pady=(2, 0))

        overall_cell = ttk.Frame(status_bar, padding=(8, 4))
        overall_cell.grid(row=0, column=3, sticky="nsew")
        ttk.Label(overall_cell, text="整体状态").pack(anchor=tk.W)
        ttk.Label(overall_cell, textvariable=self.overall_status_var, font=("Microsoft YaHei UI", 10, "bold"), wraplength=220).pack(anchor=tk.W, pady=(2, 0))

        action_cell = ttk.Frame(status_bar, padding=(8, 4))
        action_cell.grid(row=0, column=4, sticky="nsew")
        ttk.Label(action_cell, text="操作").pack(anchor=tk.W)
        ttk.Button(action_cell, text="保存当前填写内容", command=self.save_state).pack(anchor=tk.W, pady=(2, 0))

        ttk.Label(outer, text="填写或勾选后可点击“保存当前填写内容”；关闭窗口会自动保存；快捷键 Ctrl+S。", foreground="#555555").pack(fill=tk.X, pady=(4, 0))
        next_bar = ttk.LabelFrame(outer, text="下一步提示")
        next_bar.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(next_bar, textvariable=self.next_step_var, font=("Microsoft YaHei UI", 10, "bold"), wraplength=980).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=6)
        ttk.Button(next_bar, text="打开下一步页面", command=self.open_next_step_page).pack(side=tk.RIGHT, padx=8, pady=6)

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
            ("candidates", "候选股"),
            ("review", "人工复核"),
            ("record", "执行记录"),
            ("files", "文件入口"),
            ("settings", "设置"),
        ]:
            button = ttk.Button(nav, text=label, command=lambda pid=page_id: self._show_page(pid))
            button.pack(fill=tk.X, padx=8, pady=(8 if not self.nav_buttons else 4, 0))
            self.nav_buttons[page_id] = button

        self._register_page("weekly", self._build_weekly_page())
        self._register_page("auto", self._build_auto_page())
        self._register_page("risk", self._build_risk_page())
        self._register_page("candidates", self._build_candidates_page())
        self._register_page("review", self._build_review_page())
        self._register_page("record", self._build_record_page())
        self._register_page("files", self._build_files_page())
        self._register_page("settings", self._build_settings_page())

        bottom = ttk.Frame(outer)
        bottom.pack(fill=tk.X)
        ttk.Button(bottom, text="保存当前填写内容", command=self.save_state).pack(side=tk.LEFT)
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

    def open_next_step_page(self) -> None:
        if self.next_page_id in self.pages:
            self._show_page(self.next_page_id)

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

        help_box = ttk.LabelFrame(page, text="这两个检查项怎么刷新")
        help_box.pack(fill=tk.X, pady=(14, 0))
        ttk.Label(
            help_box,
            text=(
                "最新预测目录：LightGBM 模型训练/预测后的输出目录，里面必须有 predictions.csv 和 summary.json。"
                "界面显示的 run 是这次模型运行的目录名；预测区间是 predictions.csv 覆盖的交易日期范围。"
                "日常刷新建议只预测最近 30 个可用交易日，速度更快。"
            ),
            wraplength=940,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=8, pady=(8, 4))
        self._command_row(
            help_box,
            "复制刷新预测命令",
            self.prediction_command_var,
            "不填 --end-date 时，脚本会自动使用本地 510300.SH 日线已有的最新交易日。",
        )
        ttk.Label(
            help_box,
            text=(
                "小资金报告：基于上面的 predictions.csv，再按 40 万资金、最小买入金额、最多持仓数和现金保留比例做近似实盘约束回测。"
                "它不会重新训练模型，只是把最新预测结果转换成小资金可执行组合报告。"
            ),
            wraplength=940,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=8, pady=4)
        self._command_row(
            help_box,
            "复制刷新小资金报告命令",
            self.small_capital_command_var,
            "先点“刷新自动检查”，让 APP 找到最新预测目录；复制时会自动带入该目录。",
        )
        return page

    def _command_row(self, parent: ttk.Frame, button_text: str, command_var: tk.StringVar, note: str) -> None:
        ttk.Label(parent, text=note, wraplength=940, justify=tk.LEFT).pack(anchor=tk.W, padx=8, pady=(6, 2))
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, padx=8, pady=(0, 8))
        entry = ttk.Entry(row, textvariable=command_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text=button_text, command=lambda var=command_var: self.copy_command(var.get())).pack(side=tk.LEFT, padx=(8, 0))

    def _build_risk_page(self) -> ttk.Frame:
        page = self._page()
        ttk.Label(page, text="账户与风控", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(
            page,
            text=(
                "这些数值需要你从券商/QMT 账户里手动抄写。本页只做风险提示，不会读取账户、不保存到券商、不会下单。"
                "填写后点“保存账户与风控”，或使用窗口右上角/底部的“保存当前填写内容”。"
            ),
            wraplength=980,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 8))
        form = ttk.LabelFrame(page, text="账户输入")
        form.pack(fill=tk.X, pady=(10, 0))
        rows = [
            ("策略起始资金", self.start_capital_var, "第一次启用本策略时投入的资金。例如 400000。"),
            ("策略最高权益", self.high_watermark_var, "策略运行以来账户总资产最高值。刚开始可先填当前总资产或 400000。"),
            ("当前总资产", self.current_assets_var, "券商账户里的总资产/总权益，用于计算当前回撤。"),
            ("当前可用资金", self.available_cash_var, "券商账户里的可用现金，用于判断是否还能新增买入。"),
            ("当前持仓数量", self.holding_count_var, "当前股票持仓只数，不含现金。"),
        ]
        for label, var, tip in rows:
            row = ttk.Frame(form)
            row.pack(fill=tk.X, padx=8, pady=5)
            ttk.Label(row, text=label, width=16).pack(side=tk.LEFT)
            entry = ttk.Entry(row, textvariable=var, width=18)
            entry.pack(side=tk.LEFT)
            entry.bind("<KeyRelease>", lambda _event: self._refresh_risk())
            ttk.Label(row, text=tip).pack(side=tk.LEFT, padx=(10, 0))
        actions = ttk.Frame(form)
        actions.pack(fill=tk.X, padx=8, pady=(8, 10))
        ttk.Button(actions, text="保存账户与风控", command=self.save_state).pack(side=tk.LEFT)
        ttk.Button(actions, text="恢复上次保存", command=self.restore_account_snapshot).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="清空账户输入", command=self.clear_account_inputs).pack(side=tk.LEFT, padx=(8, 0))
        result = ttk.LabelFrame(page, text="风控提示")
        result.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(result, text="当前回撤").grid(row=0, column=0, padx=8, pady=8, sticky=tk.W)
        ttk.Label(result, textvariable=self.drawdown_var, font=("Microsoft YaHei UI", 12, "bold")).grid(row=0, column=1, padx=8, pady=8, sticky=tk.W)
        ttk.Label(result, text="动作").grid(row=1, column=0, padx=8, pady=8, sticky=tk.W)
        ttk.Label(result, textvariable=self.risk_action_var, font=("Microsoft YaHei UI", 12, "bold")).grid(row=1, column=1, padx=8, pady=8, sticky=tk.W)
        rule_box = ttk.LabelFrame(page, text="风控阈值说明")
        rule_box.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(
            rule_box,
            text="- 当前回撤 = 当前总资产 / 策略最高权益 - 1。\n"
            "- 回撤达到 -5%：暂停新增买入。\n"
            "- 回撤达到 -8%：股票仓位降到一半。\n"
            "- 回撤达到 -10%：停止策略并复盘。\n"
            "- 如果刚开始实盘，策略最高权益可以先等于当前总资产；以后账户创新高时再手动更新。",
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=8, pady=8)
        return page

    def _build_review_page(self) -> ttk.Frame:
        page = self._page()
        ttk.Label(page, text="人工复核", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(
            page,
            text="先记录当前持仓，再逐只复核候选股。所有勾选都只是人工确认，不会自动交易。",
            wraplength=980,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 8))

        holding_box = ttk.LabelFrame(page, text="当前持仓明细记录")
        holding_box.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(
            holding_box,
            text="从券商/QMT 持仓页抄写。格式建议：代码 名称 持仓股数 市值 成本价/现价。当前无持仓时点“填入无持仓”。",
            wraplength=980,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=8, pady=(8, 4))
        holding_actions = ttk.Frame(holding_box)
        holding_actions.pack(fill=tk.X, padx=8, pady=(0, 6))
        ttk.Button(holding_actions, text="填入无持仓", command=self.fill_no_holdings).pack(side=tk.LEFT)
        ttk.Button(holding_actions, text="插入持仓模板", command=self.insert_holding_template).pack(side=tk.LEFT, padx=(8, 0))
        self.holding_note_text = tk.Text(holding_box, height=5, wrap=tk.WORD)
        self.holding_note_text.pack(fill=tk.X, padx=8, pady=(0, 8))

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
        ttk.Label(page, text="候选股复核备注").pack(anchor=tk.W, pady=(12, 4))
        actions = ttk.Frame(page)
        actions.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(actions, text="插入候选复核模板", command=self.insert_review_template).pack(side=tk.LEFT)
        self.run_note_text = tk.Text(page, height=10, wrap=tk.WORD)
        self.run_note_text.pack(fill=tk.BOTH, expand=True)
        return page

    def _build_candidates_page(self) -> ttk.Frame:
        page = self._page()
        ttk.Label(page, text="候选股", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(
            page,
            text="自动读取最近一次小资金报告的候选记录。可以选择最新日期，也可以回看昨天、前天或上周候选。这里只是候选清单，买入前仍需到“人工复核”页逐项确认。",
            wraplength=980,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 8))
        toolbar = ttk.Frame(page)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(toolbar, text="刷新候选股", command=self.load_candidates).pack(side=tk.LEFT)
        ttk.Label(toolbar, text="候选日期").pack(side=tk.LEFT, padx=(10, 4))
        self.candidate_date_combo = ttk.Combobox(toolbar, textvariable=self.candidate_date_var, width=12, state="readonly")
        self.candidate_date_combo.pack(side=tk.LEFT)
        self.candidate_date_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_candidates())
        ttk.Button(toolbar, text="打开候选CSV", command=lambda: self.open_path(self.candidate_path_var.get())).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(toolbar, textvariable=self.candidate_status_var).pack(side=tk.LEFT, padx=(12, 0))

        columns = ("candidate_date", "code", "name", "action_hint", "shares", "value", "pred_return_5d", "pred_up_prob", "risk_score")
        self.candidate_tree = ttk.Treeview(page, columns=columns, show="headings", height=22)
        headings = {
            "candidate_date": "候选日期",
            "code": "代码",
            "name": "名称",
            "action_hint": "动作提示",
            "shares": "股数",
            "value": "目标市值",
            "pred_return_5d": "预测5日收益",
            "pred_up_prob": "上涨概率",
            "risk_score": "风险分",
        }
        widths = {
            "candidate_date": 90,
            "code": 95,
            "name": 110,
            "action_hint": 90,
            "shares": 80,
            "value": 95,
            "pred_return_5d": 100,
            "pred_up_prob": 90,
            "risk_score": 80,
        }
        for column in columns:
            self.candidate_tree.heading(column, text=headings[column])
            self.candidate_tree.column(column, width=widths[column], anchor=tk.CENTER)
        self.candidate_tree.pack(fill=tk.BOTH, expand=True)
        self.load_candidates()
        return page

    def _build_record_page(self) -> ttk.Frame:
        page = self._page()
        ttk.Label(page, text="执行记录", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(
            page,
            text="在券商/QMT 手动买卖后，回到这里记录实际动作。没有交易也要写明原因。",
            wraplength=980,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 8))
        ttk.Button(page, text="插入执行记录模板", command=self.insert_action_template).pack(anchor=tk.W, pady=(0, 6))
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

    def _build_settings_page(self) -> ttk.Frame:
        page = self._page()
        ttk.Label(page, text="设置", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(
            page,
            text="这里控制 GUI 的使用习惯，不影响模型、不下载数据、不下单。",
            wraplength=980,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 12))

        box = ttk.LabelFrame(page, text="保存设置")
        box.pack(fill=tk.X)
        ttk.Checkbutton(
            box,
            text="关闭窗口时自动保存",
            variable=self.close_auto_save_var,
        ).pack(anchor=tk.W, padx=8, pady=(8, 4))
        ttk.Label(
            box,
            text=(
                "勾选：点右上角关闭时静默保存当前填写内容。\n"
                "不勾选：关闭时弹出提示，可选择保存、不保存或取消关闭。\n"
                "无论是否勾选，Ctrl+S 和“保存当前填写内容”按钮都可以手动保存。"
            ),
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=8, pady=(0, 8))
        ttk.Button(box, text="保存设置", command=self.save_state).pack(anchor=tk.W, padx=8, pady=(0, 10))
        return page

    def load_candidates(self) -> None:
        if not hasattr(self, "candidate_tree"):
            return
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        try:
            df, status_text = self._build_candidate_view()
        except Exception as exc:
            self.candidate_status_var.set("读取候选股失败: {}: {}".format(type(exc).__name__, exc))
            self.candidate_path_var.set(str(SMALL_CAPITAL_ROOT))
            return
        if df.empty:
            self.candidate_status_var.set("候选股为空。{}".format(status_text))
            return
        view_path = OUTPUT_DIR / "candidate_view.csv"
        df.to_csv(view_path, index=False, encoding="utf-8-sig")
        self.candidate_path_var.set(str(view_path))
        for _, row in df.iterrows():
            values = (
                str(row.get("candidate_date", "")),
                str(row.get("code", "")),
                "" if pd.isna(row.get("name", "")) else str(row.get("name", "")),
                str(row.get("action_hint", "")),
                self._format_number(row.get("shares", ""), 0),
                self._format_number(row.get("value", ""), 0),
                self._format_percent(row.get("pred_return_5d", "")),
                self._format_percent(row.get("pred_up_prob", "")),
                self._format_number(row.get("risk_score", ""), 1),
            )
            self.candidate_tree.insert("", tk.END, values=values)
        rule = str(df["experiment_name"].iloc[0]) if "experiment_name" in df.columns else ""
        date = str(df["candidate_date"].iloc[0]) if "candidate_date" in df.columns else ""
        self.candidate_status_var.set("已加载 {} 只候选；日期 {}；规则 {}；{}".format(len(df), date, rule, status_text))

    def _build_candidate_view(self) -> tuple[pd.DataFrame, str]:
        run_dir = latest_dir_with_file(SMALL_CAPITAL_ROOT, "small_capital_summary.csv")
        if run_dir is None:
            fallback = latest_file(SMALL_CAPITAL_ROOT, "latest_candidates.csv")
            if fallback is None or not fallback.exists():
                return pd.DataFrame(), "未找到小资金报告，请先运行刷新小资金报告命令。"
            df = pd.read_csv(fallback, encoding="utf-8-sig")
            self._set_candidate_date_values(sorted(df["candidate_date"].astype(str).unique(), reverse=True) if "candidate_date" in df.columns else [])
            return df, "仅找到 latest_candidates.csv，不能回看更多历史日期。"

        summary = pd.read_csv(run_dir / "small_capital_summary.csv", encoding="utf-8-sig")
        if summary.empty:
            return pd.DataFrame(), "小资金汇总为空: {}".format(run_dir.name)
        preferred = summary[summary["experiment_name"] == DEFAULT_STRATEGY_ID] if "experiment_name" in summary.columns else pd.DataFrame()
        if preferred.empty:
            selected = summary.sort_values(["max_drawdown_with_cost", "total_return_with_cost"], ascending=[False, False]).iloc[0]
        else:
            selected = preferred.iloc[0]
        experiment_dir = run_dir / str(selected["source_label"]) / str(selected["experiment_name"])
        holdings_path = experiment_dir / "holdings.csv"
        trades_path = experiment_dir / "trades.csv"
        if not holdings_path.exists():
            return pd.DataFrame(), "未找到 holdings.csv: {}".format(holdings_path)

        holdings = pd.read_csv(holdings_path, encoding="utf-8-sig")
        if holdings.empty:
            return pd.DataFrame(), "holdings.csv 为空: {}".format(holdings_path)
        holdings["trade_date"] = holdings["trade_date"].astype(str)
        dates = sorted(holdings["trade_date"].dropna().astype(str).unique(), reverse=True)
        self._set_candidate_date_values(dates)
        selected_date = self.candidate_date_var.get().strip()
        if selected_date not in dates:
            selected_date = dates[0]
            self.candidate_date_var.set(selected_date)

        candidates = holdings[holdings["trade_date"] == selected_date].copy()
        candidates["candidate_date"] = selected_date
        candidates["action_hint"] = "保留/持有"
        if trades_path.exists():
            trades = pd.read_csv(trades_path, encoding="utf-8-sig")
            if not trades.empty:
                trades["trade_date"] = trades["trade_date"].astype(str)
                latest_trades = trades[trades["trade_date"] == selected_date]
                action_map = dict(zip(latest_trades["code"], latest_trades["action"]))
                candidates["action_hint"] = candidates["code"].map(action_map).fillna("保留/持有")
                candidates["action_hint"] = candidates["action_hint"].replace({"buy": "新增/加仓", "sell": "减仓/卖出"})
        candidates["source_label"] = selected["source_label"]
        candidates["experiment_name"] = selected["experiment_name"]
        candidates["experiment_name_cn"] = selected["experiment_name_cn"]
        candidates["source_run_dir"] = selected["source_run_dir"]
        columns = [
            "candidate_date",
            "code",
            "name",
            "action_hint",
            "shares",
            "value",
            "pred_return_5d",
            "pred_up_prob",
            "risk_score",
            "experiment_name",
            "experiment_name_cn",
            "source_run_dir",
        ]
        existing_columns = [column for column in columns if column in candidates.columns]
        candidates = candidates[existing_columns].sort_values(["action_hint", "value"], ascending=[True, False])
        return candidates, "可回看 {} 个候选日期；小资金run={}".format(len(dates), run_dir.name)

    def _set_candidate_date_values(self, dates: list[str]) -> None:
        if self.candidate_date_combo is None:
            return
        self.candidate_date_combo["values"] = dates

    def _format_number(self, value, digits: int) -> str:
        try:
            if pd.isna(value):
                return ""
            return "{:.{}f}".format(float(value), digits)
        except Exception:
            return str(value)

    def _format_percent(self, value) -> str:
        try:
            if pd.isna(value):
                return ""
            return "{:.2%}".format(float(value))
        except Exception:
            return str(value)

    def _restore_text_widgets(self) -> None:
        notes = self.state.get("notes", {})
        if self.holding_note_text is not None:
            self.holding_note_text.insert("1.0", notes.get("holdings", ""))
        if self.run_note_text is not None:
            self.run_note_text.insert("1.0", notes.get("review", ""))
        if self.action_note_text is not None:
            self.action_note_text.insert("1.0", notes.get("action", ""))

    def fill_no_holdings(self) -> None:
        if self.holding_note_text is None:
            return
        text = "{} 当前无股票持仓；账户持仓数量为 0。".format(today_yyyymmdd())
        self.holding_note_text.delete("1.0", tk.END)
        self.holding_note_text.insert("1.0", text)
        self.manual_checks["positions_recorded"].set(True)
        self._refresh_overall_status()

    def insert_holding_template(self) -> None:
        if self.holding_note_text is None:
            return
        template = (
            "当前持仓明细（从券商/QMT 持仓页抄写）：\n"
            "代码 | 名称 | 持仓股数 | 市值 | 成本价 | 现价 | 备注\n"
            "示例：000001.SZ | 平安银行 | 1000 | 12000 | 10.50 | 12.00 | 原有持仓\n"
        )
        self.holding_note_text.insert(tk.END, ("\n" if self.holding_note_text.get("1.0", tk.END).strip() else "") + template)

    def insert_review_template(self) -> None:
        if self.run_note_text is None:
            return
        lines = ["候选股复核："]
        candidate_path = Path(self.candidate_path_var.get().strip()) if self.candidate_path_var.get().strip() else None
        candidate_file = candidate_path if candidate_path is not None and candidate_path.exists() else latest_file(SMALL_CAPITAL_ROOT, "latest_candidates.csv")
        if candidate_file is not None:
            try:
                df = pd.read_csv(candidate_file, encoding="utf-8-sig")
                for _, row in df.iterrows():
                    code = str(row.get("code", "")).strip()
                    name = str(row.get("name", "")).strip()
                    action = str(row.get("action_hint", "")).strip()
                    lines.append(
                        "- {} {} {}：ST[ ] 停牌[ ] 涨停/一字板[ ] 重大公告[ ] 买入金额[ ] 结论：".format(
                            code,
                            name,
                            action,
                        )
                    )
            except Exception as exc:
                lines.append("- 读取候选股失败：{}: {}".format(type(exc).__name__, exc))
        else:
            lines.append("- 未找到 latest_candidates.csv，请先刷新小资金报告。")
        lines.extend(
            [
                "",
                "本周计划新增：__ 只",
                "交易后预计现金比例：__%",
                "不执行/延后执行原因：",
            ]
        )
        self.run_note_text.insert(tk.END, ("\n" if self.run_note_text.get("1.0", tk.END).strip() else "") + "\n".join(lines))

    def insert_action_template(self) -> None:
        if self.action_note_text is None:
            return
        template = (
            "{} 执行记录\n"
            "账户总资产：\n"
            "可用资金：\n"
            "实际买入：代码 | 名称 | 股数 | 成交价 | 成交金额 | 原因\n"
            "实际卖出：代码 | 名称 | 股数 | 成交价 | 成交金额 | 原因\n"
            "跳过候选：代码 | 名称 | 原因（涨停/停牌/公告/价格过高/资金不足/人工放弃）\n"
            "交易后预计现金比例：\n"
            "异常说明：\n"
        ).format(today_yyyymmdd())
        self.action_note_text.insert(tk.END, ("\n" if self.action_note_text.get("1.0", tk.END).strip() else "") + template)

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

            today = today_yyyymmdd()
            scan_start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
            data = xtdata.get_local_data(
                field_list=[],
                stock_list=[REFERENCE_STOCK],
                period="1d",
                start_time=scan_start,
                end_time=max(expected, today),
                count=-1,
                dividend_type="front",
                fill_data=False,
            )
            frame = data.get(REFERENCE_STOCK) if isinstance(data, dict) else None
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                dates = [normalize_date(idx) for idx in frame.index]
                dates = [date for date in dates if date]
                latest = max(dates) if dates else ""
                if latest >= expected:
                    return {"status": "通过", "detail": "{} 本地日线最新为 {}，已满足预期 {}。".format(REFERENCE_STOCK, latest, expected), "path": ""}
                if self._is_intraday_today(expected):
                    return {
                        "status": "通过",
                        "detail": "{} 本地日线最新为 {}；当前未到 {}，不要求今日 {} 日线。".format(
                            REFERENCE_STOCK,
                            latest or "-",
                            DAILY_DATA_READY_TIME,
                            expected,
                        ),
                        "path": "",
                    }
                if self._is_weekend_today(expected):
                    return {
                        "status": "通过",
                        "detail": "{} 本地日线最新为 {}；今天是周末，不要求 {} 日线。".format(REFERENCE_STOCK, latest or "-", expected),
                        "path": "",
                    }
                return {"status": "失败", "detail": "{} 本地日线最新为 {}，未达到预期 {}。".format(REFERENCE_STOCK, latest or "-", expected), "path": ""}
            return {"status": "失败", "detail": "{} 最近 30 天未读取到本地日线。".format(REFERENCE_STOCK), "path": ""}
        except Exception as exc:
            return {"status": "失败", "detail": "{}: {}".format(type(exc).__name__, exc), "path": ""}

    def _is_intraday_today(self, expected: str) -> bool:
        now = datetime.now()
        ready_time = datetime.strptime(DAILY_DATA_READY_TIME, "%H:%M").time()
        return expected == now.strftime("%Y%m%d") and now.weekday() < 5 and now.time() < ready_time

    def _is_weekend_today(self, expected: str) -> bool:
        now = datetime.now()
        return expected == now.strftime("%Y%m%d") and now.weekday() >= 5

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
                detail = "最新模型输出不是财务增强版；请重新运行带 --use-financial-factors 的 LightGBM。目录：{}".format(run_dir.name)
                return {"status": "失败", "detail": detail, "path": str(run_dir)}
            detail = "模型输出目录={}；预测文件=predictions.csv；可用预测日期 {} 至 {}。如需更新，请重新运行 LightGBM 训练/预测脚本。".format(
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
            return {
                "status": "未找到",
                "detail": "尚未生成小资金报告；请先用最新预测目录运行 run_small_capital_experiments.py。",
                "path": str(SMALL_CAPITAL_ROOT),
            }
        report = run_dir / "small_capital_report.md"
        return {
            "status": "通过",
            "detail": "小资金约束回测报告={}；它基于某次 predictions.csv 生成，不会自动跟随模型输出刷新。".format(run_dir.name),
            "path": str(report),
        }

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
        self.prediction_command_var.set(self._prediction_refresh_command())
        self.small_capital_command_var.set(self._small_capital_refresh_command())
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

    def restore_account_snapshot(self) -> None:
        self.start_capital_var.set(self._saved_account_snapshot.get("start_capital", "400000"))
        self.high_watermark_var.set(self._saved_account_snapshot.get("high_watermark", "400000"))
        self.current_assets_var.set(self._saved_account_snapshot.get("current_assets", ""))
        self.available_cash_var.set(self._saved_account_snapshot.get("available_cash", ""))
        self.holding_count_var.set(self._saved_account_snapshot.get("holding_count", ""))
        self._refresh_risk()

    def clear_account_inputs(self) -> None:
        self.current_assets_var.set("")
        self.available_cash_var.set("")
        self.holding_count_var.set("")
        self._refresh_risk()

    def _refresh_overall_status(self) -> None:
        auto_required = ["miniqmt", "daily_data", "financial_cache", "prediction_run"]
        auto_ok = all(self.auto_status_vars[key].get() == "通过" for key in auto_required)
        manual_ok = all(var.get() for var in self.manual_checks.values())
        action = self.risk_action_var.get()
        risk_ok = action.startswith("正常") or action.startswith("请填写")
        if auto_ok and manual_ok and risk_ok:
            self.overall_status_var.set("准备完成：可手动执行")
            self.next_step_var.set("下一步：到券商/QMT 账户手动执行买卖；执行完成后回到“执行记录”页，记录成交股票、股数、价格、跳过原因，再保存。")
            self.next_page_id = "record"
        elif not auto_ok:
            self.overall_status_var.set("自动检查未通过")
            missing = [key for key in auto_required if self.auto_status_vars[key].get() != "通过"]
            label = {
                "miniqmt": "MiniQMT 连接",
                "daily_data": "本地日线行情",
                "financial_cache": "财务缓存",
                "prediction_run": "最新预测目录",
            }.get(missing[0], "自动检查")
            self.next_step_var.set("下一步：进入“自动检查”，点击“刷新自动检查”。当前未通过项：{}。".format(label))
            self.next_page_id = "auto"
        elif not manual_ok:
            self.overall_status_var.set("仍有手动项未完成")
            manual_labels = {
                "account_recorded": ("账户与风控", "填写账户总资产、可用资金、持仓数量，并在“周度准备”勾选账户记录已完成。"),
                "no_pause_trigger": ("周度准备", "确认本周没有触发暂停交易条件，并勾选该项。"),
                "candidate_review_done": ("人工复核", "逐只检查候选股的 ST、停牌、涨停/一字板、重大公告和买入金额，并勾选候选股复核完成。"),
                "weekly_new_limit_confirmed": ("人工复核", "确认本周最多新增不超过 2-3 只，并勾选该项。"),
                "cash_reserve_confirmed": ("人工复核", "确认交易后现金比例不低于 25%，并勾选该项。"),
                "positions_recorded": ("人工复核", "记录当前持仓明细；如果没有持仓，点击“填入无持仓”。"),
            }
            missing_key = next((key for key, var in self.manual_checks.items() if not var.get()), "")
            page_name, message = manual_labels.get(missing_key, ("周度准备", "完成剩余手动确认项。"))
            page_map = {"周度准备": "weekly", "账户与风控": "risk", "人工复核": "review"}
            self.next_step_var.set("下一步：进入“{}”，{}".format(page_name, message))
            self.next_page_id = page_map.get(page_name, "weekly")
        else:
            self.overall_status_var.set("触发风控提示")
            self.next_step_var.set("下一步：不要新增买入。先进入“账户与风控”查看回撤触发原因，必要时只允许减仓或暂停策略。")
            self.next_page_id = "risk"

    def save_state(self, show_message: bool = True) -> None:
        data = self._collect_state()
        STATE_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._append_weekly_log(data)
        self.last_save_var.set(data["last_saved_at"])
        account = data["account"]
        self._saved_account_snapshot = {
            "start_capital": account.get("start_capital", ""),
            "high_watermark": account.get("high_watermark", ""),
            "current_assets": account.get("current_assets", ""),
            "available_cash": account.get("available_cash", ""),
            "holding_count": account.get("holding_count", ""),
        }
        if show_message:
            messagebox.showinfo("已保存", "已保存本周执行清单状态。")

    def on_close(self) -> None:
        if self.close_auto_save_var.get():
            try:
                self.save_state(show_message=False)
            finally:
                self.root.destroy()
            return
        choice = messagebox.askyesnocancel("关闭前保存", "是否保存当前填写内容？")
        if choice is None:
            return
        if choice:
            self.save_state(show_message=False)
        self.root.destroy()

    def save_state_shortcut(self, _event=None) -> str:
        self.save_state()
        return "break"

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
            "settings": {
                "close_auto_save": self.close_auto_save_var.get(),
            },
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
                "holdings": self.holding_note_text.get("1.0", tk.END).strip() if self.holding_note_text is not None else "",
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

    def copy_command(self, command: str) -> None:
        text = str(command).strip()
        if not text:
            messagebox.showinfo("命令为空", "当前没有可复制的命令。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        messagebox.showinfo("已复制", "命令已复制到剪贴板。")


def main() -> int:
    root = tk.Tk()
    LiveExecutionChecklistGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
