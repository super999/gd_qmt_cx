#!/usr/bin/env python3
# coding: utf-8

"""
510300 盘中低位承接监控 GUI 启动器。

GUI 只包装 intraday_low_absorb_monitor.py，不复制策略规则。
"""

import calendar
import csv
import json
import os
import queue
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import pandas as pd
from xtquant import xtdata

import matplotlib

matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import mplfinance as mpf


ROOT_DIR = Path(__file__).resolve().parents[2]
MONITOR_SCRIPT = ROOT_DIR / "code" / "run_qmt" / "intraday_low_absorb_monitor.py"
OUTPUT_DIR = ROOT_DIR / "code" / "run_qmt" / "outputs" / "intraday_low_absorb_monitor"
REPORT_DIR = ROOT_DIR / "报告" / "研究结论" / "数据摘要"
EVENT_CSV = REPORT_DIR / "510300盘中模拟监控历史回放事件日志.csv"
TRADE_CSV = REPORT_DIR / "510300盘中模拟监控历史回放模拟持仓.csv"
SUMMARY_CSV = REPORT_DIR / "510300盘中模拟监控历史回放摘要.csv"
LIVE_EVENT_CSV = OUTPUT_DIR / "live_event_log.csv"
LIVE_STATE_JSON = OUTPUT_DIR / "live_state.json"
GUI_SETTINGS_JSON = OUTPUT_DIR / "gui_settings.json"
STOCK = "510300.SH"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def today_yyyymmdd():
    return datetime.now().strftime("%Y%m%d")


def subtract_months(date_text, months):
    year = int(date_text[:4])
    month = int(date_text[4:6])
    day = int(date_text[6:8])
    month -= months
    while month <= 0:
        month += 12
        year -= 1
    last_day = calendar.monthrange(year, month)[1]
    return "{:04d}{:02d}{:02d}".format(year, month, min(day, last_day))


