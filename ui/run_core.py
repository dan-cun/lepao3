# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
"""run_core.py —— 乐跑打卡助手运行核心（无 GUI 依赖, GUI/打包共用）

职责:
  1) 运行时目录解析: env LEPAO_RUNNER_DIR > exe/脚本同目录已有 config.json(操作员模式)
     > exe/脚本同级 runtime(便携模式, 首次自动从 config.sample 骨架初始化)
  2) 依赖自检: mitmweb(路径/PATH/默认) · Clash 上游(7897) · 微信(自动唤起候选) · 成员表
  3) 动作(worker 线程中调用, 全程 log(level,text) 回调 + stop_event 可中断):
     action_full  : 起链 → 提示开小程序「数体智慧体育」→ 等新token → 自动归位 → 自动提交 → 复核
     action_dry   : 起链 → 用现有(未消耗)token 干跑(零写入)
     action_capture: 起链 → 等新token → 归位(不上交, 供首次登记)
     stop_all     : 杀 mitm + 复原系统代理(幂等)
"""
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_ROOT not in sys.path:
    sys.path.insert(0, PKG_ROOT)

FROZEN = bool(getattr(sys, "frozen", False))
MEIPASS = getattr(sys, "_MEIPASS", "")
UI_DIR = os.path.dirname(os.path.abspath(__file__))
EXE_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else UI_DIR

ADDON_SRC = os.path.join(PKG_ROOT, "工具", "mitm_lptiyu_token.py")
MITM_DEFAULT = os.path.expandvars(
    r"%LOCALAPPDATA%\Programs\Python\Python311\Scripts\mitmweb.exe")

WECHAT_CANDIDATES = [
    # 微信 4.x (Weixin)
    r"C:\Program Files\Tencent\Weixin\Weixin.exe",
    r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Tencent\Weixin\Weixin.exe"),
    # 微信 3.x (WeChat)
    r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe",
    r"C:\Program Files\Tencent\WeChat\WeChat.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Tencent\WeChat\WeChat.exe"),
]
WECHAT_NEW_CANDIDATES = [
    os.path.expandvars(r"%LOCALAPPDATA%\Tencent\WeChat\WeChatAppEx.exe"),
]
MINI_PROGRAM_HINT = "数体智慧体育"
RUNNER_VER = "1.2.0"

# 启动前是否先“强制退出当前微信登录”(杀进程+清登录态→重新扫码)
# UI 复选框 / 环境变量 LEPAO_RUN_LOGOUT=0 可关
LOGOUT_ON_RUN = os.environ.get("LEPAO_RUN_LOGOUT", "1").lower() not in ("0", "no", "false")


# ================================================================ 路径/环境
def resolve_data_dir() -> str:
    env = os.environ.get("LEPAO_RUNNER_DIR", "").strip()
    if env:
        return env
    if os.path.exists(os.path.join(EXE_DIR, "config.json")):
        return EXE_DIR                      # 操作员模式: exe 旁边就是乐跑2 运行目录
    return os.path.join(EXE_DIR, "runtime")  # 便携模式


_MITM_ORDER_NOTE = "存在≠可用: passlib×bcrypt5 会让部分安装启动即崩, 需健康探测"


def _mitm_candidates():
    """候选 mitmweb 路径(优先级): runner.json/env 指定 → D盘已知良好 venv
    → PATH → 各 Python\\Scripts。"""
    out = []
    for c in (_cfg_opt("mitmweb", ""), os.environ.get("LEPAO_MITM_EXE", ""),
              MITM_DEFAULT):
        if c and c not in out and os.path.exists(c):
            out.append(c)
    for w in (shutil.which("mitmweb"), shutil.which("mitmweb.exe")):
        if w and w not in out:
            out.append(w)
    try:
        import glob
        base = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python")
        for p in glob.glob(os.path.join(base, "*", "Scripts", "mitmweb*")):
            if p not in out:
                out.append(p)
    except Exception:
        pass
    return out


def mitm_healthy(path, timeout=25):
    """启动健康探测: `exe --version` 正常退出才算健康(结果缓存)。"""
    cached = _MITM_HEALTH_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        r = subprocess.run([path, "--version"], capture_output=True,
                           text=True, timeout=timeout,
                           creationflags=0x08004000)  # CREATE_NO_WINDOW
        h = r.returncode == 0
    except Exception:
        h = False
    _MITM_HEALTH_CACHE[path] = h
    return h


_MITM_HEALTH_CACHE = {}
_MITM_BROKEN = ""          # 候选存在但全部启动崩溃时的第一个(诊断用)


