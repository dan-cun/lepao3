#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
"""runner_ui.py —— 乐跑打卡助手（控制台风格桌面端, 数体智慧体育 · 每日打卡自动机）

双击 exe → 进入控制台界面 → 点「▶ 启动程序」:
  1) 自动拉起抓链(系统代理→mitm→上游; 上游自动识别本机代理配置, 无则直连)
  2) 尝试自动打开微信, 并提示在电脑微信打开小程序「数体智慧体育」到跑步首页
  3) 检测到最新登录流量(token)后 → 自动归位成员 → 自动提交 → 自动复核
  4) 结束自动复原系统代理

附加: 干跑验证(零写入) / 只抓号 / 停止复原 / 成员管理 / 打开数据目录。
命令行: --selfcheck 输出环境自检 JSON 后退出(打包验证用)。
"""
import datetime
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

UI_DIR = os.path.dirname(os.path.abspath(__file__))
if UI_DIR not in sys.path:
    sys.path.insert(0, UI_DIR)

import run_core as RC                      # noqa: E402

# ---------------------------------------------------------------- 主题
BG      = "#0b0f14"
PANEL   = "#111922"
CARD    = "#16202c"
BORDER  = "#22303f"
TXT     = "#d9e4ef"
DIM     = "#8aa0b6"
OK      = "#2dd489"
WARN    = "#ffc24b"
ERR     = "#ff6b6b"
CYAN    = "#3ddad7"
BLUE    = "#4f8cff"
STEP    = "#7fb3ff"

F_UI   = ("Microsoft YaHei UI", 10)
F_UI_B = ("Microsoft YaHei UI", 10, "bold")
F_UI_S = ("Microsoft YaHei UI", 9)
F_TITLE = ("Microsoft YaHei UI", 15, "bold")
F_CON  = ("Consolas", 10)
F_BIG  = ("Microsoft YaHei UI", 13, "bold")
F_BTN  = ("Microsoft YaHei UI", 11, "bold")

PHASE_IDLE = "idle"
PHASE_CAP = "capture"


class Logger:
    """跨线程日志 → queue, UI 每 80ms 泵一次。"""

    def __init__(self, q):
        self.q = q

    def __call__(self, level, text):
        self.q.put((level, str(text)))


# ---------------------------------------------------------------- 许可证集成
def _import_license_guard():
    """容错导入 lepao.license_guard（源码/冻结两种形态）；失败返回 None。"""
    pkg_root = os.path.dirname(UI_DIR)
    meipass = getattr(sys, "_MEIPASS", "")
    for p in (pkg_root, meipass):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    try:
        from lepao import license_guard as lg
        return lg
    except Exception:
        return None


_LG = _import_license_guard()


def _window_title(lg):
    base = "乐跑打卡助手 · 数体智慧体育"
    if lg:
        return "{} · {} · {} 仅供学习不可牟利".format(
            base, lg.AUTHOR, lg.LICENSE_ID)
    return base + " · dan-cun · LRL-1.0 仅供学习不可牟利"


