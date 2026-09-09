# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""lepao.capture —— 成员 token 抓取会话（被动 mitm 模型, v2.2 多成员化）

背景（提交日实测）:
  - v3 鉴权走 mitm+Clash 链才与真机同态（直连会被业务门 101）
  - token = 即抓即用消耗品（单活动会话）→ 抓取要"开着 mitm 等小程序重登",
    拿到即按**凭证身份归位**到对应成员槽（uid/学号/校园卡三键匹配 accounts 表）,
    一次抓取只保该成员一次打卡。

职责:
  1) start()  : 拉起 mitmweb(8081, addon 钩子) → 上游链(自动识别本机系统代理, 无代理则直连) → 系统代理指向 8081
  2) wait()   : 轮询 凭证.json, 等新 token（或 set-token 人工注入口）
  3) sync()   : 凭证.json（含身份+refresh_token）→ config.json 对应成员槽
  4) stop()   : 杀 mitmweb → 系统代理复原
  5) run()    : start→wait→sync, 供 CLI 一键（须先 --member 指明目标/已登记）
"""
import json
import os
import subprocess
import sys
import time
import winreg
import urllib.request

from .accounts import AccountRegistry

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
MITM_EXE = os.environ.get("LEPAO_MITM_EXE") or os.path.expandvars(
    r"%LOCALAPPDATA%\Programs\Python\Python311\Scripts\mitmweb.exe")


def _find_addon() -> str:
    """抓号 addon 路径解析(跨机/跨目录部署):
    环境变量 LEPAO_MITM_ADDON → 本部署目录内 工具\ → 数据目录根 mitm_addon.py"""
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _cfg_dir = ""
    _ecfg = os.environ.get("LEPAO2_CONFIG", "")
    if _ecfg:
        _cfg_dir = os.path.dirname(_ecfg)
    cands = [os.environ.get("LEPAO_MITM_ADDON", ""),
             os.path.join(_root, "工具", "mitm_lptiyu_token.py"),
             os.path.join(_root, "mitm_addon.py")]
    if _cfg_dir:
        cands.append(os.path.join(_cfg_dir, "mitm_addon.py"))
    for p in cands:
        if p and os.path.exists(p):
            return p
    return cands[1]   # 兜底: 部署目录内路径(缺失时 start() 会给出明确报错)


ADDON = _find_addon()
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRED = os.environ.get("LEPAO_CRED_FILE") or os.path.join(_ROOT_DIR, "凭证.json")
LEPAO2_CONFIG = os.environ.get("LEPAO2_CONFIG") or \
    os.path.join(_ROOT_DIR, "config.json")
LISTEN = ("0.0.0.0", int(os.environ.get("LEPAO_MITM_PORT") or 8081))
# 上游默认值: 环境变量显式指定 > 自动识别本机当前系统代理(见 detect_system_upstream)
UPSTREAM = os.environ.get("LEPAO_CLASH_UPSTREAM")
WEB_PORT = int(os.environ.get("LEPAO_MITM_WEB_PORT") or 8082)
WEB_PASS = os.environ.get("LEPAO_MITM_WEB_PASS") or "lepao"
FLOWS = os.environ.get("LEPAO_FLOWS_DIR") or os.path.join(_ROOT_DIR, "flows")
# -w 语义是"流文件"不是目录: 误传目录会让 mitm 启动即 Errno 13 崩
if os.path.isdir(FLOWS):
    FLOWS = os.path.join(FLOWS, "flows.mitm")

# 备选 config 位置（mitm addon 与 cli 可能从不同副本运行, 白名单取并集）
_CFG_CANDIDATES = [LEPAO2_CONFIG,
                   os.path.join(_ROOT_DIR, "config.json"),
                   os.path.join(_ROOT_DIR, "..", "config.json")]


def registry_paths():
    seen = []
    for p in _CFG_CANDIDATES:
        if p and p not in seen and os.path.exists(p):
            seen.append(p)
    return seen


class ProxyGuard:
    """系统代理备份/设置/复原（HKCU; 原值持久化到 proxy_backup.json, 跨进程可复原）"""

    BACKUP = os.path.join(os.path.dirname(LEPAO2_CONFIG), "proxy_backup.json")

    def __init__(self):
        self.saved = None

    def read(self):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as k:
            enable = winreg.QueryValueEx(k, "ProxyEnable")[0]
            server = winreg.QueryValueEx(k, "ProxyServer")[0]
        return enable, server

    def write(self, enable, server):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, int(enable))
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
        # 通知 WinINet 刷新（IE/部分 App 生效; Chromium 系走 winhttp 感知有延迟）
        try:
            import ctypes
            INTERNET_OPTION_SETTINGS_CHANGED = 39
            INTERNET_OPTION_REFRESH = 37
            ctypes.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
            ctypes.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
        except Exception:
            pass

    def set_mitm(self):
        if self.saved is None:
            self.saved = self.read()
            # 已是 mitm 值则不覆盖备份（防二次 start 把备份刷成 8081）
            if self.saved[1] == f"127.0.0.1:{LISTEN[1]}":
                if os.path.exists(self.BACKUP):
                    b = json.load(open(self.BACKUP, encoding="utf-8"))
                    self.saved = (b["enable"], b["server"])
            json.dump({"enable": self.saved[0], "server": self.saved[1]},
                      open(self.BACKUP, "w", encoding="utf-8"))
        self.write(1, f"127.0.0.1:{LISTEN[1]}")
        print(f"[capture] 系统代理 → 127.0.0.1:{LISTEN[1]} (原值已存: {self.saved[1]})")

    def restore(self):
        if self.saved is None and os.path.exists(self.BACKUP):
            b = json.load(open(self.BACKUP, encoding="utf-8"))
            self.saved = (b["enable"], b["server"])
        if self.saved:
            self.write(*self.saved)
            print(f"[capture] 系统代理已复原 → {self.saved[1]}")
            self.saved = None


def detect_system_upstream() -> str:
    """自动识别本机当前系统代理（= 微信小程序登录所用出口）。
    启用 → 'http://<ProxyServer>'(多目标取第一个); 禁用/不可读 → ''(直连模式)。
    链路上游跟随它, 业务出口与登录出口按定义同态: 机器现在用什么就走什么。
    显式覆盖: 环境变量 LEPAO_CLASH_UPSTREAM(可为 ''=强制直连) 或 runner.json clash_upstream。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as k:
            enable = winreg.QueryValueEx(k, "ProxyEnable")[0]
            if not enable:
                return ""
            server = (winreg.QueryValueEx(k, "ProxyServer")[0] or "").strip()
            if not server or server.lower() in ("<local>", "localhost", "none"):
                return ""
            server = server.split(";")[0].strip()
            return server if "://" in server else "http://" + server
    except Exception:
        return ""


