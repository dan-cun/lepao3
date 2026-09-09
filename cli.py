#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
"""Lepao CLI（乐跑打卡助手）—— 多成员体系统一入口（v2.2: 可完成"本人 + 已同意成员"的每日打卡）

本软件按「乐跑研究协议 1.0（LRL-1.0）」授权：仅供学习 · 不可牟利 ·
再发布须署名引用 https://github.com/dan-cun/lepao3 且不得删除任何声明。全文见 LICENSE。

成员模型: config.json v2 accounts 表登记每个成员(键=学号), 每成员独立
token(单活动会话消耗品)/consumed/refresh/策略。命令默认操作 active 成员,
任何命令可加成员选择器: --member <学号|别名|uid> (等价 --account/--uid/--name)。

每日打卡闭环（一条命令）:
  py -3 cli.py run                    # 为 active 成员: capture(等开小程序)→提交→watch
  py -3 cli.py run --member 某人       # 指定成员; --dry 只 build 零写入
  py -3 cli.py batch --submit         # 全部 enabled 成员逐个提交(有可用 token 的)
                                     # (默认 --dry 只出计划表; --cap 缺号者逐个等抓)

成员管理:
  py -3 cli.py account list
  py -3 cli.py account add <名字> <学号> [--alias A] [--uid N] [--school N]
  py -3 cli.py account use <学号|别名|uid>      # 设为 active
  py -3 cli.py account rm/enable/disable/show <成员>

单步:
  py -3 cli.py status [--all]         # 成员 token 状态(消耗品模型) + 真门探测
  py -3 cli.py capture [--member X]   # 仅抓 token: 拉起 mitm 链→等该成员登录→归位
  py -3 cli.py quota [--member X]     # 该成员当日额度 + 跑区配置（只读, 真门）
  py -3 cli.py build/submit [--member X] [--style S] [--zone Z]
  py -3 cli.py watch <record_id> [--member X] [--times N]
  py -3 cli.py set-token <token> [ISO] [--member X]
  py -3 cli.py h5-session [--member X] / h5-login <学号> <密码>
  py -3 cli.py wechat-logout / wechat-status   # 强制退出微信登录(清登录态) / 状态
  py -3 cli.py license                  # 打印许可证要点/署名/声明指纹/构建戳

注意: 业务调用需 mitm 在链上（config.environment.api_proxy=127.0.0.1:8081 且
mitmweb 运行; 直连被业务门 101——节点局部会话）。run/capture/batch --cap 自动拉起。
run/capture 默认**先强制退出当前微信登录**(杀进程+清登录态→重新扫码),
--no-logout 可保留当前登录态。

退出码: 0 成功/受理 | 2 token失效/待抓号 | 3 当日额度已满 | 4 其他失败 | 5 已受理但判无效
"""
import datetime
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# 统一 config 路径: capture/mitm addon 均以 LEPAO2_CONFIG 为准(默认=本部署目录 config.json)
CFG_PATH = os.environ.get("LEPAO2_CONFIG") or os.path.join(BASE, "config.json")
os.environ["LEPAO2_CONFIG"] = CFG_PATH

from lepao.accounts import AccountRegistry, AccountError      # noqa: E402
from lepao.auth import AuthState                              # noqa: E402
from lepao.pipeline import Pipeline                           # noqa: E402
from lepao.state import LocalState                            # noqa: E402
from lepao.v3api import V3Client, V3Error                     # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_ENV = {
    "api_host": "api2.lptiyu.com",
    "api_proxy": "127.0.0.1:8081",
    "h5_base": "/bdlp_h5_fitness_test/public/index.php",
    "oss_host": "lptiyu-data.oss-cn-hangzhou.aliyuncs.com",
    "app_key": "wxb2043232183dac7f",
}
DEFAULT_POLICY = {"max_per_day": 1, "style": "template",
                  "dist_lo": 2.03, "dist_hi": 2.35,
                  "pace_lo": 185, "pace_hi": 560}


def _open(sel=None):
    """返回 (registry, auth, state)。auth 绑定 sel(默认 active)。"""
    reg = AccountRegistry(CFG_PATH)
    key = reg.resolve(sel)
    auth = AuthState(reg, key)
    state = LocalState(os.path.join(BASE, "state.json"))
    return reg, auth, state