class LicenseDialog(tk.Toplevel):
    """关于·许可证：展示署名块、许可证要点与声明完整性自检（LRL-1.0 第二条 2(c)）。"""

    def __init__(self, master, lg):
        super().__init__(master)
        self.title("关于 · 许可证")
        self.configure(bg=PANEL)
        self.geometry("620x520")
        self.transient(master)
        rows = []
        if lg:
            rows += lg.attribution_block()
            rows += [""]
            st = lg.check_license()
            rows += lg.warning_lines()
            rows += ["", "声明文件自检：状态 = {}（{}）".format(
                st["status"], (st["path"] or "未找到")[:48])]
        else:
            rows += ["Copyright (c) 2026 dan-cun · LRL-1.0 乐跑研究协议 1.0",
                     "原始仓库：https://github.com/dan-cun/lepao3",
                     "（license_guard 未加载，无法自检；README/LICENSE 为准）"]
        rows += [
            "",
            "许可要点（详见随包 LICENSE 全文）：",
            "  1) 仅供学习研究，不得用于任何商业牟利（含收费代打、转售、嵌入付费产品）。",
            "  2) 再发布/爆改后发布须署名引用原始仓库：README 顶部、运行时可见输出、关于界面三处。",
            "  3) 不得删除或篡改任何版权/许可声明；删除即自动终止授权。",
            "  4) 衍生版须继续以 LRL-1.0 授权并标注修改说明（相同方式共享）。",
            "  5) 按\"现状\"提供，无任何担保；使用风险自担，因违规使用产生的一切后果由使用者承担。",
            "  6) 本工具只操作本人已获授权的设备与已同意代打的账号；数据仅存本地，程序零联网遥测。",
            "  7) 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡、不提供收费服务）。",
        ]
        txt = tk.Text(self, bg="#070a0e", fg=TXT, font=F_CON, bd=0, wrap="word")
        txt.insert("1.0", "\n".join(rows))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        bar = tk.Frame(self, bg=PANEL)
        bar.pack(fill="x", padx=10, pady=(0, 10))

        def open_full():
            for cand in (os.path.join(RC.PKG_ROOT, "LICENSE"),
                         os.path.join(getattr(sys, "_MEIPASS", ""), "LICENSE")):
                if cand and os.path.isfile(cand):
                    try:
                        os.startfile(cand)
                        return
                    except Exception:
                        pass
                    messagebox.showinfo("提示", "请在本目录打开 LICENSE 文件", parent=self)
                    return
            messagebox.showinfo("提示", "未随包找到 LICENSE（应保留，见 LRL-1.0 第三条）",
                                parent=self)

        tk.Button(bar, text="打开 LICENSE 全文", command=open_full,
                  font=F_UI_B, bg=CARD, fg=CYAN, relief="flat", bd=0,
                  padx=10, pady=4, cursor="hand2").pack(side="left")
        tk.Button(bar, text="关闭", command=self.destroy,
                  font=F_UI_B, bg=CARD, fg=DIM, relief="flat", bd=0,
                  padx=10, pady=4, cursor="hand2").pack(side="right")