def _resolve_mitmweb() -> str:
    """选**健康**的 mitmweb(带探测缓存)。用户显式指定项坏了不悄悄替换
    (capability 会报 unhealthy 供 UI 提示)。"""
    global _MITM_BROKEN
    _MITM_BROKEN = ""
    forced = _cfg_opt("mitmweb", "") or os.environ.get("LEPAO_MITM_EXE", "")
    if forced and os.path.exists(forced):
        return forced
    for p in _mitm_candidates():
        h = _MITM_HEALTH_CACHE.get(p)
        if h is None:
            h = mitm_healthy(p)
            _MITM_HEALTH_CACHE[p] = h
        if h:
            return p
        _MITM_BROKEN = _MITM_BROKEN or p
    return ""


def _resolve_wechat() -> str:
    """定位电脑版微信主程序（4.x Weixin / 3.x WeChat），多源解析(命中缓存)：
      0) runner.json 显式 wechat 路径
      1) 常见安装目录候选（Program Files / LOCALAPPDATA）
      2) 注册表 HKLM/HKCU\...\App Paths\{Weixin,WeChat}.exe
      3) 卸载表 DisplayName 反查（DisplayIcon / InstallLocation）
      4) 运行中进程映像路径（纯 WinAPI, 覆盖非标准盘安装）
      5) 各盘符 <盘>:\Tencent\... 与开始菜单快捷方式解析
    全部落空返回 ""（UI 提供「指定微信程序」手动兜底）。"""
    global _WX_CACHE
    c = _cfg_opt("wechat", "")
    if c and os.path.exists(c):
        _WX_CACHE = c
        return c
    if _WX_CACHE and os.path.exists(_WX_CACHE):
        return _WX_CACHE
    for p in WECHAT_CANDIDATES + WECHAT_NEW_CANDIDATES:
        if p and os.path.exists(p):
            _WX_CACHE = p
            return p
    p = _wechat_from_registry() or _wechat_from_process() or \
        _wechat_from_drives() or _wechat_from_startmenu()
    if p:
        _WX_CACHE = p
        _remember_wechat(p)
    return p or ""


_WX_CACHE = ""


_WECHAT_EXES = ("Weixin.exe", "WeChat.exe")


def _valid_wechat(p: str) -> str:
    try:
        p = os.path.normpath(p.strip().strip('"'))
        if p and p.lower().endswith(("weixin.exe", "wechat.exe")) and os.path.isfile(p):
            return p
    except Exception:
        pass
    return ""


def _wechat_from_registry() -> str:
    try:
        import winreg
    except ImportError:
        return ""
    for exe in _WECHAT_EXES:
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                k = winreg.OpenKey(
                    hive, "Software\\Microsoft\\Windows\\CurrentVersion"
                          "\\App Paths\\" + exe)
                v = _valid_wechat(winreg.QueryValue(k, None) or "")
                if v:
                    return v
            except OSError:
                continue
    # 卸载表反查（显示名含 微信/WeChat/Weixin 的项）
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                    r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                base = winreg.OpenKey(hive, sub)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                try:
                    k = winreg.OpenKey(base, name)
                    dn = str(winreg.QueryValueEx(k, "DisplayName")[0])
                    if not any(x in dn for x in ("微信", "WeChat", "Weixin")):
                        continue
                    for field in ("DisplayIcon", "InstallLocation"):
                        try:
                            val = str(winreg.QueryValueEx(k, field)[0])
                        except OSError:
                            continue
                        cand = val.split(",")[0] if field == "DisplayIcon" else \
                            os.path.join(val, "Weixin.exe")
                        v = _valid_wechat(cand) or _valid_wechat(
                            os.path.join(val, "WeChat.exe"))
                        if v:
                            return v
                except OSError:
                    continue
    return ""


def _wechat_from_process() -> str:
    """微信正在运行（本工具流程里很常见）→ 直接问系统拿进程映像路径。
    纯 WinAPI（CreateToolshop32Snapshot + QueryFullProcessImageNameW），
    不依赖 tasklist/wmic（Win11 已移除 wmic.exe）；再退 PowerShell 兜底。"""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE,
                                                   wintypes.DWORD,
                                                   wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD)]
        k32.OpenProcess.restype = wintypes.HANDLE

        class PROCENTRY(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_char * 260)]

        def exe_path(pid):
            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000（低权限也可查）
            h = k32.OpenProcess(0x1000, False, pid)
            if not h:
                return ""
            try:
                size = wintypes.DWORD(1024)
                buf = ctypes.create_unicode_buffer(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return _valid_wechat(buf.value)
            finally:
                k32.CloseHandle(h)
            return ""

        snap = k32.CreateToolhelp32Snapshot(0x2, 0)     # TH32CS_SNAPPROCESS
        if snap not in (None, -1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
            entry = PROCENTRY()
            entry.dwSize = ctypes.sizeof(PROCENTRY)
            ok = k32.Process32First(snap, ctypes.byref(entry))
            while ok:
                raw = (entry.szExeFile if isinstance(entry.szExeFile, bytes)
                       else bytes(entry.szExeFile)).split(b"\x00")[0]
                nm = raw.decode("mbcs", errors="ignore").lower()
                if nm in ("weixin.exe", "wechat.exe"):
                    p = exe_path(entry.th32ProcessID)
                    if p:
                        k32.CloseHandle(snap)
                        return p
                ok = k32.Process32Next(snap, ctypes.byref(entry))
            k32.CloseHandle(snap)
    except Exception:
        pass
    # PowerShell 兜底（Get-Process 的 Path 属性；本工具已依赖 powershell）
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process -Name Weixin,WeChat -ErrorAction SilentlyContinue | "
             "Select-Object -First 1 -ExpandProperty Path"],
            capture_output=True, text=True, timeout=25).stdout
        for line in out.splitlines():
            p = _valid_wechat(line)
            if p:
                return p
    except Exception:
        pass
    return ""


