#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""
mitm_lptiyu_token.py —— mitmproxy addon: 自动提取乐跑 token

功能:
  1. 监听 api2.lptiyu.com 的 loginByCode / 业务请求
  2. 解密请求体, 提取 token/uid/school_id/student_num/card_id
  3. 自动更新部署目录 凭证.json (供打卡流程使用; 路径可用 LEPAO_CRED_FILE 重定向)
  4. 记录所有 lptiyu 请求日志到部署目录 mitm_流量.log（同 LEPAO_BASE_DIR / LEPAO_MITM_LOG）

用法:
  mitmdump -p 8081 -s mitm_lptiyu_token.py --set block_global=false

依赖: mitmproxy + pycryptodome (已装 venv)
"""
import json
import os
import sys
import base64
import datetime
from urllib.parse import parse_qs

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

BASE = os.environ.get("LEPAO_BASE_DIR") or \
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 部署根目录(本文件上级)
# 凭证/日志路径可被环境变量重定向(EXE 运行器把凭证写到自己的数据目录;
# 与 run_core 的 wait_new_token 读取路径保持一致, 操作员/便携双模式)
CRED_FILE = os.environ.get("LEPAO_CRED_FILE") or os.path.join(BASE, "凭证.json")
LOG_FILE = os.environ.get("LEPAO_MITM_LOG") or os.path.join(BASE, "mitm_流量.log")

# ---------------------------------------------------------------- 白名单闸门
# 硬纪律: 本机 mitm 只捕获"已登记成员"的会话（本人 + 已同意代打并登记进
# config.json accounts 表的成员; 键=学号, uid 首次抓号后回填）。
# 白名单源（并集）:
#   1) env  LEPAO_ALLOW_UIDS(逗号分隔 uid) / LEPAO_ALLOW_STUDENTS(逗号分隔学号)
#   2) 部署目录 config.json accounts 表: 每成员 uid + student_num + card_id（v2）
#   3) 旧 v1 平铺 account.uid（迁移前兼容）
# 匹配键: uid 或 学号 或 校园卡号, 任一命中即放行; 身份字段全缺 → 拒绝落盘。
def _read_registry_identity(cfg_path):
    """返回 (uids:set, students:set) 从 v2 config 的 accounts 表。"""
    uids, students = set(), set()
    try:
        with open(cfg_path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception:
        return uids, students
    accounts = cfg.get("accounts") if isinstance(cfg, dict) else None
    if isinstance(accounts, dict):
        for a in accounts.values():
            if not isinstance(a, dict):
                continue
            for k in ("uid", "student_num", "card_id"):
                v = a.get(k)
                if v is None:
                    continue
                s = str(v).strip()
                if k == "uid" and s.isdigit():
                    uids.add(int(s))
                elif s.isdigit():
                    students.add(s)
    elif isinstance(cfg, dict) and cfg.get("account"):   # v1 平铺兼容
        a = cfg.get("account", {})
        if str(a.get("uid") or "").isdigit():
            uids.add(int(a["uid"]))
        sn = str(a.get("student_num") or "").strip()
        if sn.isdigit():
            students.add(sn)
    return uids, students


def _allow_identity():
    uids, students = set(), set()
    own = os.environ.get("LEPAO_ALLOW_UIDS", "")
    for x in own.split(","):
        x = x.strip()
        if x.isdigit():
            uids.add(int(x))
    own_s = os.environ.get("LEPAO_ALLOW_STUDENTS", "")
    for x in own_s.split(","):
        x = x.strip()
        if x.isdigit():
            students.add(x)
    # 真源: 部署目录 config.json 的 accounts 表（多副本取并集; LEPAO2_CONFIG 优先）
    cands = []
    env_cfg = os.environ.get("LEPAO2_CONFIG", "")
    if env_cfg:
        cands.append(env_cfg)
    cands += [os.path.join(BASE, "config.json"),
              os.path.join(BASE, "..", "config.json")]
    for p in cands:
        u, s = _read_registry_identity(p)
        uids |= u
        students |= s
    return uids, students


ALLOW_UIDS, ALLOW_STUDENTS = _allow_identity()
_refused = set()


def identity_allowed(cred):
    """True=允许落盘。凭据身份(uid/学号/卡号)至少一项命中白名单; 无身份字段→拒绝。"""
    u = (cred or {}).get("uid")
    sn = str((cred or {}).get("student_num") or "").strip()
    cd = str((cred or {}).get("card_id") or "").strip()
    if u not in (None, "", 0):
        try:
            if int(u) in ALLOW_UIDS:
                return True
        except (TypeError, ValueError):
            pass
    for v in (sn, cd):
        if v.isdigit() and v in ALLOW_STUDENTS:
            return True
    return False

AES_KEY = b"Wet2C8d34f62ndi3"
AES_IV = b"K6iv85jBD8jgf32D"


def decrypt_data(data_b64: str):
    try:
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        pt = unpad(cipher.decrypt(base64.b64decode(data_b64)), 16)
        return pt.decode("utf-8")
    except Exception:
        return None


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def save_cred(cred):
    """保存凭证。

    删除过期逻辑: 旧实现无条件合并旧值并把 update_time 刷成当前时间,
    导致"token 从未变化"也被记成"凭证已更新"的假象。
    现在: 仅当 token 值与缓存不同(真正换新)或缓存缺失时才写入,
    并记录 update_time; 相同则跳过(避免污染更新时间戳)。
    """
    old = {}
    if os.path.exists(CRED_FILE):
        try:
            with open(CRED_FILE, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            pass
    new_token = (cred or {}).get("token")
    old_token = old.get("token")
    # ---- 白名单闸门: 未登记成员一律不落盘（他人活动凭据, 见上方注释）----
    if not identity_allowed(cred):
        uid = cred.get("uid")
        if uid not in _refused:
            _refused.add(uid)
            masked = str(new_token or "")[:6]
            print(f"[token] 拒绝落盘: uid={uid} 学号={cred.get('student_num')} "
                  f"不在白名单(uid={sorted(ALLOW_UIDS)}, "
                  f"学号={sorted(ALLOW_STUDENTS)[:6]}{'…' if len(ALLOW_STUDENTS) > 6 else ''}) "
                  f"(token {masked}…未保存; 仅登记成员可捕获 —— 先 `account add` 登记)")
            log(f"REFUSED uid={uid} student={cred.get('student_num')} 非白名单, 未落盘")
        return
    if new_token and old_token and new_token == old_token:
        # token 未变化: 只补齐缺失的非 token 字段, 不刷新 update_time
        merged = {**old, **{k: v for k, v in cred.items() if v is not None and k != "token"}}
        with open(CRED_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        return
    merged = {**old, **{k: v for k, v in cred.items() if v is not None}}
    merged["source"] = "mitmproxy_auto"
    merged["update_time"] = datetime.datetime.now().isoformat()
    with open(CRED_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    changed = "新" if (not old_token or old_token != new_token) else "同"
    print(f"[token] 凭证已更新({changed}): token={merged.get('token','')[:12]}... uid={merged.get('uid')}")
    log(f"凭证已更新({changed}): token={merged.get('token','')[:12]}... uid={merged.get('uid')}")


def extract_from_request(flow):
    """从请求体解密提取凭证"""
    body = flow.request.get_text() if flow.request.content else ""
    if "data=" not in body:
        return None
    try:
        qs = parse_qs(body)
        plain = json.loads(decrypt_data(qs["data"][0]))
    except Exception:
        return None
    cred = {}
    for k in ("uid", "token", "school_id", "student_num", "card_id", "term_id"):
        if plain.get(k) not in (None, "", 0):
            cred[k] = plain[k]
    return cred or None


def extract_from_response(flow):
    """从 loginByCode 响应提取 access_token"""
    try:
        rj = json.loads(flow.response.get_text())
    except Exception:
        return None
    if not isinstance(rj, dict):
        return None
    data = rj.get("data")
    if not isinstance(data, str):
        return None
    plain = decrypt_data(data)
    if not plain:
        return None
    try:
        obj = json.loads(plain)
    except Exception:
        return None
    cred = {}
    td = obj.get("token_data") if isinstance(obj, dict) else None
    if isinstance(td, dict) and td.get("access_token"):
        cred["token"] = td["access_token"]
        # 2026-09-08 flows(2) 实证: token_data 带 30 天期 refresh_token (P17 在侦)
        if td.get("refresh_token"):
            cred["refresh_token"] = td["refresh_token"]
        if td.get("refresh_expire"):
            cred["refresh_expire"] = td["refresh_expire"]
    if isinstance(obj, dict) and obj.get("uid"):
        cred["uid"] = obj["uid"]
    return cred or None


def is_lptiyu(flow):
    """判断是否为乐跑流量: 域名或 IPv6 别名(api2/data 均解析到 CDN IPv6)"""
    host = flow.request.pretty_host or ""
    path = flow.request.path or ""
    if "lptiyu" in host:
        return True
    # 业务 API 特征
    if path.startswith("/v3/api.php/"):
        return True
    # 静态资源(图片/轨迹文件)
    if path.startswith("/Public/Upload/"):
        return True
    return False


def request(flow):
    if not is_lptiyu(flow):
        return
    try:
        path = flow.request.path
        host = flow.request.pretty_host
        print(f"[req] {flow.request.method} {host}{path}")
        log(f"REQ {flow.request.method} {host}{path}")
        cred = extract_from_request(flow)
        if cred and cred.get("token"):
            save_cred(cred)
    except Exception as e:
        print(f"[req-error] {e}")


def response(flow):
    if not is_lptiyu(flow):
        return
    try:
        path = flow.request.path
        host = flow.request.pretty_host
        status = flow.response.status_code
        print(f"[resp] {path} -> {status}")
        log(f"RESP {host}{path} -> {status}")
        if "loginByCode" in path or "loginAuth" in path:
            cred = extract_from_response(flow)
            if cred:
                save_cred(cred)
    except Exception as e:
        print(f"[resp-error] {e}")
