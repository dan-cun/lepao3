# 乐跑协议研究 · Lepao Research（乐跑打卡助手）

> **版权与授权（请先阅读）**　Copyright (c) 2026 **dan-cun**
> 原始仓库：<https://github.com/dan-cun/lepao3>
> 许可证：**乐跑研究协议 1.0（LRL-1.0）** —— **非商业授权软件**。
> · **仅供学习**（协议逆向 / 抓包 / 加密算法研究）。本研究与任何学校、乐跑平台、腾讯**无关联、无授权、无背书**。
> · **不可牟利**：禁止售卖、收费代打卡、会员制、广告引流、付费社群分享、嵌入商业产品。
> · **爆改后再发布必须署名引用**：保留全部声明文件与每个源文件顶部的版权头，并在 README 开头、
>   程序运行时可见输出、「关于」界面**三处**注明原作者与上述仓库地址，标注你自己的修改说明，
>   删除作者联系方式改用你自己的，且必须以**同一份 LRL-1.0** 继续授权。
>   **删除或隐藏声明 = 授权自动终止（LRL-1.0 第三条）。**
> · **风险自负**：按"现状（AS IS）"提供，无任何担保；账号限制、成绩作废、数据丢失等后果由使用者承担。
> · 只为**本人或已明确同意代打**的成员服务；凭证与抓包仅存本机，请短期保留、用后即清。
> **技术讨论 + QQ 1224145544**（仅限技术讨论：不提供代打卡、不代恢复数据、不解答学校纪律处理问题）
>
> 完整条款 [LICENSE](LICENSE) ｜ 简明版 [DISCLAIMER.md](DISCLAIMER.md) ｜ 第三方授权清单 [NOTICE](NOTICE) ｜ 披露与下架 [SECURITY.md](SECURITY.md) ｜ 投稿 [CONTRIBUTING.md](CONTRIBUTING.md)
> 发行前自检：`py -3 tools/apply_headers.py --check` && `py -3 tools/verify_release.py`

---

## 内部研究记录（以下为本仓库技术文档，2026-09-08 提交实测定版 · 09-08 v2.2 多成员化）

> 某高校乐跑（lptiyu）微信小程序协议层研究。分层、可复用、带生命周期与状态持久化的系统。
> **2.1.0**: 控制面按 R_OK_REF 成功提交的"token=消耗品"逻辑定版（capture/auth/pipeline/cli）;
> 数据面（crypto/transport/track/record）是逐字段验证过的黄金形态, 冻结不动。
> **2.2.0**: 系统优化 = **多成员注册表 + 批量打卡** —— config 升 v2 accounts 表,
> 每成员（本人 + 已登记同意的成员）token/consumed/策略独立; capture 凭证按身份归位;
> 新增 `account` 管理与 `batch` 批量命令。
> （R_OK_REF status=1 "自动确认有效", 与真人黄金 R_GOLD_REF 逐字段一致）。

## 架构
```
lepao/
  crypto.py     算法层    AES-128-CBC(key/iv 固定) + SignMD5(secret 固定) + random6 [冻结]
  transport.py  传输层    真机指纹头组(小写/顺序/flag头) + 出口绑定链(CONNECT 隧道, mitm→Clash) + 解码
  v3api.py      主API     token 门; 加密 POST → 解密响应; 端点封装(beforeRun/stopRun/getOssSts/getTermList=真门…)
  accounts.py   成员表    config v2 accounts(键=学号): 每成员 uid/school/policy/auth 独立; v1 自动迁移;
                          resolve(选择器: 学号|别名|uid) / key_of_cred(凭证三键归位) / fill_identity(首次抓号回填)
  capture.py    抓取会话  ProxyGuard + mitm 起停 + 等新token + sync(**按凭证身份归位到成员槽**)
  auth.py       生命周期  成员级 token=单活动会话消耗品: set_token→probe(真门)→(用)→mark_consumed
  h5api.py      H5面      token+session 双通道; P15 登录链(dddocr); 免密桥=R4
  track.py      轨迹引擎  template(真人模板反指纹, ✅主力) + math(备用) + log/step 构造 [冻结]
  record.py     文件/OSS  轨迹文件命名仿真(两次Date.now()) + OSS STS 上传(真机 multipart 逐字节) [冻结]
  pipeline.py   流水线    0真门→1跑区→2额度→3服务器时间→4轨迹→[dry]→6 OSS→7 stopRun→8复核(positive判定)→consumed
  state.py      持久化    state.json 证据链(dryrun/提交/复核, 记录含 uid/成员标识)
cli.py          入口      account/run/capture/batch/status/quota/build/submit/watch/set-token/h5-*
config.json     配置      v2: version/active/accounts{}/environment/policy/runtime
config.sample.json       占位模板(初始化/参考)
proxy_backup.json         系统代理原值持久备份(capture 自动维护, 复原依据)
data/真实轨迹_035明文.json  真人轨迹模板(本地文件; 含真实个人轨迹数据, 不入库, 需自备)
```

