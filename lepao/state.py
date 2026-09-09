# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""lepao.state —— 本地状态持久化（state.json: 提交/干跑证据链）"""
import datetime
import json
import os


class LocalState:
    def __init__(self, path):
        self.path = path
        self.dir = os.path.dirname(path)
        os.makedirs(self.dir, exist_ok=True)
        self.data = {"dryruns": [], "submissions": [], "watch_log": []}
        if os.path.exists(path):
            try:
                self.data.update(json.load(open(path, encoding="utf-8")))
            except Exception:
                pass

    def _persist(self):
        json.dump(self.data, open(self.path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    def _now(self):
        return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    def save_dryrun(self, out, uid=None, member=None):
        e = {"at": self._now(), "record_file": out.get("oss_record_file"),
             "points": out.get("track_points"),
             "style": (out.get("stopRun_biz") or {}).get("game_id"),
             "uid": uid, "member": member}
        self.data["dryruns"].append(e)
        self.data["dryruns"] = self.data["dryruns"][-30:]
        self._persist()
        p = os.path.join(self.dir, "dryrun_最新.json")
        json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def save_submission(self, summary):
        e = dict(summary)
        e["at"] = self._now()
        self.data["submissions"].append(e)
        self.data["submissions"] = self.data["submissions"][-60:]
        self._persist()

    def log_watch(self, record_id, status, reason=""):
        self.data["watch_log"].append({"at": self._now(), "record_id": record_id,
                                       "status": status, "reason": reason})
        self.data["watch_log"] = self.data["watch_log"][-120:]
        self._persist()

    def last_submission(self):
        return self.data["submissions"][-1] if self.data["submissions"] else None
