# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""Lepao（乐跑协议体系 v2.2）—— 微信小程序协议逆向研究：多成员批量打卡

层次:
  crypto    算法层 (AES-128-CBC / SignMD5)
  transport 传输层 (真机指纹头组 / 直连约束 / 解码)
  v3api     主 API 客户端 (无状态 token 门)
  h5api     H5 面客户端 (token+session 双通道 / P15 登录链)
  accounts  成员注册表 (v2 config: 每成员独立 uid/auth/policy; v1 自动迁移)
  auth      成员级 token 生命周期 (单活动会话消耗品, 每成员独立)
  capture   token 抓取会话 (白名单=登记成员; 凭证按身份归位)
  track     轨迹引擎 (template 反指纹主力 / math 备用)
  record    文件命名仿真 + OSS 上传
  pipeline  成员级每日任务流水线 (dry-run 优先)
  state     本地证据链持久化

纪律: 只提交登记成员(本人+已同意者)的打卡; 提交必须显式 --submit/run/batch;
token 由 mitm 被动捕获并按身份归位; 每成员每日 1 次有效, 批量错峰不并发。
"""
__version__ = "2.2.0"