def _sel_of(args):
    """从参数中提取成员选择器(值), 无则 None。"""
    for flag in ("--member", "--account", "--student", "--name", "--alias", "--uid"):
        if flag in args:
            i = args.index(flag)
            if i + 1 < len(args):
                return args[i + 1]
    return None


def _pol(reg, key, extra=None):
    pol = reg.merged_policy(key)
    if extra:
        pol.update(extra)
    return pol


# ================================================================ 成员管理
def cmd_account(args):
    if not args:
        print(__doc__)
        return 4
    sub = args[0]
    rest = args[1:]
    reg = AccountRegistry(CFG_PATH)
    if sub == "list":
        for i, k in enumerate(reg.keys()):
            a = reg.acc(k)
            au = a.get("auth") or {}
            st = "未抓号" if not au.get("token") else \
                 ("已消耗" if au.get("consumed_at") else "在使用")
            act = "★" if reg.config.get("active") == k else " "
            print(f"{act}[{i}] {a.get('alias') or a.get('name') or k} "
                  f"uid={a.get('uid') or '-'} 学号={a.get('student_num')} "
                  f"token={str(au.get('token') or '')[:8]}…[{st}] "
                  f"{'(停用)' if not a.get('enabled', True) else ''}")
        if not reg.keys():
            print("成员表为空 → `account add <名字> <学号>` 登记")
        return 0
    if sub == "add":
        if len(rest) < 2:
            print("用法: account add <名字> <学号> [--alias A] [--uid N] "
                  "[--school N] [--remark R]")
            return 4
        name, sn = rest[0], rest[1]

        def _flag(f, default=None):
            return rest[rest.index(f) + 1] if f in rest else default
        alias = _flag("--alias")
        uid = _flag("--uid")
        school = _flag("--school")
        remark = _flag("--remark", "")
        key = reg.add(name, sn, alias=alias,
                      uid=int(uid) if uid else 0,
                      school_id=int(school) if school else 837,
                      remark=remark)
        print(f"已登记成员 {name} (键={key}); mitm 白名单即刻生效。"
              f"首次打卡: 该成员登录小程序一次 → `cli.py capture --member {key}`")
        return 0
    if sub == "rm":
        key = reg.remove(rest[0] if rest else None)
        print(f"已移除成员 {key}")
        return 0
    if sub in ("enable", "disable"):
        key = reg.set_flag(rest[0] if rest else None, enabled=(sub == "enable"))
        print(f"成员 {key} 已{sub}")
        return 0
    if sub == "use":
        key = reg.set_active(rest[0] if rest else None)
        print(f"active 成员 → {key}")
        return 0
    if sub == "show":
        sel = rest[0] if rest else None
        key = reg.resolve(sel)
        a = reg.acc(key)
        safe = dict(a)
        au = safe.get("auth") or {}
        if au.get("token"):
            au = {**au, "token": str(au["token"])[:8] + "…",
                  "refresh_token": (str(au.get("refresh_token") or "")[:8] + "…"
                                    if au.get("refresh_token") else "")}
        safe["auth"] = au
        print(json.dumps(safe, ensure_ascii=False, indent=1))
        return 0
    print(f"未知子命令: {sub}\n{__doc__}")
    return 4


# ================================================================ 单步
def cmd_status(args):
    if "--all" in args:
        reg = AccountRegistry(CFG_PATH)
        for k in reg.resolve_all():
            auth = AuthState(reg, k)
            print(auth.describe())
        return 0
    reg, auth, state = _open(_sel_of(args))
    print(auth.describe())
    if auth.consumed_at or not auth.token:
        print("提示: token 已消耗/未装载 → `capture --member <成员>` 或 `run` 重抓")
        ok = False
    else:
        ok = auth.probe()
    last = state.last_submission()
    if last:
        print(f"最近提交: {last}")
    return 0 if ok else 2


def _wx_logout_if_needed(args, relaunch=True):
    """按 --logout/--no-logout 决定是否先强制退出当前微信登录(默认退出)。"""
    if "--no-logout" in args:
        print("[wx] --no-logout: 保留当前微信登录")
        return False
    from lepao import wechat_ctl as WX
    st = WX.status()
    print(f"[wx] 阶段0 强制退出当前微信登录: 进程 {len(st['procs'])} 个, "
          f"登录缓存 {st['cache']} 项…")
    WX.logout(full=True)
    if relaunch:
        p = WX.relaunch_wechat()
        print(f"[wx] 微信已重新唤起({p}) —— 请扫码登录微信后, 只打开一次小程序"
              f"「数体智慧体育」到跑步首页")
    else:
        print("[wx] 已清除微信登录态(下次使用需重新扫码)")
    return True


