# -*- coding: utf-8 -*-
"""噪声库：value / perlin / simplex / fBm / worley / 域扭曲（纯标准库标量版）。

**2D 与 3D 是两套并列的入口**：``value_noise`` / ``perlin_noise`` / ``fbm`` / 
``worley_noise`` 吃 ``(x, y)``；``perlin3`` / ``fbm3`` 吃 ``(x, y, z)``。
3D 那一套不是 2D 的参数化封装，而是**另一套算法**（8 角点 + 12 梯度集 +
五次 fade），用于体积雾、实体纹理、3D 密度场这类"按世界坐标采样"的场合。

借鉴记账（铁律 10）
------------------
- **算法与整数哈希移植自 ``procgen``**（MIT，<https://gitee.com/siwpan/procgen>，
  ``procgen/noise.py``）：value / perlin / fbm / fractal / simplex 五个函数与
  ``_hash2`` 整数格点哈希。借用范围：算法与哈希常数；numpy 向量化实现按本仓
  「纯标准库 + 标量」立场重写。
- **3D 梯度噪声依 Ken Perlin《Improving Noise》(SIGGRAPH 2002) 的算法定义**，
  参考实现见作者在 NYU 的公开页 <https://cs.nyu.edu/~perlin/noise/>。借用范围：
  **12 个梯度方向与五次 fade 多项式 ``6t⁵−15t⁴+10t³``**（论文公开的数学定义，
  不含可版权的代码表达）—— 本实现的哈希、``period`` 环绕、归一化与裁剪口径
  均为自写，未复制其代码行。许可备注：Perlin **未**对 classic noise 申请专利
  （2001 年那项专利覆盖的是 simplex 的 3D+ 实现），参考实现标注
  ``COPYRIGHT 2002 KEN PERLIN`` 并在论文中公开发表。
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
  3D 的 ``perlin3`` 同样是"归一化 + 裁剪"，并且**在整数格点上恰好返回 0.5**
  （梯度噪声的定义性质：角点处位移向量为零 ⇒ 点积为零）—— 这条比任何 golden
  都更能证明"这是梯度噪声而不是值噪声"。
"""
from __future__ import annotations

import math
import random

