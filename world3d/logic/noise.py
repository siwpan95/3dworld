# -*- coding: utf-8 -*-
"""噪声库：value / perlin / simplex / fBm / worley / 域扭曲（纯标准库标量版）。

借鉴记账（铁律 10）
------------------
- **算法与整数哈希移植自 ``procgen``**（MIT，<https://gitee.com/siwpan/procgen>，
  ``procgen/noise.py``）：value / perlin / fbm / fractal / simplex 五个函数与
  ``_hash2`` 整数格点哈希。借用范围：算法与哈希常数；numpy 向量化实现按本仓
  「纯标准库 + 标量」立场重写。
- **simplex 置换表交叉参考 ``mc-clone/MC2``**（MIT，``mcclone/core/noise.py``）：
  用 ``random.Random(seed)`` 构建置换表（stdlib 确定性，禁全局 random/时间/
  urandom）。procgen 原版用 ``numpy.random.RandomState`` —— 两者置换表不同，
  故 simplex 数值与 procgen 不一致，但**自一致**（同 seed 同输出）。
- **worley / domain_warp / heightmap 为本仓自研**（标准算法，非移植）：
  procgen 的 Voronoi 在 ``town.py`` 是城市划区生成，不是噪声函数。

确定性口径（与 terrain.py 一致）
------------------------------
- **纯函数、零依赖**：只 import ``math`` / ``random``，不碰 pyglet / GL /
  全局随机状态；同 ``(坐标, seed)`` 必然得到同一输出。
- **整数位运算哈希**（不是 ``fract(sin(x)*43758.5)``）：sin 精度跨平台不一致，
  位运算在所有平台逐位相同。
- **int64 回绕语义精确复刻**：``_wrap64`` 把任意精度整数折叠成 int64 补码，
  与 procgen 的 numpy.int64 运算逐位一致 —— value/perlin/fbm 的输出因此与
  procgen 原版**逐位相同**（交叉验证过）。
- **输出统一 0..1**：perlin/simplex 按理论最大幅度归一化后裁剪，与 procgen 一致。
"""
from __future__ import annotations

import math
import random

__all__ = [
    'value_noise', 'perlin_noise', 'simplex_noise', 'fbm', 'fractal_noise',
    'worley_noise', 'domain_warp', 'heightmap',
]

# ------------------------------------------------------------------ 整数哈希 --

_M64 = (1 << 64) - 1
_S64 = 1 << 63


def _wrap64(h: int) -> int:
    """把任意精度整数折叠成 int64 补码语义（与 numpy.int64 逐位一致）。

    Python 整数不回绕，而 procgen 的 numpy 实现每步乘法都按 int64 截断 ——
    不折叠的话大坐标下哈希值会发散（测试 ``test_hash_matches_numpy_semantics``
    钉住这一点）。
    """
    h &= _M64
    return h - (1 << 64) if h & _S64 else h


def _hash2(ix: int, iy: int, seed: int) -> float:
    """整数格点 -> 确定性伪随机 [0,1)。移植自 procgen（int64 语义复刻）。"""
    h = _wrap64(ix * 374761393 + iy * 668265263 + seed * 1013904223)
    h = _wrap64((h ^ (h >> 13)) * 1274126177)
    h = h ^ (h >> 16)
    return (h & 0x7FFFFFFF) / 0x7FFFFFFF


def _smooth(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, t: float) -> float:
    return a + t * (b - a)


def _clip01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


# 2D 梯度噪声理论最大投影幅度（√(xf²+yf²) 在角点最大 ≈ √2/2）
_PERLIN_NORM = math.sqrt(2.0) / 2.0


# ------------------------------------------------------------------ 值噪声 --

def value_noise(x: float, y: float, seed: int = 0) -> float:
    """值噪声：格点随机值 + 平滑插值，返回 0..1。"""
    xi = math.floor(x)
    yi = math.floor(y)
    xf = x - xi
    yf = y - yi
    v00 = _hash2(xi, yi, seed)
    v10 = _hash2(xi + 1, yi, seed)
    v01 = _hash2(xi, yi + 1, seed)
    v11 = _hash2(xi + 1, yi + 1, seed)
    u = _smooth(xf)
    v = _smooth(yf)
    return _lerp(_lerp(v00, v10, u), _lerp(v01, v11, u), v)


# ------------------------------------------------------------------ Perlin --

def perlin_noise(x: float, y: float, seed: int = 0) -> float:
    """Perlin 梯度噪声（格点角度梯度），返回 0..1。"""
    xi = math.floor(x)
    yi = math.floor(y)
    xf = x - xi
    yf = y - yi
    ang00 = _hash2(xi, yi, seed) * math.tau
    ang10 = _hash2(xi + 1, yi, seed) * math.tau
    ang01 = _hash2(xi, yi + 1, seed) * math.tau
    ang11 = _hash2(xi + 1, yi + 1, seed) * math.tau
    d00 = math.cos(ang00) * xf + math.sin(ang00) * yf
    d10 = math.cos(ang10) * (xf - 1.0) + math.sin(ang10) * yf
    d01 = math.cos(ang01) * xf + math.sin(ang01) * (yf - 1.0)
    d11 = math.cos(ang11) * (xf - 1.0) + math.sin(ang11) * (yf - 1.0)
    u = _smooth(xf)
    v = _smooth(yf)
    n = _lerp(_lerp(d00, d10, u), _lerp(d01, d11, u), v)
    # 按理论最大幅度归一化，使输出能覆盖完整 0..1 区间（提升对比度）
    return _clip01((n / _PERLIN_NORM + 1.0) / 2.0)


# ------------------------------------------------------------------ 分形叠加 --