def cmd_wechat_logout(args):
    """强制退出当前微信登录(杀进程+清登录态缓存)。--no-cache 只杀进程; --no-relaunch 不唤起。"""
    from lepao import wechat_ctl as WX
    full = "--no-cache" not in args
    st = WX.status()
    print(f"[wx] 微信进程 {len(st['procs'])} 个, 登录缓存 {st['cache']} 项 → 退出登录…")
    WX.logout(full=full)
    if "--no-relaunch" not in args:
        p = WX.relaunch_wechat()
        print(f"[wx] 微信已重新唤起({p}) —— 请扫码登录后使用")
    return 0


def cmd_wechat_status(_args):
    from lepao import wechat_ctl as WX
    st = WX.status()
    print(f"微信进程: {len(st['procs'])} 个" + (f" -> {st['procs'][:8]}" if st["procs"] else ""))
    print(f"登录态缓存: {st['cache']} 项 ({WX.RADIUM_USERS})")
    return 0


def cmd_capture(args):
    from lepao import capture as CAP
    sel = _sel_of(args)
    t = 600
    if "--timeout" in args:
        t = int(args[args.index("--timeout") + 1])
    # 预检: 成员已登记(白名单前提)
    reg = AccountRegistry(CFG_PATH)
    key = reg.resolve(sel)
    print(f"[capture] 目标成员: {key} ({reg.acc(key).get('alias') or reg.acc(key).get('name')})")
    _wx_logout_if_needed(args)
    res = CAP.run(wait_timeout=t, member=key)
    if res:
        _key, auth = res
        print(f"[capture] 完成 → 成员[{auth.label}] token 已归位")
        try:
            auth.probe()
        except Exception as e:
            print(f"[capture] 探测异常(链已关属正常): {e}")
        return 0
    return 2


def cmd_quota(args):
    reg, auth, state = _open(_sel_of(args))
    pol = _pol(reg, auth.key)
    client = auth.make_client(pace=0.5)
    try:
        r = client.get_term_list()
    except OSError as e:
        print(f"[FATAL] 业务链不通: {e.__class__.__name__} "
              f"(environment.api_proxy 指向的 mitm 未在跑 → `cli.py capture` 自动拉起)")
        return 4
    except AccountError as e:
        print(f"[FATAL] {e}")
        return 2
    if isinstance(r, dict) and r.get("status") == 101:
        print(f"[FATAL] 成员[{auth.label}] token 失效(101) → `capture --member {auth.label}`")
        return 2
    cfg2 = client.before_run()
    rule = (cfg2.get("time_rule_arr") or [{}])[0]
    pts = (cfg2.get("run_line_info") or {}).get("point_list") or []
    print(f"[{auth.label}] 跑区: {cfg2.get('run_zone_id')} {cfg2.get('run_zone_name')} "
          f"| 打卡点: {len(pts)}")
    for p in pts:
        print(f"  - {p.get('id')}: {p.get('jingwei')}")
    print(f"时间规则: min={rule.get('min_distance')} max={rule.get('max_distance')}km")
    term = str(cfg2.get("term_id") or "0")
    day0 = int(time.mktime(datetime.date.today().timetuple()))
    done = 0
    ids = []
    r = client.get_term_run_record(term, 1)
    for rec in (r.get("list") or []) if isinstance(r, dict) else []:
        try:
            st = int(rec.get("start_time") or 0)
            dist = float(rec.get("distance") or 0)
        except (TypeError, ValueError):
            continue
        if st >= day0 and dist > 0:
            done += 1
        ids.append(f"#{rec.get('id')} {rec.get('distance')}km "
                   f"{datetime.datetime.fromtimestamp(st).strftime('%m-%d %H:%M')}")
    print(f"今日已完成: {done} (policy.max_per_day={pol.get('max_per_day')})")
    print("最近记录: " + " | ".join(ids[:5]))
    return 0


def _run_pipeline(reg, key, pol, submit):
    auth = AuthState(reg, key)
    state = LocalState(os.path.join(BASE, "state.json"))
    pipe = Pipeline(auth, state, policy=pol)
    return pipe.run(submit=submit)