class MonitorGui:
    def __init__(self, root):
        self.root = root
        self.root.title("510300 盘中低位承接监控")
        self.root.geometry("1280x820")
        self.process = None
        self.output_queue = queue.Queue()
        self.current_run_kind = ""
        self.settings = self._load_settings()

        self.date_var = tk.StringVar(value=self.settings.get("date", today_yyyymmdd()))
        self.start_var = tk.StringVar(value=self.settings.get("start", subtract_months(today_yyyymmdd(), 3)))
        self.end_var = tk.StringVar(value=self.settings.get("end", today_yyyymmdd()))
        self.poll_var = tk.StringVar(value=self.settings.get("poll_seconds", "10"))
        self.skip_download_var = tk.BooleanVar(value=bool(self.settings.get("skip_download", True)))
        self.print_events_var = tk.BooleanVar(value=bool(self.settings.get("print_events", True)))
        self.print_trades_var = tk.BooleanVar(value=bool(self.settings.get("print_trades", True)))

        self.status_var = tk.StringVar(value="未启动")
        self.latest_bar_var = tk.StringVar(value="-")
        self.latest_price_var = tk.StringVar(value="-")
        self.live_signal_var = tk.StringVar(value="-")
        self.summary_signal_var = tk.StringVar(value="-")
        self.summary_event_var = tk.StringVar(value="-")
        self.summary_trade_var = tk.StringVar(value="-")
        self.summary_win_var = tk.StringVar(value="-")
        self.summary_compound_var = tk.StringVar(value="-")
        self.summary_last_signal_var = tk.StringVar(value="-")

        self._build_ui()
        self._refresh_summary()
        self._refresh_live_state()
        self.root.after(150, self._drain_output_queue)
        self.root.after(2000, self._periodic_live_state_refresh)

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Label(
            outer,
            text="510300 盘中低位承接监控",
            font=("Microsoft YaHei UI", 14, "bold"),
        )
        header.pack(anchor=tk.W)

        route = ttk.Label(
            outer,
            text="路线：1 日线优质区间扫描 / 2 统计共同信号 / 3 候选买点规则回测 / 4 盘中买入口径 / 5 卖点设计；当前为监控执行工具。",
        )
        route.pack(anchor=tk.W, pady=(4, 10))

        controls = ttk.LabelFrame(outer, text="参数")
        controls.pack(fill=tk.X)
        row1 = ttk.Frame(controls)
        row1.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(row1, text="单日日期 YYYYMMDD").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.date_var, width=12).pack(side=tk.LEFT, padx=(6, 20))
        ttk.Label(row1, text="区间开始").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.start_var, width=12).pack(side=tk.LEFT, padx=(6, 20))
        ttk.Label(row1, text="区间结束").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.end_var, width=12).pack(side=tk.LEFT, padx=(6, 20))
        ttk.Label(row1, text="live 检查间隔秒").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.poll_var, width=8).pack(side=tk.LEFT, padx=(6, 0))

        row2 = ttk.Frame(controls)
        row2.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Checkbutton(row2, text="replay 跳过下载", variable=self.skip_download_var).pack(side=tk.LEFT)
        ttk.Checkbutton(row2, text="打印事件日志", variable=self.print_events_var).pack(side=tk.LEFT, padx=(18, 0))
        ttk.Checkbutton(row2, text="打印买卖明细", variable=self.print_trades_var).pack(side=tk.LEFT, padx=(18, 0))
        ttk.Label(row2, text="状态：").pack(side=tk.RIGHT)
        ttk.Label(row2, textvariable=self.status_var).pack(side=tk.RIGHT)

        summary = ttk.LabelFrame(outer, text="最近一次 replay 摘要")
        summary.pack(fill=tk.X, pady=(10, 0))
        self._summary_item(summary, "预警次数", self.summary_signal_var, 0)
        self._summary_item(summary, "事件数", self.summary_event_var, 1)
        self._summary_item(summary, "模拟交易数", self.summary_trade_var, 2)
        self._summary_item(summary, "胜率", self.summary_win_var, 3)
        self._summary_item(summary, "复合收益", self.summary_compound_var, 4)
        self._summary_item(summary, "最近触发", self.summary_last_signal_var, 5)

        live_status = ttk.LabelFrame(outer, text="live 状态")
        live_status.pack(fill=tk.X, pady=(10, 0))
        self._summary_item(live_status, "最新5m时间", self.latest_bar_var, 0)
        self._summary_item(live_status, "最新价", self.latest_price_var, 1)
        self._summary_item(live_status, "触发状态", self.live_signal_var, 2)

        actions = ttk.LabelFrame(outer, text="常用操作")
        actions.pack(fill=tk.X, pady=(10, 0))
        row3 = ttk.Frame(actions)
        row3.pack(fill=tk.X, padx=8, pady=8)
        ttk.Button(row3, text="收盘后查今天", command=self.replay_today).pack(side=tk.LEFT)
        ttk.Button(row3, text="查指定日期", command=self.replay_single_date).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row3, text="查指定区间", command=self.replay_custom_range).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row3, text="查过去3个月", command=self.replay_last_3_months).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row3, text="live 查一次", command=self.live_once).pack(side=tk.LEFT, padx=(24, 0))
        ttk.Button(row3, text="启动 live 持续盯盘", command=self.live_start).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row3, text="停止 live", command=self.stop_process).pack(side=tk.LEFT, padx=(8, 0))

        row4 = ttk.Frame(actions)
        row4.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(row4, text="查看 replay 事件表", command=self.show_replay_events).pack(side=tk.LEFT)
        ttk.Button(row4, text="查看 live 事件表", command=self.show_live_events).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row4, text="打开 replay 买卖明细", command=lambda: self.open_path(TRADE_CSV)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row4, text="打开输出目录", command=lambda: self.open_path(OUTPUT_DIR)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row4, text="清空窗口输出", command=self.clear_output).pack(side=tk.RIGHT)

        explain = ttk.LabelFrame(outer, text="怎么理解这些按钮")
        explain.pack(fill=tk.X, pady=(10, 0))
        text = (
            "查一次：程序读取一次当前数据，输出结果后退出。持续盯盘：程序一直运行，每隔几秒检查一次。\n"
            "历史日期或收盘后查今天：用 replay。盘中正在运行：用 live。事件表里双击某一行可查看当天 K 线。"
        )
        ttk.Label(explain, text=text, justify=tk.LEFT).pack(anchor=tk.W, padx=8, pady=8)

        output_frame = ttk.LabelFrame(outer, text="程序输出")
        output_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.output = tk.Text(output_frame, wrap=tk.NONE, font=("Consolas", 10))
        self.output.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll = ttk.Scrollbar(output_frame, orient=tk.VERTICAL, command=self.output.yview)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.output.configure(yscrollcommand=yscroll.set)

    def _summary_item(self, parent, label, variable, col):
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=col, padx=12, pady=8, sticky="w")
        ttk.Label(frame, text=label).pack(anchor=tk.W)
        ttk.Label(frame, textvariable=variable, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor=tk.W)

    def replay_today(self):
        self.date_var.set(today_yyyymmdd())
        self.replay_single_date()

    def replay_single_date(self):
        date = self.date_var.get().strip()
        if not self._valid_date(date):
            messagebox.showerror("日期错误", "单日日期必须是 YYYYMMDD，例如 20260427")
            return
        args = ["--mode", "replay", "--start-date", date, "--end-date", date]
        self._add_replay_options(args)
        self.run_command(args, "replay 单日 {}".format(date), replace_existing=True, kind="replay")

    def replay_last_3_months(self):
        end = self.end_var.get().strip() or today_yyyymmdd()
        if not self._valid_date(end):
            messagebox.showerror("日期错误", "区间结束必须是 YYYYMMDD，例如 20260427")
            return
        start = subtract_months(end, 3)
        self.start_var.set(start)
        self.end_var.set(end)
        args = ["--mode", "replay", "--start-date", start, "--end-date", end]
        self._add_replay_options(args)
        self.run_command(args, "replay 过去3个月 {}-{}".format(start, end), replace_existing=True, kind="replay")

    def replay_custom_range(self):
        start = self.start_var.get().strip()
        end = self.end_var.get().strip()
        if not self._valid_date(start):
            messagebox.showerror("日期错误", "区间开始必须是 YYYYMMDD，例如 20260127")
            return
        if not self._valid_date(end):
            messagebox.showerror("日期错误", "区间结束必须是 YYYYMMDD，例如 20260427")
            return
        if start > end:
            messagebox.showerror("日期错误", "区间开始不能晚于区间结束。")
            return
        args = ["--mode", "replay", "--start-date", start, "--end-date", end]
        self._add_replay_options(args)
        self.run_command(args, "replay 指定区间 {}-{}".format(start, end), replace_existing=True, kind="replay")

    def live_once(self):
        args = ["--mode", "live", "--max-loops", "1"]
        self.run_command(args, "live 查一次", replace_existing=True, kind="live")

    def live_start(self):
        poll = self.poll_var.get().strip() or "10"
        if not poll.isdigit() or int(poll) <= 0:
            messagebox.showerror("参数错误", "live 检查间隔秒必须是正整数，例如 10")
            return
        args = ["--mode", "live", "--poll-seconds", poll]
        self.run_command(args, "live 持续盯盘", replace_existing=False, kind="live")

    def _add_replay_options(self, args):
        if self.skip_download_var.get():
            args.append("--skip-download")
        if self.print_events_var.get():
            args.append("--print-events")
        if self.print_trades_var.get():
            args.append("--print-trades")
        args.extend(["--print-limit", "120"])

    def run_command(self, args, label, replace_existing, kind):
        self._save_settings()
        if self.process is not None and self.process.poll() is None:
            if not replace_existing:
                messagebox.showwarning("已有程序运行中", "当前已有 live 或 replay 正在运行，请先停止。")
                return
            self.stop_process()

        cmd = [sys.executable, "-u", str(MONITOR_SCRIPT)] + args
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        self.current_run_kind = kind
        self._append_output("\n===== {} =====\n{}\n\n".format(label, " ".join(cmd)))
        self.status_var.set("运行中：{}".format(label))
        if kind == "live":
            self.live_signal_var.set("等待行情")

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            cmd,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
        threading.Thread(target=self._reader_thread, daemon=True).start()
        threading.Thread(target=self._waiter_thread, args=(label, kind), daemon=True).start()

    def stop_process(self):
        if self.process is None or self.process.poll() is not None:
            self.status_var.set("已停止")
            return
        self._append_output("\n[GUI] 正在停止当前程序...\n")
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.status_var.set("已停止")

    def _reader_thread(self):
        if self.process is None or self.process.stdout is None:
            return
        for line in self.process.stdout:
            self.output_queue.put(line)

    def _waiter_thread(self, label, kind):
        if self.process is None:
            return
        code = self.process.wait()
        self.output_queue.put("\n[GUI] {} 已结束，退出码={}\n".format(label, code))
        self.output_queue.put(("__DONE__", kind, code))

    def _drain_output_queue(self):
        try:
            while True:
                item = self.output_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "__DONE__":
                    self._handle_done(item[1], item[2])
                else:
                    self._append_output(item)
                    self._parse_live_line(str(item))
        except queue.Empty:
            pass
        self.root.after(150, self._drain_output_queue)

    def _handle_done(self, kind, code):
        if kind == "replay":
            self._refresh_summary()
        if kind == "live" and code == 0:
            self.status_var.set("已停止")
        else:
            self.status_var.set("空闲" if code == 0 else "异常退出")

    def _append_output(self, text):
        self.output.insert(tk.END, text)
        self.output.see(tk.END)

    def _parse_live_line(self, line):
        if "live heartbeat" not in line:
            return
        bar_match = re.search(r"latest_bar=(\d+)", line)
        status_match = re.search(r"status=([^ ]+)", line)
        close_match = re.search(r"close=([0-9.]+)", line)
        if bar_match:
            self.latest_bar_var.set(self._format_event_time(bar_match.group(1)))
        if status_match:
            self.live_signal_var.set(status_match.group(1))
        if close_match:
            self.latest_price_var.set(close_match.group(1))

    def clear_output(self):
        self.output.delete("1.0", tk.END)

    def open_path(self, path):
        path = Path(path)
        if not path.exists():
            messagebox.showinfo("文件不存在", "还没有生成：\n{}".format(path))
            return
        os.startfile(str(path))

    def show_replay_events(self):
        self._show_event_window(EVENT_CSV, "replay 事件日志表")

    def show_live_events(self):
        self._show_event_window(LIVE_EVENT_CSV, "live 事件日志表")

    def _show_event_window(self, path, title):
        path = Path(path)
        if not path.exists():
            messagebox.showinfo("文件不存在", "还没有生成事件日志。\n请先运行对应功能。")
            return
        rows = self._read_csv(path)
        self._show_event_table(rows, title, path)

    def _read_csv(self, path):
        with Path(path).open("r", encoding="utf-8-sig", newline="") as file_obj:
            return list(csv.DictReader(file_obj))

    def _show_event_table(self, rows, title_text, csv_path):
        window = tk.Toplevel(self.root)
        window.title(title_text)
        window.geometry("1220x680")
        row_by_item = {}

        outer = ttk.Frame(window, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(outer, text=title_text, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor=tk.W)

        top = ttk.Frame(outer)
        top.pack(fill=tk.X, pady=(4, 8))
        count_var = tk.StringVar()
        ttk.Label(top, textvariable=count_var).pack(side=tk.LEFT)
        ttk.Label(top, text="筛选").pack(side=tk.RIGHT)
        filter_var = tk.StringVar(value="全部")
        filter_box = ttk.Combobox(
            top,
            textvariable=filter_var,
            values=["全部", "买入预警", "候选买入提示", "模拟买入", "退出提示", "候选买入跳过", "模拟买入跳过"],
            width=14,
            state="readonly",
        )
        filter_box.pack(side=tk.RIGHT, padx=(0, 8))

        columns = [
            ("event_time", "时间", 170),
            ("event_label", "事件", 110),
            ("price", "价格", 80),
            ("entry_offset_bars", "第几根5m", 80),
            ("position_id", "持仓号", 70),
            ("status", "状态", 100),
            ("message", "说明", 620),
        ]
        table_frame = ttk.Frame(outer)
        table_frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(table_frame, columns=[col[0] for col in columns], show="headings", height=22)
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=60, stretch=(key == "message"))

        yscroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        def visible_rows():
            label = filter_var.get()
            if label == "全部":
                return rows
            return [row for row in rows if row.get("event_label") == label]

        def reload_table():
            tree.delete(*tree.get_children())
            row_by_item.clear()
            view = visible_rows()
            if view:
                for row in view:
                    values = []
                    for key, _, _ in columns:
                        if key == "event_time":
                            values.append(self._format_event_time(row.get(key, "")))
                        else:
                            values.append(self._cell(row.get(key, "")))
                    item = tree.insert("", tk.END, values=values)
                    row_by_item[item] = row
            else:
                item = tree.insert("", tk.END, values=["", "无事件", "", "", "", "", "当前筛选条件下没有事件。"])
                row_by_item[item] = None
            count_var.set(self._event_count_text(view))

        def on_double_click(_event):
            selected = tree.selection()
            if not selected:
                return
            row = row_by_item.get(selected[0])
            if row:
                self.show_kline_window(row)

        filter_box.bind("<<ComboboxSelected>>", lambda _event: reload_table())
        tree.bind("<Double-1>", on_double_click)
        reload_table()

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(buttons, text="刷新", command=lambda: self._reload_event_table(window, csv_path, title_text)).pack(side=tk.LEFT)
        ttk.Button(buttons, text="打开 CSV", command=lambda: self.open_path(csv_path)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(buttons, text="关闭", command=window.destroy).pack(side=tk.RIGHT)

    def _reload_event_table(self, window, csv_path, title_text):
        window.destroy()
        self._show_event_window(csv_path, title_text)

    def show_kline_window(self, event_row):
        trade_date = str(event_row.get("trade_date") or str(event_row.get("event_time", ""))[:8])
        if not self._valid_date(trade_date):
            messagebox.showerror("日期错误", "事件日期无效，无法加载 K 线。")
            return
        window = tk.Toplevel(self.root)
        window.title("{} 当日 K 线".format(self._format_trade_date(trade_date)))
        window.geometry("1160x760")

        period_var = tk.StringVar(value="5m")
        top = ttk.Frame(window, padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text="日期：{}，双击事件：{}".format(self._format_trade_date(trade_date), event_row.get("event_label", ""))).pack(side=tk.LEFT)
        ttk.Button(top, text="显示 5m", command=lambda: draw("5m")).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(top, text="显示 1m", command=lambda: draw("1m")).pack(side=tk.RIGHT)

        chart_frame = ttk.Frame(window)
        chart_frame.pack(fill=tk.BOTH, expand=True)
        canvas_holder = {"canvas": None, "toolbar": None}

        def draw(period):
            period_var.set(period)
            for child in chart_frame.winfo_children():
                child.destroy()
            try:
                frame = self._load_intraday_frame(period, trade_date)
            except Exception as exc:
                ttk.Label(chart_frame, text="当前本地无 {} 数据：{}".format(period, exc)).pack(padx=20, pady=20)
                return
            if frame.empty:
                ttk.Label(chart_frame, text="当前本地无 {} 数据。".format(period)).pack(padx=20, pady=20)
                return
            fig = self._build_kline_figure(frame, trade_date, period)
            canvas = FigureCanvasTkAgg(fig, master=chart_frame)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            toolbar = NavigationToolbar2Tk(canvas, chart_frame)
            toolbar.update()
            canvas_holder["canvas"] = canvas
            canvas_holder["toolbar"] = toolbar

        draw("5m")

    def _load_intraday_frame(self, period, trade_date):
        data = xtdata.get_local_data(
            field_list=[],
            stock_list=[STOCK],
            period=period,
            start_time=trade_date,
            end_time=trade_date,
            count=-1,
            dividend_type="front",
            fill_data=True,
        ).get(STOCK)
        if data is None or data.empty:
            return pd.DataFrame()
        frame = data.copy()
        frame.index = frame.index.astype(str)
        frame["bar_time"] = frame.index.astype(str)
        frame = frame[frame["bar_time"].str[:8] == trade_date].copy()
        if frame.empty:
            return pd.DataFrame()
        for col in ["open", "high", "low", "close", "volume"]:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=["open", "high", "low", "close"]).sort_values("bar_time")
        frame.index = pd.to_datetime(frame["bar_time"], format="%Y%m%d%H%M%S")
        return frame.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})

    def _build_kline_figure(self, frame, trade_date, period):
        event_rows = [row for row in self._read_csv(EVENT_CSV) if str(row.get("trade_date", "")) == trade_date]
        addplots = []
        markers = {
            "买入预警": ("v", "orange"),
            "候选买入提示": ("^", "royalblue"),
            "模拟买入": ("^", "green"),
            "退出提示": ("x", "red"),
            "候选买入跳过": ("o", "gray"),
            "模拟买入跳过": ("o", "gray"),
        }
        for label, (marker, color) in markers.items():
            series = pd.Series(float("nan"), index=frame.index)
            for row in event_rows:
                if row.get("event_label") != label:
                    continue
                event_time = str(row.get("event_time", ""))
                if len(event_time) < 14:
                    continue
                timestamp = pd.to_datetime(event_time, format="%Y%m%d%H%M%S", errors="coerce")
                if pd.isna(timestamp):
                    continue
                if timestamp not in series.index:
                    nearest_pos = frame.index.searchsorted(timestamp)
                    if nearest_pos >= len(frame.index):
                        nearest_pos = len(frame.index) - 1
                    timestamp = frame.index[nearest_pos]
                price = pd.to_numeric(pd.Series([row.get("price")]), errors="coerce").iloc[0]
                if pd.isna(price):
                    price = frame.loc[timestamp, "Close"]
                series.loc[timestamp] = float(price)
            if series.notna().any():
                addplots.append(mpf.make_addplot(series, type="scatter", marker=marker, markersize=90, color=color))

        fig, _axes = mpf.plot(
            frame[["Open", "High", "Low", "Close", "Volume"]],
            type="candle",
            volume=True,
            style="charles",
            addplot=addplots if addplots else None,
            title="{} {} K线".format(self._format_trade_date(trade_date), period),
            returnfig=True,
            figsize=(11, 7),
            tight_layout=True,
        )
        return fig

    def _refresh_summary(self):
        signal_count = self._read_summary_json_value("signal_count")
        event_count = self._read_summary_json_value("event_count")
        trade_count = self._read_summary_json_value("simulated_trade_count")
        self.summary_signal_var.set(str(signal_count) if signal_count is not None else "-")
        self.summary_event_var.set(str(event_count) if event_count is not None else "-")
        self.summary_trade_var.set(str(trade_count) if trade_count is not None else "-")

        if SUMMARY_CSV.exists():
            rows = self._read_csv(SUMMARY_CSV)
            if rows:
                row = rows[0]
                self.summary_win_var.set(self._pct(row.get("win_rate")))
                self.summary_compound_var.set(self._pct(row.get("compounded_return")))
        last_signal = self._last_signal_date()
        self.summary_last_signal_var.set(last_signal or "-")

    def _periodic_live_state_refresh(self):
        self._refresh_live_state()
        self.root.after(2000, self._periodic_live_state_refresh)

    def _refresh_live_state(self):
        if not LIVE_STATE_JSON.exists():
            return
        try:
            state = json.loads(LIVE_STATE_JSON.read_text(encoding="utf-8"))
        except Exception:
            return
        latest_bar = state.get("latest_bar_time")
        latest_price = state.get("latest_price")
        latest_status = state.get("latest_status")
        if latest_bar:
            self.latest_bar_var.set(self._format_event_time(str(latest_bar)))
        if latest_price is not None:
            self.latest_price_var.set(str(latest_price))
        if latest_status:
            self.live_signal_var.set(str(latest_status))

    def _read_summary_json_value(self, key):
        path = OUTPUT_DIR / "summary.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8")).get(key)
        except Exception:
            return None

    def _last_signal_date(self):
        if not EVENT_CSV.exists():
            return ""
        rows = [row for row in self._read_csv(EVENT_CSV) if row.get("event_label") == "买入预警"]
        if not rows:
            return ""
        return self._format_event_time(rows[-1].get("event_time", ""))

    def _event_count_text(self, rows):
        if not rows:
            return "事件数量：0"
        counts = {}
        for row in rows:
            label = row.get("event_label", "") or row.get("event_type", "")
            counts[label] = counts.get(label, 0) + 1
        parts = ["{} {}".format(label, count) for label, count in sorted(counts.items())]
        return "事件数量：{}；{}".format(len(rows), "，".join(parts))

    def _cell(self, value):
        if value is None:
            return ""
        text = str(value)
        if text.lower() == "nan":
            return ""
        return text

    def _pct(self, value):
        try:
            return "{:.2%}".format(float(value))
        except Exception:
            return "-"

    def _format_event_time(self, value):
        text = self._cell(value)
        if len(text) < 12 or not text[:12].isdigit():
            return text
        year = text[:4]
        month = text[4:6]
        day = text[6:8]
        hour = text[8:10]
        minute = text[10:12]
        return "{}年{}月{}日 {}:{}".format(year, month, day, hour, minute)

    def _format_trade_date(self, value):
        text = self._cell(value)
        if len(text) != 8 or not text.isdigit():
            return text
        return "{}年{}月{}日".format(text[:4], text[4:6], text[6:8])

    def _valid_date(self, text):
        if len(text) != 8 or not text.isdigit():
            return False
        try:
            datetime.strptime(text, "%Y%m%d")
            return True
        except ValueError:
            return False

    def _load_settings(self):
        if not GUI_SETTINGS_JSON.exists():
            return {}
        try:
            return json.loads(GUI_SETTINGS_JSON.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_settings(self):
        GUI_SETTINGS_JSON.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "date": self.date_var.get().strip(),
            "start": self.start_var.get().strip(),
            "end": self.end_var.get().strip(),
            "poll_seconds": self.poll_var.get().strip(),
            "skip_download": bool(self.skip_download_var.get()),
            "print_events": bool(self.print_events_var.get()),
            "print_trades": bool(self.print_trades_var.get()),
        }
        GUI_SETTINGS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    root = tk.Tk()
    app = MonitorGui(root)

    def on_close():
        app._save_settings()
        if app.process is not None and app.process.poll() is None:
            if not messagebox.askyesno("确认退出", "当前程序仍在运行，是否停止并退出？"):
                return
            app.stop_process()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
