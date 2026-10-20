# -*- coding: utf-8 -*-
"""角色抠图与合成：把 ImageGen 定妆图抠出，叠进 3D 静帧。

- ``cutout``  优先 rembg（AI 抠图），失败退白底色度键
- ``composite`` 把 1~3 个角色按 (x_ratio, height_ratio, zorder) 摆进静帧：
  x_ratio   人物中心在画面宽度的比例位置
  height_ratio  人物身高占画面高度的比例（近大远小）
  人物脚底落在 ``ground`` 指定的画面高度比例处
- ``match_light`` 按场景时段（tod）给人物调色，避免"贴图感"

用法::

    python probes/l100/l100_cast.py --test       # 抠图质检
"""
from __future__ import annotations

import argparse
import glob
import os

import numpy as np
from PIL import Image, ImageFilter

_HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = os.path.dirname(_HERE)
ROOT = os.path.dirname(PROBES)
CHAR_DIR = os.path.join(PROBES, 'out', 'l100', 'characters')
CUT_DIR = os.path.join(CHAR_DIR, 'cutouts')

# 定妆图 → 角色（按文件名前缀匹配）
KEYS = {'货车司机': 'shimancang', '女押运员': 'miaoxiaoman',
        '年轻女性定妆照': 'tangning', '合作社职员': 'erzhu'}
ALIAS = {'石满仓': 'shimancang', '苗小满': 'miaoxiaoman',
         '汤宁': 'tangning', '二柱': 'erzhu'}


def _find_src(who):
    who = ALIAS.get(who, who)
    for p in glob.glob(os.path.join(CHAR_DIR, '*.png')):
        base = os.path.basename(p)
        for k, name in KEYS.items():
            if k in base and who == name:
                return p
    return None