def cmd_build(args):
    sel = _sel_of(args)
    reg = AccountRegistry(CFG_PATH)
    key = reg.resolve(sel)
    style = "template"
    if "--style" in args:
        style = args[args.index("--style") + 1]
    pol = _pol(reg, key, {"style": style})
    out, code = _run_pipeline(reg, key, pol, submit=False)
    if out:
        print(json.dumps({k: out[k] for k in ("track_points", "oss_record_file")},
                         ensure_ascii=False))
    return code


def cmd_submit(args):
    sel = _sel_of(args)
    reg = AccountRegistry(CFG_PATH)
    key = reg.resolve(sel)
    style = "template"
    zone = None
    if "--style" in args:
        style = args[args.index("--style") + 1]
    if "--zone" in args:
        zone = args[args.index("--zone") + 1]
    pol = _pol(reg, key, {"style": style})
    # submit 阶段临时放宽当日额度: 只拦"当日已有有效记录"的情况,
    # 失效记录(打点异常等)不占位 —— 服务端自己判, 客户端设 2 让路
    if "max_per_day" not in args:
        pol["max_per_day"] = int(pol.get("max_per_day", 1))
    if "--force" in args:
        pol["max_per_day"] = 9
    _out, code = _run_pipeline(reg, key, pol, submit=True)
    return code


def cmd_watch(args):
    rid = args[0]
    n = 3
    if "--times" in args:
        n = int(args[args.index("--times") + 1])
    reg, auth, state = _open(_sel_of(args[1:]))
    client = auth.make_client(pace=1.0)
    for i in range(n):
        try:
            d = client.record_detail(rid)
        except OSError as e:
            print(f"[FATAL] 业务链不通: {e.__class__.__name__} (mitm 未在跑 → cli.py capture)")
            return 4
        st = d.get("record_status") if isinstance(d, dict) else "?"
        rs = (d.get("record_failed_reason") or "") if isinstance(d, dict) else ""
        positive = "有效" in rs and "无效" not in rs and "异常" not in rs
        print(f"[{i+1}/{n}] status={st} reason={rs!r}"
              f"{' → 判定有效' if positive else ''} "
              f"dist={(d or {}).get('distance')}")
        state.log_watch(rid, st, rs)
        if str(st) == "1" and (positive or not rs.strip()):
            return 0
        if str(st) == "5":
            return 5
        if i < n - 1:
            time.sleep(45)
    return 0


def cmd_set_token(args):
    sel = _sel_of(args)
    tok = args[0]
    issued = args[1] if len(args) > 1 and not args[1].startswith("--") else None
    reg, auth, _ = _open(sel)
    auth.set_token(tok, issued_at=issued, source="cli")
    auth.probe()
    return 0


def _stop_mitm_chain(guard=None):
    import subprocess
    subprocess.run(["powershell", "-c",
                    "Get-Process mitmweb -ErrorAction SilentlyContinue | Stop-Process -Force"])
    try:
        if guard is None:
            from lepao import capture as CAP
            guard = CAP.ProxyGuard()
        guard.restore()
    except Exception:
        pass


def cmd_run(args):
    """capture → submit → (自动 watch)  目标成员 = 选择器或 active。"""
    sel = _sel_of(args)
    reg = AccountRegistry(CFG_PATH)
    key = reg.resolve(sel)
    label = reg.acc(key).get("alias") or key
    pol = _pol(reg, key)
    if "--style" in args:
        pol["style"] = args[args.index("--style") + 1]
    do_cap = "--no-capture" not in args
    dry = "--dry" in args
    guard = None
    if do_cap:
        from lepao import capture as CAP
        print(f"[run:{label}] 阶段0: 强制退出当前微信登录…")
        _wx_logout_if_needed(args)
        print(f"[run:{label}] 阶段1/3 capture: mitm 链已拉, 请扫码登录微信并打开"
              f"「数体智慧体育」小程序到跑步首页(勿重复打开)")
        guard = CAP.ProxyGuard()
        proc, guard = CAP.start(guard)          # 链全程保持: 抓取与业务共用同一 mitm
        cred, changed = CAP.wait(wait_timeout=600)
        if not changed:
            print(f"[run:{label}] capture 失败/超时 → 终止 (链已复原)")
            _stop_mitm_chain(guard)
            return 2
        CAP.sync(cred, member=key)              # 按凭证身份归位(校验成员匹配)
        reg = AccountRegistry(CFG_PATH)         # 重载: token 已落槽
        auth = AuthState(reg, key)
        if not auth.token:
            print(f"[run:{label}] sync 后仍无 token(凭证身份与成员不符?) → 终止")
            _stop_mitm_chain(guard)
            return 2
        pol = _pol(reg, key)
        print(f"[run:{label}] mitm 保链中（提交后自动关; 请勿再操作小程序）")
    try:
        print(f"[run:{label}] 阶段2/3 pipeline", "dry-run" if dry else "submit")
        out, code = _run_pipeline(reg, key, pol, submit=not dry)
        if code != 0:
            return code
        if not dry and out and out.get("record_id"):
            print(f"[run:{label}] 阶段3/3 异步复核 {out['record_id']}")
            return cmd_watch([str(out["record_id"]), "--times", "3"])
        return code
    finally:
        if do_cap:
            _stop_mitm_chain(guard)


