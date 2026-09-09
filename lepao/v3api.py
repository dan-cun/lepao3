# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""lepao.v3api —— v3 主 API 客户端（乐跑小程序协议）

鉴权模型（实证）: 无状态 token 门。合法 token 即通过; PHPSESSID 无关。
门后业务层信任 uid 参数（读面 IDOR 已实证, 本客户端纪律: 只写本人 uid）。

请求形态: POST /v3/api.php/{Module}/{method}
  body = ostype=5&data=<AES(json+sign)>
  公共参数: uid, token, school_id, term_id, course_id, class_id, student_num, card_id,
            timestamp, version, nonce, ostype
"""
import json
import os
import time
import urllib.parse

from . import transport
from .crypto import decrypt_data, encrypt_data, random6, sign_md5


class V3Error(Exception):
    def __init__(self, code, msg=""):
        self.code = code
        self.msg = msg
        super().__init__(f"V3Error({code}) {msg[:120]}")


class V3Client:
    def __init__(self, uid, token, school_id, student_num="", card_id="",
                 term_id=0, course_id=0, class_id=0, host="api2.lptiyu.com",
                 timeout=15, proxy=None, pace=0.0):
        self.host = host
        self.timeout = timeout
        self.proxy = proxy or os.environ.get("LPTIYU_PROXY") or None
        self.pace = pace  # 全局限速间隔(秒), 0=不限
        self._last = 0.0
        self.base = {
            "uid": uid,
            "token": token,
            "school_id": school_id,
            "term_id": term_id,
            "course_id": course_id,
            "class_id": class_id,
            "student_num": student_num,
            "card_id": card_id or student_num,
        }

    # ------------------------------------------------------------ 底层
    def _pace(self):
        if self.pace <= 0:
            return
        d = time.time() - self._last
        if d < self.pace:
            time.sleep(self.pace - d)
        self._last = time.time()

    def post(self, path: str, biz: dict = None, flag: bool = False):
        """加密 POST → 解密响应 dict。status=101 抛 V3Error(101)。"""
        self._pace()
        p = dict(self.base)
        p.update({"timestamp": int(time.time()), "version": 1,
                  "nonce": random6(), "ostype": 5})
        if biz:
            p.update(biz)
        p["sign"] = sign_md5(p)
        body = urllib.parse.urlencode(
            {"ostype": 5, "data": encrypt_data(json.dumps(p, ensure_ascii=False))}
        ).encode("utf-8")

        conn = transport.make_conn(self.host, 443, self.timeout, self.proxy)
        resp = transport.post_form(conn, path, body, flag=flag)
        raw = resp.read()
        conn.close()
        text = transport.decode_body(raw, resp.getheader("Content-Encoding")) \
            .decode("utf-8", "replace")
        try:
            j = json.loads(text)
        except Exception:
            raise V3Error(resp.status, f"非JSON响应: {text[:200]}")
        if isinstance(j.get("data"), str) and len(j.get("data")) > 32:
            try:
                j = json.loads(decrypt_data(j["data"]))
            except Exception:
                pass
        return j

    # ------------------------------------------------------------ 端点
    def get_school_info(self):
        """凭证校验（真机冷启动第一调用）。status=101 = token 失效。"""
        return self.post("/v3/api.php/WpLogin/getSchoolInfo", {})

    def before_run(self):
        """跑区/打卡点/时间规则配置（真机: 无 game_id 业务参数）"""
        return self.post("/v3/api.php/Run2/beforeRunV260", {})

    def set_run_zone(self, zone_id):
        return self.post("/v3/api.php/Run/setRunZone", {"run_zone_id": str(zone_id)})

    def get_term_list(self):
        return self.post("/v3/api.php/WpRun/getTermList", {})

    def get_term_run_record(self, term_id, page=1):
        return self.post("/v3/api.php/WpRun/getTermRunRecord",
                         {"term_id": str(term_id), "page": page})

    def get_timestamp(self):
        """服务器时间"""
        r = self.post("/v3/api.php/Run/getTimestampV278", {})
        try:
            return int(r["timestamp"])
        except Exception:
            return int(time.time())

    def get_oss_sts(self):
        """OSS STS 临时凭证（真机: 无业务参数）"""
        return self.post("/v3/api.php/WpIndex/getOssSts", {})

    def stop_run(self, biz: dict):
        """提交跑步记录（真机唯一带 flag 头的业务接口之一）"""
        return self.post("/v3/api.php/Run/stopRunV278", biz, flag=True)

    def record_detail(self, record_id, year=None):
        """记录详情（权威 status 来源）"""
        import datetime
        y = year or str(datetime.date.today().year)
        return self.post("/v3/api.php/Run/recordDetailV270",
                         {"record_id": str(record_id), "year_num": y})

    def login_by_code(self, code, ty="2"):
        """微信 code 换 token（真机登录; 本体系默认被动模式——token 由 mitm 捕获供给）"""
        return self.post("/v3/api.php/WpLogin/loginByCode",
                         {"app_key": transport.APP_ID, "nickname": "",
                          "avatar": "", "ty": ty, "code": code}, flag=True)
