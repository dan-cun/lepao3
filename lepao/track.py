# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""lepao.track —— 轨迹生成（双引擎, 移植自乐跑1已验证模块）

engine=template (P11 主力, 反指纹核心):
  真人模板 (数据模板_035明文.json, 226 点, 服务器判有效 record_status=1)
  ENU 相似变换 (相位对齐+尺度+旋转) → 目标距离缩放 → 平滑 GPS 噪声
  保留真实 s/b/c 字段序列 (速度含停走段, 无 sin 周期)
engine=math (v5 备用): 多圈锚点环 + 速度模型 + 打卡减速扫描
"""
import json
import math
import os
import random

TEMPLATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "真实轨迹_035明文.json")

DT = 2.833  # 真机采样步长(s) ±0.02 抖动


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def quantize_geo(v):
    """真机坐标 = 1/3600000° 量化后的 double（math 引擎用; 模板引擎保留 float64 自然精度）"""
    return int(round(v * 3600000.0)) / 3600000.0


def parse_jingwei(s):
    a, b = s.split(",")
    return float(a), float(b)


def tpl_used_seconds(track, min_s=300, max_s=2400, rnd=None):
    """模板用时: 按 s 字段(实测 m/s)逐段积分; 无 s 段按 2.5 m/s; 超窗兜底 600-760s"""
    rnd = rnd or random.Random()
    tot = 0.0
    for i in range(1, len(track)):
        d = haversine_m(track[i-1]["a"], track[i-1]["o"], track[i]["a"], track[i]["o"])
        s = track[i].get("s")
        tot += d / max(0.6, s) if s else d / 2.5
    if tot < min_s or tot > max_s:
        tot = rnd.uniform(600, 760)
    return int(tot)


def template_used_seconds(track, min_s=300, max_s=2400, rnd=None):
    return tpl_used_seconds(track, min_s, max_s, rnd)


# ================================================================ 模板引擎
class TemplateGenerator:
    """真人模板相似变换生成器（P11: 035 形态服务器判有效）"""

    def __init__(self, points, distance_km=None, seed=None, pace_factor=None,
                 template_file=TEMPLATE_FILE):
        self.rnd = random.Random(seed)
        self.points = points
        self.distance_km = distance_km
        self.pace_factor = (pace_factor if pace_factor is not None
                            else self.rnd.uniform(0.82, 1.18))
        with open(template_file, encoding="utf-8") as f:
            self.tpl = json.load(f)

    @staticmethod
    def _to_enu(lat, lng, lat0, lng0):
        return ((lng - lng0) * 111320.0 * math.cos(math.radians(lat0)),
                (lat - lat0) * 111320.0)

    @staticmethod
    def _from_enu(x, y, lat0, lng0):
        return (lat0 + y / 111320.0,
                lng0 + x / (111320.0 * max(math.cos(math.radians(lat0)), 1e-6)))

    def _path_len(self, coords):
        return sum(haversine_m(coords[i-1][0], coords[i-1][1], coords[i][0], coords[i][1])
                   for i in range(1, len(coords)))

    @staticmethod
    def _ring(pts_dict):
        vals = list(pts_dict.items())
        clat = sum(v[1][0] for v in vals) / len(vals)
        clng = sum(v[1][1] for v in vals) / len(vals)

        def ang(kv):
            return math.atan2(kv[1][0] - clat,
                              (kv[1][1] - clng) * math.cos(math.radians(clat)))
        return sorted(vals, key=ang), clat, clng

    def generate(self, return_meta=False):
        rnd = self.rnd
        tpl_ring, tclat, tclng = self._ring(
            {str(p["point_id"]): (p["latitude"], p["longtitude"]) for p in self.points}
            if self.points else {})
        if not tpl_ring:  # 无实时锚点 → 模板原样
            tpl_ring, tclat, tclng = self._ring(
                {str(i): (p["a"], p["o"]) for i, p in enumerate(self.tpl[:4])})
            live_dst = tpl_ring
        else:
            # 实时锚点环 vs 模板环: 最佳相位对齐
            src_ring = self._ring(
                {str(i): (p["a"], p["o"]) for i, p in enumerate(self.tpl[:len(tpl_ring)])})[0]
            n = min(len(src_ring), len(tpl_ring))
            best, best_cost = None, 1e18
            for off in range(n):
                cand = tpl_ring[off:] + tpl_ring[:off]
                cost = sum(haversine_m(t[1][0], t[1][1], c[1][0], c[1][1])
                           for t, c in zip(src_ring[:n], cand))
                if cost < best_cost:
                    best_cost, best = cost, cand
            pairs = list(zip(src_ring[:n], best))
            live_dst = best

        lclat = sum(q[1][0] for q in live_dst) / len(live_dst)
        lclng = sum(q[1][1] for q in live_dst) / len(live_dst)

        if pairs:
            src = [self._to_enu(p[1][0], p[1][1], tclat, tclng) for _, p in pairs]
            dst = [self._to_enu(q[1][0], q[1][1], lclat, lclng) for _, q in pairs]
            sca_, rot_ = [], []
            for (x0, y0), (x1, y1) in zip(src, dst):
                sca_.append(math.hypot(x1, y1) / max(math.hypot(x0, y0), 1e-6))
                rot_.append(math.atan2(y1, x1) - math.atan2(y0, x0))
            scale = sum(sca_) / len(sca_)
            rot = math.atan2(sum(math.sin(r) for r in rot_),
                             sum(math.cos(r) for r in rot_))
        else:
            scale, rot = 1.0, 0.0
        cosr, sinr = math.cos(rot), math.sin(rot)

        coords = []
        for p in self.tpl:
            x, y = self._to_enu(p["a"], p["o"], tclat, tclng)
            coords.append(self._from_enu(x * scale * cosr - y * scale * sinr,
                                         x * scale * sinr + y * scale * cosr,
                                         lclat, lclng))
        if self.distance_km:
            cur = self._path_len(coords)
            if cur > 100:
                f = self.distance_km * 1000.0 / cur
                coords = [self._from_enu(px * f, py * f, lclat, lclng)
                          for px, py in (self._to_enu(a, o, lclat, lclng)
                                         for a, o in coords)]

        track = []
        w = 0.0
        for i, p in enumerate(self.tpl):
            w = max(-9.0, min(9.0, w + rnd.gauss(0, 0.9)))
            dx, dy = w + rnd.gauss(0, 1.1), rnd.gauss(0, 1.3)
            px, py = self._to_enu(*coords[i], lclat, lclng)
            lat, lng = self._from_enu(px + dx, py + dy, lclat, lclng)
            q = {"a": lat, "o": lng}
            if "s" in p:
                q["s"] = round(max(0.01, min(7.6, p["s"] * self.pace_factor + rnd.gauss(0, 0.05))), 2)
            if "b" in p:
                q["b"] = p["b"]
            q["c"] = p["c"]
            if i == 0:
                q.pop("s", None)
            track.append(q)

        meta = {
            "center": (round(lclat, 6), round(lclng, 6)),
            "scale": round(scale, 4),
            "rot_deg": round(math.degrees(rot), 1),
            "pace_factor": round(self.pace_factor, 3),
            "path_m": round(self._path_len([(p["a"], p["o"]) for p in track])),
            "anchor_cover": [
                round(min(haversine_m(p["a"], p["o"], q[1][0], q[1][1]) for p in track), 1)
                for q in live_dst],
        }
        return (track, meta) if return_meta else track


# ================================================================ 数学引擎
def math_track(cps, target_m, rnd=None):
    """多圈锚点环 (v5 备用引擎)。cps: [(lat,lng,pid)...]; 返回 (track, crossings, used, dist_km)"""
    rnd = rnd or random.Random()
    order = list(cps)
    if rnd.random() < 0.5:
        order = order[::-1]
    k = rnd.randrange(len(order))
    order = order[k:] + order[:k]

    def mid(a, b, wob):
        m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        d = haversine_m(a[0], a[1], b[0], b[1])
        lat_o = math.copysign(wob * d / 111000.0 * rnd.uniform(0.5, 1.0), rnd.random() - 0.5)
        lng_o = math.copysign(wob * d / (111000.0 * math.cos(math.radians(m[0])))
                              * rnd.uniform(0.5, 1.0), rnd.random() - 0.5)
        return (m[0] + lat_o, m[1] + lng_o)

    route = []
    lap = 0
    perim = sum(haversine_m(order[i][0], order[i][1],
                            order[(i + 1) % len(order)][0], order[(i + 1) % len(order)][1])
                for i in range(len(order)))
    while perim * (lap + 1) < target_m * 1.15:
        ox = rnd.uniform(-0.00006, 0.00006)
        oy = rnd.uniform(-0.00006, 0.00006)
        seq = [(p[0] + ox, p[1] + oy) for p in order]
        for i in range(len(seq)):
            a = seq[i]
            b = seq[(i + 1) % len(seq)]
            route.append(a)
            route.append(mid(a, b, 0.16))
        lap += 1
    route.append(route[0])
    cum = [0.0]
    for i in range(1, len(route)):
        cum.append(cum[-1] + haversine_m(route[i-1][0], route[i-1][1], route[i][0], route[i][1]))

    def route_at(s):
        s = min(max(s, 0.0), cum[-1] - 0.01)
        lo, hi = 0, len(cum) - 1
        while lo < hi:
            m = (lo + hi) // 2
            if cum[m] < s:
                lo = m + 1
            else:
                hi = m
        i = max(lo - 1, 0)
        seg = cum[i+1] - cum[i] or 1.0
        t = (s - cum[i]) / seg
        return (route[i][0] + (route[i+1][0] - route[i][0]) * t,
                route[i][1] + (route[i+1][1] - route[i][1]) * t)

    base_v = rnd.uniform(3.45, 3.95)
    period = rnd.uniform(150, 230)
    phase = rnd.uniform(0, 6.28)
    track, crossings = [], {}
    s = t = 0.0
    i = 0
    jl = jn = 0.0
    while True:
        v = base_v * (1 + 0.12 * math.sin(2 * math.pi * t / period + phase))
        v += rnd.gauss(0, 0.22)
        if t < 30:
            v *= 0.55 + 0.45 * (t / 30)
        remaining = target_m - s
        if remaining < 100:
            v *= max(0.18, remaining / 100)
        la, lo_ = route_at(s)
        for cp in cps:
            if haversine_m(la, lo_, cp[0], cp[1]) < 14 and rnd.random() < 0.45:
                v *= rnd.uniform(0.35, 0.6)
                break
        v = max(0.02, min(v, 6.2))
        jl = jl * 0.86 + rnd.gauss(0, 1.6) / 111000.0
        jn = jn * 0.86 + rnd.gauss(0, 1.9) / 111000.0 / math.cos(math.radians(la))
        if rnd.random() < 0.008:
            jl += rnd.uniform(-0.00012, 0.00012)
            jn += rnd.uniform(-0.00012, 0.00012)
        lat, lng = la + jl, lo_ + jn

        pt = {"a": quantize_geo(lat), "o": quantize_geo(lng)}
        if i > 0 and rnd.random() > 0.004:
            pt["s"] = round(max(0.02, v + rnd.gauss(0, 0.15)), 2)
        dmin = min(haversine_m(pt["a"], pt["o"], cp[0], cp[1]) for cp in cps)
        b = None
        if dmin < 18:
            b = 2 if rnd.random() < 0.75 else (1 if rnd.random() < 0.4 else None)
        elif rnd.random() < 0.32:
            b = 1
        if b is not None:
            pt["b"] = b
        c = abs(rnd.gauss(0.06, 0.05))
        if rnd.random() < 0.015:
            c += rnd.uniform(0.15, 0.5)
        pt["c"] = f"{min(c, 0.60):.2f}"
        track.append(pt)

        for cp in cps:
            pid = cp[2]
            if pid not in crossings and haversine_m(pt["a"], pt["o"], cp[0], cp[1]) <= 18:
                crossings[pid] = (i, s)
        s += v * (DT + rnd.uniform(-0.02, 0.02))
        t += DT
        i += 1
        if s >= target_m and all(cp[2] in crossings for cp in cps):
            break
        if i > 460:
            break

    for _ in range(2):
        pt = dict(track[-1])
        pt["a"] = quantize_geo(track[-1]["a"] + rnd.gauss(0, 1.0) / 111000.0)
        pt["o"] = quantize_geo(track[-1]["o"] + rnd.gauss(0, 1.1) / 111000.0)
        pt["s"] = round(rnd.uniform(0.01, 0.06), 2)
        pt["b"] = 2
        track.append(pt)
        t += DT

    dist_m = 0.0
    for a, b_ in zip(track, track[1:]):
        dist_m += haversine_m(a["a"], a["o"], b_["a"], b_["o"])
    return track, crossings, int(round(t)), round(dist_m / 1000.0, 2)


# ================================================================ log/step
CHECKIN_RADIUS_M = 40.0  # 首通过判定半径（真人 035 实证: 注册点 13~40m 偏差均被接受）


def build_log_data_template(track, points, start_time, end_time, rnd=None):
    """模板引擎 log_data —— 2026-09-08 真人035逐字段逆向修正版
    真人语义 (035 明文, status=1 有效):
      coordinates = 通过时刻 GPS（轨迹点原值, 3/4 点与轨迹 0.0m 重合, 1 点 13.1m）
      time        = 首通过墙钟偏移（s 积分时间映射, 起点近的点前载到 +4s/+9s）
      distance    = 首通过时累计公里（0.156→0.16 / 0.223→0.22 / 0.000→"0.0" 精确吻合）
      order       = 配置点序, 不按时间排序（真人 24280@155s 排在 24281@4s 之前）
    修正前 bug: distance 误用 haversine 米值（4.59 > 全程 2.31km → 同步"打点异常"）"""
    rnd = rnd or random.Random()
    n = len(track)
    cum_d = [0.0]
    for a, b_ in zip(track, track[1:]):
        cum_d.append(cum_d[-1] + haversine_m(a["a"], a["o"], b_["a"], b_["o"]))
    # s 积分累计时间（与轨迹速度字段一致 → 服务器回放轨迹可自洽）
    cum_t = [0.0]
    for a, b_ in zip(track, track[1:]):
        d = haversine_m(a["a"], a["o"], b_["a"], b_["o"])
        s = b_.get("s")
        cum_t.append(cum_t[-1] + (d / max(0.5, s) if s else d / 2.833))
    used = max(1, end_time - start_time)
    scale = used / max(1e-6, cum_t[-1])  # 轨迹时间 → 墙钟（真人 498s→646s, scale 1.30）
    entries = []
    for p in points:  # 保持配置点序, 不排序
        la, lo = p["latitude"], p["longtitude"]
        idx = None
        for k in range(n):  # 首通过 = 第一个进入半径的轨迹点
            if haversine_m(track[k]["a"], track[k]["o"], la, lo) <= CHECKIN_RADIUS_M:
                idx = k
                break
        if idx is None:  # 兜底: 最近点
            idx = min(range(n),
                      key=lambda k: haversine_m(track[k]["a"], track[k]["o"], la, lo))
        t_off = max(0.0, min(float(used), cum_t[idx] * scale + rnd.uniform(-3.0, 3.0)))
        d_km = cum_d[idx] / 1000.0
        entries.append({
            "latitude": track[idx]["a"], "longitude": track[idx]["o"],
            "longtitude": track[idx]["o"], "point_id": str(p["point_id"]),
            "time": int(start_time + t_off),
            "distance": "0.0" if d_km < 0.005 else round(d_km, 2),
        })
    return entries


def build_log_data_math(cps, track, crossings, start_ts):
    """数学引擎 log_data（真机形态: 距离 "0.0" 字符串 quirk + 双拼写）"""
    rows = []
    cum = [0.0]
    for a, b_ in zip(track, track[1:]):
        cum.append(cum[-1] + haversine_m(a["a"], a["o"], b_["a"], b_["o"]))
    for cp in cps:
        pid = cp[2]
        if pid not in crossings:
            idx, _ = min(enumerate(track),
                         key=lambda it: haversine_m(it[1]["a"], it[1]["o"], cp[0], cp[1]))
            crossings[pid] = (idx, 0.0)
        idx, _ = crossings[pid]
        p = track[idx]
        dkm = round(cum[min(idx, len(cum) - 1)] / 1000.0, 2)
        rows.append({
            "latitude": p["a"], "longitude": p["o"],
            "distance": "0.0" if dkm == 0 else dkm,
            "point_id": str(pid), "time": int(start_ts + idx * DT),
            "longtitude": p["o"],
        })
    return rows


def build_step_info(used_time, rnd=None):
    """真机计步形态: 桶数=used//60+1, 稀疏不规则值"""
    rnd = rnd or random.Random()
    n = max(2, used_time // 60 + 1)
    lst = []
    for _ in range(n):
        r = rnd.random()
        if r < 0.12:
            lst.append(rnd.randint(0, 6))
        elif r < 0.25:
            lst.append(rnd.randint(70, 92))
        else:
            lst.append(rnd.randint(7, 40))
    total = sum(lst)
    if total < 180:
        lst[rnd.randrange(n)] += 180 - total + rnd.randint(0, 40)
    elif total > 360:
        k = rnd.randrange(n)
        lst[k] = max(0, lst[k] - (total - rnd.randint(300, 355)))
    return {"interval": 60, "list": lst}
