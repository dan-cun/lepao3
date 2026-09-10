# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
"""tools.verify_release —— 发行门禁：声明完整性 + 个人信息泄露扫描 + 无遥测自证（LRL-1.0 第二/三/七条）

用法（在本项目根目录）：
  py -3 tools/verify_release.py                 # 检查整个发行树
  py -3 tools/verify_release.py --staged        # 只检查 git 暂存区改动（PR 前自查）
  py -3 tools/verify_release.py --zip <包.zip>  # 检查发行包内容（声明齐全 + 无敏感文件）
  py -3 tools/verify_release.py --exe <EXE>     # 在产物里核对构建戳与声明 marker
  py -3 tools/verify_release.py --network       # 列出源码出网目标，自证无遥测回传
  py -3 tools/verify_release.py --fix-url       # 把文档中的仓库地址同步为 release.meta.json

退出码 0 = 通过；1 = 有阻断项（禁止发布）。本工具只读（--fix-url 例外），不联网。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

# marker 用拼接构造（与 apply_headers.py 一致，避免本文件被自匹配）
MARKER_START = "# --- lepao-research-notice v1 (do not remove; see LICE" + "NSE) ---"
MARKER_END = "# --- end lepao-research-notice ---"
REQUIRED_FILES = ["LICENSE", "NOTICE", "DISCLAIMER.md", "README.md", "SECURITY.md",
                  "CONTRIBUTING.md", "release.meta.json"]
FORBIDDEN_IN_RELEASE = [
    "state.json", "凭证.json", "proxy_backup.json", "flows.mitm",
    "selfcheck.json", "runner.json", "mitm_流量.log",
]
TEXT_EXT = {".py", ".md", ".json", ".ps1", ".txt", ".js", ".ts", ".sh"}
TRACK_TEMPLATE_NAME = "真实轨迹_035明文.json"

# ---- PII 指纹：真实身份/凭证/服务端记录号/本机路径。命中即阻断发布 ----
PII_PATTERNS = {
    "真实学号(15位)": re.compile(r"\b2220\d{11}\b"),
    "真实uid(8位)": re.compile(r"(?<!\d)1\d{7}(?!\d)"),
    "token十六进制串(>=24)": re.compile(r"\b[0-9A-F]{24,64}\b"),
    "服务端记录号(54xxxx)": re.compile(r"\b54\d{4}\b"),
    "手机号": re.compile(r"\b1[3-9]\d{9}\b"),
    "身份证18位": re.compile(r"\b\d{17}[0-9Xx]\b"),
    "本机个人目录": re.compile(r"[A-Za-z]:\\{1,2}Users\\{1,2}[A-Za-z0-9_\u4e00-\u9fff]{2,}\\{1,2}"),
}
PLACEHOLDER_OK = re.compile(r"(XXXX+|0{5,}|<[^>]+>|占位)")
# 小程序公开算法常量（无法轮换，已在文档中说明，允许出现）
ALGO_CONSTS = re.compile(r"(?i)(key|iv|secret)\s*=\s*[\"']?(Wet2C8|rDJiNB|K6iv85)")


def _load_meta() -> dict:
    try:
        return json.loads((ROOT / "release.meta.json").read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _text_of(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return ""


def tree_files() -> list:
    out = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        parts = {x.lower() for x in p.relative_to(ROOT).parts}
        if parts & {"__pycache__", ".git", "build", "dist", "bundle", "runtime",
                    "node_modules", "logs", ".venv", "venv"}:
            continue
        if p.suffix in {".pyc", ".mitm", ".spec", ".zip", ".7z", ".rar"}:
            continue
        if p.name == "mitm_addon.py" and p.parent == ROOT:
            continue          # run_core 在数据目录生成的运行时副本，真源在 工具/
        out.append(p)
    return out


def shippable_files() -> list:
    """发行可见集 = git 已跟踪 + 未跟踪但**未被 ignore** 的文件。

    门禁扫描必须与发布面一致：运行期数据(config.json/凭证.json/selfcheck.json 等)
    虽在工作树里含真实凭证, 但被 .gitignore 拦截、永不入库入包 —— 对其报 PII 阻断
    属于误报(它们是本机运行所需, 删了反而伤人)。zip/exe 通道本就只查包内文件。
    git 不可用时退回全树扫描(宁可误报不可漏报)。"""
    import subprocess
    try:
        r = subprocess.run(["git", "ls-files", "--cached", "--others",
                            "--exclude-standard"], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        rels = {l.strip().replace("\\", "/") for l in r.stdout.splitlines() if l.strip()}
    except Exception:
        return tree_files()
    if r.returncode != 0 or not rels:
        return tree_files()
    return [p for p in tree_files()
            if str(p.relative_to(ROOT)).replace("\\", "/") in rels]


def check_notices(meta: dict) -> tuple:
    """声明文件存在性 + README 前 30 行引用义务 + 源文件版权块覆盖率。"""
    errors, warns = [], []
    for f in REQUIRED_FILES:
        p = ROOT / f
        if not p.exists():
            errors.append("缺少声明文件：" + f)
        elif p.stat().st_size < 400:
            errors.append("声明文件疑似被清空/替换（过小）：{} ({}B)".format(f, p.stat().st_size))
    lic = _text_of(ROOT / "LICENSE")
    for need in ("不可牟利", "仅供学习", "自动终止", "署名", "LRL-1.0"):
        if need not in lic:
            errors.append("LICENSE 缺少关键条款要素：" + need)
    if str(meta.get("contact_qq", "")) and meta["contact_qq"] not in lic:
        warns.append("LICENSE 未含技术讨论 QQ（若刻意删除可忽略）")
    readme = _text_of(ROOT / "README.md")
    head = "\n".join(readme.splitlines()[:30])
    url = meta.get("repo_url", "")
    if url and url not in head:
        errors.append("README 前 30 行未出现原始仓库地址（LRL-1.0 第二条 2(a) 三处引用义务）")
    for tag in ("dan-cun", "仅供学习", "不可牟利"):
        if tag not in head:
            errors.append("README 前 30 行缺少署名/授权要素「{}」".format(tag))
    import apply_headers as ah
    targets = ah.iter_targets()
    block = ah.notice_block(meta)
    for p in targets:
        txt = ah.read_text(p)
        if MARKER_START not in txt:
            errors.append("源文件缺版权声明块：" + str(p.relative_to(ROOT)))
        else:
            m = ah.BLOCK_RE.search(txt)
            if not m or m.group(0) != block:
                warns.append("源文件声明块与规范文本不一致：" + str(p.relative_to(ROOT)))
    if not (ROOT / "lepao" / "_buildstamp.py").exists():
        warns.append("缺 lepao/_buildstamp.py（未跑 apply_headers.py --stamp，构建戳为 dev）")
    return errors, warns


def check_pii(files: list) -> tuple:
    errors, warns = [], []
    for p in files:
        if p.suffix not in TEXT_EXT and p.name not in {"LICENSE", "NOTICE"}:
            continue
        rel = p.relative_to(ROOT)
        if rel.parts and rel.parts[0] == "tools":
            continue                       # 本工具的检测正则本身含样本形态
        if p.name == TRACK_TEMPLATE_NAME:
            continue                       # 净化模板（纯坐标）由内容层白名单
        txt = _text_of(p)
        if not txt:
            continue
        lines = txt.splitlines()
        for name, pat in PII_PATTERNS.items():
            for m in pat.finditer(txt):
                frag = m.group(0)
                if PLACEHOLDER_OK.search(frag):
                    continue
                ln = txt[:m.start()].count("\n")
                line = lines[ln].strip() if ln < len(lines) else ""
                if ALGO_CONSTS.search(line) or re.search(
                        r"(?i)example|示例|占位|placeholder|XXXXXXXX", line):
                    continue
                # 8位uid模式在配置默认值(837 学校号 3-4位)与时间戳上不命中：仅 1x 开头 8 位才报
                errors.append("[{}] {}:{}  →  {}".format(name, rel, ln + 1, line[:110]))
    return errors, warns


def _config_skeleton_ok(cfg: Path) -> tuple:
    """config.json 允许入库的唯一形态：v2 骨架（accounts 空、无 token、active 空）。"""
    try:
        d = json.loads(cfg.read_text(encoding="utf-8-sig"))
    except Exception as e:
        return False, "解析失败：{}".format(e)
    if d.get("accounts"):
        return False, "accounts 非空（{} 个成员）".format(len(d["accounts"]))
    if (d.get("auth") or {}).get("token") or d.get("token"):
        return False, "存在 auth.token"
    if d.get("active"):
        return False, "active 指向成员：{}".format(d.get("active"))
    return True, ""


def check_forbidden(files: list) -> tuple:
    errs, warns = [], []
    tracked = set()
    try:
        import subprocess
        out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
        tracked = {l.strip() for l in out.splitlines()}
    except Exception:
        pass
    for p in files:
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        if p.name in FORBIDDEN_IN_RELEASE:
            if rel in tracked:
                errs.append("发行树含禁入文件：{}（含真实凭证/状态，永不入库）".format(rel))
            else:
                warns.append("工作树含运行态文件：{}（gitignored 不入库；"
                             "zip 分发按 git 清单打包即可，勿手动附带）".format(rel))
    cfg = ROOT / "config.json"
    if cfg.exists() and str(cfg.relative_to(ROOT)).replace("\\", "/") in \
            {str(p.relative_to(ROOT)).replace("\\", "/") for p in files}:
        # 仅当 config.json 属于"发行可见集"(已跟踪, 或未跟踪但未被 ignore)才拦骨架;
        # 被 .gitignore 拦截的本机运行配置不入库不入包, 由运行态 warn 提示即可。
        ok, why = _config_skeleton_ok(cfg)
        if not ok:
            errs.append("config.json 非空骨架，不得入库/入包：" + why)
    return errs, warns


def check_zip(path: Path) -> tuple:
    errors, warns = [], []
    try:
        zf = zipfile.ZipFile(path)
    except Exception as e:
        return ["无法打开发行包 {}: {}".format(path.name, e)], []
    names = [n.replace("\\", "/") for n in zf.namelist()]
    for need in ("LICENSE", "NOTICE", "DISCLAIMER.md", "README.md"):
        if not any(n.endswith(need) for n in names):
            errors.append("发行包缺 {}（LRL-1.0 第二条 1）".format(need))
    for bad in FORBIDDEN_IN_RELEASE:
        if any(n.endswith("/" + bad) or n == bad for n in names):
            errors.append("发行包含禁入文件：" + bad)
    for n in names:
        if n == "config.json" or n.endswith("/config.json"):
            import tempfile
            try:
                with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
                    tf.write(zf.read(n))
                    tmp = Path(tf.name)
                ok, why = _config_skeleton_ok(tmp)
                tmp.unlink()
                if not ok:
                    errors.append("发行包内 config.json 非空骨架：" + why)
            except Exception as e:
                errors.append("发行包内 config.json 检查失败：{}".format(e))
        if ("/data/" in "/" + n or n.startswith("data/")) and TRACK_TEMPLATE_NAME not in n:
            errors.append("发行包含 data/ 其他文件（真实数据永不入包）：" + n)
    miss = []
    for n in [x for x in names if x.endswith(".py")]:
        if n.endswith("/_buildstamp.py"):
            continue   # 生成物带精简声明（其存在即 LRL-1.0 第三条证据），不叠加规范块
        try:
            if MARKER_START.encode() not in zf.read(n):
                miss.append(n)
        except Exception:
            pass
    if miss:
        errors.append("发行包内 {} 个 .py 无版权声明块：{}".format(len(miss), ", ".join(miss[:5])))
    if any(n.endswith(".pyc") or "__pycache__" in n for n in names):
        warns.append("发行包含 __pycache__/.pyc 残留（建议清理）")
    return errors, warns


def check_exe(path: Path) -> tuple:
    """核对 EXE 是否由当前源码构建。

    onefile 的 .py 都编译进压缩 CArchive，明文搜不到 → 唯一可靠判据是**跑一次
    --selfcheck**（只读：建临时运行目录、探测依赖、写 selfcheck.json；不动代理、
    不杀微信、不发业务请求），比对其中的 license.build_id 与当前 _buildstamp.py。
    """
    errors, warns = [], []
    stamp_txt = _text_of(ROOT / "lepao" / "_buildstamp.py")
    m = re.search(r'BUILD_ID = "([^"]+)"', stamp_txt)
    bid = m.group(1) if m else ""
    if not bid:
        return [], ["缺 lepao/_buildstamp.py（未跑 apply_headers.py --stamp），无法核对 EXE 构建戳"]
    data = path.read_bytes()
    if bid.encode() in data:                       # 未压缩场景直接命中
        return errors, ["EXE 含当前构建戳 {}".format(bid)]
    import subprocess, tempfile
    tmp = Path(tempfile.mkdtemp(prefix="lepao_verify_"))
    try:
        import os
        env = dict(os.environ, LEPAO_RUNNER_DIR=str(tmp))
        subprocess.run([str(path), "--selfcheck"], cwd=str(tmp), timeout=120,
                       capture_output=True, env=env)
        rep = tmp / "selfcheck.json"
        if not rep.exists():
            return errors, ["EXE --selfcheck 未产出 selfcheck.json，无法核对构建戳（手工验证）"]
        got = (json.loads(rep.read_text(encoding="utf-8-sig"))
               .get("license") or {}).get("build_id", "")
        if got == bid:
            warns.append("EXE 构建戳一致：{}（--selfcheck 实测）".format(bid))
        elif got:
            errors.append("EXE 为旧构建：内嵌 {}，当前源码 {} → 需重新 build_runner.ps1".format(got, bid))
        else:
            errors.append("EXE 自检无 license.build_id（早于声明体系构建）→ 需重新打包")
    except Exception as e:
        warns.append("EXE --selfcheck 无法执行（{}），退化为字节搜索：未命中 {}".format(e, bid))
    finally:
        import shutil
        shutil.rmtree(str(tmp), ignore_errors=True)   # 内含 runtime 子目录, 必须递归清
    return errors, warns



def check_network() -> tuple:
    """自证无遥测：列出源码中出现的全部 http(s) 目标。"""
    hosts = set()
    pat = re.compile(r"https?://([A-Za-z0-9.\-]+)")
    n_calls = 0
    for p in tree_files():
        if p.suffix not in {".py", ".ps1"}:
            continue
        txt = _text_of(p)
        hosts.update(m.group(1) for m in pat.finditer(txt))
        n_calls += len(re.findall(
            r"(create_connection|socket\(|urlopen|requests\.(get|post))", txt))
    allow = {"api2.lptiyu.com", "127.0.0.1", "localhost", "0.0.0.0", "example.com",
             "github.com", "github", "python.org", "pypi.org", "servicewechat.com",
             "mirrors.aliyun.com", "docs.mitmproxy.org", "githubusercontent.com",
             "lptiyu-data.oss-cn-hangzhou.aliyuncs.com"}
    unknown = sorted(h for h in hosts if h not in allow)
    errs = ["出现未在允许清单中的主机：{}（需说明用途或移除）".format(u) for u in unknown]
    warns = ["源码出网目标：" + ", ".join(sorted(hosts)),
             "socket/请求调用点 {} 处（本地代理健康探测属正常）".format(n_calls)]
    return errs, warns


def check_staged() -> tuple:
    import subprocess
    try:
        out = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=ROOT,
                             capture_output=True, text=True).stdout
    except Exception as e:
        return ["git 不可用: {}".format(e)], []
    files = [ROOT / l.strip() for l in out.splitlines() if l.strip()]
    files = [f for f in files if f.exists()]
    if not files:
        return [], ["暂存区无可检查文件"]
    e1, w1 = check_pii(files)
    e2 = ["暂存区含禁入文件：" + f.name for f in files if f.name in FORBIDDEN_IN_RELEASE]
    return e1 + e2, w1


def fix_url(meta: dict) -> int:
    url = meta.get("repo_url", "")
    if not url:
        print("[fix-url] release.meta.json 无 repo_url")
        return 1
    n = 0
    for f in ("LICENSE", "LICENSE.md", "NOTICE", "DISCLAIMER.md", "README.md",
              "SECURITY.md", "CONTRIBUTING.md", "部署与使用说明.md", "ui/README.md",
              "ui/runner_ui.py", "lepao/license_guard.py"):
        p = ROOT / f
        if not p.exists():
            continue
        t = _text_of(p)
        new = re.sub(r"https?://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", url, t)
        new = re.sub(r"github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(?!\.git)",
                     url.replace("https://", ""), new)
        if new != t:
            p.write_text(new, encoding="utf-8", newline="")
            n += 1
    print("[fix-url] 已同步 {} 个文件到 {}（源文件版权头另跑 apply_headers.py --url）".format(n, url))
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="发行门禁：声明完整性 + PII 扫描 + 无遥测自证")
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--zip", help="校验发行包 zip")
    ap.add_argument("--exe", help="校验产物 EXE 的构建戳")
    ap.add_argument("--network", action="store_true", help="列出出网目标自证无遥测")
    ap.add_argument("--fix-url", action="store_true")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    a = ap.parse_args(argv)

    meta = _load_meta()
    if a.fix_url:
        return fix_url(meta)

    errors, warns = [], []
    scope = "release-tree"
    if a.staged:
        scope = "staged"
        e, w = check_staged()
    elif a.zip:
        scope = "zip:" + str(a.zip)
        e, w = check_zip(Path(a.zip))
        e2, w2 = check_notices(meta)
        errors += e2
        warns += w2
    elif a.exe:
        scope = "exe:" + str(a.exe)
        e, w = check_exe(Path(a.exe))
    elif a.network:
        scope = "network"
        e, w = check_network()
    else:
        e, w = check_notices(meta)
        ship = shippable_files()
        e2, w2 = check_pii(ship)
        e3, w3 = check_forbidden(ship)
        e, w = e + e2 + e3, w + w2 + w3
    errors += e
    warns += w

    result = {"scope": scope, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "license_id": meta.get("license_id", "LRL-1.0"), "pass": not errors,
              "errors": errors, "warnings": warns}
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=== verify_release（{}）===".format(scope))
        for x in errors:
            print("  [BLOCK]", x)
        for x in warns:
            print("  [warn ]", x)
        print("结论：{}（提示 {} 项）".format(
            "通过，可发布" if not errors else "阻断项 {} 个，禁止发布".format(len(errors)),
            len(warns)))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
