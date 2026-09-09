# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""lepao.wechat_ctl —— 强制退出当前微信登录（进程 + 登录态缓存）

背景（2026-09-09 失败链分析）:
  - 打卡失败根因之一是"反复 loginByCode 相互顶号": PC 微信/小程序多实例残留,
    每次小程序冷启动都会静默换号; 任务用的 token 被后续登录顶死(101)。
  - 解法(用户指令): 打卡前**强制退出当前微信登录** —— 结束全部微信进程
    (Weixin/WeChat/WeChatAppEx…), 并清除登录态缓存
    (%APPDATA%\\Tencent\\xwechat\\radium\\users\\*),
    使下次启动必须重新扫码 → 从"干净登录"开始, 之后只开一次小程序、
    立即提交, 不再被旧会话/多实例干扰。

用法(模块):
  from lepao import wechat_ctl as WX
  WX.status()          # 运行进程/缓存数量
  WX.logout(full=True) # 杀进程 + 清缓存(强制重新扫码); full=False 只杀进程
"""
import os
import shutil
import subprocess

PROC_NAMES = ("Weixin.exe", "WeChat.exe", "WeChatApp.exe", "WeChatAppEx.exe")
RADIUM_USERS = os.path.join(os.environ.get("APPDATA", ""), "Tencent",
                            "xwechat", "radium", "users")


def running():
    """返回运行中的微信相关进程 [(name, pid), …]"""
    out = []
    for name in PROC_NAMES:
        try:
            r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}",
                                "/FO", "CSV", "/NH"],
                               capture_output=True, text=True, timeout=10)
        except Exception:
            continue
        for line in r.stdout.splitlines():
            parts = [p.strip().strip('"') for p in line.split('","')]
            if parts and parts[0].lower() == name.lower() and len(parts) > 1:
                out.append((parts[0], parts[1]))
    return out


def kill_all():
    """结束全部微信进程。返回被杀进程名列表。"""
    killed = []
    for name in PROC_NAMES:
        try:
            r = subprocess.run(["taskkill", "/F", "/IM", name],
                               capture_output=True, text=True, timeout=20)
            if "SUCCESS" in r.stdout or "成功" in r.stdout:
                killed.append(name)
        except Exception:
            pass
    return killed


def cache_count():
    """登录态缓存目录下账号缓存数量。"""
    try:
        if not os.path.isdir(RADIUM_USERS):
            return 0
        return len([e for e in os.listdir(RADIUM_USERS)])
    except Exception:
        return 0


def clear_cache():
    """清除微信登录态缓存(xwechat radium users)。返回清除条目数。"""
    removed = 0
    try:
        if os.path.isdir(RADIUM_USERS):
            for entry in os.listdir(RADIUM_USERS):
                p = os.path.join(RADIUM_USERS, entry)
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
                    removed += 1
                except Exception:
                    pass
    except Exception:
        pass
    return removed


def status():
    return {"procs": running(), "cache": cache_count()}


def relaunch_wechat() -> str:
    """重新唤起微信(返回启动的路径; 找不到返回 '')。"""
    cands = [
        # 微信 4.x (Weixin)
        r"C:\Program Files\Tencent\Weixin\Weixin.exe",
        r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tencent\Weixin\Weixin.exe"),
        # 微信 3.x (WeChat)
        r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe",
        r"C:\Program Files\Tencent\WeChat\WeChat.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tencent\WeChat\WeChat.exe"),
    ]
    for p in cands:
        if p and os.path.exists(p):
            try:
                os.startfile(p)
                return p
            except Exception:
                pass
    return ""


def logout(full=True, log=None):
    """强制退出当前微信登录。
    full=True: 杀进程 + 清登录态缓存(下次须扫码登录); False: 仅杀进程。
    返回 (killed:list, cleared:int)。"""
    def _l(msg):
        if log:
            log("info", msg)
    procs = running()
    if not procs:
        _l("[微信退出] 微信进程未运行")
    else:
        n = len(procs)
        _l(f"[微信退出] 结束 {n} 个微信进程({', '.join(sorted(set(p for p, _ in procs)))})…")
        killed = kill_all()
        _l(f"[微信退出] 已结束: {', '.join(killed) if killed else '(无)'}")
    cleared = 0
    if full:
        c0 = cache_count()
        if c0:
            cleared = clear_cache()
            _l(f"[微信退出] 已清除登录态缓存 {cleared} 项(下次需重新扫码登录)")
        else:
            _l("[微信退出] 无登录态缓存可清(已是未登录态)")
    return killed if procs else [], cleared
