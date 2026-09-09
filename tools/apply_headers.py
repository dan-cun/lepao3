# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
"""tools.apply_headers —— 源文件版权声明块写入/校验/构建戳（LRL-1.0 防删声明配套工具）

用法（在本项目根目录）：
  py -3 tools/apply_headers.py            # 给所有 .py 补上/刷新声明块（幂等）
  py -3 tools/apply_headers.py --check    # 只检查哪些文件缺块/块过期，不写文件（退出码 1 = 有缺失）
  py -3 tools/apply_headers.py --stamp    # 补块 + 生成 lepao/_buildstamp.py + release.manifest.json
  py -3 tools/apply_headers.py --url <新仓库地址>   # 改了 repo_url 后一次性同步全部文件头

声明块的规范文本由本文件唯一决定；verify_release.py 与 lepao/license_guard.py 按 marker 识别。
本工具以 UTF-8 读写，绝不改变文件其余内容的编码。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# marker 常量用拼接构造：避免本文件自身源码被"已含块"误匹配（apply 自毁 bug 修复）
MARKER_START = "# --- lepao-research-notice v1 (do not remove; see LICE" + "NSE) ---"
MARKER_END = "# --- end lepao-research-notice ---"
META_FILE = ROOT / "release.meta.json"

DEFAULT_META = {
    "display_name": "乐跑协议研究 Lepao Research",
    "author": "dan-cun",
    "repo_url": "https://github.com/dan-cun/lepao3",
    "contact_qq": "1224145544",
    "copyright_year": "2026",
    "license_name": "乐跑研究协议 1.0",
    "license_id": "LRL-1.0",
    "version": "1.0.0",
}

# 不处理的目录（构建产物/第三方源码/数据）
SKIP_DIR_PARTS = {
    "__pycache__", "build", "dist", "bundle", "runtime", "node_modules",
    ".git", ".venv", "venv", "site-packages", "data",
}
# 需要带声明块的文件（发行面）
INCLUDE_GLOBS = ("cli.py", "lepao/*.py", "ui/*.py", "tools/*.py", "工具/*.py")

BLOCK_RE = re.compile(re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END), re.S)


def load_meta() -> dict:
    meta = dict(DEFAULT_META)
    try:
        meta.update(json.loads(META_FILE.read_text(encoding="utf-8-sig")))
    except Exception:
        pass
    return meta


def notice_block(meta: dict) -> str:
    url = meta.get("repo_url") or DEFAULT_META["repo_url"]
    author = meta.get("author", "dan-cun")
    qq = meta.get("contact_qq", "1224145544")
    year = str(meta.get("copyright_year", "2026"))
    lic = meta.get("license_name", "乐跑研究协议 1.0")
    lid = meta.get("license_id", "LRL-1.0")
    return "\n".join([
        MARKER_START,
        "# {} · {}".format(meta.get("display_name", DEFAULT_META["display_name"]), url),
        "# Copyright (c) {} {} · 技术讨论 QQ {}（仅作技术讨论，不提供代打卡）".format(year, author, qq),
        "# 许可证 {}（{}）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除".format(lid, lic),
        "# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。",
        MARKER_END,
    ])


def _skipped(p: Path) -> bool:
    parts = {x.lower() for x in p.relative_to(ROOT).parts}
    return bool(parts & SKIP_DIR_PARTS)


def iter_targets() -> list:
    seen, out = set(), []
    for pat in INCLUDE_GLOBS:
        for p in sorted(ROOT.glob(pat)):
            if p.is_file() and p.suffix == ".py" and not _skipped(p) and p.resolve() not in seen:
                seen.add(p.resolve())
                out.append(p)
    # 生成物（_buildstamp.py）自带精简声明，不叠加规范块
    return [p for p in out if p.name != "_buildstamp.py"]


def _insert_pos(lines: list) -> int:
    """声明块插入点：shebang / coding 行之后，模块 docstring 之前。"""
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("#!"):
            i += 1
            continue
        if re.match(r"^#.*coding[:=]", ln) or re.match(r"^# ?-[*]-", ln) or re.match(r"^# ?vim:", ln):
            i += 1
            continue
        if not ln.strip():
            i += 1
            continue
        break
    return i


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def write_text(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8", newline="")


def apply_one(p: Path, block: str, force: bool) -> str:
    """返回 created / changed / unchanged。"""
    raw = read_text(p)
    m = BLOCK_RE.search(raw)
    if m:
        if m.group(0) == block and not force:
            return "unchanged"
        new = raw[:m.start()] + block + raw[m.end():]
        write_text(p, new)
        return "changed"
    lines = raw.split("\n")
    pos = _insert_pos(lines)
    lines.insert(pos, block)
    write_text(p, "\n".join(lines))
    return "created"


def sha256_of(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def stamp(meta: dict) -> dict:
    """生成 _buildstamp.py 与 release.manifest.json（构建标识 + 声明文件指纹）。"""
    ver = meta.get("version", "1.0.0")
    files = {"LICENSE": ROOT / "LICENSE", "NOTICE": ROOT / "NOTICE",
             "DISCLAIMER.md": ROOT / "DISCLAIMER.md"}
    hashes = {k: (sha256_of(v) if v.exists() else "") for k, v in files.items()}
    seed = (hashes["LICENSE"] or "0" * 64)[:8]
    now = datetime.now()
    build_id = "LRL1.0-{}-{}-{}".format(ver, now.strftime("%Y%m%d"), seed)
    write_text(ROOT / "lepao" / "_buildstamp.py",
               "# -*- coding: utf-8 -*-\n"
               "# Copyright (c) {} {} · LRL-1.0 · 构建戳文件，请勿手改/删除（LRL-1.0 第三条）\n".format(
                   meta.get("copyright_year", "2026"), meta.get("author", "dan-cun")) +
               '"""\u6784\u5efa\u6233\uff08\u7531 tools/apply_headers.py --stamp \u751f\u6210\uff0c\u52ff\u624b\u6539/\u5220\u9664\uff09\u3002\n\n'
               '\u7528\u9014\uff1a\u7248\u672c\u6838\u5bf9\u3001\u95ee\u9898\u53cd\u9988\u3001\u4ee5\u53ca\u53bb\u58f0\u660e\u526f\u672c\u7684\u6bd4\u5bf9\u8bc1\u636e\uff08LRL-1.0 \u7b2c\u5341\u4e00\u6761\uff09\u3002\n'
               '\u672c\u6587\u4ef6\u4e0d\u8054\u7f51\u3001\u4e0d\u56de\u4f20\u4efb\u4f55\u4fe1\u606f\u3002\n"""\n'
               'BUILD_ID = "{}"\n'.format(build_id) +
               'BUILD_DATE = "{}"\n'.format(now.strftime("%Y-%m-%d %H:%M:%S")) +
               'LICENSE_ID = "{}"\n'.format(meta.get("license_id", "LRL-1.0")) +
               'VERSION = "{}"\n'.format(ver) +
               'UPSTREAM = "{}"\n'.format(meta.get("repo_url", "")) +
               'AUTHOR = "{}"\n'.format(meta.get("author", "")))
    manifest = {
        "license_id": meta.get("license_id", "LRL-1.0"),
        "build_id": build_id,
        "version": ver,
        "upstream": meta.get("repo_url", ""),
        "author": meta.get("author", ""),
        "contact_qq": meta.get("contact_qq", ""),
        "generated": now.strftime("%Y-%m-%d %H:%M:%S"),
        "sha256": hashes,
        "marker": MARKER_START,
    }
    write_text(ROOT / "release.manifest.json",
               json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="\u6e90\u6587\u4ef6\u7248\u6743\u58f0\u660e\u5757\u5199\u5165/\u6821\u9a8c/\u6784\u5efa\u6233")
    ap.add_argument("--check", action="store_true", help="只检查不写文件")
    ap.add_argument("--force", action="store_true", help="即使内容一致也重写")
    ap.add_argument("--stamp", action="store_true", help="生成构建戳与声明指纹")
    ap.add_argument("--url", help="更新 release.meta.json 的 repo_url 并同步所有文件头")
    ap.add_argument("--version", help="更新 release.meta.json 的 version（用于 --stamp）")
    a = ap.parse_args(argv)

    meta = load_meta()
    if a.url:
        meta["repo_url"] = a.url
    if a.version:
        meta["version"] = a.version
    if (a.url or a.version) and META_FILE.exists():
        write_text(META_FILE, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        print("[meta] 已更新 release.meta.json（repo_url={} version={}）".format(
            meta.get("repo_url"), meta.get("version")))

    block = notice_block(meta)
    targets = iter_targets()
    if a.check:
        missing, stale = [], []
        for p in targets:
            txt = read_text(p)
            if MARKER_START not in txt:
                missing.append(p)
            else:
                m = BLOCK_RE.search(txt)
                if not m or m.group(0) != block:
                    stale.append(p)
        print("[check] 应带声明块的文件 {} 个；缺失 {}，与规范文本不一致 {}".format(
            len(targets), len(missing), len(stale)))
        for p in missing:
            print("  MISSING", p.relative_to(ROOT))
        for p in stale:
            print("  STALE  ", p.relative_to(ROOT))
        if not (missing or stale):
            print("  [OK] 全部一致")
        return 1 if (missing or stale) else 0

    stats = {"created": 0, "changed": 0, "unchanged": 0}
    for p in targets:
        stats[apply_one(p, block, a.force)] += 1
    print("[apply] 新建 {} / 更新 {} / 已一致 {}（共 {} 文件）".format(
        stats["created"], stats["changed"], stats["unchanged"], len(targets)))
    if a.stamp:
        m = stamp(meta)
        print("[stamp] build_id={}".format(m["build_id"]))
        for k, v in m["sha256"].items():
            print("        {:15s} sha256={}…".format(k, v[:16]))
    print("[next] 发行前请运行  py -3 tools/verify_release.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
