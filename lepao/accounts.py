# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""lepao.accounts —— 多成员账号注册表（v2.2 新增: 支持完成多个成员的每日打卡）

背景（承接 v2.1 消耗品模型）:
  - v2.1 只有"本人单账号"（config.account + config.auth 平铺）。
    系统优化目标: 在登记表中登记"本人 + 已同意代打的其他成员"，各自
    token（单活动会话消耗品）/consumed/策略完全独立 → 逐成员闭环打卡。

config.json v2 schema:
{
  "version": 2,
  "active": "<member_key>",            // 默认操作对象（cli 不带选择器时用它）
  "accounts": {                        // 成员表; key = 学号(首次抓号前即可登记)
    "<student_num>": {
      "uid": 0,                        // loginByCode 首次抓号后自动回填
      "school_id": 837,
      "student_num": "…",
      "card_id": "…",
      "name": "显示名", "alias": "短别名",
      "enabled": true,
      "remark": "",
      "policy": {},                    // 覆盖全局 policy（距离窗/配速窗/style）
      "auth": {                        // 该成员独立 auth(消耗品模型字段)
        "token": "", "issued_at": "", "source": "",
        "consumed_at": "", "refresh_token": "", "refresh_expire": 0,
        "ttl_class": "single-active-session", "probe_evidence": []
      }
    }, ...
  },
  "environment": {…},                 // 不变
  "policy": {…全局默认…},              // 不变 + 可被成员级覆盖
  "runtime": {"spacing_min": 5, "spacing_max": 10}   // batch 相邻成员错峰间隔(分钟)
}