## config v2（成员注册表）
```jsonc
{ "version": 2,
  "active": "XXXXXXXXX",            // 默认操作成员(命令不带选择器时用)
  "accounts": {                           // 键 = 学号（登记即可, 抓号后回填 uid）
    "XXXXXXXXX": { "uid": 0000000, "school_id": 837, "student_num": "…",
                         "card_id": "…", "name": "…", "alias": "…", "enabled": true,
                         "policy": {},    // 成员级覆盖(距离窗/配速窗/style), 空=继承全局
                         "auth": { "token": …, "consumed_at": …, "probe_evidence": […] } },
    "…其他成员(已同意代打, 登记后即入 mitm 白名单)…" },
  "environment": {…}, "policy": {…全局默认…},
  "runtime": { "spacing_min": 5, "spacing_max": 10 } }   // batch 错峰间隔(分钟)
```
v1 平铺(account/auth)配置首次读取时**自动迁移**, 无需手工改。

## 鉴权与出口模型（R3/R4/R5 实证, 每成员独立）
- **token = 即抓即用消耗品**: 单活动会话(新 loginByCode 顶旧号); 无统一 TTL;
  死亡形态恒 101; 提交完成即 mark_consumed → 该成员下次打卡前重新 capture。
- **出口绑定**: 业务会话与登录出口同态 → 直连必 101, 必须走真机同款链
  `系统代理→mitm(127.0.0.1:8081)→Clash(7897)`。capture/run/batch 自动保证。
- **真门 = getTermList**（WpRun）; getSchoolInfo 是免 token 公开口(假阳性, 已弃用)。
- **白名单**: mitm addon 只放行 accounts 表登记成员的 uid/学号/卡号(见 工具/mitm_lptiyu_token.py);
  未登记者的凭证拒绝落盘 → **先 account add 登记, 该成员登录才有意义**。

## 每日打卡闭环（定版操作）
**新机/异地首次部署先跑 `.\一键配置.ps1`**（含 mitmproxy 根证书自动安装：证书不受信 = 微信
拒绝 mitm 证书 → 永远抓不到登录流量；`py -3 cli.py cert` 可随时体检，`cert --install` 可修）。
```bash
cd <项目根目录>
py -3 cli.py account list                     # 成员总览(未抓号/在使用/已消耗)
py -3 cli.py run --member 某成员              # 一键: 该成员登录小程序→capture→提交→watch
py -3 cli.py run                              # 同左, 目标=active 成员
py -3 cli.py batch --submit                   # 全员逐个提交: 有可用token的自动打, 缺号的提示重抓
#   成员登记(本人之外须先征得同意):
py -3 cli.py account add <名字> <学号> [--alias 短名] [--school 837]
#   单个成员缺号时的两步:
py -3 cli.py capture --member 某成员          # 该成员登录小程序 → token 归位到其槽
py -3 cli.py submit --member 某成员           # 用其(未消耗)token 提交
#   查看/复核:
py -3 cli.py status --all                     # 全员 token 状态
py -3 cli.py watch <record_id> --member 某成员
```
退出码: 0 过审 | 2 token 失效/待抓号 | 3 额度满 | 4 其他(含链不通) | 5 受理但判无效

## 运行纪律（硬约束）
1. **只打卡已登记成员**（本人 + 明确同意代打的同学）; 登记=白名单, 未登记凭证不会落盘。
2. `build`/`batch` 默认 dry-run 零写入；`submit`/`run`/`batch --submit` 才真正打卡。
3. 不自动操作微信：token 由 mitm **被动**捕获（人开小程序, addon 落 凭证.json,
   按 uid/学号归位进对应成员槽）。
4. 每成员每日上限 1 次有效（policy.max_per_day=1）; 判无效记录不占位由服务端裁定,
   复核 exit 5 后可重试。**禁止并发/同刻多成员提交**, 用 batch 错峰(5~10min 随机)。
5. 模板引擎为唯一推荐 style（数学圆形态曾被服务端异步作废 status=5，先例在前）。

## 许可与免责声明

本项目按根目录 [LICENSE](LICENSE) 中的《乐跑研究协议 1.0（LRL-1.0）》发布，要点：
**不可牟利** · 修改/再分发**必须引用出处**（原作者 dan-cun + 原始仓库地址，README/运行输出/关于界面三处）
且完整保留声明与源文件版权头 · **仅供学习研究** · 相同方式共享（衍生版继续用 LRL-1.0） ·
**删除或篡改声明即自动终止授权** · 作者不作任何担保、不承担因使用产生的任何后果（含账号处理）。
简明版见 [DISCLAIMER.md](DISCLAIMER.md)，第三方组件授权见 [NOTICE](NOTICE)，
披露政策 [SECURITY.md](SECURITY.md)，贡献规则 [CONTRIBUTING.md](CONTRIBUTING.md)。
**技术讨论：QQ 1224145544**（仅作技术讨论）。
维护工具：`tools/apply_headers.py`（版权头/构建戳）与 `tools/verify_release.py`（发行门禁）。

## 版本
- 2.2.0（本版）: 多成员注册表(v2 config) + 凭证身份归位 capture + account/batch CLI
  + 白名单注册表化(uid/学号双键) + 成员级 policy; 数据面继续冻结。
- 2.1.0（09-08 定版）: 消耗品模型 + capture/ProxyGuard + run 一键闭环 + 真门探测
  + positive 判定 + sync 死号守卫 + 链断友好报错。
- 2.0.0: 体系重构（分层模块 + 统一 CLI）。