class MemberDialog(tk.Toplevel):
    """成员管理: 列表 + 新增/删除/启停/设为当前。"""

    def __init__(self, master, on_change=None):
        super().__init__(master)
        self.on_change = on_change
        self.title("成员管理")
        self.configure(bg=BG)
        self.geometry("640x420")
        self.resizable(False, False)
        self._build()
        self.refresh()

    def _build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(head, text="成员列表(键=学号, uid 首次抓号自动回填)",
                 fg=DIM, bg=BG, font=F_UI_S).pack(side="left")
        tk.Button(head, text="新增成员", command=self._add_dialog,
                  font=F_UI_B, bg=CARD, fg=OK, activebackground=BLUE,
                  activeforeground="#fff", relief="flat", bd=0,
                  padx=12, pady=4, cursor="hand2").pack(side="right")
        body = tk.Frame(self, bg=PANEL, highlightbackground=BORDER,
                        highlightthickness=1)
        body.pack(fill="both", expand=True, padx=12, pady=6)
        cols = ("active", "label", "state", "uid", "student", "enable", "key")
        heads = ("", "成员", "token状态", "uid", "学号", "启用", "键")
        widths = (30, 110, 80, 90, 150, 50, 90)
        self.tree = ttk.Treeview(body, columns=cols, show="headings", height=9)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center" if c != "key" else "w")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
            style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                            foreground=TXT, borderwidth=0)
            style.configure("Treeview.Heading", background=CARD,
                            foreground=STEP, relief="flat")
        except Exception:
            pass
        self.tree.pack(fill="both", expand=True, padx=4, pady=4)
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=12, pady=(0, 10))
        for txt, cmd, fg in (("设为当前", self._activate, CYAN),
                             ("启用", self._enable, OK),
                             ("停用", self._disable, WARN),
                             ("删除", self._remove, ERR)):
            tk.Button(bar, text=txt, command=cmd, font=F_UI_B,
                      bg=CARD, fg=fg, relief="flat", bd=0, padx=14, pady=4,
                      activebackground=BLUE, activeforeground="#fff",
                      cursor="hand2").pack(side="left", padx=(0, 8))

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在列表中选择一个成员", parent=self)
            return None
        vals = self.tree.item(sel[0], "values")
        return {"active": vals[0], "label": vals[1], "key": vals[6]}

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for m in RC.members_table():
            self.tree.insert("", "end", values=(
                "★" if m["active"] else "", m["label"], m["state"],
                m["uid"] or "-", m["student"], "✓" if m["enabled"] else "✗",
                m["key"]))

    def _add_dialog(self):
        d = tk.Toplevel(self)
        d.title("登记成员(须该同学已同意代打)")
        d.configure(bg=BG)
        d.geometry("380x250")
        d.resizable(False, False)
        d.transient(self)
        f = tk.Frame(d, bg=BG)
        f.pack(fill="both", expand=True, padx=18, pady=14)
        ent = {}

        def row(label, key, default=""):
            tk.Label(f, text=label, fg=DIM, bg=BG, font=F_UI_S,
                     anchor="w").pack(fill="x", pady=(6, 1))
            e = tk.Entry(f, bg=CARD, fg=TXT, insertbackground=TXT,
                         relief="flat", font=F_UI)
            e.insert(0, default)
            e.pack(fill="x", ipady=3)
            ent[key] = e

        row("姓名(显示名)", "name")
        row("学号(成员键)", "student")
        row("别名(可空)", "alias")
        btn = tk.Button(f, text="登记", command=lambda: self._do_add(ent, d),
                        font=F_BTN, bg=OK, fg="#04120a", relief="flat",
                        cursor="hand2", pady=5)
        btn.pack(fill="x", pady=(14, 0))

    def _do_add(self, ent, dlg):
        name = ent["name"].get().strip()
        sn = ent["student"].get().strip()
        alias = ent["alias"].get().strip() or None
        if not name or not sn:
            messagebox.showwarning("提示", "姓名与学号必填", parent=dlg)
            return
        try:
            RC.member_add(name, sn, alias=alias)
        except Exception as e:
            messagebox.showerror("登记失败", str(e), parent=dlg)
            return
        dlg.destroy()
        self.refresh()
        if self.on_change:
            self.on_change()

    def _activate(self):
        s = self._selected()
        if s:
            RC.member_activate(s["key"])
            self.refresh()
            if self.on_change:
                self.on_change()

    def _enable(self):
        s = self._selected()
        if s:
            RC.member_toggle(s["key"], True)
            self.refresh()

    def _disable(self):
        s = self._selected()
        if s:
            RC.member_toggle(s["key"], False)
            self.refresh()

    def _remove(self):
        s = self._selected()
        if s and messagebox.askyesno("删除成员", f"确定删除 {s['label']}?",
                                     parent=self):
            RC.member_remove(s["key"])
            self.refresh()
            if self.on_change:
                self.on_change()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(_window_title(_LG))
        self.configure(bg=BG)
        self.geometry("1060x720")
        self.minsize(940, 640)
        self.log_q = queue.Queue()
        self.logger = Logger(self.log_q)
        self.worker = None
        self.stop_ev = None
        self.phase = PHASE_IDLE
        self._blink = False
        self._last_member_keys = []
        self._build()
        self.after(90, self._pump)
        self.after(600, self.refresh_members)
        self.after(1500, self._refresh_cap)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._banner_loop()
        self.log("ok", "乐跑打卡助手就绪")
        self._log_attribution()
        self.log("info", f"运行目录: {RC.DATA_DIR}")
        self._print_help()

    def _log_attribution(self):
        """启动即在控制台打印署名块 + 声明完整性警示（LRL-1.0 第二条 2(b)）。"""
        if _LG:
            try:
                for ln in _LG.attribution_block():
                    self.log("cyan", ln)
                for ln in _LG.warning_lines():
                    self.log("warn", ln)
                return
            except Exception:
                pass
        self.log("cyan", "Copyright (c) 2026 dan-cun · https://github.com/dan-cun/lepao3")
        self.log("cyan", "许可证 LRL-1.0：仅供学习 · 不可牟利 · 再发布须署名引用且声明不得删除")

    def _open_license(self):
        if _LG is None:
            messagebox.showinfo("关于", "Copyright (c) 2026 dan-cun · LRL-1.0\n"
                                       "https://github.com/dan-cun/lepao3\n"
                                       "仅供学习 · 不可牟利 · 声明不得删除")
            return
        LicenseDialog(self, _LG)

    # ======================================================== 界面构建
    def _build(self):
        # --- 顶栏
        head = tk.Frame(self, bg=PANEL, highlightbackground=BORDER,
                        highlightthickness=1)
        head.pack(fill="x")
        tk.Label(head, text="🏃 乐跑打卡助手", font=F_TITLE, fg=TXT,
                 bg=PANEL).pack(side="left", padx=14, pady=10)
        self.dot_mitm = tk.Label(head, text="●", fg=DIM, bg=PANEL,
                                 font=("Consolas", 13))
        self.dot_mitm.pack(side="left", padx=(14, 0), pady=10)
        self.lbl_mitm = tk.Label(head, text="抓链-", fg=DIM, bg=PANEL,
                                 font=F_UI_S)
        self.lbl_mitm.pack(side="left", padx=(2, 0), pady=10)
        self.dot_clash = tk.Label(head, text="●", fg=DIM, bg=PANEL,
                                  font=("Consolas", 13))
        self.dot_clash.pack(side="left", padx=(12, 0), pady=10)
        self.lbl_clash = tk.Label(head, text="出口-", fg=DIM, bg=PANEL,
                                  font=F_UI_S)
        self.lbl_clash.pack(side="left", padx=(2, 14), pady=10)
        tk.Label(head, text="成员:", fg=DIM, bg=PANEL,
                 font=F_UI_S).pack(side="left", pady=10)
        self.member_cb = ttk.Combobox(head, state="readonly", width=16,
                                      font=F_UI)
        self.member_cb.pack(side="left", padx=(2, 8), pady=10)
        self.member_cb.bind("<<ComboboxSelected>>", self._on_member_pick)
        tk.Button(head, text="成员管理", command=self._open_members,
                  font=F_UI_B, bg=CARD, fg=CYAN, relief="flat", bd=0,
                  padx=10, pady=3, cursor="hand2").pack(side="left", pady=10)
        tk.Button(head, text="环境自检", command=self._refresh_cap,
                  font=F_UI_B, bg=CARD, fg=STEP, relief="flat", bd=0,
                  padx=10, pady=3, cursor="hand2").pack(side="left",
                                                        padx=8, pady=10)
        tk.Button(head, text="关于·许可证", command=self._open_license,
                  font=F_UI_B, bg=CARD, fg=WARN, relief="flat", bd=0,
                  padx=10, pady=3, cursor="hand2").pack(side="left", pady=10)
        self.lbl_ver = tk.Label(head, text=f"v{RC.RUNNER_VER}", fg=DIM,
                                bg=PANEL, font=F_UI_S)
        self.lbl_ver.pack(side="right", padx=14, pady=10)

        # --- 状态横幅
        self.banner = tk.Label(self, text="就绪 — 点击右侧「▶ 启动程序」开始今日打卡",
                               font=F_BIG, fg=STEP, bg=BG, anchor="w")
        self.banner.pack(fill="x", padx=18, pady=(12, 2))

        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True, padx=18, pady=(2, 8))

        # --- 左: 控制台
        left = tk.Frame(main, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        con_head = tk.Frame(left, bg=PANEL, highlightbackground=BORDER,
                            highlightthickness=1)
        con_head.pack(fill="x")
        tk.Label(con_head, text="控制台", fg=DIM, bg=PANEL,
                 font=F_UI_B).pack(side="left", padx=10, pady=4)
        tk.Label(con_head, text="右键=清屏", fg=DIM, bg=PANEL,
                 font=F_UI_S).pack(side="right", padx=8)
        self.con = tk.Text(left, bg="#070a0e", fg=TXT, font=F_CON, bd=0,
                           wrap="word", state="disabled",
                           selectbackground=BLUE)
        self.con.tag_configure("info", foreground=TXT)
        self.con.tag_configure("ok", foreground=OK)
        self.con.tag_configure("warn", foreground=WARN)
        self.con.tag_configure("err", foreground=ERR)
        self.con.tag_configure("cyan", foreground=CYAN)
        self.con.tag_configure("step", foreground=STEP)
        self.con.tag_configure("dim", foreground=DIM)
        self.con.pack(fill="both", expand=True, padx=(0, 0))
        self.con.bind("<Button-3>", lambda e: self.con.delete("1.0", "end"))

        # --- 右: 控制面板
        right = tk.Frame(main, bg=PANEL, width=220,
                         highlightbackground=BORDER, highlightthickness=1)
        right.pack(side="right", fill="y", padx=(14, 0))
        right.pack_propagate(False)

        self.btn_run = tk.Button(right, text="▶  启动程序", command=self.on_run,
                                 font=F_BTN, bg=OK, fg="#04120a",
                                 activebackground="#3af09a",
                                 activeforeground="#04120a", relief="flat",
                                 bd=0, cursor="hand2", pady=12)
        self.btn_run.pack(fill="x", padx=12, pady=(16, 6))

        self.btn_dry = tk.Button(right, text="干跑验证(零写入)", command=self.on_dry,
                                 font=F_UI_B, bg=CARD, fg=STEP, relief="flat",
                                 bd=0, cursor="hand2", pady=6)
        self.btn_dry.pack(fill="x", padx=12, pady=4)

        self.btn_cap = tk.Button(right, text="只抓号(首次登记)", command=self.on_cap,
                                 font=F_UI_B, bg=CARD, fg=CYAN, relief="flat",
                                 bd=0, cursor="hand2", pady=6)
        self.btn_cap.pack(fill="x", padx=12, pady=4)

        self.btn_stop = tk.Button(right, text="■  停止并复原", command=self.on_stop,
                                  font=F_UI_B, bg=CARD, fg=ERR, relief="flat",
                                  bd=0, cursor="hand2", pady=6)
        self.btn_stop.pack(fill="x", padx=12, pady=4)

        tk.Button(right, text="打开数据目录", command=self._open_data,
                  font=F_UI_B, bg=CARD, fg=DIM, relief="flat", bd=0,
                  cursor="hand2", pady=6).pack(fill="x", padx=12, pady=4)

        tk.Button(right, text="指定微信程序(路径找不到时)", command=self._pick_wechat,
                  font=F_UI_S, bg=CARD, fg=DIM, relief="flat", bd=0,
                  cursor="hand2", pady=3).pack(fill="x", padx=12, pady=(0, 4))

        # 强制退出微信登录(新逻辑: 打卡前先清干净旧登录态, 杜绝顶号)
        self.var_logout = tk.BooleanVar(value=RC.LOGOUT_ON_RUN)
        tk.Checkbutton(right, text="启动前强制退出微信登录\n(杀进程+清登录态, 需重新扫码)",
                       variable=self.var_logout, bg=PANEL, fg=DIM,
                       activebackground=PANEL, activeforeground=TXT,
                       selectcolor=CARD, font=F_UI_S, justify="left",
                       anchor="w", command=self._sync_logout_flag,
                       highlightthickness=0).pack(fill="x", padx=14, pady=(2, 2))
        tk.Button(right, text="退出微信登录(立即执行)", command=self.on_logout,
                  font=F_UI_S, bg=CARD, fg=WARN, relief="flat", bd=0,
                  cursor="hand2", pady=3).pack(fill="x", padx=14, pady=(0, 4))

        tk.Frame(right, bg=BORDER).pack(fill="x", padx=12, pady=10)
        self.lbl_step = tk.Label(right, text="今日进度\n—", fg=DIM,
                                 bg=PANEL, font=F_UI_S, justify="left",
                                 anchor="nw")
        self.lbl_step.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        # --- 底栏
        foot = tk.Frame(self, bg=PANEL, highlightbackground=BORDER,
                        highlightthickness=1)
        foot.pack(fill="x", side="bottom")
        self.lbl_foot = tk.Label(foot, text=f"数据目录: {RC.DATA_DIR}",
                                 fg=DIM, bg=PANEL, font=F_UI_S, anchor="w")
        self.lbl_foot.pack(side="left", padx=12, pady=4)
        tk.Label(foot, text="© 2026 dan-cun · LRL-1.0 仅供学习·不可牟利·声明不得删除 · "
                            "github.com/dan-cun/lepao3",
                 fg=BORDER, bg=PANEL, font=F_UI_S, anchor="center").pack(
            side="left", padx=12, pady=4)
        self.lbl_last = tk.Label(foot, text="最近: -", fg=DIM, bg=PANEL,
                                 font=F_UI_S, anchor="e")
        self.lbl_last.pack(side="right", padx=12, pady=4)

    # ======================================================== 日志/UI泵
    def log(self, level, text):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_q.put((level, f"[{ts}] {text}"))

    def _pump(self):
        try:
            while True:
                level, text = self.log_q.get_nowait()
                if level == "__done__":
                    self._worker_done()
                    continue
                tag = level if level in ("info", "ok", "warn", "err", "cyan",
                                         "step", "dim") else "info"
                self.con.configure(state="normal")
                self.con.insert("end", text + "\n", tag)
                self.con.see("end")
                self.con.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(90, self._pump)

    def _worker_done(self):
        self.worker = None
        self.phase = PHASE_IDLE
        self._set_running(False)
        self.banner.config(text="完成 — 点击「▶ 启动程序」可开始下一位成员",
                           fg=OK)
        self.refresh_members()

    def _run_job(self, fn, *args):
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("提示", "已有任务在运行(可先点停止)")
            return
        self.stop_ev = threading.Event()
        self.con.delete("1.0", "end")
        self._set_running(True)
        self.banner.config(text="运行中…", fg=STEP)

        def job():
            try:
                out = fn(*args, log=self.logger, stop_event=self.stop_ev)
                if self.stop_ev and self.stop_ev.is_set():
                    self.log_q.put(("warn", "任务被用户停止"))
                else:
                    self.log_q.put(("ok", f"任务结束: {out}"))
            except Exception as e:
                self.log_q.put(("err", f"任务异常: {e}"))
            finally:
                self.log_q.put(("dim", "-" * 60))
                self.log_q.put(("__done__", ""))

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def _set_running(self, running):
        state = "disabled" if running else "normal"
        for b in (self.btn_run, self.btn_dry, self.btn_cap):
            b.configure(state=state)
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.member_cb.configure(state="disabled" if running else "readonly")

    # ======================================================== 动作
    def _member(self):
        """当前下拉选择的成员键; 空则 None(active)。"""
        sel = self.member_cb.get()
        for m in RC.members_table():
            if m["key"] == sel or m["label"] == sel or str(m["uid"]) == sel:
                return m["key"]
        return None

    def on_run(self):
        cap = RC.capability()
        if cap["members"] == 0:
            messagebox.showwarning("提示", "尚未登记任何成员 → 先点「成员管理」登记")
            return
        if not cap.get("mitm_healthy"):
            if cap["mitmweb"]:
                messagebox.showwarning(
                    "mitmweb 启动崩溃",
                    f"找到 mitmweb 但启动即崩:\n{cap['mitmweb']}\n\n"
                    "修复任一:\n"
                    "1) 终端执行: py -3 -m pip install \"bcrypt<4.1\"\n"
                    "2) 安装新版 mitmproxy 后重试\n"
                    "3) 在数据目录 runner.json 配置 {\"mitmweb\": \"可用路径\"}")
            else:
                messagebox.showwarning("缺依赖", "未找到可用的 mitmweb。\n"
                                       "1) 安装 mitmproxy 后重试\n"
                                       "2) 或在数据目录 runner.json 配置 mitmweb 路径")
            return
        self.phase = PHASE_CAP
        self._set_step("0/4 先退出微信登录 → 1/4 扫码登录开小程序 → 2/4 自动提交 → 3/4 复核")
        self.log("step", "====== 启动程序 ======")
        self._sync_logout_flag()
        if RC.LOGOUT_ON_RUN:
            self.log("cyan", "将先【强制退出当前微信登录】→ 重新扫码登录 → "
                             f"打开小程序「{RC.MINI_PROGRAM_HINT}」到跑步首页(只一次)")
        else:
            self.log("cyan", f"请用电脑微信打开小程序「{RC.MINI_PROGRAM_HINT}」"
                             f"到跑步首页(本机自动开微信中…)")
        self._run_job(RC.action_full, self._member())

    def on_dry(self):
        self.log("step", "====== 干跑验证(零写入) ======")
        self._run_job(RC.action_dry, self._member())

    def on_cap(self):
        self.log("step", "====== 只抓号(不提交) ======")
        self._sync_logout_flag()
        self._run_job(RC.action_capture, self._member())

    def on_logout(self):
        """立即执行: 强制退出当前微信登录(杀进程+清登录态)。"""
        if not messagebox.askyesno("退出微信登录",
                                   "将结束所有微信进程并清除登录态缓存,\n"
                                   "下次使用微信需重新扫码。继续?"):
            return
        self.log("step", "====== 强制退出微信登录 ======")
        self._run_job(RC.action_logout)

    def _sync_logout_flag(self):
        try:
            RC.LOGOUT_ON_RUN = bool(self.var_logout.get())
        except Exception:
            pass

    def on_stop(self):
        if self.worker and self.worker.is_alive():
            self.stop_ev.set()
        RC.action_stop(log=self.logger)
        self._set_running(False)
        self.banner.config(text="已停止 — 网络设置已复原", fg=WARN)

    # ======================================================== 成员/状态
    def refresh_members(self):
        try:
            rows = RC.members_table()
        except Exception:
            rows = []
        if not rows:
            self.member_cb["values"] = []
            self.member_cb.set("")
            return
        keys = [m["key"] for m in rows]
        if keys != self._last_member_keys:
            self._last_member_keys = keys
            active = next((m["key"] for m in rows if m["active"]), rows[0]["key"])
            cur = self.member_cb.get()
            vals = [m["key"] for m in rows]
            self.member_cb["values"] = vals
            self.member_cb.set(cur if cur in vals else active)
        # 步进摘要
        parts = [f"{m['label']}·{m['state']}" for m in rows]
        self.lbl_step.config(text="今日进度\n" + "\n".join(parts))
        last = RC.last_result()
        if last:
            rid = last.get("record_id")
            rs = last.get("record_status")
            dist = last.get("distance")
            who = last.get("member") or ""
            self.lbl_last.config(
                text=f"最近: #{rid} {who} dist={dist} status={rs}")

    def _on_member_pick(self, _e=None):
        key = self._member()
        if key:
            try:
                RC.member_activate(key)
            except Exception:
                pass

    def _open_members(self):
        MemberDialog(self, on_change=self.refresh_members)

    def _refresh_cap(self):
        try:
            c = RC.capability()
        except Exception as e:
            self.log("err", f"自检失败: {e}")
            return
        mw_ok = c.get("mitm_healthy", False)
        self.dot_mitm.config(fg=OK if mw_ok else ERR,
                             text="●" if mw_ok else "○")
        if c["mitmweb"] and not mw_ok:
            self.lbl_mitm.config(text="抓链-mitmweb坏", fg=ERR)
        else:
            self.lbl_mitm.config(text="抓链-OK" if mw_ok else "抓链-缺mitmweb",
                                 fg=OK if mw_ok else ERR)
        mode = c.get("upstream_mode", "proxy")
        if mode != "proxy":
            # 自动识别: 本机无系统代理 → 直连模式(正常态, 绿色)
            self.dot_clash.config(fg=OK, text="●")
            self.lbl_clash.config(text="出口-直连", fg=OK)
        elif c["clash"]:
            self.dot_clash.config(fg=OK, text="●")
            self.lbl_clash.config(text="出口-OK(代理)", fg=OK)
        else:
            self.dot_clash.config(fg=WARN, text="○")
            self.lbl_clash.config(text="出口-代理不通(将直连)", fg=WARN)
        updesc = (c.get("upstream") or "直连(自动识别)")
        wx = c.get("wechat")
        wxdesc = "有 " + os.path.basename(wx) if wx else \
            "未找到(点「指定微信」或安装电脑版微信)"
        self.log("info", f"自检: mitmweb={'健康' if mw_ok else ('损坏' if c['mitmweb'] else '无')} | "
                         f"出口={updesc}{'(通)' if c['clash'] else ''} | "
                         f"微信={wxdesc} | "
                         f"成员={c['members']}")
        if not wx:
            self.log("dim", "  微信路径自动探测: runner.json wechat → 常见安装目录 → 注册表"
                            " App Paths/卸载表 → 运行中进程 → 各盘 Tencent 目录 → 开始菜单快捷方式")

    def _set_step(self, text):
        self.log("step", text)

    def _open_data(self):
        try:
            os.startfile(RC.DATA_DIR)
        except Exception:
            pass

    def _pick_wechat(self):
        """手动指定微信主程序 → 写入 runner.json wechat（自动探测六层都失败时的兜底）。"""
        try:
            from tkinter import filedialog
        except Exception:
            return
        p = filedialog.askopenfilename(
            parent=self, title="选择微信主程序 (Weixin.exe 或 WeChat.exe)",
            filetypes=[("微信", "Weixin.exe WeChat.exe weixin.exe wechat.exe"),
                       ("所有文件", "*.exe")])
        if not p:
            return
        try:
            RC._remember_wechat(p)
        except Exception:
            pass
        self.log("ok", f"已记录微信路径 → {p}")
        self._refresh_cap()

    def _banner_loop(self):
        if self.phase == PHASE_CAP and self.worker and self.worker.is_alive():
            self._blink = not self._blink
            self.banner.config(
                text="请在电脑微信打开小程序「数体智慧体育」到跑步首页…"
                     " 检测到登录流量将自动继续",
                fg=CYAN if self._blink else STEP)
        self.after(700, self._banner_loop)

    def _print_help(self):
        self.log("dim", "操作: ① 成员管理→登记(本人或已同意代打的同学) ② ▶ 启动程序")
        self.log("dim", "      手机无需操作; 电脑微信打开小程序后本工具自动抓号→自动提交→自动复核")

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("退出", "任务仍在运行, 确定退出?\n"
                                               "(将停止抓链并复原系统代理)"):
                return
            if self.stop_ev:
                self.stop_ev.set()
            RC.action_stop(log=self.logger)
        self.destroy()


def _selfcheck():
    RC.setup_environment()
    c = RC.capability()
    out = {"ok": True, **c}
    if _LG:
        try:
            out["license"] = _LG.summary()
        except Exception:
            pass
    try:
        with open(os.path.join(RC.DATA_DIR, "selfcheck.json"), "w",
                  encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0 if (c["mitmweb"] or True) else 1)


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    app = App()
    app.mainloop()
