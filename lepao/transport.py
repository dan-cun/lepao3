# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""lepao.transport —— 传输层: 真机指纹头组 + 出口链约束 + 响应解码

真机指纹（2026-09-07 flows_手机 逐字节实录）:
  - 头名全小写, 固定顺序: content-length, content-type, [flag], charset, referer, user-agent, accept-encoding
  - loginByCode / stopRunV278 额外带 `flag: [object Boolean]`（小程序 JS String(Boolean) 缺陷）

出口绑定（2026-09-08 R3 实测修正, 覆盖旧"WAF 拒代理 405→必须直连"误诊）:
  - 会话接口(beforeRunV260/stopRunV278/…)与**登录时的出口路径同态**:
    真机走 系统代理→mitm(8081)→Clash(7897), 同 token 直连或纯 Clash 一律 101,
    走同一条 mitm 链则 200（节点局部会话）。
  - ⇒ proxy 不是"例外开关"而是**常态**: config.environment.api_proxy=127.0.0.1:8081
    （由 cli capture/run 自动保证链在位）。旧"405 拒代理"诊断源于当时 token 恰好已死。
"""
import gzip
import http.client
import zlib

try:
    import brotli as _brotli
except ImportError:
    try:
        import brotlicffi as _brotli
    except ImportError:
        _brotli = None

APP_ID = "wxb2043232183dac7f"
REFERER = f"https://servicewechat.com/{APP_ID}/13/page-frame.html"

UA_ANDROID = ("Mozilla/5.0 (Linux; Android 10; Redmi Note 7 Build/QKQ1.190910.002; wv) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/142.0.7444.173 "
              "Mobile Safari/537.36 XWEB/1420283 MMWEBSDK/20260604 MMWEBID/2837 "
              "MicroMessenger/8.0.77.3160(0x28004D35) WeChat/arm64 Weixin NetType/WIFI "
              "Language/zh_CN ABI/arm64 MiniProgramEnv/android")

_ACCEPT_ENC = "gzip, deflate, br" if _brotli else "gzip, deflate"


def make_conn(host: str, port: int = 443, timeout: int = 15,
              proxy: str = None):
    """建立连接。proxy='host:port' 时走 CONNECT 隧道（打卡常态: mitm 8081→Clash,
    与登录出口同态, 见模块头"出口绑定"）; None/空 = 直连（对登录链外的会话必 101）。"""
    if proxy and ":" in proxy:
        ph, pp = proxy.rsplit(":", 1)
        conn = http.client.HTTPSConnection(ph, int(pp), timeout=timeout)
        conn.set_tunnel(host, port)
    else:
        conn = http.client.HTTPSConnection(host, port, timeout=timeout)
    try:
        conn.default_headers.clear()
    except Exception:
        pass
    return conn


def post_form(conn, path: str, body: bytes,
              flag: bool = False, accept_encoding: str = None):
    """以真机指纹头组发送 form POST（头名小写+顺序锁定）"""
    conn.putrequest("POST", path, skip_accept_encoding=True)
    conn.putheader("content-length", str(len(body)))
    conn.putheader("content-type", "application/x-www-form-urlencoded")
    if flag:
        conn.putheader("flag", "[object Boolean]")
    conn.putheader("charset", "utf-8")
    conn.putheader("referer", REFERER)
    conn.putheader("user-agent", UA_ANDROID)
    conn.putheader("accept-encoding", accept_encoding or _ACCEPT_ENC)
    conn.endheaders(body)
    return conn.getresponse()


def decode_body(raw: bytes, content_encoding: str) -> bytes:
    """按 Content-Encoding 解码（gzip/br/deflate）"""
    enc = (content_encoding or "").lower().strip()
    try:
        if "gzip" in enc:
            return gzip.decompress(raw)
        if "br" in enc and _brotli:
            return _brotli.decompress(raw)
        if "deflate" in enc:
            try:
                return zlib.decompress(raw, -15)
            except zlib.error:
                return zlib.decompress(raw)
    except Exception:
        return raw
    return raw
