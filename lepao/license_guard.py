# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
"""lepao.license_guard —— 声明完整性校验与署名文本唯一来源（LRL-1.0 第二条/第三条配套）

设计目标（刻意克制，不是 DRM）：
1. 署名可见性：CLI 横幅、GUI 窗口标题/关于框、日志首行都取本模块的 attribution_block()，
   满足 LRL-1.0 第二条"三处引用义务"中"运行时可见输出"与"关于界面"两处。
2. 声明完整性：启动时计算随包 LICENSE 文件的 SHA-256，与本模块内固定的可接受指纹比对；
   缺失/被改写 → 在日志与界面打印警示（并写入 selfcheck.json），**不阻断功能**：
   合法使用者可能自行整理目录，阻断只会伤害他们；而警示与构建戳可作为作者主张权利的证据。
3. 零联网：本模块不发起任何网络请求，不回传任何信息（SECURITY.md 有对应承诺）。

可覆盖：LEPAO_LICENSE_FILE 指定 LICENSE 路径；LEPAO_LICENSE_CHECK=0 关闭校验（仅静音，不改署名）。
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

__version__ = "1.0.0"

# ---- 署名常量（唯一来源；改仓库地址请同时更新 release.meta.json 并跑 tools/apply_headers.py --url） ----
AUTHOR = "dan-cun"
UPSTREAM = "https://github.com/dan-cun/lepao3"
LICENSE_ID = "LRL-1.0"
LICENSE_NAME = "乐跑研究协议 1.0"
CONTACT_QQ = "1224145544"
COPYRIGHT = "Copyright (c) 2026 dan-cun"
DISCLAIMER_SHORT = "仅供学习 · 不可牟利 · 再发布须署名引用且声明不得删除 · 风险自负"

# 可接受的 LICENSE 文本指纹（tools/apply_headers.py --stamp 后由维护者刷新；LRL-1.0 第三条第 1 款）
ACCEPTED_LICENSE_SHA256 = {
    "2f34fb00c61f065548c0530431dfb5a5e5bfc14a36e5f2793102bc1ac138bc62",  # LRL-1.0 v1.0.0 (2026-09-09, lepao3)
}

_STATE: dict = {"checked": False, "status": "unchecked", "detail": "", "path": ""}


def _candidate_paths() -> list:
    """按优先级找随包 LICENSE：显式 env → EXE 同目录 → 冻结 _MEIPASS → 包上级 → 当前目录。"""
    out: list = []
    env = os.environ.get("LEPAO_LICENSE_FILE", "").strip()
    if env:
        out.append(Path(env))
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        exe = Path(sys.executable)
        out += [exe.parent / "LICENSE", exe.parent / "LICENSE.md"]
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            out.append(Path(bundle) / "LICENSE")
    here = Path(__file__).resolve()
    for base in (here.parents[1], here.parents[2], Path.cwd()):
        out += [base / "LICENSE", base / "LICENSE.md"]
    seen, uniq = set(), []
    for p in out:
        try:
            k = str(p.resolve())
        except Exception:
            k = str(p)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check_license(silent_env_ok: bool = True) -> dict:
    """校验随包 LICENSE 完整性，返回 {status, path, sha256, detail}。

    status: ok | missing | modified(被改写) | skipped(env 关闭)
    """
    if _STATE["checked"]:
        return dict(_STATE)
    st = {"status": "ok", "path": "", "sha256": "", "detail": ""}
    if silent_env_ok and os.environ.get("LEPAO_LICENSE_CHECK", "").strip() in ("0", "off", "false"):
        st.update(status="skipped", detail="LEPAO_LICENSE_CHECK 关闭")
        _STATE.update(checked=True, **st)
        return dict(_STATE)
    tried = []
    for p in _candidate_paths():
        tried.append(str(p))
        if not p.is_file():
            continue
        digest = sha256_file(p)
        st["path"] = str(p)
        st["sha256"] = digest
        known = {k for k in ACCEPTED_LICENSE_SHA256 if k != "REPLACE_AT_RELEASE"}
        if not known:
            st["status"] = "ok"            # 发布前尚未固化指纹：存在即通过
            st["detail"] = "指纹表未固化（开发态）"
        elif digest in known:
            st["status"] = "ok"
        else:
            st["status"] = "modified"
            st["detail"] = ("LICENSE 内容与本构建的授权文本不一致（疑似被改写/替换为其他许可）。"
                            " 原始出处：" + UPSTREAM + "（" + LICENSE_ID + "）")
        break
    else:
        st["status"] = "missing"
        st["detail"] = ("未找到随包 LICENSE 声明文件；已尝试：" + " | ".join(tried[:4]) +
                        " …… 按 LRL-1.0 第三条第 3 款，删除声明即终止授权。完整条款见原始仓库。")
    _STATE.update(checked=True, **st)
    return dict(_STATE)


def warning_lines() -> list:
    """需要打印到日志/界面的警示行（status=ok/skipped 时为空）。"""
    st = check_license()
    if st["status"] in ("ok", "skipped"):
        return []
    return [
        "!" * 68,
        "[声明完整性警示] " + st["detail"],
        "[出处] {} · {} · {}（{}）".format(AUTHOR, UPSTREAM, LICENSE_NAME, LICENSE_ID),
        "[提示] 本软件仅供学习研究，不可牟利；再发布须保留全部声明并署名引用原始仓库。"
        " 技术讨论 QQ {}".format(CONTACT_QQ),
        "!" * 68,
    ]


def build_id() -> str:
    """构建戳（tools/apply_headers.py --stamp 生成）；缺失时返回 dev 标识。"""
    try:
        from ._buildstamp import BUILD_ID  # type: ignore
        return str(BUILD_ID)
    except Exception:
        return "LRL1.0-dev"


def version() -> str:
    try:
        from ._buildstamp import VERSION  # type: ignore
        return str(VERSION)
    except Exception:
        return __version__


def attribution_block(prefix: str = "") -> list:
    """三处引用义务共用的署名块（README 一处由 verify_release.py 校验，不在这里生成）。"""
    return [
        "{}{} · {}".format(prefix, COPYRIGHT, AUTHOR),
        "{}原始仓库：{} · 许可证：{}（{}）—— 非商业授权软件".format(
            prefix, UPSTREAM, LICENSE_NAME, LICENSE_ID),
        "{}{} · 构建 {} · 技术讨论 QQ {}".format(
            prefix, DISCLAIMER_SHORT, build_id(), CONTACT_QQ),
    ]


def print_banner(log=print) -> None:
    """运行时可见署名横幅（CLI 启动、日志首行、GUI 启动横幅共用）。"""
    for ln in attribution_block():
        log(ln)
    for ln in warning_lines():
        log(ln)


def summary() -> dict:
    """给 selfcheck.json / 环境自检用。"""
    st = check_license()
    return {
        "license_id": LICENSE_ID,
        "license_name": LICENSE_NAME,
        "upstream": UPSTREAM,
        "author": AUTHOR,
        "contact_qq": CONTACT_QQ,
        "build_id": build_id(),
        "license_status": st["status"],
        "license_path": st["path"],
        "license_sha256": st["sha256"][:16],
    }