def mitm_running():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{LISTEN[1]}/", timeout=2):
            return True
    except Exception:
        import socket
        s = socket.socket()
        s.settimeout(2)
        try:
            s.connect(("127.0.0.1", LISTEN[1]))
            return True
        except Exception:
            return False
        finally:
            s.close()


def start(proxy_guard: ProxyGuard = None, force=False):
    """拉起 mitmweb（可选链 Clash 上游）+ 系统代理指向。返回 (proc, guard)。
    - UPSTREAM 为空 → 直连模式（mitm 不出网走本机默认出口, 与小程序登录出口同态即可）
    - 端口占用: 健康 mitm 复用; 非 mitm 残留/force 杀 mitmweb 再起
    - 失败异常带 stderr 日志尾部(定位崩溃原因, 如 passlib/bcrypt 不兼容)
    """
    guard = proxy_guard or ProxyGuard()
    if mitm_running():
        if not force:
            print(f"[capture] 8081 已有进程 → 仅设系统代理")
            guard.set_mitm()
            return None, guard
        # force: 杀残留 mitmweb 再起（防 EADDRINUSE 假死）
        subprocess.run(["powershell", "-c",
                        "Get-Process mitmweb -ErrorAction SilentlyContinue | Stop-Process -Force"])
        time.sleep(1.2)
    args = [MITM_EXE, "-p", str(LISTEN[1]), "--listen-host", LISTEN[0],
            "--web-port", str(WEB_PORT), "--web-host", "127.0.0.1",
            "--set", f"console_password={WEB_PASS}", "-s", ADDON,
            "--set", "block_global=false", "-w", FLOWS]
    # 上游在调用时刻解析: env 显式指定(含 ''=强制直连) > 自动识别本机当前系统代理
    if "LEPAO_CLASH_UPSTREAM" in os.environ:
        upstream = os.environ["LEPAO_CLASH_UPSTREAM"]
    else:
        upstream = detect_system_upstream()
    if upstream:
        args += ["--mode", f"upstream:{upstream}"]
    err_path = os.path.join(os.path.dirname(FLOWS) if FLOWS else
                            os.path.dirname(LEPAO2_CONFIG),
                            "mitm_stderr.log")
    try:
        errf = open(err_path, "a", encoding="utf-8", errors="replace")
        errf.write(f"\n==== start {time.strftime('%F %T')} ====\n")
        errf.flush()
    except Exception:
        errf = subprocess.DEVNULL
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=errf,
                            creationflags=0x08)  # CREATE_NO_WINDOW
    for _ in range(12):
        time.sleep(0.7)
        if mitm_running():
            break
        if proc.poll() is not None:      # 已崩溃退出 → 早停
            break
    else:
        pass
    if not mitm_running():
        tail = ""
        try:
            if errf is not subprocess.DEVNULL:
                errf.close()
            with open(err_path, encoding="utf-8", errors="replace") as f:
                tail = "".join(f.readlines()[-30:])[-1500:]
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        raise RuntimeError(
            f"mitmweb 未能拉起 (exe={MITM_EXE})\n"
            f"stderr 尾部:\n{tail or '(空; 检查端口/防火墙)'}")
    try:
        if errf is not subprocess.DEVNULL:
            errf.close()
    except Exception:
        pass
    guard.set_mitm()
    print(f"[capture] mitmweb 就绪 (upstream={upstream or '(直连模式)'}), console=: {WEB_PORT}")
    # 起链后立刻自检根证书：未受信 = mitm 能起但小程序拒绝其签发证书 → 永远等不到号
    try:
        from . import certctl
        print("[capture] " + certctl.summary_line())
        _st = certctl.status()
        if not _st["trusted"]:
            print("[capture] 修复: py -3 cli.py cert --install  "
                  "(或双击 %USERPROFILE%\\.mitmproxy\\mitmproxy-ca-cert.cer → 受信任的根证书颁发机构)")
    except Exception:
        pass
    return proc, guard


