# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""lepao.h5api —— H5 面客户端（ThinkPHP 5.0.14 独立应用, P4/P15 实证模型）

双鉴权通道（实证不对称性, 与 v3 主 API 不同）:
  A. token 通道: access_token + uid 参数（uid 与 token 绑定, 无 IDOR）;
     sign 可空（空不校验）; **无时间戳新鲜度校验**（老报文可重放）
  B. session 通道: PHPSESSID Cookie（学号+密码+验证码登录后, 响应无 token 字段）

H5 独立登录链（P15, 非微信, 免 access_token）:
  GET  index/Login/getToken                          → {"status":1,"code":"<v-token>"}
  GET  index/Login/verify?token=<v-token>&random=<ms> → PNG 验证码 + Set-Cookie PHPSESSID
       （PHPSESSID 每请求轮换 → 必须携带最新值）
  POST index/Login/login {school_id, student_num, password, verify,
       login_type:2, token:<v-token>}
  校验顺序: 验证码 → 学号/密码; 错误串 "验证码错误" / "学号或密码输入有误，请重新输入";
  实测无锁定（5 连错不锁）; 成功 → PHPSESSID session
"""
import json
import time
import urllib.parse
import http.client
import ssl

try:
    import ddddocr
except ImportError:
    ddddocr = None

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

CAPTCHA_URL = "https://servicewechat.com/wxb2043232183dac7f/13/page-frame.html"


class H5Client:
    def __init__(self, host="api2.lptiyu.com",
                 base="/bdlp_h5_fitness_test/public/index.php",
                 school_id=837, timeout=15):
        self.host = host
        self.base = base
        self.school_id = school_id
        self.timeout = timeout
        self.cookie = ""      # 通道 B: PHPSESSID
        self.token = ""       # 通道 A: access_token
        self.uid = ""

    # ------------------------------------------------------------ 底层
    def call(self, module: str, method: str, params: dict = None,
             token: str = "", uid: str = "", sign: str = "",
             extra_headers: dict = None, raw: bool = False):
        """POST /index/{Module}/{method}。返回 (http_status, json_or_bytes)。

        响应判别: JSON status:1 成功 / status:0 业务拒绝（方法存在, 看 info 原文）/
        非 JSON（32KB TP 错误页 = 方法不存在或框架错误）。
        """
        p = dict(params or {})
        p.setdefault("access_token", token or self.token)
        p.setdefault("uid", uid or self.uid)
        p.setdefault("sign", sign)
        body = urllib.parse.urlencode(p).encode()
        conn = http.client.HTTPSConnection(self.host, timeout=self.timeout, context=_CTX)
        hdrs = {"Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                              "MicroMessenger/8.0.49",
                "Referer": CAPTCHA_URL, "Host": self.host}
        if self.cookie:
            hdrs["Cookie"] = self.cookie
        if extra_headers:
            hdrs.update(extra_headers)
        conn.request("POST", f"{self.base}/index/{module}/{method}", body=body, headers=hdrs)
        r = conn.getresponse()
        new_cookie = r.getheader("Set-Cookie") or ""
        if new_cookie:
            self.cookie = new_cookie.split(";")[0]   # PHPSESSID 轮换 → 跟最新
        data = r.read()
        conn.close()
        if raw:
            return r.status, data
        try:
            return r.status, json.loads(data.decode("utf-8", "replace"))
        except Exception:
            return r.status, data[:300]

    def get(self, path: str, raw: bool = False, extra_headers: dict = None):
        """GET（登录链的 getToken/verify 用）"""
        conn = http.client.HTTPSConnection(self.host, timeout=self.timeout, context=_CTX)
        hdrs = {"User-Agent": "Mozilla/5.0", "Referer": CAPTCHA_URL, "Host": self.host}
        if self.cookie:
            hdrs["Cookie"] = self.cookie
        if extra_headers:
            hdrs.update(extra_headers)
        conn.request("GET", f"{self.base}{path}", headers=hdrs)
        r = conn.getresponse()
        new_cookie = r.getheader("Set-Cookie") or ""
        if new_cookie:
            self.cookie = new_cookie.split(";")[0]
        data = r.read()
        conn.close()
        if raw:
            return r.status, data
        try:
            return r.status, json.loads(data.decode("utf-8", "replace"))
        except Exception:
            return r.status, data[:300]

    # ------------------------------------------------------------ 登录链 (P15)
    def login(self, student_num: str, password: str, school_id: int = None,
              ocr_retries: int = 3, verbose=True):
        """学号+密码+验证码 完整登录。成功 → self.cookie 持有 session。返回响应 dict/None。"""
        if ddddocr is None:
            raise RuntimeError("需要 ddddocr: pip install ddddocr")
        ocr = ddddocr.DdddOcr(show_ad=False)
        sid = school_id or self.school_id
        for attempt in range(ocr_retries):
            st, j = self.get("/index/Login/getToken")
            v_token = (j or {}).get("code", "") if isinstance(j, dict) else ""
            if not v_token:
                continue
            st, png = self.get(f"/index/Login/verify?token={v_token}&random={int(time.time()*1000)}", raw=True)
            code = ocr.classification(png)
            st, j = self.call("Login", "login", {
                "school_id": sid, "student_num": student_num,
                "password": password, "verify": code,
                "login_type": 2, "token": v_token,
            })
            if verbose:
                info = j.get("info") if isinstance(j, dict) else str(j)[:80]
                print(f"[h5.login 尝试{attempt+1}] OCR={code!r} → {info}")
            if isinstance(j, dict) and j.get("status") == 1:
                return j
            if isinstance(j, dict) and j.get("info") == "验证码错误":
                continue  # 换码重试
            return j      # 学号/密码错误等 → 中止
        return None

    # ------------------------------------------------------------ session 自检
    def session_check(self, uid=None, year="2025"):
        """PHPSESSID 通道业务可达性验证（token 传空, 靠 cookie）"""
        out = {}
        st, j = self.call("Index", "checkLogin", {})
        out["checkLogin"] = j if isinstance(j, dict) else str(j)[:80]
        st, j = self.call("Report", "getStudentScore",
                          {"uid": uid or self.uid, "year_num": year})
        out["getStudentScore"] = j if isinstance(j, dict) else str(j)[:80]
        st, j = self.call("Offline", "index", {})
        out["Offline_index"] = j if isinstance(j, dict) else str(j)[:80]
        return out
