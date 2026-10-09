"""
Hermes Service Runner - Orchestrate service startup with retry-on-failure.
"""
import json
import locale
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# 打包为 exe（PyInstaller 冻结）后 __file__ 指向临时解包目录；
# 配置必须固定读写「exe 同目录」，源码运行时即脚本同目录。
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_BASE_DIR, "hermes_commands.json")

# Detect system encoding for subprocess output
SYS_ENCODING = locale.getpreferredencoding(False) or "utf-8"

COLUMNS = ("#", "启用", "类型", "命令", "运行环境", "成功信号", "重试间隔(s)", "最大重试", "状态")
COL_WIDTHS = (30, 40, 60, 280, 80, 120, 70, 55, 100)


class HermesRunner:
    def __init__(self, root):
        self.root = root
        self.root.title("Hermes Service Runner")
        self.root.geometry("1080x700")
        self.root.minsize(950, 550)

        self.next_id = 1
        self.row_items: dict[str, dict] = {}  # iid -> CommandItem dict
        self.running = False
        self.stop_requested = False
        self.current_process: subprocess.Popen | None = None
        self.running_processes: list[subprocess.Popen] = []

        self._build_ui()
        self._load_config()

    # ── UI Construction ──────────────────────────────────────────────

    def _build_ui(self):
        # ---- Toolbar ----
        toolbar = ttk.Frame(self.root, padding=5)
        toolbar.pack(fill=tk.X)

        ttk.Button(toolbar, text="＋ 添加", command=self._add_row).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="ー 删除", command=self._remove_row).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=2)
        ttk.Button(toolbar, text="▲ 上移", command=self._move_up).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="▼ 下移", command=self._move_down).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=2)
        ttk.Button(toolbar, text="📂 加载配置", command=self._load_config).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="💾 保存配置", command=self._save_config).pack(side=tk.LEFT, padx=2)

        # ---- Tip bar ----
        tip_bar = ttk.Frame(self.root, padding=(8, 4))
        tip_bar.pack(fill=tk.X)
        ttk.Label(tip_bar,
                  text="💡 命令栏直接填写在 WSL(bash) 或 CMD 中输入的原始命令。勾选 = 启用；取消勾选 = 选中运行。",
                  foreground="#555").pack(side=tk.LEFT)

        # ---- Treeview table ----
        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5, 0))

        self.tree = ttk.Treeview(tree_frame, columns=COLUMNS, show="headings",
                                 selectmode="extended", height=10)
        for col, width in zip(COLUMNS, COL_WIDTHS):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, anchor=tk.CENTER if col != "命令" else tk.W,
                             minwidth=width)

        # scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        # Bind double-click to edit
        self.tree.bind("<Double-1>", self._on_double_click)

        # ---- Action bar ----
        action_bar = ttk.Frame(self.root, padding=5)
        action_bar.pack(fill=tk.X)

        self.run_all_btn = ttk.Button(action_bar, text="▶ 启动全部 (按顺序)", command=self._run_all)
        self.run_all_btn.pack(side=tk.LEFT, padx=2)

        self.run_sel_btn = ttk.Button(action_bar, text="▶ 启动选中 (未勾选的)", command=self._run_selected)
        self.run_sel_btn.pack(side=tk.LEFT, padx=2)

        self.stop_btn = ttk.Button(action_bar, text="■ 停止", command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=2)

        self.progress = ttk.Progressbar(action_bar, mode="indeterminate", length=100)
        self.progress.pack(side=tk.LEFT, padx=(10, 0))

        ttk.Button(action_bar, text="清空日志", command=self._clear_log).pack(side=tk.RIGHT, padx=2)

        # ---- Output log ----
        log_frame = ttk.LabelFrame(self.root, text=" 输出日志 ", padding=3)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        self.log = scrolledtext.ScrolledText(log_frame, height=8, state=tk.DISABLED,
                                             font=("Consolas", 9))
        self.log.pack(fill=tk.BOTH, expand=True)

    # ── Row operations ───────────────────────────────────────────────

    def _default_item(self):
        return {
            "command": "",
            "env": "WSL",
            "enabled": True,
            "cmd_type": "service",
            "ready_signal": "",
            "retry_delay": 3,
            "max_retries": 0,
        }

    def _item_to_values(self, item: dict) -> list:
        return [
            "",  # placeholder for index, filled later
            "✓" if item["enabled"] else "✗",
            item["cmd_type"],
            item["command"],
            item["env"],
            item["ready_signal"],
            str(item["retry_delay"]),
            str(item["max_retries"]) if item["max_retries"] > 0 else "无限",
            "● 等待",
        ]

    def _add_row(self):
        item = self._default_item()
        iid = str(self.next_id)
        self.next_id += 1
        self.row_items[iid] = item
        self.tree.insert("", tk.END, iid=iid, values=self._item_to_values(item))
        self._renumber()

    def _remove_row(self):
        sel = self.tree.selection()
        for iid in sel:
            self.tree.delete(iid)
            del self.row_items[iid]
        self._renumber()

    def _move_up(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        prev_iid = self.tree.prev(iid)
        if prev_iid:
            self.tree.move(iid, "", self.tree.index(prev_iid))
            self._renumber()

    def _move_down(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        next_iid = self.tree.next(iid)
        if next_iid:
            self.tree.move(iid, "", self.tree.index(next_iid) + 1)
            self._renumber()

    def _renumber(self):
        for i, iid in enumerate(self.tree.get_children(), 1):
            values = list(self.tree.item(iid, "values"))
            values[0] = str(i)
            self.tree.item(iid, values=values)

    def _on_double_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        column = self.tree.identify_column(event.x)
        col_idx = int(column[1:]) - 1
        iid = self.tree.identify_row(event.y)
        if not iid or iid not in self.row_items:
            return

        col_name = COLUMNS[col_idx]
        if col_name in ("#", "状态"):
            return  # read-only

        item = self.row_items[iid]
        self._edit_cell(iid, col_idx, col_name, item)

    def _edit_cell(self, iid, col_idx, col_name, item):
        bbox = self.tree.bbox(iid, column=COLUMNS[col_idx])
        if not bbox:
            return
        x, y, w, h = bbox

        if col_name == "启用":
            # toggle
            item["enabled"] = not item["enabled"]
            self._update_row(iid)
            return

        if col_name in ("类型", "运行环境"):
            field_name = "cmd_type" if col_name == "类型" else "env"
            values_list = {"类型": ["oneshot", "service"], "运行环境": ["WSL", "CMD"]}
            var = tk.StringVar(value=str(item[field_name]))
            cb = ttk.Combobox(self.tree, textvariable=var, values=values_list[col_name],
                              state="readonly")
            cb.place(x=x, y=y, width=w, height=h)
            cb.bind("<<ComboboxSelected>>", lambda _: self._commit_combo(iid, col_name, var, cb))
            cb.bind("<FocusOut>", lambda _: cb.destroy())
            cb.focus_set()
            return

        # Text entry
        field_map = {
            "命令": "command", "成功信号": "ready_signal",
            "重试间隔(s)": "retry_delay", "最大重试": "max_retries",
        }
        field = field_map.get(col_name)
        if not field:
            return

        current = item[field]
        if isinstance(current, int):
            current = str(current)
        var = tk.StringVar(value=str(current))
        entry = ttk.Entry(self.tree, textvariable=var)
        entry.place(x=x, y=y, width=w, height=h)

        def commit(_=None):
            val = var.get().strip()
            if field in ("retry_delay", "max_retries"):
                try:
                    item[field] = max(0, int(val))
                except ValueError:
                    item[field] = 0 if field == "max_retries" else 3
            else:
                item[field] = val
            self._update_row(iid)
            entry.destroy()

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.focus_set()

    def _commit_combo(self, iid, col_name, var, widget):
        item = self.row_items[iid]
        val = var.get()
        if col_name == "类型":
            item["cmd_type"] = val
        elif col_name == "运行环境":
            item["env"] = val
        self._update_row(iid)
        widget.destroy()

    def _update_row(self, iid):
        item = self.row_items[iid]
        values = list(self.tree.item(iid, "values"))
        values[1] = "✓" if item["enabled"] else "✗"
        values[2] = item["cmd_type"]
        values[3] = item["command"]
        values[4] = item["env"]
        values[5] = item["ready_signal"]
        values[6] = str(item["retry_delay"])
        values[7] = str(item["max_retries"]) if item["max_retries"] > 0 else "无限"
        self.tree.item(iid, values=values)

    def _set_status(self, iid, text, color_tag=None):
        values = list(self.tree.item(iid, "values"))
        values[8] = text
        self.tree.item(iid, values=values)
        if color_tag:
            self.tree.tag_configure(color_tag, foreground=color_tag)
            self.tree.item(iid, tags=(color_tag,))
        else:
            self.tree.item(iid, tags=())

    # ── Persistence ──────────────────────────────────────────────────

    def _save_config(self):
        data = []
        for iid in self.tree.get_children():
            item = self.row_items[iid]
            data.append({
                "command": item["command"],
                "env": item["env"],
                "enabled": item["enabled"],
                "cmd_type": item["cmd_type"],
                "ready_signal": item["ready_signal"],
                "retry_delay": item["retry_delay"],
                "max_retries": item["max_retries"],
            })
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log("[系统] 配置已保存")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def _load_config(self, *_):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.tree.delete(*self.tree.get_children())
            self.row_items.clear()
            for d in data:
                item = {
                    "command": d.get("command", ""),
                    "env": d.get("env", "WSL"),
                    "enabled": d.get("enabled", True),
                    "cmd_type": d.get("cmd_type", "service"),
                    "ready_signal": d.get("ready_signal", ""),
                    "retry_delay": d.get("retry_delay", 3),
                    "max_retries": d.get("max_retries", 0),
                }
                iid = str(self.next_id)
                self.next_id += 1
                self.row_items[iid] = item
                self.tree.insert("", tk.END, iid=iid, values=self._item_to_values(item))
            self._renumber()
            self._log("[系统] 配置已加载")
        except Exception as e:
            messagebox.showerror("加载失败", str(e))

    # ── Logging ──────────────────────────────────────────────────────

    def _log(self, text):
        self.log.config(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def _clear_log(self):
        self.log.config(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.config(state=tk.DISABLED)

    # ── Execution ────────────────────────────────────────────────────

    def _build_cmdline(self, item: dict):
        if item["env"] == "WSL":
            # -i = interactive (loads .bashrc), -l = login (loads .profile)
            # This ensures WSL starts in ~ with full env like ~$ prompt
            return ["wsl", "bash", "-il", "-c", item["command"]]
        else:
            return ["cmd", "/c", "chcp 65001 > nul && " + item["command"]]

    def _get_ordered_items(self):
        """Return list of (iid, item) in tree display order."""
        result = []
        for iid in self.tree.get_children():
            if iid in self.row_items:
                result.append((iid, self.row_items[iid]))
        return result

    def _run_all(self):
        if self.running:
            return
        enabled = [(iid, it) for iid, it in self._get_ordered_items()
                   if it["enabled"] and it["command"].strip()]
        if not enabled:
            messagebox.showinfo("提示", "没有可执行的命令")
            return
        self._start_execution(enabled)

    def _run_selected(self):
        if self.running:
            return
        disabled = [(iid, it) for iid, it in self._get_ordered_items()
                    if not it["enabled"] and it["command"].strip()]
        if not disabled:
            messagebox.showinfo("提示", "请先取消勾选要运行的命令")
            return
        self._start_execution(disabled)

    def _start_execution(self, items):
        self.running = True
        self.stop_requested = False
        self._set_buttons_state(tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress.start(10)
        threading.Thread(target=self._execute_sequence, args=(items,), daemon=True).start()

    def _stop(self):
        self.stop_requested = True
        if self.current_process:
            try:
                self.current_process.terminate()
            except Exception:
                pass
        for p in self.running_processes:
            try:
                p.terminate()
            except Exception:
                pass
        self.running_processes.clear()
        self._log("\n[系统] ⚠ 用户停止了执行\n")

    def _set_buttons_state(self, state):
        self.run_all_btn.config(state=state)
        self.run_sel_btn.config(state=state)

    def _execute_sequence(self, items):
        for iid, item in items:
            if self.stop_requested:
                break

            env_label = item["env"]

            if item["cmd_type"] == "oneshot":
                ok = self._run_oneshot(iid, item, env_label)
            else:
                ok = self._run_service(iid, item, env_label)

            if self.stop_requested:
                break

            if not ok:
                self._log(f"[系统] 该命令最终未能成功，后续命令已跳过\n")

        self.current_process = None
        self.running = False
        self.root.after(0, self._on_execution_done)

    def _run_oneshot(self, iid, item, env_label):
        attempt = 0
        while not self.stop_requested:
            attempt += 1
            self.root.after(0, lambda id=iid, a=attempt: self._set_status(id, f"● 第{a}次运行"))
            self._log(f"\n━━━ [{env_label}] {item['command']}  (第 {attempt} 次) ━━━")

            try:
                self.current_process = subprocess.Popen(
                    self._build_cmdline(item),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )

                for raw_line in self.current_process.stdout:
                    if self.stop_requested:
                        self.current_process.terminate()
                        break
                    self._log(self._decode_line(raw_line))

                ret = self.current_process.wait()
                self.current_process = None

                if self.stop_requested:
                    return False

                if ret == 0:
                    self.root.after(0, lambda id=iid: self._set_status(id, "● 完成", "green"))
                    self._log(f"[系统] ✓ 命令执行成功")
                    return True
                else:
                    self.root.after(0, lambda id=iid, r=ret: self._set_status(id, f"● 失败({r})"))
                    self._log(f"[系统] 退出码 {ret}，准备重试...")

            except Exception as e:
                self.current_process = None
                self._log(f"[系统] 执行异常: {e}")
                self.root.after(0, lambda id=iid: self._set_status(id, "● 异常"))

            if self._should_stop_retry(iid, item, attempt):
                return False
            self._wait_retry(item)

        return False

    def _run_service(self, iid, item, env_label):
        attempt = 0
        while not self.stop_requested:
            attempt += 1
            self.root.after(0, lambda id=iid, a=attempt: self._set_status(id, f"● 第{a}次启动"))
            self._log(f"\n━━━ [{env_label}] {item['command']}  (第 {attempt} 次) ━━━")

            try:
                self.current_process = subprocess.Popen(
                    self._build_cmdline(item),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )

                signal_matched = False
                signal_pattern = item["ready_signal"].strip()

                for raw_line in self.current_process.stdout:
                    if self.stop_requested:
                        self.current_process.terminate()
                        break
                    line = self._decode_line(raw_line)
                    self._log(line)

                    if signal_pattern and not signal_matched:
                        try:
                            if re.search(signal_pattern, line):
                                signal_matched = True
                                self._log(f"[系统] ✓ 检测到成功信号: {signal_pattern}")
                                self.running_processes.append(self.current_process)
                                self.current_process = None
                                self.root.after(0, lambda id=iid: self._set_status(id, "● 运行中", "green"))
                                self._log(f"[系统] 服务已转入后台运行")
                                return True
                        except re.error as e:
                            self._log(f"[系统] ⚠ 正则错误: {e}")
                            break

                ret = self.current_process.wait()
                self.current_process = None

                if self.stop_requested:
                    return False

                if signal_matched:
                    return True

                if not signal_pattern and ret == 0:
                    self.root.after(0, lambda id=iid: self._set_status(id, "● 完成", "green"))
                    self._log(f"[系统] ✓ 命令执行成功")
                    return True

                self.root.after(0, lambda id=iid, r=ret: self._set_status(id, f"● 退出({r})"))
                if signal_pattern:
                    self._log(f"[系统] 进程退出但未检测到信号 '{signal_pattern}'，准备重试...")
                else:
                    self._log(f"[系统] 进程退出码 {ret}，准备重试...")

            except Exception as e:
                self.current_process = None
                self._log(f"[系统] 执行异常: {e}")
                self.root.after(0, lambda id=iid: self._set_status(id, "● 异常"))

            if self._should_stop_retry(iid, item, attempt):
                return False
            self._wait_retry(item)

        return False

    def _should_stop_retry(self, iid, item, attempt):
        if item["max_retries"] > 0 and attempt >= item["max_retries"]:
            self.root.after(0, lambda id=iid, mx=item["max_retries"]:
                            self._set_status(id, f"● 已达{mx}次"))
            self._log(f"[系统] 已达最大重试次数 ({item['max_retries']})，放弃")
            return True
        return False

    def _wait_retry(self, item):
        delay = item["retry_delay"]
        self._log(f"[系统] 等待 {delay}s 后重试...")
        for _ in range(delay):
            if self.stop_requested:
                return
            time.sleep(1)

    def _decode_line(self, raw_line: bytes) -> str:
        """Decode subprocess output, trying multiple encodings."""
        # Try UTF-8 first, then system encoding, then fallback
        for enc in ("utf-8", SYS_ENCODING, "gbk", "cp936", "latin-1"):
            try:
                return raw_line.decode(enc).rstrip("\r\n")
            except (UnicodeDecodeError, LookupError):
                continue
        return raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

    def _on_execution_done(self):
        self.progress.stop()
        self._set_buttons_state(tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self._log("\n[系统] ══════ 全部启动流程结束 ══════\n")

    def on_close(self):
        if self.current_process:
            try:
                self.current_process.terminate()
            except Exception:
                pass
        for p in self.running_processes:
            try:
                p.terminate()
            except Exception:
                pass
        self._save_config()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = HermesRunner(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