def wait(timeout_s=420, base_token=None, interval=2, stable_reads=6):
    """等新 token 落到 凭证.json（小程序冷启动必然 loginByCode）。
    返回 (cred_dict, changed:bool)。超时 changed=False。
    稳定判据: 新 token 连续 stable_reads 次不变才收——小程序双冷启动会在
    ~8s 内轮换 loginByCode(实测 11:11 4E47→505ACCFB), 中间值=死会话→101。"""
    if base_token is None:
        try:
            base_token = json.load(open(CRED, encoding="utf-8-sig"))["token"]
        except Exception:
            base_token = ""
    print(f"[capture] 等 loginByCode 新 token (基准 {base_token[:8] if base_token else '空'}…, "
          f"超时 {timeout_s}s) —— 请该成员打开微信「数体智慧体育」小程序至跑步首页")
    t0 = time.time()
    cur, same = None, 0
    while time.time() - t0 < timeout_s:
        try:
            cred = json.load(open(CRED, encoding="utf-8-sig"))
        except Exception:
            time.sleep(interval)
            continue
        tok = cred.get("token")
        if tok and tok != base_token:
            if tok == cur:
                same += 1
            else:
                cur, same = tok, 1
            if same >= stable_reads:
                print(f"[capture] 新 token 稳定到手: {tok[:8]}… "
                      f"uid={cred.get('uid')} "
                      f"refresh={'有' if cred.get('refresh_token') else '无'} "
                      f"({time.time() - t0:.0f}s)")
                return cred, True
        time.sleep(interval)
    print("[capture] 超时: 未捕获新 token —— 三查: ①系统代理已指 mitm(8081) ②微信已重新扫码登录"
          "并打开小程序 ③**mitm 根证书受信**(不受信=TLS 被拒, 永远没有 loginByCode 流量; "
          "自检: py -3 cli.py cert, 安装: py -3 cli.py cert --install)")

    try:
        return json.load(open(CRED, encoding="utf-8-sig")), False
    except Exception:
        return {}, False


