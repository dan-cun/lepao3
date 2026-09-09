# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""lepao.record —— 轨迹文件命名仿真 + OSS 上传（真机形态逐字节复刻）

实证清单（2026-09-07 flows_手机 实录）:
  - record_file(业务参数) = "run_record/{date}/{dir3}/{ms}-{n}.txt"（无 Public/Upload/file/ 前缀）
  - OSS 表单 key          = "Public/Upload/file/" + record_file
  - dir3 = 构造 key 时刻 ms%1000; fname ms 晚于 dir 时刻几~几十ms（两次 Date.now()）
  - 表单字段顺序: OSSAccessKeyId, key, policy, signature, x-oss-security-token, file（无 callback）
  - boundary: "----MultipartBoundary--"+27 随机字符
  - policy: {"expiration":"<UTC ms>","conditions":[["content-length-range",0,1073741824]]}
  - file 部分 filename = 本地落盘纯毫秒名（比 key 文件名早几十ms）
"""
import base64
import datetime
import hashlib
import hmac
import http.client
import random

OSS_HOST = "lptiyu-data.oss-cn-hangzhou.aliyuncs.com"
_REFER = "https://servicewechat.com/wxb2043232183dac7f/13/page-frame.html"
_UA = ("Mozilla/5.0 (Linux; Android 10; Redmi Note 7 Build/QKQ1.190910.002; wv) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/142.0.7444.173 "
       "Mobile Safari/537.36 XWEB/1420283 MMWEBSDK/20260604 MMWEBID/2837 "
       "MicroMessenger/8.0.77.3160(0x28004D35) WeChat/arm64 Weixin NetType/WIFI "
       "Language/zh_CN ABI/arm64 MiniProgramEnv/android")


def iso_utc_ms(ms):
    ms = int(ms)
    s = datetime.datetime.fromtimestamp(ms / 1000.0, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.")
    return s + f"{ms % 1000:03d}Z"


def make_record_file(rnd=None):
    """返回 (fname_ms, record_file, fname)。dir 段与 fname 来自不同时刻（真机两次 Date.now()）"""
    rnd = rnd or random.Random()
    now_ms = int(datetime.datetime.now().timestamp() * 1000)
    dir_ms = now_ms - rnd.randint(8, 60)
    dir3 = f"{dir_ms % 1000:03d}"
    fname_ms = dir_ms + rnd.randint(5, 40)
    fname = f"{fname_ms}-{rnd.randint(10, 99)}.txt"
    record_file = f"run_record/{datetime.date.today().isoformat()}/{dir3}/{fname}"
    return fname_ms, record_file, fname


def upload_to_oss(file_b64_text, sts, now_ms, record_file, rnd=None):
    """复刻 wx.uploadFile → OSS POST。返回 (http_status, body)"""
    rnd = rnd or random.Random()
    key = "Public/Upload/file/" + record_file
    policy_src = ('{"expiration":"%s","conditions":[["content-length-range",0,1073741824]]}'
                  % iso_utc_ms(now_ms + 3600000))
    policy = base64.b64encode(policy_src.encode()).decode()
    signature = base64.b64encode(hmac.new(sts["AccessKeySecret"].encode(),
                                          policy.encode(), hashlib.sha1).digest()).decode()
    boundary = "----MultipartBoundary--" + "".join(
        rnd.choices("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=27))

    def field(name, value):
        return (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n").encode()

    local_fname = f"{int(now_ms) - rnd.randint(20, 90)}.txt"
    file_part = (f"--{boundary}\r\n"
                 f"Content-Disposition: form-data; name=\"file\"; filename=\"{local_fname}\"\r\n"
                 f"Content-Type: text/plain\r\n\r\n").encode() + file_b64_text.encode() + b"\r\n"
    body = b"".join([
        field("OSSAccessKeyId", sts["AccessKeyId"]),
        field("key", key),
        field("policy", policy),
        field("signature", signature),
        field("x-oss-security-token", sts["SecurityToken"]),
        file_part,
        f"--{boundary}--\r\n".encode(),
    ])

    conn = http.client.HTTPSConnection(OSS_HOST, timeout=30)
    try:
        conn.default_headers.clear()
    except Exception:
        pass
    conn.putrequest("POST", "/", skip_accept_encoding=True)
    conn.putheader("Connection", "keep-alive")
    conn.putheader("Content-Length", str(len(body)))
    conn.putheader("charset", "utf-8")
    conn.putheader("content-type", f"multipart/form-data; boundary={boundary}")
    conn.putheader("Referer", _REFER)
    conn.putheader("User-Agent", _UA)
    conn.putheader("Accept-Encoding", "gzip, deflate, br")
    conn.endheaders(body)
    resp = conn.getresponse()
    status = resp.status
    try:
        rbody = resp.read().decode("utf-8", "replace")
    except Exception:
        rbody = ""
    conn.close()
    return status, rbody