def _white_key(img):
    """白底色度键：亮度阈值 + 从顶部泛洪剔除残余背景 + 边缘羽化。

    rembg 在 Python 3.13 + onnxruntime 下会 segfault，这里用几何法兜底：
    1) 亮度 + 低饱和（背景是灰白）判背景
    2) 从四边泛洪，只保留与人物连通的前景
    """
    import collections
    a = np.asarray(img.convert('RGB')).astype(np.float32)
    lum = a.mean(axis=2)
    sat = a.max(axis=2) - a.min(axis=2)
    bg = (lum > 150) & (sat < 40)
    h, w = bg.shape
    # 泛洪：从边界开始，4 邻域
    seen = np.zeros_like(bg)
    q = collections.deque()
    for x in range(w):
        for y in (0, h - 1):
            if bg[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if bg[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and bg[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                q.append((ny, nx))
    fg = ~seen
    alpha = fg.astype(np.float32)
    # 只保留最大连通域：剔除与人物分离的地面阴影/残渣
    try:
        from scipy import ndimage
        lab, n = ndimage.label(alpha > 0.3)
        if n > 1:
            sizes = ndimage.sum(alpha, lab, range(1, n + 1))
            keep = 1 + int(np.argmax(sizes))
            alpha[lab != keep] = 0.0
    except Exception:
        pass
    # 底部 13% 渐隐：去掉脚下残余
    fade = np.ones((h, 1), dtype=np.float32)
    k = int(h * 0.13)
    fade[-k:, 0] = np.linspace(1.0, 0.0, k)
    alpha = alpha * fade
    m = Image.fromarray((alpha * 255).astype(np.uint8))
    m = m.filter(ImageFilter.MinFilter(7))            # 先收缩去残渣
    m = m.filter(ImageFilter.MaxFilter(3)).filter(
        ImageFilter.GaussianBlur(1.0))
    out = img.convert('RGBA')
    out.putalpha(m)
    return out


def cutout(who, force=False):
    """返回角色 RGBA 抠图路径（缓存到 cutouts/）。"""
    who = ALIAS.get(who, who)
    src = _find_src(who)
    if src is None:
        raise FileNotFoundError(f'no portrait for {who}')
    os.makedirs(CUT_DIR, exist_ok=True)
    dst = os.path.join(CUT_DIR, who + '.png')
    if os.path.exists(dst) and not force:
        return dst
    img = Image.open(src)
    # rembg 在 Python 3.13 下 segfault（numba/onnxruntime），直接用白底色度键
    cut = _white_key(img)
    # 底部水印裁掉（"AI生成"角标在最底部 3%）
    w, h = cut.size
    cut = cut.crop((0, 0, w, int(h * 0.94)))
    cut.save(dst)
    return dst


# ------------------------------------------------------------ 光照匹配 --
def match_light(img_rgba, tod='day'):
    """按场景时段给角色调色（简单的乘法 grade，与 3D 帧同一倾向）。"""
    a = np.asarray(img_rgba).astype(np.float32)
    rgb, alpha = a[:, :, :3], a[:, :, 3:]
    if tod == 'night':
        rgb *= np.array([0.42, 0.50, 0.72])          # 冷蓝 + 压暗
    elif tod == 'dusk':
        rgb *= np.array([0.82, 0.62, 0.48])          # 橙暮
    elif tod == 'dawn':
        rgb *= np.array([0.88, 0.74, 0.66])
    elif tod == 'gold':
        rgb *= np.array([0.95, 0.82, 0.66])
    out = np.concatenate([np.clip(rgb, 0, 255), alpha], axis=2)
    return Image.fromarray(out.astype(np.uint8), 'RGBA')


def composite(frame_png, cast, out_png, tod='day', grade=True):
    """把角色清单合成到静帧上。

    cast: list of dict(who, x, h, ground[, upper])
      x       中心横向位置 0~1
      h       人物身高占画高比例（upper 时为上半身高度）
      ground  人物底边纵向位置 0~1（1 = 画面底）
      upper   True = 只用上半身（坐姿/近景）
    """
    frame = Image.open(frame_png).convert('RGBA')
    W, H = frame.size
    for c in cast:
        fig = Image.open(cutout(c['who'])).convert('RGBA')
        if c.get('upper'):
            fig = fig.crop((0, 0, fig.width, int(fig.height * 0.62)))
        fig = match_light(fig, tod)
        th = int(H * c['h'])
        tw = int(fig.width * th / fig.height)
        fig = fig.resize((tw, th), Image.LANCZOS)
        gx = int(W * c['x'] - tw / 2)
        gy = int(H * c['ground'] - th)
        frame.alpha_composite(fig, (gx, gy))
    out = frame.convert('RGB')
    if grade:
        from l100_render import _grade
        out = _grade(out)
    os.makedirs(os.path.dirname(out_png) or '.', exist_ok=True)
    out.save(out_png)
    return out_png


# ---------------------------------------------------------- 出场解析 --
MAIN = ('石满仓', '苗小满', '汤宁', '二柱')


def cast_for(scene_blocks, place_key, pose_key):
    """按场次出场名单 + 地点 + 构图，返回摆位清单（空 = 不放人）。"""
    who = []
    for kind, a, b in scene_blocks:
        if kind == 'meta' and a == '出场':
            import re
            txt = (b or '').replace('、', ' ').replace('，', ' ')
            who = [re.sub(r'[（(][^）)]*[）)]', '', w).strip()
                   for w in txt.split()]
            who = [w for w in who if w]
            break
    who = [w for w in who if w in MAIN]
    if not who:
        return []

    if place_key == 'cab':
        if pose_key in ('two_shot',):
            seats = {'石满仓': 0.30, '苗小满': 0.66, '汤宁': 0.66,
                     '二柱': 0.30}
            return [{'who': w, 'x': seats.get(w, 0.5), 'h': 0.50,
                     'ground': 0.94, 'upper': True} for w in who[:2]]
        if pose_key in ('driver', 'passenger'):
            w0 = who[0]
            x = 0.30 if pose_key == 'driver' else 0.66
            if w0 not in ('石满仓',) and pose_key == 'driver':
                x = 0.30
            return [{'who': w0, 'x': x, 'h': 0.56, 'ground': 0.96,
                     'upper': True}]
        return []
    if place_key == 'office':
        xs = [(0.36, 0.50), (0.64, 0.46)]
        return [{'who': w, 'x': xs[i][0], 'h': xs[i][1], 'ground': 0.99,
                 'upper': True} for i, w in enumerate(who[:2])]
    if place_key == 'boxbody':
        xs = [(0.40, 0.44), (0.62, 0.40)]
        return [{'who': w, 'x': xs[i][0], 'h': xs[i][1], 'ground': 0.98,
                 'upper': True} for i, w in enumerate(who[:2])]
    # 外景：站立全身，近大远小
    if pose_key in ('establish', 'overhead', 'long_road', 'horizon'):
        return []                      # 大远景不放人（贴图感太强）
    spots = [(0.44, 0.34, 0.94), (0.62, 0.30, 0.96), (0.30, 0.27, 0.98)]
    return [{'who': w, 'x': spots[i][0], 'h': spots[i][1],
             'ground': spots[i][2]} for i, w in enumerate(who[:3])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--test', action='store_true')
    args = ap.parse_args()
    for who in ('shimancang', 'miaoxiaoman', 'tangning', 'erzhu'):
        p = cutout(who)
        im = Image.open(p)
        al = np.asarray(im)[:, :, 3]
        print(f'{who:<12s} {im.size} alpha_mean={al.mean():.1f}')


if __name__ == '__main__':
    main()