def _open_registry():
    return AccountRegistry(LEPAO2_CONFIG)


def sync(cred=None, member=None):
    """凭证.json → config.json **对应成员槽**（身份三键归位）。
    守卫: 同一成员"token 与已消耗者相同" → 不重激活（防 mitm 旧业务流写回）;
    凭证身份匹配不到任何登记成员 → 拒绝写入（防误归位到活动成员）。
    返回 (member_key, auth_dict) 或 None。"""
    cred = cred or (json.load(open(CRED, encoding="utf-8-sig"))
                    if os.path.exists(CRED) else {})
    if not cred.get("token"):
        print("[capture] sync 跳过: 凭证无 token")
        return None
    reg = _open_registry()
    key = reg.key_of_cred(cred)
    if key is None and member:
        try:
            key = reg.resolve(member)
        except Exception:
            key = None
    if key is None:
        if os.environ.get("LEPAO_SYNC_ACTIVE_FALLBACK") == "1":
            key = reg.resolve()
        else:
            print(f"[capture] sync 拒绝: 凭证身份 "
                  f"(uid={cred.get('uid')}, student={cred.get('student_num')}) "
                  f"未匹配任何登记成员 → 不写入。先 `account add` 登记该成员 "
                  f"(LEPAO_SYNC_ACTIVE_FALLBACK=1 可强制归活动成员)")
            return None
    reg.fill_identity(key, cred)          # 首次抓号回填 uid/school/card
    from .auth import AuthState
    auth = AuthState(reg, key)
    if (cred.get("token") == auth.token and auth.consumed_at
            and os.environ.get("LEPAO_SYNC_FORCE") != "1"):
        print(f"[capture] sync 跳过: 成员[{auth.label}] token 与已消耗者相同 "
              f"({cred['token'][:8]}…), 不重激活 (LEPAO_SYNC_FORCE=1 可强制)")
        return key, auth
    ut = cred.get("update_time") or ""
    auth.set_token(cred["token"],
                   issued_at=(ut.split(".")[0] if ut else
                              time.strftime("%Y-%m-%dT%H:%M:%S")),
                   source=cred.get("source", "mitm_capture"),
                   refresh_token=cred.get("refresh_token"),
                   refresh_expire=cred.get("refresh_expire"))
    print(f"[capture] 已同步成员[{auth.label}] (uid={auth.uid}, "
          f"token={auth.token[:8]}…, issued={auth.issued_at})")
    return key, auth


def stop(proc, guard: ProxyGuard):
    """关 mitmweb + 复原系统代理。"""
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        print("[capture] mitmweb 已关闭")
    guard.restore()


def run(wait_timeout=420, member=None):
    """一键: start → wait → sync(归位 member 或按凭证身份)。
    成功返回 (key, auth), 否则 None（代理/进程保持, 便于重试）。"""
    g = ProxyGuard()
    proc, guard = start(g)
    try:
        cred, changed = wait(wait_timeout)
        if not changed:
            return None
        return sync(cred, member=member)
    except Exception as e:
        print(f"[capture] 异常: {e}", file=sys.stderr)
        return None
    finally:
        stop(proc, guard)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    g = ProxyGuard()
    if cmd == "start":
        proc, guard = start(g)
        input("mitm 已启动, 等小程序重登完按回车结束并同步(或直接 Ctrl+C 后跑 sync)…")
        cred, ch = wait(timeout_s=5)
        if ch or cred.get("token"):
            sync(cred)
        stop(proc, guard)
    elif cmd == "sync":
        sync(member=sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "stop":
        subprocess.run(["powershell", "-c",
                        "Get-Process mitmweb -ErrorAction SilentlyContinue | Stop-Process -Force"])
        g.restore()
    else:
        ok = run(member=sys.argv[2] if len(sys.argv) > 2 else None)
        sys.exit(0 if ok else 1)
