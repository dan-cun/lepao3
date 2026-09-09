# 乐跑打卡助手（桌面版 EXE）· 数体智慧体育

> **授权**：本程序按「乐跑研究协议 1.0（LRL-1.0）」提供 —— 仅供学习 · 不可牟利 ·
> 再发布须署名引用原始仓库 <https://github.com/dan-cun/lepao3> 并保留全部声明（LICENSE/NOTICE/版权头）。
> 界面「关于·许可证」与启动日志常驻署名；删除声明即自动终止授权。技术讨论 QQ 1224145544。

> 给"正常用户"用的可视化控制台：双击 exe → 点「▶ 启动程序」→ 电脑微信打开小程序
> 「数体智慧体育」到跑步首页 → 工具**自动**完成 抓流量(token)→归位→提交→复核，
> 全程自动复原系统代理。手机端零操作。

## 一键使用（最终用户）
```text
1. 双击  乐跑打卡助手.exe  （首次使用会提示缺少依赖/登记成员）
2. 点    成员管理 → 新增成员: 填 姓名 + 学号(该同学须已同意)
3. 点    ▶ 启动程序
4. 电脑微信打开小程序「数体智慧体育」到跑步首页（工具会尝试自动开微信并持续提示）
5. 工具检测到登录流量后 → 自动提交 → 自动复核 → 显示结果
```
- 一个成员打完自动标"已消耗"，点「▶ 启动程序」换微信登录下一个成员即可（或批量由
  命令行 `cli.py batch` 承担，见根目录 README）。
- 不确定时先点「干跑验证(零写入)」——走全链路但不上传不提交。
- 「只抓号」用于首次登记或单独补号。
- 「停止并复原」= 停抓链 + 恢复系统代理。

## 运行目录（数据放哪）
| 模式 | 位置 | 说明 |
|---|---|---|
| 便携模式(默认) | `exe同目录\runtime\` | 首次自动创建; config.json/凭证/日志都在这里 |
| 操作员模式 | exe 放已有运行目录（旁边有 config.json） | 检测到旁边有 config.json 即直接复用 |
| 覆盖 | 环境变量 `LEPAO_RUNNER_DIR` | 指向任意 v2 运行目录 |

依赖自检点（右上「环境自检」/右下状态点）：
- **mitmweb**：PATH 有即用；或在 `runtime\runner.json` 写 `{"mitmweb":"完整路径"}`；
  或用环境变量 `LEPAO_MITM_EXE`。
- **Clash(7897 出口链)**：需在跑；`runner.json` 可改 `clash_upstream`。
- **微信**：自动查找常见安装路径；找不到会在 `runner.json` 里指定 `"wechat"`。
- **成员**：成员表(学号键)即 mitm 白名单——未登记的流量一律不落盘。

## 打包(开发者, 本目录)
```powershell
powershell -ExecutionPolicy Bypass -File build_runner.ps1
# 产物: dist\乐跑打卡助手.exe  (onefile ~20MB, 无需 Python)
# 验证: .\dist\乐跑打卡助手.exe --selfcheck   (写 runtime\selfcheck.json)
```
环境: Python 3.11 + `pip install pyinstaller pillow`；PyInstaller 版本 6.x 实测。
内嵌: lepao 协议库(解密/签名/轨迹/OSS) + 轨迹模板 + mitm 抓号 addon + 图标。
打包前请确认 `..\lepao\capture.py` 已支持 `LEPAO_*` 环境覆盖(打包机/目标机路径不同)。

## 实现说明
- `runner_ui.py`  Tk 控制台界面(深色控制台风): 日志流/状态灯/成员选择/一键动作;
  全部动作放 worker 线程, queue 泵到界面, 可「停止并复原」。
- `run_core.py`  无 GUI 依赖的运行核心: 运行目录/环境变量/依赖自检/链管理/
  等 token / 自动归位 / 提交 / 复核; 复用 lepao 包(accounts/auth/capture/pipeline)。
- `make_icon.py` 生成应用图标(需 Pillow + 微软雅黑)。

## 注意
- 只打卡**已登记且同意**的成员; 每成员每日有效 ≤1 次; 错峰, 禁并发。
- 服务端有防作弊与抽查; 本工具用于本人及知情同意的同学日常自动化, 风险自担。
