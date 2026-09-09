# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""lepao.auth —— 成员级 token 生命周期（v2.2: 每个成员独立一套消耗品状态）

实证模型（承接 v2.1, R3/R4 实弹）:
  - access_token = **单活动会话消耗品**: 每次 loginByCode 轮换旧号; 死亡形态 101;
    提交完成即标记 consumed → 该成员下次打卡前必须重新 capture。
  - v2.2 多成员化: AuthState 绑定一个成员(accounts 表键); token/consumed/refresh
    证据全部落在该成员自己的 auth 槽, 互不污染; 真门探测 getTermList 不变。
"""
import datetime
import json

from .accounts import AccountRegistry, AccountError
from .v3api import V3Client, V3Error


class AuthState:
    def __init__(self, registry: AccountRegistry, key: str = None):
        self.reg = registry
        self.config_path = registry.config_path
        self.key = registry.resolve(key)
        self.config = registry.config
        self._profile = registry.acc(self.key)
        self._auth = self._profile.setdefault("auth", {})
        if not isinstance(self._auth, dict):
            self._auth = self._profile["auth"] = {}

    # ------------------------------------------------------------ 成员/身份
    @property
    def member_key(self):
        return self.key

    @property
    def uid(self):
        return self._profile.get("uid") or None

    @property
    def school_id(self):
        return self._profile.get("school_id") or 0

    @property
    def student_num(self):
        return str(self._profile.get("student_num") or "")

    @property
    def card_id(self):
        return str(self._profile.get("card_id") or self.student_num or "")

    @property
    def label(self):
        a = self._profile
        return a.get("alias") or a.get("name") or self.key

    def require_uid(self):
        if not self.uid:
            raise AccountError(
                f"成员[{self.label}]尚未完成首次抓号(缺 uid): "
                f"请该成员在小程序登录一次, 跑 `capture --member {self.label}`")
        return self.uid

    # ------------------------------------------------------------ token 字段
    @property
    def token(self):
        return self._auth.get("token", "")

    @token.setter
    def token(self, v):
        self._auth["token"] = v or ""

    @property
    def issued_at(self):
        return self._auth.get("issued_at", "")

    @issued_at.setter
    def issued_at(self, v):
        self._auth["issued_at"] = v or ""

    @property
    def source(self):
        return self._auth.get("source", "")

    @source.setter
    def source(self, v):
        self._auth["source"] = v or ""

    @property
    def refresh_token(self):
        return self._auth.get("refresh_token", "")

    @refresh_token.setter
    def refresh_token(self, v):
        self._auth["refresh_token"] = v or ""

    @property
    def refresh_expire(self):
        return self._auth.get("refresh_expire", 0)

    @refresh_expire.setter
    def refresh_expire(self, v):
        self._auth["refresh_expire"] = int(v or 0)

    @property
    def consumed_at(self):
        return self._auth.get("consumed_at", "")

    @consumed_at.setter
    def consumed_at(self, v):
        self._auth["consumed_at"] = v or ""

    @property
    def ttl_class(self):
        return self._auth.get("ttl_class", "single-active-session")

    @ttl_class.setter
    def ttl_class(self, v):
        self._auth["ttl_class"] = v or "single-active-session"

    @property
    def evidence(self):
        return self._auth.setdefault("probe_evidence", [])

    # ------------------------------------------------------------ 工具
    @staticmethod
    def _now_iso():
        return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    def age_hours(self) -> float:
        if not self.issued_at:
            return 0.0
        try:
            t0 = datetime.datetime.strptime(self.issued_at, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return 0.0
        return (datetime.datetime.now() - t0).total_seconds() / 3600.0

    def persist(self):
        self.reg.persist()

    # ------------------------------------------------------------ 生命周期
    def make_client(self, pace=0.0):
        env = self.config.get("environment", {})
        return V3Client(
            uid=self.require_uid(), token=self.token,
            school_id=self.school_id,
            student_num=self.student_num, card_id=self.card_id,
            host=env.get("api_host", "api2.lptiyu.com"),
            pace=pace,
            proxy=env.get("api_proxy") or None)

    def probe(self, verbose=True):
        """真门存活探测 (getTermList, 无副作用)。返回 True/False, 记录证据。"""
        if not self.token or self.consumed_at:
            if verbose:
                print(f"[auth:{self.label}] 无可用 token/已消耗 → probe 跳过")
            return False
        ok = False
        try:
            r = self.make_client().get_term_list()
            ok = isinstance(r, dict) and r.get("status") != 101
        except V3Error:
            ok = False
        except Exception:
            ok = False
        self.evidence.append({"at": self._now_iso(), "alive": bool(ok),
                              "age_h": round(self.age_hours(), 2)})
        self.evidence[:] = self.evidence[-30:]
        if verbose:
            e = self.evidence[-1]
            print(f"[auth:{self.label}] 真门探测 @ {e['at']} alive={ok} "
                  f"age={e['age_h']}h source={self.source or '-'}")
        self.persist()
        return ok

    def set_token(self, token: str, issued_at: str = None, source: str = "manual",
                  refresh_token: str = None, refresh_expire: int = None):
        """装载新捕获 token（capture 流程 / 人工注入唯一入口）。
        单活动会话: 旧 token 就此作废, 装载前先落 consumed_at 存证。"""
        if self.token and self.token != token and not self.consumed_at:
            self.consumed_at = self._now_iso()
        self.token = token
        self.issued_at = issued_at or self._now_iso()
        self.source = source
        self.consumed_at = ""
        self.ttl_class = "single-active-session"
        if refresh_token:
            self.refresh_token = refresh_token
        if refresh_expire:
            self.refresh_expire = int(refresh_expire)
        self.evidence.append({"at": self._now_iso(), "alive": None, "age_h": 0.0,
                              "note": f"token 装载 source={source}"})
        self.evidence[:] = self.evidence[-30:]
        self.persist()
        print(f"[auth:{self.label}] token 已装载 (issued={self.issued_at}, "
              f"refresh={'在档' if self.refresh_token else '无'})")

    def mark_consumed(self):
        """提交成功即标记消耗——该成员下次打卡必须重新 capture。"""
        if not self.token or self.consumed_at:
            return
        self.consumed_at = self._now_iso()
        self.evidence.append({"at": self._now_iso(), "alive": None,
                              "age_h": round(self.age_hours(), 2),
                              "note": "stopRun 提交完成, token 标记消耗"})
        self.evidence[:] = self.evidence[-30:]
        self.persist()
        print(f"[auth:{self.label}] token 已标记消耗 (该成员下次打卡前重新 capture)")

    def describe(self) -> str:
        if not self.token:
            return f"[{self.label}] 无 token（未抓号/未装载）"
        state = "已消耗" if self.consumed_at else "在使用"
        rf = "refresh 30d 在档(P17)" if self.refresh_token else "无 refresh"
        return (f"[{self.label}] uid={self.uid or '-'} "
                f"token={self.token[:8]}… [{state}] 签发={self.issued_at or '-'} "
                f"龄={self.age_hours():.2f}h | {rf}")