def _wechat_from_drives() -> str:
    import string
    for d in [x + ":\\" for x in string.ascii_uppercase
              if os.path.isdir(x + ":\\")]:
        if d.upper().startswith("C:\\"):
            continue                      # C 盘候选层已查过
        for pat in ("Tencent\\Weixin\\Weixin.exe",
                    "Tencent\\WeChat\\WeChat.exe",
                    "Program Files\\Tencent\\Weixin\\Weixin.exe",
                    "微信\\Weixin.exe", "Weixin\\Weixin.exe"):
            p = _valid_wechat(os.path.join(d, pat))
            if p:
                return p
    return ""


def _wechat_from_startmenu() -> str:
    """开始菜单/公共桌面快捷方式 → 解析 .lnk 目标（无需第三方库：
    .lnk 内 target 路径可 ASCII 粗提取，失败则回退 weixin:// 协议探测）。"""
    dirs = []
    for env in ("APPDATA", "PROGRAMDATA"):
        base = os.path.expandvars("%{}%".format(env))
        if base:
            dirs += [os.path.join(base, r"Microsoft\Windows\Start Menu\Programs"),
                     os.path.join(base, "Desktop")]
    dirs = [d for d in dirs if os.path.isdir(d)]
    for d in dirs:
        try:
            for root, _, fs in os.walk(d):
                for f in fs:
                    if not f.lower().endswith(".lnk"):
                        continue
                    if not any(x in f for x in ("微信", "WeChat", "Weixin")):
                        continue
                    p = _lnk_target(os.path.join(root, f))
                    if p:
                        return p
        except Exception:
            continue
    return ""


def _lnk_target(path: str) -> str:
    """从 .lnk 二进制里粗提取 WeChat/Weixin 主程序路径（ANSI 与 UTF-16 两种编码都试），
    不做完整 MS-SHLLINK 解析——目标只有一个 exe，扫出来即可。失败返回 ""。"""
    try:
        raw = open(path, "rb").read()
    except Exception:
        return ""
    cands = []
    for m in re.finditer(rb"[A-Za-z]:[\\/][\x20-\x7e]{3,254}?\.(?:exe|EXE)", raw):
        cands.append(m.group(0).decode("mbcs", errors="ignore"))
    u = raw.decode("utf-16-le", errors="ignore")
    for m in re.finditer(r"[A-Za-z]:[\\/][^\x00]{3,254}?\.exe", u):
        cands.append(m.group(0))
    for c in cands:
        p = _valid_wechat(c)
        if p:
            return p
    return ""