def fbm(x: float, y: float, octaves: int = 4, lacunarity: float = 2.0,
        gain: float = 0.5, seed: int = 0) -> float:
    """分形布朗运动（fBm）：多层 perlin 叠加，返回 0..1。

    参数：
        octaves:    叠加层数（默认 4）
        lacunarity: 每层频率倍增（默认 2.0）
        gain:       每层振幅衰减（默认 0.5）
        seed:       随机种子（第 o 层用 seed+o，层间不相关）
    """
    total = 0.0
    amp = 1.0
    freq = 1.0
    maxv = 0.0
    for o in range(octaves):
        total += amp * perlin_noise(x * freq, y * freq, seed=seed + o)
        maxv += amp
        amp *= gain
        freq *= lacunarity
    return _clip01(total / maxv)


def fractal_noise(x: float, y: float, octaves: int = 4, seed: int = 0) -> float:
    """分形噪声：fBm 的便捷封装（默认 lacunarity=2.0 / gain=0.5）。"""
    return fbm(x, y, octaves=octaves, lacunarity=2.0, gain=0.5, seed=seed)


# ------------------------------------------------------------------ Simplex --

def _perm_table(seed: int) -> list:
    """由 seed 生成长度 512 的排列表（0..255 洗牌后复制）。

    用 ``random.Random(seed)``（mc-clone 口径）：stdlib 确定性，
    禁全局 random / 时间 / os.urandom。
    """
    rng = random.Random(seed)
    p = list(range(256))
    rng.shuffle(p)
    return p + p


# 12 个二维梯度方向（8 个唯一方向，重复凑满 12 供 idx % 12 索引）
_GRAD2 = (
    (1, 1), (-1, 1), (1, -1), (-1, -1),
    (1, 0), (-1, 0), (1, 0), (-1, 0),
    (0, 1), (0, -1), (0, 1), (0, -1),
)


def simplex_noise(x: float, y: float, seed: int = 0) -> float:
    """2D Simplex 噪声（Stefan Gustavson 算法），返回 0..1。

    相比 perlin 方向伪影更少、计算更快。置换表来自 ``random.Random(seed)``
    （见模块 docstring 的借鉴记账：与 procgen 的 RandomState 版数值不同）。
    """
    perm = _perm_table(seed)
    F2 = 0.5 * (math.sqrt(3.0) - 1.0)
    G2 = (3.0 - math.sqrt(3.0)) / 6.0
    s = (x + y) * F2
    i = math.floor(x + s)
    j = math.floor(y + s)
    t = (i + j) * G2
    x0 = x - (i - t)
    y0 = y - (j - t)
    i1, j1 = (1, 0) if x0 > y0 else (0, 1)

    n = 0.0
    for ox, oy, oi, oj in ((0.0, 0.0, 0, 0),
                           (i1 - G2, j1 - G2, i1, j1),
                           (1.0 - 2.0 * G2, 1.0 - 2.0 * G2, 1, 1)):
        cx = x0 - ox
        cy = y0 - oy
        t2 = 0.5 - cx * cx - cy * cy
        if t2 > 0.0:
            gi = perm[(i + oi) & 255]
            gj = perm[(j + oj) & 255]
            idx = (gi + gj) & 255
            gx, gy = _GRAD2[idx % 12]
            n += (t2 * t2) * (t2 * t2) * (gx * cx + gy * cy)
    return _clip01((70.0 * n + 1.0) / 2.0)


# ------------------------------------------------------------------ Worley --

def worley_noise(x: float, y: float, seed: int = 0) -> float:
    """Worley（Voronoi）F1 噪声：到最近特征点的欧氏距离，0..1。

    本仓自研（标准算法）：每格一个特征点（位置由 ``_hash2`` 定），
    查 3×3 邻域取最近。生物群区 / 细胞纹理的常用基元。
    """
    xi = math.floor(x)
    yi = math.floor(y)
    best = 2.0  # 上界：3×3 邻域内最大可能距离 < 2
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            cx = xi + dx
            cy = yi + dy
            px = cx + _hash2(cx, cy, seed)
            py = cy + _hash2(cx, cy, seed ^ 0x9E3779B9)
            d = math.hypot(x - px, y - py)
            if d < best:
                best = d
    return _clip01(best)


# ------------------------------------------------------------------ 域扭曲 --

def domain_warp(x: float, y: float, seed: int = 0, strength: float = 1.0,
                octaves: int = 2) -> tuple:
    """域扭曲：用 fBm 偏移采样坐标（iq 的经典技术），返回扭曲后的 (x, y)。

    本仓自研（标准技术）：``wx/wy`` 两路独立 fBm（第二路换盐换偏移），
    强度 ``strength`` 控制扭曲幅度。配合 fBm 采样可得「地形被流体冲刷过」
    的有机形态。
    """
    wx = fbm(x, y, octaves=octaves, seed=seed)
    wy = fbm(x + 5.2, y + 1.3, octaves=octaves, seed=seed ^ 0x51ED270B)
    return (x + strength * (wx - 0.5) * 2.0,
            y + strength * (wy - 0.5) * 2.0)


# ------------------------------------------------------------------ 网格辅助 --

def heightmap(w: int, h: int, scale: float = 0.1, seed: int = 0,
              octaves: int = 4, lacunarity: float = 2.0,
              gain: float = 0.5) -> list:
    """w×h 高度图（行主序 list of lists，每行 w 个 float，0..1）。

    本仓自研的便利封装：``heightmap[y][x] = fbm(x*scale, y*scale, ...)``。
    纯逻辑层产物，可直接喂给 ``logic/terrain.py`` 的着色/发射管线。
    """
    return [[fbm(xx * scale, yy * scale, octaves=octaves,
                 lacunarity=lacunarity, gain=gain, seed=seed)
             for xx in range(w)] for yy in range(h)]
