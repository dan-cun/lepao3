# -*- coding: utf-8 -*-
# --- lepao-research-notice v1 (do not remove; see LICENSE) ---
# 乐跑协议研究（Lepao Research） · https://github.com/dan-cun/lepao3
# Copyright (c) 2026 dan-cun · 技术讨论 QQ 1224145544（仅作技术讨论，不提供代打卡）
# 许可证 LRL-1.0（乐跑研究协议 1.0）：仅供学习 · 不可牟利 · 再发布须署名引用原始仓库 · 声明不得删除
# 删除或篡改本块即依 LRL-1.0 第三条自动终止授权；衍生版须继续以 LRL-1.0 授权并标注修改说明。
# --- end lepao-research-notice ---
# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
"""make_icon.py —— 生成应用图标(深底渐变圆角 + 「跑」字)。缺 Pillow/字体则跳过。"""
import math
import os

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
OUT_ICO = os.path.join(ICON_DIR, "lepao.ico")

SIZE = 256
RADIUS = 56


def gradient_color(t):
    """t∈[0,1]: 深青绿 → 亮绿 渐变"""
    c0 = (0x0E, 0x3A, 0x33)   # 深青
    c1 = (0x16, 0xA0, 0x5F)   # 亮绿
    return tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))


def main():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("skip icon: no Pillow")
        return
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 对角渐变 + 圆角遮罩
    for y in range(SIZE):
        t = y / SIZE
        col = gradient_color(t)
        for x in range(SIZE):
            pass
        d.line([(0, y), (SIZE, y)], fill=col + (255,))
    mask = Image.new("L", (SIZE, SIZE), 0)
    dm = ImageDraw.Draw(mask)
    dm.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=RADIUS, fill=255)
    img.putalpha(mask)
    # 「跑」字
    font_path = None
    for p in (r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\Deng.ttf"):
        if os.path.exists(p):
            font_path = p
            break
    try:
        font = ImageFont.truetype(font_path, 150) if font_path else None
    except Exception:
        font = None
    if font is not None:
        d2 = ImageDraw.Draw(img)
        bbox = d2.textbbox((0, 0), "跑", font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d2.text(((SIZE - w) / 2 - bbox[0], (SIZE - h) / 2 - bbox[1]),
                "跑", font=font, fill=(0xF4, 0xFF, 0xF7, 255))
    os.makedirs(ICON_DIR, exist_ok=True)
    img.save(OUT_ICO, sizes=[(16, 16), (32, 32), (48, 48), (64, 64),
                             (128, 128), (256, 256)])
    print("icon written:", OUT_ICO)


if __name__ == "__main__":
    main()