def cmd_batch(args):
    """批量: 默认只出计划表(dry)。--submit 实际逐个提交; --cap 对缺号成员逐个等抓。"""
    submit = "--submit" in args
    do_cap = "--cap" in args
    sel = _sel_of(args)
    reg = AccountRegistry(CFG_PATH)
    keys = reg.resolve_all(sel)
    rt = reg.config.get("runtime", {}) or {}
    sp_lo = int(rt.get("spacing_min", 5) or 5)
    sp_hi = int(rt.get("spacing_max", 10) or 10)
    rows = []
    for k in keys:
        a = reg.acc(k)
        au = a.get("auth") or {}
        fresh = bool(au.get("token")) and not au.get("consumed_at")
        rows.append({"key": k, "label": a.get("alias") or a.get("name") or k,
                     "uid": a.get("uid"), "fresh": fresh,
                     "enabled": a.get("enabled", True),
                     "tok": str(au.get("token") or "")[:8]})
    pending = [r for r in rows if r["enabled"] and not r["fresh"]]
    todo = [r for r in rows if r["enabled"] and r["fresh"]]
    print(f"[batch] 登记成员 {len(rows)} | 可提交 {len(todo)} | 待抓号 {len(pending)}")
    for r in rows:
        flag = ("✓" if r["fresh"] else ("待抓号" if r["enabled"] else "停用"))
        print(f"  - {r['label']:<12} uid={r['uid'] or '-'} token={r['tok']}… [{flag}]")
    if not submit:
        print("[batch] dry: 未提交 (加 --submit 实际逐个提交, 错峰间隔 "
              f"{sp_lo}~{sp_hi}min; --cap 先对缺号者逐个等抓)")
        return 0
    if not todo and not (do_cap and pending):
        print("[batch] 无可用 token 成员 → 先跑 capture/或 --cap")
        return 2

    # ---- 执行 ----
    from lepao import capture as CAP
    import random as _rnd
    guard = None
    if do_cap:
        if "--no-logout" not in args:
            _wx_logout_if_needed(args)
        print(f"[batch] 阶段0: 拉 mitm 链(供缺号成员登录抓取)")
        guard = CAP.ProxyGuard()
        proc, guard = CAP.start(guard)
    worst = 0
    submitted = 0
    for r in rows:
        if not r["enabled"]:
            continue
        try:
            auth = AuthState(reg, r["key"])
        except AccountError:
            continue
        if not auth.token or auth.consumed_at:
            if not do_cap:
                continue
            print(f"[batch] 等成员[{r['label']}] 登录抓号(≤300s)…")
            cred, changed = CAP.wait(timeout_s=300)
            if changed:
                CAP.sync(cred, member=r["key"])
                auth = AuthState(reg, r["key"])
            if not auth.token or auth.consumed_at:
                print(f"[batch] 成员[{r['label']}] 未抓到号 → 跳过")
                worst = max(worst, 2)
                continue
        pol = _pol(reg, auth.key)
        print(f"\n[batch] === 提交 成员[{auth.label}] uid={auth.uid} ===")
        _out, code = _run_pipeline(reg, auth.key, pol, submit=True)
        print(f"[batch] 成员[{auth.label}] 退出码={code}")
        if code == 0:
            submitted += 1
        else:
            worst = max(worst, code)
        reg = AccountRegistry(CFG_PATH)   # 每轮重载(mark_consumed 已持久化)
        # 错峰: 相邻成员之间随机间隔(即使失败也歇一下, 防服务端连击)
        last = (rows.index(r) == len(rows) - 1)
        if not last:
            gap = _rnd.randint(sp_lo * 60, sp_hi * 60)
            print(f"[batch] 错峰等待 {gap // 60}min …")
            time.sleep(gap)
    if guard is not None:
        _stop_mitm_chain(guard)
    print(f"[batch] 完成: 成功提交 {submitted}/{len(rows)} 成员")
    return worst