__all__ = [
    'value_noise', 'perlin_noise', 'simplex_noise', 'fbm', 'fractal_noise',
    'worley_noise', 'domain_warp', 'heightmap',
    'perlin3', 'fbm3',
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


# ============================================================== 3D 梯度噪声 ==
# 与上面 2D 那套并列的另一套算法（Perlin 2002）。差别不只是"多一个坐标"：
#
#   2D 版（perlin_noise）    每个格点一个**角度** → cos/sin 变成梯度向量 → 4 角点
#   3D 版（perlin3）         每个格点一个**哈希整数** → 查 12 梯度的位技巧 → 8 角点
#
# 3D 版不在格点上存 256 个随机单位向量（那是 1985 年的做法）：随机梯度会成簇，
# 邻近梯度方向靠近时出现成片亮斑（splotchy）。2002 改进版固定 12 个方向
# （立方体 12 条棱的中点），彼此不会靠得太近，而且点积退化成加法。

#: 3D 归一化常数 = **实测可达的理论最大值 1.0**（与 2D 版用 √2/2 同口径：
#: 都按"能取到的最大幅度"归一，而不是按典型幅度缩放到好看）。
#:
#: 为什么不是 2（单角点 ``|g·d|`` 的粗上界）：8 个角点的权重由 fade 的乘积给出、
#: 和恒为 1，靠得越近的那个角点权重越小 —— 单个角点吃不到 2。真正取到 1.0 的配置
#: 是 x 方向落在格界、y/z 落在格心，八个角点的梯度交替成棋盘（``±1,±1,0`` 全在
#: 梯度集里），此时 8 份贡献同号相加。穷举 16³ 格 × 11³ 内点实测 argmax =
#: ``perlin3(-4.0, -2.5, -3.5, seed=7) = 1.0``，由 ``tests/test_noise.py`` 钉住。
_PERLIN3_NORM = 1.0


def _fade(t: float) -> float:
    """五次淡入淡出 ``6t⁵−15t⁴+10t³``（一阶、二阶导在两端都为 0）。

    对照：更常见的三次式 ``t*t*(3-2t)`` 只保证一阶导连续，二阶导在格点边界
    跳变 —— 拿去做凹凸/法线会看见格子边的折角，这正是 2002 版换五次的原因。
    """
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _grad3(h: int, x: float, y: float, z: float) -> float:
    """Perlin 2002 的梯度点积（位技巧版：16 个槽位复用 12 个方向）。

    12 个方向的分量只取 -1/0/1，所以点积就是"挑两个分量相加再定符号"，
    没有乘法 —— 这是论文里"顺手也更快"的那部分。
    """
    h &= 15
    u = x if h < 8 else y
    v = y if h < 4 else (x if h == 12 or h == 14 else z)
    return (-u if h & 1 else u) + (-v if h & 2 else v)


def _hash3(ix: int, iy: int, iz: int, seed: int) -> int:
    """整数格点 -> 哈希整数（与 ``_hash2`` 同族的整数位运算，int64 回绕）。

    为什么不像 ``engine/proctex.py`` 那样用 ``fract(sin(x)*43758.5)``：
    ``sin`` 的大参数精度跨平台不一致，而位运算在所有平台逐位相同 —— 噪声库
    要能当 golden 基准，就不能依赖浮点超越函数。
    """
    h = _wrap64(ix * 374761393 + iy * 668265263 + iz * 2654435761
                + seed * 1013904223)
    h = _wrap64((h ^ (h >> 13)) * 1274126177)
    return h ^ (h >> 16)


def perlin3(x: float, y: float, z: float, seed: int = 0,
            period: int = 0) -> float:
    """3D Perlin 梯度噪声，返回 0..1（**0.5 = 整数格点上的零交叉**）。

    参数：
        seed:   可选种子（换种子得到另一套梯度查表）
        period: >0 时格点索引按 ``period`` 环绕 ⇒ 在 ``period`` 立方体内
                **无缝可铺贴**。体积雾/实体纹理要"平铺不接缝"时必给
                （对应 GLSL 侧 ``fract`` 采样；不给就只是连续、不一定无缝）。

    为什么值得单独一套：2D 版本只能做高度场（一张图决定一个 y），
    而 3D 版本能吃**世界坐标** ``(x, y, z)``，于是同一份代码可以生成
    体积雾密度、大理石实体纹理、体素洞穴的 3D 密度场 —— 都不需要 UV。
    """
    if period < 0:
        raise ValueError(f'period 不能为负，收到 {period}')
    fx = math.floor(x)
    fy = math.floor(y)
    fz = math.floor(z)
    tx, ty, tz = x - fx, y - fy, z - fz
    xi, yi, zi = int(fx), int(fy), int(fz)

    if period:
        x0 = xi % period
        y0 = yi % period
        z0 = zi % period
        x1 = (x0 + 1) % period
        y1 = (y0 + 1) % period
        z1 = (z0 + 1) % period
    else:
        x0, y0, z0 = xi, yi, zi
        x1, y1, z1 = xi + 1, yi + 1, zi + 1

    u, v, w = _fade(tx), _fade(ty), _fade(tz)

    n000 = _grad3(_hash3(x0, y0, z0, seed), tx, ty, tz)
    n100 = _grad3(_hash3(x1, y0, z0, seed), tx - 1.0, ty, tz)
    n010 = _grad3(_hash3(x0, y1, z0, seed), tx, ty - 1.0, tz)
    n110 = _grad3(_hash3(x1, y1, z0, seed), tx - 1.0, ty - 1.0, tz)
    n001 = _grad3(_hash3(x0, y0, z1, seed), tx, ty, tz - 1.0)
    n101 = _grad3(_hash3(x1, y0, z1, seed), tx - 1.0, ty, tz - 1.0)
    n011 = _grad3(_hash3(x0, y1, z1, seed), tx, ty - 1.0, tz - 1.0)
    n111 = _grad3(_hash3(x1, y1, z1, seed), tx - 1.0, ty - 1.0, tz - 1.0)

    z0_plane = _lerp(_lerp(n000, n100, u), _lerp(n010, n110, u), v)
    z1_plane = _lerp(_lerp(n001, n101, u), _lerp(n011, n111, u), v)
    n = _lerp(z0_plane, z1_plane, w)
    return _clip01((n / _PERLIN3_NORM + 1.0) / 2.0)


def fbm3(x: float, y: float, z: float, octaves: int = 4,
         lacunarity: float = 2.0, gain: float = 0.5, seed: int = 0,
         period: int = 0) -> float:
    """3D 分形布朗运动：多层 ``perlin3`` 叠加，返回 0..1。

    ``period`` 是**最粗那层**的格点周期；第 ``o`` 层自动用
    ``period * lacunarity**o``（取整），所以只要 ``period`` 与 ``lacunarity``
    搭配得当（默认 2.0），**每一层都各自无缝**，叠起来仍是无缝的。

    ⚠ 层间用 ``seed + o`` 去相关（与 2D 的 ``fbm`` 同口径）—— 少了这一步，
    各层会在同一处起伏，叠出来只是"更陡的单层"，看不出分形细节。
    """
    total = 0.0
    amp = 1.0
    freq = 1.0
    maxv = 0.0
    for o in range(octaves):
        p = int(round(period * freq)) if period else 0
        total += amp * perlin3(x * freq, y * freq, z * freq,
                               seed=seed + o, period=p)
        maxv += amp
        amp *= gain
        freq *= lacunarity
    return _clip01(total / maxv)
