#!/usr/bin/env python3
# coding: utf-8

"""
510300 盘中低位承接监控 GUI 启动器。

这个文件只负责把常用命令包装成按钮，不实现策略逻辑。
实际策略仍由 intraday_low_absorb_monitor.py 执行。
"""

import calendar
import csv
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


ROOT_DIR = Path(__file__).resolve().parents[2]
MONITOR_SCRIPT = ROOT_DIR / "code" / "run_qmt" / "intraday_low_absorb_monitor.py"
OUTPUT_DIR = ROOT_DIR / "code" / "run_qmt" / "outputs" / "intraday_low_absorb_monitor"
REPORT_DIR = ROOT_DIR / "报告" / "研究结论" / "数据摘要"
EVENT_CSV = REPORT_DIR / "510300盘中模拟监控历史回放事件日志.csv"
TRADE_CSV = REPORT_DIR / "510300盘中模拟监控历史回放模拟持仓.csv"
LIVE_EVENT_CSV = OUTPUT_DIR / "live_event_log.csv"
LIVE_STATE_JSON = OUTPUT_DIR / "live_state.json"


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
        self.root.geometry("1120x760")
        self.process = None
        self.output_queue = queue.Queue()

        self.date_var = tk.StringVar(value=today_yyyymmdd())
        self.start_var = tk.StringVar(value=subtract_months(today_yyyymmdd(), 3))
        self.end_var = tk.StringVar(value=today_yyyymmdd())
        self.poll_var = tk.StringVar(value="10")
        self.skip_download_var = tk.BooleanVar(value=True)
        self.print_events_var = tk.BooleanVar(value=True)
        self.print_trades_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="空闲")

        self._build_ui()
        self.root.after(150, self._drain_output_queue)

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
        ttk.Label(row2, textvariable=self.status_var).pack(side=tk.RIGHT)

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
        ttk.Button(row4, text="打开 replay 事件日志", command=lambda: self.open_path(EVENT_CSV)).pack(side=tk.LEFT)
        ttk.Button(row4, text="打开 replay 买卖明细", command=lambda: self.open_path(TRADE_CSV)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row4, text="打开 live 事件日志", command=lambda: self.open_path(LIVE_EVENT_CSV)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row4, text="打开 live 状态文件", command=lambda: self.open_path(LIVE_STATE_JSON)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row4, text="打开输出目录", command=lambda: self.open_path(OUTPUT_DIR)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row4, text="清空窗口输出", command=self.clear_output).pack(side=tk.RIGHT)

        explain = ttk.LabelFrame(outer, text="怎么理解这些按钮")
        explain.pack(fill=tk.X, pady=(10, 0))
        text = (
            "查一次：程序读取一次当前数据，输出结果后退出。适合临时问“现在有没有”。\n"
            "持续盯盘：程序一直运行，每隔几秒检查一次，直到你点“停止 live”或关闭窗口。适合盘中等信号。\n"
            "历史日期或收盘后查今天：用 replay。盘中正在运行：用 live。"
        )
        ttk.Label(explain, text=text, justify=tk.LEFT).pack(anchor=tk.W, padx=8, pady=8)

        output_frame = ttk.LabelFrame(outer, text="程序输出")
        output_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.output = tk.Text(output_frame, wrap=tk.NONE, font=("Consolas", 10))
        self.output.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll = ttk.Scrollbar(output_frame, orient=tk.VERTICAL, command=self.output.yview)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.output.configure(yscrollcommand=yscroll.set)

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
        self.run_command(args, "replay 单日 {}".format(date), replace_existing=True)

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
        self.run_command(args, "replay 过去3个月 {}-{}".format(start, end), replace_existing=True)

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
        self.run_command(args, "replay 指定区间 {}-{}".format(start, end), replace_existing=True)

    def live_once(self):
        args = ["--mode", "live", "--max-loops", "1"]
        self.run_command(args, "live 查一次", replace_existing=True)

    def live_start(self):
        poll = self.poll_var.get().strip() or "10"
        if not poll.isdigit() or int(poll) <= 0:
            messagebox.showerror("参数错误", "live 检查间隔秒必须是正整数，例如 10")
            return
        args = ["--mode", "live", "--poll-seconds", poll]
        self.run_command(args, "live 持续盯盘", replace_existing=False)

    def _add_replay_options(self, args):
        if self.skip_download_var.get():
            args.append("--skip-download")
        if self.print_events_var.get():
            args.append("--print-events")
        if self.print_trades_var.get():
            args.append("--print-trades")
        args.extend(["--print-limit", "120"])

    def run_command(self, args, label, replace_existing):
        if self.process is not None and self.process.poll() is None:
            if not replace_existing:
                messagebox.showwarning("已有程序运行中", "当前已有 live 或 replay 正在运行，请先停止。")
                return
            self.stop_process()

        cmd = [sys.executable, "-u", str(MONITOR_SCRIPT)] + args
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        self._append_output("\n===== {} =====\n{}\n\n".format(label, " ".join(cmd)))
        self.status_var.set("运行中：{}".format(label))

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
        threading.Thread(target=self._waiter_thread, args=(label,), daemon=True).start()

    def stop_process(self):
        if self.process is None or self.process.poll() is not None:
            self.status_var.set("空闲")
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

    def _waiter_thread(self, label):
        if self.process is None:
            return
        code = self.process.wait()
        self.output_queue.put("\n[GUI] {} 已结束，退出码={}\n".format(label, code))
        self.output_queue.put(("__STATUS__", "空闲"))

    def _drain_output_queue(self):
        try:
            while True:
                item = self.output_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "__STATUS__":
                    self.status_var.set(item[1])
                else:
                    self._append_output(item)
        except queue.Empty:
            pass
        self.root.after(150, self._drain_output_queue)

    def _append_output(self, text):
        self.output.insert(tk.END, text)
        self.output.see(tk.END)

    def clear_output(self):
        self.output.delete("1.0", tk.END)

    def open_path(self, path):
        path = Path(path)
        if not path.exists():
            messagebox.showinfo("文件不存在", "还没有生成：\n{}".format(path))
            return
        os.startfile(str(path))

    def show_replay_events(self):
        if not EVENT_CSV.exists():
            messagebox.showinfo("文件不存在", "还没有生成 replay 事件日志。\n请先运行一次 replay。")
            return
        rows = self._read_csv(EVENT_CSV)
        self._show_event_table(rows)

    def _read_csv(self, path):
        with Path(path).open("r", encoding="utf-8-sig", newline="") as file_obj:
            return list(csv.DictReader(file_obj))

    def _show_event_table(self, rows):
        window = tk.Toplevel(self.root)
        window.title("replay 事件日志表")
        window.geometry("1180x640")

        outer = ttk.Frame(window, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(
            outer,
            text="replay 事件日志",
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        title.pack(anchor=tk.W)

        count_text = self._event_count_text(rows)
        ttk.Label(outer, text=count_text).pack(anchor=tk.W, pady=(4, 8))

        columns = [
            ("event_time", "时间", 140),
            ("event_label", "事件", 100),
            ("price", "价格", 80),
            ("entry_offset_bars", "第几根5m", 80),
            ("position_id", "持仓号", 70),
            ("status", "状态", 100),
            ("message", "说明", 620),
        ]

        table_frame = ttk.Frame(outer)
        table_frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(
            table_frame,
            columns=[col[0] for col in columns],
            show="headings",
            height=22,
        )
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

        if rows:
            for row in rows:
                values = []
                for key, _, _ in columns:
                    if key == "event_time":
                        values.append(self._format_event_time(row.get(key, "")))
                    else:
                        values.append(self._cell(row.get(key, "")))
                tree.insert("", tk.END, values=values)
        else:
            tree.insert("", tk.END, values=["", "无事件", "", "", "", "", "当前 replay 没有触发任何事件。"])

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(buttons, text="刷新", command=lambda: self._reload_event_table(window)).pack(side=tk.LEFT)
        ttk.Button(buttons, text="打开 CSV", command=lambda: self.open_path(EVENT_CSV)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(buttons, text="关闭", command=window.destroy).pack(side=tk.RIGHT)

    def _reload_event_table(self, window):
        window.destroy()
        self.show_replay_events()

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

    def _valid_date(self, text):
        if len(text) != 8 or not text.isdigit():
            return False
        try:
            datetime.strptime(text, "%Y%m%d")
            return True
        except ValueError:
            return False


def main():
    root = tk.Tk()
    app = MonitorGui(root)

    def on_close():
        if app.process is not None and app.process.poll() is None:
            if not messagebox.askyesno("确认退出", "当前程序仍在运行，是否停止并退出？"):
                return
            app.stop_process()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
