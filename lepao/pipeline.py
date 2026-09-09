# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""lepao.pipeline —— 每日任务流水线（dry-run 优先, 提交需显式授权标志）

流程（对齐真机调用时序）:
  0 凭证预检 getSchoolInfo → 1 beforeRunV260(跑区/打卡点/时间规则)
  → [可选切跑区 setRunZone] → 2 当日额度预检 getTermList+getTermRunRecord
  → 3 服务器时间 getTimestampV278 → 4 轨迹生成(template/math, 重试保证覆盖+窗口)
  → 5 时间轴+log_data+step_info → 6 文件命名+组装 stopRun 业务参数
  → [dry-run 到此为止, 零写入]
  → 7 getOssSts + OSS 上传 → 8 stopRunV278(flag) → 9 recordDetailV270 复核

退出码: 0 成功/受理 | 2 token失效 | 3 当日额度已满 | 4 其他失败 | 5 已受理但不计成绩
"""
import datetime
import json
import random
import sys
import time

from . import record as REC
from . import track as TRK
from .crypto import encrypt_data
from .v3api import V3Error


class Pipeline:
    def __init__(self, auth, state, policy=None, verbose=True, stop_event=None):
        self.auth = auth
        self.state = state
        self.cfg = auth.config
        self.policy = dict({"max_per_day": 1, "style": "template",
                            "pace_lo": 185, "pace_hi": 560,
                            "dist_lo": 2.03, "dist_hi": 2.35},
                           **(policy or {}))
        self.v = verbose
        self.rnd = random.Random()
        self.stop_event = stop_event  # 可中断: UI 停止按钮 / 顶号看门狗

    def _sleep(self, sec):
        """可中断 sleep: 检测 stop_event(用户停止 / 检测到新登录顶号)。"""
        if sec <= 0:
            return
        t0 = time.time()
        while time.time() - t0 < sec:
            if self.stop_event is not None and self.stop_event.is_set():
                raise V3Error(9, "task interrupted (user stop or token rotated)")
            time.sleep(0.2)

    def _member_tag(self):
        try:
            lb = self.auth.label
            return f"{lb}#{self.auth.uid}" if self.auth.uid else lb
        except Exception:
            return "?"

    def log(self, msg):
        if self.v:
            print(f"[{self._member_tag()}] {msg}")

    # ------------------------------------------------------------ 步骤
    def _config(self, client):
        # [0] 真鉴权门预检: getTermList（getSchoolInfo 是免 token 公开口 = 假阳性源, 弃用）
        try:
            r = client.get_term_list()
            if isinstance(r, dict) and r.get("status") == 101:
                raise V3Error(101, "token 失效(真门 getTermList)")
        except V3Error as e:
            if e.code == 101:
                raise
        s = client.get_school_info()
        if not isinstance(s, dict) or "school_name" not in s:
            raise V3Error(-1, f"学校信息异常: {str(s)[:200]}")
        self.log(f"[0] token 有效(真门) | {s.get('school_name')}")
        self._sleep(self.rnd.uniform(0.6, 1.5))
        return self._before(client)

    def _before(self, client):
        cfg = client.before_run()
        if not isinstance(cfg, dict) or not cfg.get("run_line_info"):
            raise V3Error(-1, f"beforeRunV260 异常: {str(cfg)[:200]}")
        zone = str(cfg.get("run_zone_id") or "")
        pts = []
        for p in cfg["run_line_info"].get("point_list") or []:
            try:
                lat, lng = TRK.parse_jingwei(str(p.get("jingwei") or "").strip())
                pts.append((lat, lng, str(p.get("id"))))
            except Exception:
                continue
        if len(pts) < 2:
            raise V3Error(-1, "跑区打卡点缺失")
        tr0 = (cfg.get("time_rule_arr") or [{}])[0]
        rule = {
            "zone": zone,
            "zone_name": str(cfg.get("run_zone_name") or ""),
            "term_id": str(cfg.get("term_id") or "0"),
            "pts": pts,
            "min_d": float(tr0.get("min_distance") or 2.0),
            "max_d": float(tr0.get("max_distance") or 10.0),
        }
        self.log(f"[1] 跑区={zone}({rule['zone_name']}) 打卡点x{len(pts)} "
                 f"学期={rule['term_id']} 距离窗口={rule['min_d']}~{rule['max_d']}km")
        return rule

    def _quota(self, client, term_id, submit):
        self._sleep(self.rnd.uniform(0.5, 1.3))
        client.get_term_list()
        day0 = int(time.mktime(datetime.date.today().timetuple()))
        done = 0
        for page in (1, 2):
            r = client.get_term_run_record(term_id, page)
            if not isinstance(r, dict):
                break
            for rec in r.get("list", []):
                try:
                    st = int(rec.get("start_time") or 0)
                    dist = float(rec.get("distance") or 0)
                except (TypeError, ValueError):
                    continue
                if st >= day0 and dist > 0:
                    done += 1
            if not r.get("list"):
                break
            self._sleep(self.rnd.uniform(0.6, 1.4))
        self.log(f"[2] 今日已完成 {done} 次 (上限 {self.policy['max_per_day']})")
        if done >= self.policy["max_per_day"] and submit:
            raise V3Error(3, "当日额度已满, 不提交")
        return done

    def _gen_track(self, rule, style):
        """返回 (track, log_builder_kind, used, dist_km)"""
        if style == "template":
            for attempt in range(10):
                target = self.rnd.uniform(
                    max(rule["min_d"] + 0.02, self.policy["dist_lo"]),
                    min(self.policy["dist_hi"], rule["max_d"] - 0.02))
                pts_d = [{"point_id": p[2], "latitude": p[0], "longtitude": p[1]}
                         for p in rule["pts"]]
                gen = TRK.TemplateGenerator(pts_d, distance_km=target,
                                            seed=self.rnd.randint(0, 10**9))
                tr_, meta = gen.generate(return_meta=True)
                dist_ = meta["path_m"] / 1000.0
                cover = all(c <= 40.0 for c in meta["anchor_cover"])
                self.log(f"[4.{attempt}] tpl 点={len(tr_)} 距离={dist_:.2f}km "
                         f"锚点近距={meta['anchor_cover']}")
                if cover and rule["min_d"] <= dist_ <= rule["max_d"]:
                    used = TRK.tpl_used_seconds(tr_)
                    pace = used / dist_
                    if not (self.policy["pace_lo"] <= pace <= self.policy["pace_hi"]):
                        used = int(dist_ * self.rnd.uniform(230, 480))
                    return tr_, "template", used, dist_, meta
        else:
            for attempt in range(12):
                target = self.rnd.uniform(
                    max(rule["min_d"] + 0.02, self.policy["dist_lo"]),
                    min(self.policy["dist_hi"], rule["max_d"] - 0.02)) * 1000
                tr_, cr_, used_, dist_ = TRK.math_track(rule["pts"], target, self.rnd)
                pace = used_ / dist_ if dist_ > 0 else 9e9
                cover = all(p[2] in cr_ for p in rule["pts"])
                self.log(f"[4.{attempt}] math 点={len(tr_)} 距离={dist_}km "
                         f"用时={used_}s 配速={pace:.0f}s/km 覆盖={cover}")
                if cover and rule["min_d"] <= dist_ <= rule["max_d"] \
                        and self.policy["pace_lo"] <= pace <= self.policy["pace_hi"]:
                    return tr_, "math", used_, dist_, {"crossings": cr_}
        raise V3Error(4, f"轨迹生成失败 style={style} (重试耗尽)")

    # ------------------------------------------------------------ 主入口
    def run(self, submit=False, zone_override=None):
        """执行流水线。返回 (summary_dict|None, exit_code)"""
        client = self.auth.make_client(pace=0.8)
        try:
            rule = self._config(client)
            if zone_override and zone_override != rule["zone"]:
                client.set_run_zone(zone_override)
                self.log(f"[1b] setRunZone → {zone_override}")
                rule = self._before(client)
                rule["zone"] = zone_override
            self._quota(client, rule["term_id"], submit)
            self._sleep(self.rnd.uniform(0.6, 1.4))
            srv_now = client.get_timestamp()
            self.log(f"[3] 服务器时间={srv_now}")

            style = self.policy.get("style", "template")
            track, kind, used, dist_km, meta = self._gen_track(rule, style)
            end_time = srv_now - self.rnd.randint(15, 75)
            start_time = end_time - used
            if kind == "template":
                pts_d = [{"point_id": p[2], "latitude": p[0], "longtitude": p[1]}
                         for p in rule["pts"]]
                log_rows = TRK.build_log_data_template(track, pts_d, start_time, end_time)
                steps = TRK.build_step_info(used)
                step_num = int(sum(steps["list"]))
            else:
                log_rows = TRK.build_log_data_math(rule["pts"], track, meta["crossings"],
                                                   start_time)
                steps = TRK.build_step_info(used)
                step_num = int(sum(steps["list"]))

            txt = encrypt_data(json.dumps(track, ensure_ascii=False, separators=(",", ":")))
            now_ms, record_file, fname = REC.make_record_file(self.rnd)
            g = {
                "term_id": 1,
                "game_id": rule["zone"],
                "start_time": int(start_time),
                "end_time": int(end_time),
                "distance": float(dist_km),
                "record_img": "",
                "log_data": json.dumps(log_rows, ensure_ascii=False, separators=(",", ":")),
                "file_img": "",
                "is_running_area_valid": 1,
                "mobileDeviceId": 1,
                "mobileModel": 1,
                "mobileOsVersion": 1,
                "step_info": json.dumps(steps, separators=(",", ":")),
                "step_num": int(step_num),
                "used_time": int(used),
                "record_file": record_file,
            }
            self.log(f"[6] record_file={record_file} txt={len(txt)}B 点数={len(track)}")

            if not submit:
                out = {"stopRun_biz": g, "track_points": len(track),
                       "track_head": track[:3], "track_tail": track[-2:],
                       "log_data": log_rows, "oss_record_file": record_file,
                       "meta": {k: v for k, v in meta.items() if k != "crossings"}}
                self.state.save_dryrun(out, uid=self.auth.uid,
                                       member=self._member_tag())
                self.log("[dry-run] 全参数已存 state (零写入: 未取STS/未上传/未提交)")
                return out, 0

            self._sleep(self.rnd.uniform(0.4, 1.2))
            sts = client.get_oss_sts()
            if not isinstance(sts, dict) or "AccessKeySecret" not in sts:
                raise V3Error(4, f"getOssSts 异常: {str(sts)[:200]}")
            status, rbody = REC.upload_to_oss(txt, sts, now_ms, record_file, self.rnd)
            self.log(f"[6b] OSS 上传 status={status}")
            if status not in (200, 203, 204):
                raise V3Error(4, f"OSS 上传失败: {rbody[:600]}")

            self._sleep(self.rnd.uniform(0.8, 2.4))
            self.log(f"[7] stopRun 距离={dist_km}km 用时={used}s 步数={step_num} "
                     f"配速={int(used / dist_km)}s/km")
            resp = client.stop_run(g)
            self.log("[7] 响应: " + json.dumps(resp, ensure_ascii=False)[:300])

            detail = None
            if isinstance(resp, dict) and resp.get("record_id"):
                self._sleep(self.rnd.uniform(2.5, 6.0))
                detail = client.record_detail(resp["record_id"])
                if isinstance(detail, dict):
                    self.log(f"[8] recordDetail: status={detail.get('record_status')} "
                             f"reason={detail.get('record_failed_reason')!r} "
                             f"dist={detail.get('distance')}")
            summary = None
            if isinstance(resp, dict):
                stt = (detail.get("record_status") if isinstance(detail, dict)
                       and detail.get("record_status") is not None else resp.get("status"))
                rsn = (detail.get("record_failed_reason") if isinstance(detail, dict)
                       else resp.get("record_failed_reason")) or ""
                summary = {
                    "record_id": resp.get("record_id"),
                    "distance": resp.get("distance") or (detail or {}).get("distance"),
                    "used_time": resp.get("time") or resp.get("used_time"),
                    "record_status": stt,
                    "uid": resp.get("uid"),
                    "member": self._member_tag(),
                    "game_id": resp.get("game_id") or rule["zone"],
                    "record_failed_reason": rsn,
                    "style": style,
                }
                self.log("RUN_RESULT:" + json.dumps(summary, ensure_ascii=False))
                self.state.save_submission(summary)
                # token=单活动会话消耗品: 提交链走完即标记消耗, 下轮重新 capture
                try:
                    self.auth.mark_consumed()
                except Exception:
                    pass
                # record_failed_reason 字段名是坑: 服务端也用它装肯定判定文本
                # ("自动确认有效"=过审, 与真人黄金记录逐字段一致(实证); 否定例: "打卡点异常")
                positive = (not rsn.strip()) or ("有效" in rsn and "无效" not in rsn
                                                 and "不" not in rsn and "异常" not in rsn)
                if summary["record_id"] and str(stt) in ("0", "1") and positive:
                    return summary, 0
                if summary["record_id"] and not positive:
                    self.log(f"[WARN] 记录已创建但被判无效: {rsn}")
                    return summary, 5
            return (resp if isinstance(resp, dict) else None), 4
        except V3Error as e:
            self.log(f"[FATAL] V3Error({e.code}) {e.msg[:200]}")
            return None, (9 if e.code == 9 else (2 if e.code == 101 else (3 if e.code == 3 else 4)))
        except OSError as e:
            self.log(f"[FATAL] 业务链不通 {e.__class__.__name__}: {e} "
                     f"(mitm 未在跑 → cli.py capture/run 自动拉起)")
            return None, 4