v1 平铺 (account/auth) 自动迁移 → v2，无需手工改。
成员身份三键（uid / student_num / card_id）任一匹配即可归位凭证。
"""
import json
import os


class AccountError(Exception):
    pass


def _digits(x):
    try:
        return str(int(x))
    except (TypeError, ValueError):
        return None


class AccountRegistry:
    """config.json 的 v2 视图: 成员增删改查 + 选择器 + 凭证归位 + 迁移。"""

    def __init__(self, config_path: str):
        self.config_path = config_path
        if not os.path.exists(config_path):
            raise FileNotFoundError(
                f"配置不存在: {config_path}（首次使用先 `account add` 创建, "
                f"或复制 config.sample.json）")
        with open(config_path, encoding="utf-8-sig") as f:
            self.config = json.load(f)
        self._migrate()

    # ---------------------------------------------------------- 持久化/迁移
    def persist(self):
        json.dump(self.config, open(self.config_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    def _migrate(self):
        """v1(account/auth 平铺) → v2(accounts 表)。幂等。"""
        cfg = self.config
        if cfg.get("version") == 2 and isinstance(cfg.get("accounts"), dict):
            return
        legacy = cfg.pop("account", None) or {}
        legacy_auth = cfg.pop("auth", None) or {}
        acc = {
            "uid": legacy.get("uid") or 0,
            "school_id": legacy.get("school_id") or 0,
            "student_num": str(legacy.get("student_num") or ""),
            "card_id": str(legacy.get("card_id") or legacy.get("student_num") or ""),
            "name": legacy.get("name") or "本人",
            "alias": legacy.get("alias") or "本人",
            "enabled": True,
            "remark": "v1 平铺配置自动迁移",
            "policy": {},
            "auth": legacy_auth,
        }
        key = acc["student_num"] or str(acc["uid"])
        cfg.setdefault("accounts", {})[key] = acc
        cfg.setdefault("active", key)
        cfg.setdefault("runtime", {"spacing_min": 5, "spacing_max": 10})
        cfg.setdefault("policy", {})
        cfg["version"] = 2
        self.persist()

    # ---------------------------------------------------------- 成员访问
    def keys(self):
        return list(self.config.get("accounts", {}).keys())

    def acc(self, key: str) -> dict:
        a = self.config.get("accounts", {}).get(key)
        if a is None:
            raise AccountError(f"成员不存在: {key}")
        return a

    def enabled_keys(self):
        return [k for k in self.keys() if self.acc(k).get("enabled", True)]

    # ---------------------------------------------------------- 选择器
    def resolve(self, sel=None) -> str:
        """把 None/uid/学号/别名/name/键 解析为成员键。
        未指定: active → 第一个 enabled。失败抛 AccountError。"""
        accounts = self.config.get("accounts", {})
        if not accounts:
            raise AccountError("成员表为空: 先 `cli.py account add <name> <student_num>`")
        cand = None
        if sel is not None and str(sel).strip():
            s = str(sel).strip()
            if s in accounts:
                cand = s
            else:
                d = _digits(s)
                for k, a in accounts.items():
                    if d is not None and _digits(a.get("uid")) == d:
                        cand = k
                        break
                if cand is None:
                    for k, a in accounts.items():
                        if a.get("alias") == s or a.get("name") == s \
                                or str(a.get("student_num") or "") == s \
                                or str(a.get("card_id") or "") == s:
                            cand = k
                            break
        if cand is None:
            act = self.config.get("active")
            if act and act in accounts:
                cand = act
        if cand is None:
            for k, a in accounts.items():
                if a.get("enabled", True):
                    cand = k
                    break
        if cand is None:
            raise AccountError(f"选择器无匹配成员: {sel!r}")
        return cand

    def resolve_all(self, sel=None):
        """批量场景: 指定则 [key], 否则全部 enabled 成员(按登记序)。"""
        if sel is not None and str(sel).strip():
            return [self.resolve(sel)]
        return [k for k in self.keys() if self.acc(k).get("enabled", True)]

    # ---------------------------------------------------------- 凭证归位
    def key_of_cred(self, cred: dict):
        """按抓包凭证的 uid/student_num/card_id 找成员键。None=无匹配。"""
        if not cred:
            return None
        c_uid = _digits(cred.get("uid"))
        c_sn = _digits(cred.get("student_num"))
        c_cd = _digits(cred.get("card_id"))
        for k, a in self.config.get("accounts", {}).items():
            if c_uid and _digits(a.get("uid")) == c_uid:
                return k
        for k, a in self.config.get("accounts", {}).items():
            if c_sn and _digits(a.get("student_num")) == c_sn:
                return k
            if c_cd and _digits(a.get("card_id")) == c_cd:
                return k
        if c_sn and c_sn in self.config.get("accounts", {}):
            return c_sn
        return None

    def fill_identity(self, key: str, cred: dict):
        """首次抓号: 用凭证回填缺失身份字段(uid/school_id/card_id)。uid=0 视为缺失。"""
        a = self.acc(key)
        changed = False

        def _uid_missing(acc_uid):
            d = _digits(acc_uid)
            return d is None or int(d) == 0

        if _digits(cred.get("uid")) and _uid_missing(a.get("uid")):
            a["uid"] = int(cred["uid"]); changed = True
        if cred.get("school_id") and not a.get("school_id"):
            a["school_id"] = cred["school_id"]; changed = True
        if cred.get("student_num"):
            a["student_num"] = str(cred["student_num"]); changed = True
        if cred.get("card_id"):
            a["card_id"] = str(cred["card_id"]); changed = True
        if changed:
            self.persist()
        return changed

    # ---------------------------------------------------------- CRUD
    def add(self, name: str, student_num: str, alias=None, school_id=0,
            uid=0, remark="", card_id=None):
        key = str(student_num).strip()
        if not key:
            raise AccountError("学号不能为空（成员键）")
        if key in self.config.get("accounts", {}):
            raise AccountError(f"成员已存在: {key}")
        self.config.setdefault("accounts", {})[key] = {
            "uid": int(uid) if uid else 0,
            "school_id": int(school_id) if school_id else 837,
            "student_num": key,
            "card_id": str(card_id or key),
            "name": name,
            "alias": alias or name,
            "enabled": True,
            "remark": remark,
            "policy": {},
            "auth": {"token": "", "issued_at": "", "source": "",
                     "consumed_at": "", "refresh_token": "",
                     "refresh_expire": 0,
                     "ttl_class": "single-active-session",
                     "probe_evidence": []},
        }
        # 空表/无有效 active 时, 新成员自动成为 active; 已有 active 不抢占
        act = self.config.get("active")
        if not act or act not in self.config.get("accounts", {}):
            self.config["active"] = key
        self.persist()
        return key

    def remove(self, sel) -> str:
        key = self.resolve(sel)
        del self.config["accounts"][key]
        if self.config.get("active") == key:
            rest = self.keys()
            self.config["active"] = rest[0] if rest else ""
        self.persist()
        return key

    def set_flag(self, sel, enabled: bool) -> str:
        key = self.resolve(sel)
        self.acc(key)["enabled"] = enabled
        self.persist()
        return key

    def set_active(self, sel) -> str:
        key = self.resolve(sel)
        self.config["active"] = key
        self.persist()
        return key

    def global_policy(self) -> dict:
        return dict(self.config.get("policy", {}) or {})

    def member_policy(self, key: str) -> dict:
        p = self.acc(key).get("policy") or {}
        return dict(p)

    def merged_policy(self, key: str, extra: dict = None) -> dict:
        pol = self.global_policy()
        pol.update(self.member_policy(key))
        if extra:
            pol.update(extra)
        return pol