def _remember_wechat(p: str) -> None:
    """把自动发现的微信路径回写 runner.json(下次免全盘找, 也便于用户看见改错)。"""
    try:
        fp = os.path.join(DATA_DIR, "runner.json")
        j = {}
        if os.path.exists(fp):
            with open(fp, encoding="utf-8-sig") as f:
                j = json.load(f)
        if j.get("wechat") != p:
            j["wechat"] = p
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(j, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _cfg_opt(key, default=""):
    try:
        with open(os.path.join(DATA_DIR, "runner.json"),
                  encoding="utf-8-sig") as f:   # 容错记事本 BOM
            j = json.load(f)
        return j.get(key, default)
    except Exception:
        return default


DATA_DIR = resolve_data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
CRED_PATH = os.path.join(DATA_DIR, "凭证.json")
FLOWS_DIR = os.path.join(DATA_DIR, "flows")
ADDON_PATH = os.path.join(DATA_DIR, "mitm_addon.py")


def setup_environment() -> dict:
    """确保运行目录 + 环境变量(必须在 import lepao.capture 之前调用)。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(FLOWS_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        sample = os.path.join(MEIPASS, "config.sample.json") if FROZEN else \
            os.path.join(PKG_ROOT, "config.sample.json")
        base = {}
        if os.path.exists(sample):
            try:
                base = json.load(open(sample, encoding="utf-8"))
            except Exception:
                base = {}
        # 首次落地: 只继承环境/策略骨架, 成员表置空(强制先登记真实成员, 占位符不落地)
        skeleton = {
            "version": 2,
            "active": "",
            "accounts": {},
            "environment": (base.get("environment") or {
                "api_host": "api2.lptiyu.com",
                "api_proxy": "127.0.0.1:8081",
                "h5_base": "/bdlp_h5_fitness_test/public/index.php",
                "oss_host": "lptiyu-data.oss-cn-hangzhou.aliyuncs.com",
                "app_key": "wxb2043232183dac7f"}),
            "policy": (base.get("policy") or {"max_per_day": 1,
                                              "style": "template",
                                              "dist_lo": 2.03,
                                              "dist_hi": 2.35,
                                              "pace_lo": 185,
                                              "pace_hi": 560}),
            "runtime": (base.get("runtime") or {"spacing_min": 5,
                                                "spacing_max": 10}),
        }
        json.dump(skeleton, open(CONFIG_PATH, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    # mitm addon: 常态落在 data 目录(独立进程读取, 不依赖解包临时区)
    addon_src = os.path.join(MEIPASS, "mitm_addon.py") if FROZEN and \
        os.path.exists(os.path.join(MEIPASS, "mitm_addon.py")) else ADDON_SRC
    if os.path.exists(addon_src):
        try:
            shutil.copy(addon_src, ADDON_PATH)
        except Exception:
            pass
    os.environ["LEPAO2_CONFIG"] = CONFIG_PATH
    os.environ["LEPAO_CRED_FILE"] = CRED_PATH
    # mitm 的 -w 是"流文件"路径(不是目录!传目录会 Errno 13 启动即崩)
    os.environ["LEPAO_FLOWS_DIR"] = os.path.join(FLOWS_DIR, "capture.flows")
    if os.path.exists(ADDON_PATH):
        os.environ["LEPAO_MITM_ADDON"] = ADDON_PATH
    mw = _resolve_mitmweb()
    if mw:
        os.environ["LEPAO_MITM_EXE"] = mw
    # 上游: runner.json 显式 clash_upstream 才写 env(优先); 未设 → 不写 env,
    # 由 capture.start 在调用时刻自动识别本机系统代理(跟随机器配置, 不在 init 锁死)。
    up_cfg = _cfg_opt("clash_upstream", "")
    if up_cfg in ("direct", "none", "-"):
        os.environ["LEPAO_CLASH_UPSTREAM"] = "direct"
    elif up_cfg:
        os.environ["LEPAO_CLASH_UPSTREAM"] = up_cfg
    return {"data_dir": DATA_DIR, "config": CONFIG_PATH, "mitmweb": mw,
            "upstream": up_cfg or "(自动识别: 系统代理)"}


def clash_up(host="127.0.0.1", port=7897, timeout=1.2) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def parse_upstream(up: str):
    """'http://127.0.0.1:7897' → ('127.0.0.1', 7897)"""
    try:
        import urllib.parse as _u
        pr = _u.urlsplit(up if "://" in up else "http://" + up)
        return (pr.hostname or "127.0.0.1", pr.port or 7897)
    except Exception:
        return ("127.0.0.1", 7897)


def resolve_upstream() -> str:
    """链路上游统一解析(自动识别机器配置)。优先级:
    1) runner.json clash_upstream 显式值  2) env LEPAO_CLASH_UPSTREAM
    3) 自动识别本机当前系统代理(=微信登录出口, 见 capture.detect_system_upstream)。
    'direct'/'none' = 强制直连; 结果为空 → 直连模式。不做在线探测。"""
    up = _cfg_opt("clash_upstream", "")
    if up in ("direct", "none", "-"):
        return ""
    if not up:
        up = os.environ.get("LEPAO_CLASH_UPSTREAM", "")
        if up in ("direct", "none", "-"):
            return ""
    if up:
        return up
    try:
        from lepao import capture as CAP
        return CAP.detect_system_upstream()
    except Exception:
        return ""


def effective_upstream() -> str:
    """出口同态自动判定: 解析上游(显式>自动识别系统代理); 在听→沿用; 不在→''(直连模式)。
    (真正要求是 登录与业务走同一出口, 而非必须有代理)"""
    up = resolve_upstream()
    if not up:
        return ""
    h, p = parse_upstream(up)
    return up if clash_up(h, p) else ""


def mitm_up(port=8081) -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1.2)
        s.close()
        return True
    except Exception:
        return False


def capability() -> dict:
    mw = _resolve_mitmweb()
    mw_ok = bool(mw) and mitm_healthy(mw) if mw else False
    up = resolve_upstream()                      # 显式配置 > 自动识别系统代理
    _h, _p = parse_upstream(up) if up else (None, None)
    up_alive = bool(up) and clash_up(_h, _p)
    try:
        from lepao.accounts import AccountRegistry
        reg = AccountRegistry(CONFIG_PATH)
        members = len(reg.keys())
        has_active = bool(reg.config.get("active"))
    except Exception:
        reg, members, has_active = None, 0, False
    return {
        "runtime": DATA_DIR, "config": CONFIG_PATH,
        "mitmweb": mw or None, "mitm_healthy": mw_ok,
        "clash": up_alive,
        "upstream": up, "upstream_mode": ("proxy" if up else "direct"),
        "wechat": _resolve_wechat() or None,
        "members": members, "has_active": has_active,
        "frozen": FROZEN, "version": RUNNER_VER,
    }


# ================================================================ 微信唤起
def open_wechat() -> str:
    p = _resolve_wechat()
    if p:
        try:
            os.startfile(p)
            return p
        except Exception:
            pass
    # 兜底：走 weixin:// / wechat:// 协议启动（注册过协议但未落在可探路径时仍可唤起）
    for proto in ("weixin://", "wechat:"):
        try:
            os.startfile(proto)
            return proto
        except Exception:
            continue
    return ""


# ================================================================ 链管理
def start_chain(log):
    """起 mitm 链(代理→mitm→[上游?])。上游自动识别本机配置: 有系统代理且在听→沿链;
    无代理/代理不在→自动直连模式(登录与业务同出口 = 本机 mitm, 出口同态成立)。
    返回 (guard, proc)。"""
    from lepao import capture as CAP
    up = effective_upstream()
    os.environ["LEPAO_CLASH_UPSTREAM"] = up       # capture.start 在调用时刻重读
    guard = CAP.ProxyGuard()
    proc, guard = CAP.start(guard, force=True)
    tail = f"→{up}" if up else "→直连"
    log("info", f"mitm 链就绪: 系统代理→127.0.0.1:{CAP.LISTEN[1]}{tail}")
    return guard, proc


def stop_all(guard=None, log=None):
    """杀 mitm + 复原系统代理(幂等, 主线程可直接调用)。"""
    try:
        subprocess.run(["powershell", "-c",
                        "Get-Process mitmweb -ErrorAction SilentlyContinue | Stop-Process -Force"],
                       capture_output=True, timeout=15)
    except Exception:
        pass
    if guard is not None:
        try:
            guard.restore()
        except Exception as e:
            if log:
                log("warn", f"代理复原异常: {e}")
    else:
        try:
            from lepao import capture as CAP
            g = CAP.ProxyGuard()
            g.restore()
        except Exception:
            pass


# ================================================================ token 等待
def _read_cred():
    try:
        with open(CRED_PATH, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def wait_new_token(base_token, timeout_s, stop_event, interval=2, log=None,
                   stable_reads=6):
    """等 凭证.json 出现新 token(区别于 base), 且**连续 stable_reads 次不再变化**才返回。
    必要性(11:11 实测): 小程序双冷启动会在 ~8s 内轮换 loginByCode(4E47→505ACCFB),
    抓到中间值 = 已被服务端作废的死会话 → 真门 101。稳定判据防此竞态(6×2=12s 静默)。"""
    t0 = time.time()
    cur, same = None, 0
    while not stop_event.is_set():
        if time.time() - t0 > timeout_s:
            if log:
                log("warn", "等待超时: 未检测到新登录(小程序未打开?)")
            return None
        cred = _read_cred()
        tok = cred.get("token")
        if tok and tok != base_token:
            if tok == cur:
                same += 1
            else:
                cur, same = tok, 1
                if log:
                    log("info", f"检测到新登录 token={tok[:8]}… (等稳定, 防双登录轮换)")
            if same >= stable_reads:
                if log:
                    log("ok", f"登录流量稳定 → token={tok[:8]}… "
                              f"uid={cred.get('uid')} 自动继续")
                return cred
        time.sleep(interval)
    return None


# ================================================================ 复核
def watch_record(record_id, stop_event, log, max_times=3):
    from lepao.accounts import AccountRegistry
    from lepao.auth import AuthState
    reg = AccountRegistry(CONFIG_PATH)
    auth = AuthState(reg, reg.resolve())
    client = auth.make_client(pace=1.0)
    for i in range(max_times):
        if stop_event.is_set():
            return None
        try:
            d = client.record_detail(record_id)
        except Exception as e:
            log("err", f"复核请求失败: {e}")
            return None
        st = d.get("record_status") if isinstance(d, dict) else "?"
        rs = (d.get("record_failed_reason") or "") if isinstance(d, dict) else ""
        positive = "有效" in rs and "无效" not in rs and "异常" not in rs
        log("info", f"[复核 {i+1}/{max_times}] status={st} reason={rs!r}")
        if str(st) == "1" and (positive or not rs.strip()):
            log("ok", "复核通过: 记录有效 ✅")
            return True
        if str(st) == "5":
            log("warn", "复核: 记录已受理但被服务端判无效 (可稍后重试一次)")
            return False
        for _ in range(9):
            if stop_event.is_set():
                return None
            time.sleep(5)
    log("info", "复核暂未终判(服务端异步), 稍后可打开小程序查看")
    return None


# ================================================================ 动作
_LOG_F = None


def _flog(level, text):
    """落盘日志(data_dir/logs/runner_YYYYMMDD.log), 排障必备(EXE 无控制台时仍可查)。"""
    global _LOG_F
    try:
        if _LOG_F is None:
            d = os.path.join(DATA_DIR, "logs")
            os.makedirs(d, exist_ok=True)
            _LOG_F = open(os.path.join(d, "runner_" + time.strftime("%Y%m%d") + ".log"),
                          "a", encoding="utf-8")
        _LOG_F.write(f"[{time.strftime('%H:%M:%S')}] [{level}] {text}\n")
        _LOG_F.flush()
    except Exception:
        pass


def _logged(log):
    """把任意 log 回调包装为 落盘 + 回调 双写(幂等: 已包装不再包)。"""
    if log is None:
        log = print
    if getattr(log, "_file_mirror", False):
        return log

    def _l(level, text):
        _flog(level, text)
        log(level, text)
    _l._file_mirror = True
    _emit_attribution(_l)
    return _l


_ATTR_DONE = False


def _import_license_guard():
    """容错导入 lepao.license_guard（源码/冻结两种形态）；失败返回 None，绝不让主流程崩。"""
    for p in (PKG_ROOT, MEIPASS):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    try:
        from lepao import license_guard as lg
        return lg
    except Exception:
        return None


def _emit_attribution(log):
    """首次日志时输出署名块 + 声明完整性警示（LRL-1.0 第二条 2(b)：运行时可见输出）。"""
    global _ATTR_DONE
    if _ATTR_DONE:
        return
    _ATTR_DONE = True
    lg = _import_license_guard()
    if not lg:
        return
    try:
        for ln in lg.attribution_block():
            log("info", ln)
        for ln in lg.warning_lines():
            log("warn", ln)
    except Exception:
        pass


def wechat_logout_phase(full=True, log=None, relaunch=True):
    """阶段0: 强制退出当前微信登录(杀进程+清登录态缓存) → 重新唤起等待扫码。
    返回 True(已执行)/False。"""
    from lepao import wechat_ctl as WX
    log = _logged(log or print)
    procs = WX.running()
    log("step", f"[阶段0] 强制退出当前微信登录(进程 {len(procs)} 个, "
                f"登录缓存 {WX.cache_count()} 项)…")
    WX.logout(full=full, log=log)
    wx = ""
    if relaunch:
        wx = open_wechat()
        log("cyan", f"微信已{'重新唤起' if wx else '关闭(未找到程序, 请手动双击微信)'} —— "
                    f"请**扫码登录微信**后, 只打开一次小程序「{MINI_PROGRAM_HINT}」到跑步首页")
    return True


def action_logout(log=print, stop_event=None, full=True):
    """手动『退出微信登录』: 杀进程+清登录态(下次扫码), 不启链。"""
    log = _logged(log)
    try:
        wechat_logout_phase(full=full, log=log, relaunch=False)
        log("ok", "已强制退出微信登录(下次使用需重新扫码)")
        return "成功"
    except Exception as e:
        log("err", f"退出登录异常: {e}")
        return "失败"


def _auth_for(key):
    from lepao.accounts import AccountRegistry
    from lepao.auth import AuthState
    reg = AccountRegistry(CONFIG_PATH)
    return reg, AuthState(reg, reg.resolve(key))


def _friendly_err(e) -> str:
    s = str(e)
    low = s.lower()
    if "未能拉起" in s or "eaddrinuse" in low:
        hint = ""
        if _MITM_BROKEN and not _MITM_HEALTH_CACHE.get(_MITM_BROKEN, True):
            hint = ("\n  修复任一: ① py -3 -m pip install \"bcrypt<4.1\""
                    "  ② 在 runner.json 配 {\"mitmweb\":\"良好安装路径\"}")
        return ("mitmweb 启动失败(崩溃/端口占用)。崩溃日志见数据目录"
                f" mitm_stderr.log{hint}")
    if "permission denied" in low or "winerror 5" in low:
        return "权限不足: 修改系统代理需当前用户可写 HKCU(罕见), 或手动关安全软件拦截"
    return s[:300]


def action_full(member=None, log=print, stop_event=None):
    """启动程序: [阶段0 强制退出微信登录] → 起链 → 提示扫码+开「数体智慧体育」
    → 自动抓号(稳定判据) → 自动归位 → 自动提交(顶号看门狗) → 自动复核"""
    log = _logged(log)
    stop_event = stop_event or threading_placeholder()
    from lepao import capture as CAP
    cap = capability()
    if not cap["mitm_healthy"]:
        if cap["mitmweb"]:
            log("err", f"mitmweb 不健康(启动即崩): {cap['mitmweb']}\n"
                       f"  修复: py -3 -m pip install \"bcrypt<4.1\"  或 在本目录"
                       f" runner.json 配置可用 mitmweb 路径")
        else:
            log("err", "缺少 mitmweb: 请安装 mitmproxy(或在本目录 runner.json 配置 mitmweb 路径)")
        return "缺依赖"
    if cap["upstream_mode"] == "proxy" and not cap["clash"]:
        log("warn", f"本机系统代理({cap['upstream']}) 未在听 → 自动**直连模式**(小程序登录与"
                    f"业务同走本机出口, 满足出口同态; 若手机微信当时用了代理则需保持一致)")
    elif cap["upstream_mode"] != "proxy":
        log("info", "出口自动识别: 本机无系统代理 → mitm 直连模式(登录与打卡同出口, 同态成立)")
    else:
        log("info", f"出口自动识别: 沿用本机系统代理 {cap['upstream']}(与微信登录出口一致)")
    reg, auth = _auth_for(member)
    log("ok", f"目标成员: {auth.label} (uid={auth.uid or '待抓号回填'})")
    base = _read_cred().get("token", "")
    guard = None
    try:
        guard, _proc = start_chain(log)
        if LOGOUT_ON_RUN:
            # 修改点1: 先强制退出当前微信登录 → 干净状态重新扫码, 杜绝旧会话/多实例顶号
            wechat_logout_phase(full=True, log=log, relaunch=True)
        else:
            wx = open_wechat()
            log("info", f"已{'尝试自动打开微信(' + wx + ')' if wx else '未找到微信, 请手动打开'}"
                        f" —— 请用电脑微信打开小程序「{MINI_PROGRAM_HINT}」到跑步首页")
        log("info", "等待登录流量(扫码登录并打开小程序后自动识别; 建议此后勿再打开/刷新小程序)…")
        cred = wait_new_token(base, 600, stop_event, log=log)
        if cred is None:
            return "超时未抓号"
        if stop_event.is_set():
            return "已取消"
        res = CAP.sync(cred, member=auth.key)
        if not res:
            log("err", "凭证归位失败: 该成员未登记?(先点『成员管理』登记)")
            return "未登记"
        _key, auth2 = res
        log("ok", f"token 已归位成员[{auth2.label}] → 自动开始提交(勿操作小程序)")
        if stop_event.is_set():
            return "已取消"
        return _submit_run(auth2.key, log, stop_event)
    except Exception as e:
        log("err", _friendly_err(e))
        return "失败"
    finally:
        if guard is not None:
            stop_all(guard, log)


def action_capture(member=None, log=print, stop_event=None):
    """只抓号(首次登记): [可选退出微信登录] → 起链 → 等 token → 归位。"""
    log = _logged(log)
    from lepao import capture as CAP
    cap = capability()
    if not cap["mitm_healthy"]:
        log("err", ("mitmweb 不健康(启动即崩): " + (cap["mitmweb"] or ""))
            if cap["mitmweb"] else "缺少 mitmweb 依赖")
        return "缺依赖"
    reg, auth = _auth_for(member)
    log("ok", f"抓号目标: {auth.label} (学号 {auth.student_num})")
    base = _read_cred().get("token", "")
    guard = None
    try:
        guard, _proc = start_chain(log)
        if LOGOUT_ON_RUN:
            wechat_logout_phase(full=True, log=log, relaunch=True)
        else:
            open_wechat()
            log("info", f"请用电脑微信打开小程序「{MINI_PROGRAM_HINT}」到跑步首页(等待登录流量)…")
        cred = wait_new_token(base, 600, stop_event, log=log)
        if cred is None:
            return "超时未抓号"
        res = CAP.sync(cred, member=auth.key)
        if not res:
            log("err", "凭证归位失败: 成员未登记?")
            return "未登记"
        _key, auth2 = res
        log("ok", f"抓号完成: 成员[{auth2.label}] uid={auth2.uid} token 已就位")
        return "成功"
    except Exception as e:
        log("err", f"异常: {e}")
        return "失败"
    finally:
        if guard is not None:
            stop_all(guard, log)


def _submit_run(member_key, log, stop_event):
    """用成员现有(未消耗)token 跑完整提交链。
    修改点2: 顶号看门狗 —— 提交期间若 凭证.json 出现新 token(用户又打开了小程序),
    立即中断, 防止“提交到一半被新登录顶死 → 服务端不落记录还静默失败”。"""
    from lepao.accounts import AccountRegistry
    from lepao.auth import AuthState
    from lepao.pipeline import Pipeline
    from lepao.state import LocalState
    reg = AccountRegistry(CONFIG_PATH)
    auth = AuthState(reg, member_key)
    if not auth.token or auth.consumed_at:
        log("warn", f"成员[{auth.label}] 无可用 token(未抓号/已消耗) → 需先抓号")
        return "需抓号"
    expect = auth.token
    watch_stop = threading.Event()

    def _rot_watch():
        while not watch_stop.is_set() and not (stop_event and stop_event.is_set()):
            c = _read_cred()
            t = c.get("token")
            if t and t != expect:
                log("err", "检测到新登录流量(顶号!) → 中断本次提交。原因: 自动提交阶段"
                            "小程序又被打开/刷新。请等本次结束, 勿在提交中操作小程序")
                if stop_event:
                    stop_event.set()
                return
            time.sleep(1)
    _flog("step", f"看门狗启动: expect={expect[:8]}… (提交期间 token 变化即中断)")
    tw = threading.Thread(target=_rot_watch, daemon=True)
    tw.start()
    try:
        pol = reg.merged_policy(member_key)
        pipe = Pipeline(auth, LocalState(STATE_PATH), policy=pol,
                        stop_event=stop_event)
        out, code = pipe.run(submit=True)
        if code == 9:
            log("warn", "提交被中断(顶号/用户停止) → 本次未提交。重新点『启动程序』即可")
            return "被顶号"
        if code == 0 and out and out.get("record_id"):
            watch_record(out["record_id"], stop_event, log)
            return "成功"
        if code == 5:
            log("warn", "已受理但判无效, 今日可再试一次")
            return "判无效"
        if code == 3:
            log("warn", "今日额度已满")
        return f"退出码{code}"
    finally:
        watch_stop.set()
        _flog("step", "看门狗关闭")


def action_dry(member=None, log=print, stop_event=None):
    """干跑(零写入): 起链 → 真门/beforeRun/额度/轨迹全流程, 不取STS/不上传/不提交。"""
    log = _logged(log)
    from lepao.accounts import AccountRegistry
    from lepao.auth import AuthState
    from lepao.pipeline import Pipeline
    from lepao.state import LocalState
    reg, auth = _auth_for(member)
    if not auth.token or auth.consumed_at:
        log("warn", f"成员[{auth.label}] 无可用 token → 干跑需先抓号(点『启动程序』)")
        return "需抓号"
    guard = None
    try:
        guard, _proc = start_chain(log)
        pol = reg.merged_policy(auth.key)
        pipe = Pipeline(auth, LocalState(STATE_PATH), policy=pol,
                        stop_event=stop_event)
        _out, code = pipe.run(submit=False)
        log("info", f"干跑结束 退出码={code} (0=该成员在链上全流程可跑)")
        return "成功" if code == 0 else f"退出码{code}"
    except Exception as e:
        log("err", _friendly_err(e))
        return "失败"
    finally:
        if guard is not None:
            stop_all(guard, log)


def threading_placeholder():
    import threading
    return threading.Event()


def action_stop(log=print):
    log = _logged(log)
    stop_all(None, log)
    log("warn", "已停止并复原系统代理")


# ================================================================ 成员管理
def members_table():
    from lepao.accounts import AccountRegistry
    reg = AccountRegistry(CONFIG_PATH)
    out = []
    for i, k in enumerate(reg.keys()):
        a = reg.acc(k)
        au = a.get("auth") or {}
        if not au.get("token"):
            st = "未抓号"
        elif au.get("consumed_at"):
            st = "已消耗"
        else:
            st = "在使用"
        out.append({"key": k, "index": i, "label": a.get("alias") or a.get("name") or k,
                    "uid": a.get("uid") or 0, "student": a.get("student_num") or k,
                    "enabled": bool(a.get("enabled", True)), "state": st,
                    "active": reg.config.get("active") == k,
                    "token": str(au.get("token") or "")[:8]})
    return out


def member_add(name, student_num, alias=None, school_id=837):
    from lepao.accounts import AccountRegistry
    reg = AccountRegistry(CONFIG_PATH)
    key = reg.add(name, student_num, alias=alias, school_id=int(school_id))
    return key


def member_remove(key):
    from lepao.accounts import AccountRegistry
    reg = AccountRegistry(CONFIG_PATH)
    return reg.remove(key)


def member_toggle(key, enabled):
    from lepao.accounts import AccountRegistry
    reg = AccountRegistry(CONFIG_PATH)
    return reg.set_flag(key, enabled)


def member_activate(key):
    from lepao.accounts import AccountRegistry
    reg = AccountRegistry(CONFIG_PATH)
    return reg.set_active(key)


def last_result():
    try:
        from lepao.state import LocalState
        st = LocalState(STATE_PATH)
        return st.last_submission()
    except Exception:
        return None


# 模块加载即落地运行目录 + 设定 LEPAO_* 环境(lepao.capture 等 import 前生效)
setup_environment()