def cmd_h5_session(args):
    from lepao.h5api import H5Client
    reg, auth, _state = _open(_sel_of(args))
    c = H5Client(school_id=auth.school_id)
    uid = str(auth.uid or "")
    c.token = auth.token
    c.uid = uid
    out = c.session_check(uid=uid)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def cmd_h5_login(args):
    from lepao.h5api import H5Client
    reg = AccountRegistry(CFG_PATH)
    auth = AuthState(reg, reg.resolve())
    c = H5Client(school_id=auth.school_id)
    j = c.login(args[0], args[1])
    if j and j.get("status") == 1:
        print("登录成功: PHPSESSID=" + c.cookie)
        out = c.session_check(uid=str(auth.uid or ""))
        print(json.dumps(out, ensure_ascii=False, indent=1))
        sd = os.path.join(BASE, "state")
        os.makedirs(sd, exist_ok=True)
        with open(os.path.join(sd, "h5_session.json"), "w", encoding="utf-8") as f:
            json.dump({"cookie": c.cookie, "at": datetime.datetime.now().isoformat(),
                       "resp": j}, f, ensure_ascii=False, indent=1)
        return 0
    print("登录失败: " + json.dumps(j, ensure_ascii=False)[:300])
    return 4


def _bootstrap_if_empty():
    """config.json 缺失 → 若存在 config.sample.json 则以它初始化(含环境/策略骨架)。"""
    if os.path.exists(CFG_PATH) or not os.path.exists(os.path.join(BASE, "config.sample.json")):
        return
    try:
        import shutil
        shutil.copy(os.path.join(BASE, "config.sample.json"), CFG_PATH)
        print(f"[init] 已从 config.sample.json 初始化 {CFG_PATH}")
    except Exception as e:
        print(f"[init] 初始化失败: {e}")


def cmd_license(_args):
    """打印许可证要点、署名块、声明文件指纹与构建戳（LRL-1.0 第二/三条）。"""
    from lepao import license_guard as lg
    lg.print_banner()
    lic = next((p for p in lg._candidate_paths() if p.is_file()), None)
    print("-" * 68)
    if lic:
        print(f"[LICENSE] 全文见 {lic}")
    else:
        print("[LICENSE] 未找到 LICENSE 文件 —— 请到原始仓库获取完整条款")
    print(lg.DISCLAIMER_SHORT)
    print(f"技术讨论 QQ {lg.CONTACT_QQ}（仅作技术讨论，不提供代打卡/数据恢复）")
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    if args[0] in ("license", "--license", "-L", "notice"):
        return cmd_license(args[1:])
    # 运行时可见署名（LRL-1.0 第二条 2(b)）：走 stderr，不污染命令 stdout
    try:
        from lepao import license_guard as _lg
        sys.stderr.write(" | ".join(_lg.attribution_block()) + "\n")
        for _w in _lg.warning_lines():
            sys.stderr.write(_w + "\n")
    except Exception:
        pass
    _bootstrap_if_empty()
    cmd, rest = args[0], args[1:]
    table = {
        "account": cmd_account, "status": cmd_status, "quota": cmd_quota,
        "build": cmd_build, "submit": cmd_submit, "watch": cmd_watch,
        "set-token": cmd_set_token, "capture": cmd_capture,
        "run": cmd_run, "batch": cmd_batch,
        "wechat-logout": cmd_wechat_logout, "wechat-status": cmd_wechat_status,
        "h5-session": cmd_h5_session, "h5-login": cmd_h5_login,
        "license": cmd_license,
    }
    if cmd not in table:
        print(f"未知命令: {cmd}\n{__doc__}")
        return 4
    try:
        return table[cmd](rest)
    except AccountError as e:
        print(f"[FATAL] {e}")
        return 2
    except FileNotFoundError as e:
        print(f"[FATAL] 配置缺失: {e} (先 `account add` 登记或复制 config.sample.json)")
        return 4


if __name__ == "__main__":
    sys.exit(main())
