# -*- coding: utf-8 -*-
"""实体纹理（solid texture）：**按世界坐标**求材质，不需要 UV。

为什么需要另一套
----------------
`engine/proctex.py` 是 UV 空间的路子：`(u, v) ∈ [0,1)²` + 格点数 `cells`，
生成规则里到处是"周期必须对齐才能 REPEAT 无缝"的约束（`make_marble` 的
`vein_scale` 必须取偶数就是这条）。它的产物是**贴图**，要经过 UV 展开 ——
于是接缝、拉伸、图集打包的问题全都跟着来。

实体纹理换一个前提：材质是**空间坐标的函数** `M(x, y, z)`。于是：

- **没有 UV**，也就没有接缝（接缝是参数化的产物，不是材质的产物）；
- **没有拉伸**（曲面怎么弯，材质都按真实尺寸分布）；
- **不需要图集**，也就不需要为每块几何体管理贴图槽。

代价是每次采样都要真算噪声（见下面的成本说明）。

为什么落在 logic 层而不是 `engine/proctex.py`
---------------------------------------------
- 这里是**纯函数 + 纯数学**，不碰 GL、不上传纹理 —— 属于逻辑层；
- engine 是更底层（core/logic/presentation 的公共件都下沉在 engine），
  **engine 不得反向依赖 logic**，而本模块要用 `logic/noise.py` 的 `fbm3`；
- 它的消费方是**网格顶点色**（`vertex_colors`），不是贴图槽 —— 这本来就不是
  贴图管线的活。

借纪（铁律 10）：实体纹理是 Ken Perlin 1985《An Image Synthesizer》提出的
"solid texture" 概念；本模块的噪声全部来自本仓 `logic/noise.py`（其账见该模块
docstring）。大理石那条 `sin(scale·z + k·turb(p))` 的配方借自公开的图形学笔记
（Ray Tracing: The Next Week 一脉），属**思路**档，实现自写。

成本（本机实测，供排期参考）
--------------------------
`sample()` 每次 3~4 层 `perlin3`，约 **40~55 µs**。逐顶点烘焙请按
"顶点数 × 50µs" 估：1 万顶点约 0.5 s，6 万顶点约 3 s。要更快就降 `octaves`，
或者只在顶点密度高的地方用它（远端 LOD 用常量色）。
"""
from __future__ import annotations

import math

from world3d.logic.noise import fbm3

__all__ = ['KINDS', 'sample', 'vertex_colors', 'material_preview']

#: 可用的材质种类（名字 -> 一句话说明；测试断言两两可区分）
KINDS = {
    'marble': '大理石：z 向脉纹被 fBm 扭曲（明暗条纹 + 细晶粒）',
    'rust': '锈蚀钢：噪阈值分锈/钢两相，锈处低金属高粗糙',
    'rock': '岩层：y 向分层 + fBm 破碎感',
    'wood': '木料：绕 y 轴的环形年轮被 fBm 扰动',
    'moss': '苔岩：噪阈值决定苔藓覆盖（绿）与裸岩（灰）',
}

_EPS = 1e-9


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _clip01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _turb(n: float) -> float:
    """fBm 的 0..1 → 0..1 的"湍流"量：``|2n−1|``。

    为什么要取绝对值和翻折：fBm 直接线性相加得到的是"云团"，而自然界里
    被冲刷出锐利边界的纹理（大理石脉、木纹、锈斑）靠的是**零等值线**——
    把 0.5 附近的过渡压成一条锐线，才会有"脉络"而不是"雾团"。
    """
    return abs(2.0 * n - 1.0)


def sample(x: float, y: float, z: float, kind: str = 'marble',
           seed: int = 0, scale: float = 1.0) -> tuple:
    """世界坐标 -> ``(r, g, b, roughness, metallic)``，全部 0..1。

    ``scale`` 是世界尺度（``0.5`` = 材质放大一倍）。它替代了 UV 空间里的
    "平铺次数"：几何体越大，同一个 ``scale`` 下纹理重复得越多。
    """
    if kind not in KINDS:
        raise ValueError(f'未知材质 {kind!r}（可选：{"、".join(KINDS)}）')
    if scale <= 0.0:
        raise ValueError(f'scale 必须 > 0，收到 {scale}')
    px, py, pz = x * scale, y * scale, z * scale

    if kind == 'marble':
        # 文章那条式子的 3D 版：sin(沿脉方向 + k·turb(p))，再 0.5(1+·) 压回 0..1。
        # ⚠ 脉纹方向不能只取 z：实体纹理是纯空间函数，切片方向是任意的 ——
        #   `sin(2.6·z + …)` 在 z=常数的切片里退化成同心云团（第一版实测就是
        #   "灰糊一片"，看着像雾不像石头）。取斜向 (z + 0.45x) 后任意切片都有脉。
        n = fbm3(px * 0.55, py * 0.55, pz * 0.55, octaves=4, seed=seed)
        v = 0.5 * (1.0 + math.sin(7.5 * (pz + 0.45 * px) + 2.6 * _turb(n)))
        lum = _clip01(0.92 - 0.34 * v)              # 脉纹是**暗**的
        grain = fbm3(px * 3.1, py * 3.1, pz * 3.1, octaves=2, seed=seed + 71)
        lum = _clip01(lum + (grain - 0.5) * 0.06)   # 细晶粒
        return (lum, lum, lum * 0.985, _clip01(0.22 + 0.40 * v), 0.0)

    if kind == 'rust':
        n = fbm3(px * 0.8, py * 0.8, pz * 0.8, octaves=4, seed=seed)
        fine = fbm3(px * 4.2, py * 4.2, pz * 4.2, octaves=2, seed=seed + 13)
        r_amt = _clip01((n * 0.78 + fine * 0.22 - 0.46) * 3.2)   # 两相：钢 / 锈
        base = (0.52, 0.54, 0.57)                                # 钢
        rust = (0.47 + 0.12 * fine, 0.22, 0.09)                  # 锈
        rgb = tuple(_lerp(base[i], rust[i], r_amt) for i in range(3))
        rough = _clip01(0.30 + 0.55 * r_amt)
        metal = _clip01(1.0 - r_amt)
        return (rgb[0], rgb[1], rgb[2], rough, metal)

    if kind == 'rock':
        layer = fbm3(px * 0.35, py * 1.6, pz * 0.35, octaves=3, seed=seed)
        bands = 0.5 * (1.0 + math.sin(py * 2.2 + 3.0 * _turb(layer)))
        break_up = fbm3(px * 2.4, py * 2.4, pz * 2.4, octaves=2, seed=seed + 29)
        lum = _clip01(0.34 + 0.20 * bands + (break_up - 0.5) * 0.16)
        return (_clip01(lum * 1.02), lum, _clip01(lum * 0.94),
                _clip01(0.72 + 0.18 * break_up), 0.0)

    if kind == 'wood':
        radial = math.sqrt(px * px + pz * pz)
        n = fbm3(px * 0.7, py * 0.45, pz * 0.7, octaves=3, seed=seed)
        ring = 0.5 * (1.0 + math.sin(radial * 7.0 + 4.0 * _turb(n)))
        lum = _clip01(0.30 + 0.26 * ring)
        return (_clip01(lum * 1.35), _clip01(lum * 0.88), _clip01(lum * 0.55),
                _clip01(0.55 + 0.20 * ring), 0.0)

    # moss：阈值决定苔藓覆盖，露出的部分是灰岩
    n = fbm3(px * 0.65, py * 0.95, pz * 0.65, octaves=4, seed=seed)
    cover = _clip01((n - 0.52) * 4.0)
    stone = (0.44, 0.44, 0.42)
    moss = (0.24, 0.38, 0.16)
    rgb = tuple(_lerp(stone[i], moss[i], cover) for i in range(3))
    return (rgb[0], rgb[1], rgb[2], _clip01(0.80 - 0.18 * cover), 0.0)


def vertex_colors(points, kind: str = 'marble', seed: int = 0,
                  scale: float = 1.0) -> list:
    """批量烘焙顶点色：``points`` 是 ``(x, y, z)`` 的可迭代，返回同长 ``[(r,g,b)]``。

    这就是"实体纹理"在本引擎里的落地形态 —— 网格本来就是**逐顶点着色**
    （`engine/geometry.py` 与 `logic/voxel.py` 都发 position + color），
    所以按顶点求材质、并进 color 缓冲即可，不需要 UV、不需要贴图槽。
    """
    out = []
    for p in points:
        r, g, b, _rough, _metal = sample(p[0], p[1], p[2], kind, seed, scale)
        out.append((r, g, b))
    return out


def material_preview(kind: str, size: int = 64, seed: int = 0,
                     axis: str = 'xy') -> list:
    """取一张 ``size × size`` 的**切片**（世界坐标在切片平面上扫）—— 只给肉眼看。

    返回行主序的 ``[(r,g,b)]``，长度 ``size*size``。``axis`` 决定哪两根轴张开：
    ``xy`` / ``xz`` / ``yz``（第三轴固定在 0，切片越界处会自然重复，因为噪声
    是空间函数、不是贴图）。用途是"这纹理到底像不像"—— 这类判断没法写断言。
    """
    if axis not in ('xy', 'xz', 'yz'):
        raise ValueError(f'axis 只能是 xy / xz / yz，收到 {axis!r}')
    if size < 2:
        raise ValueError(f'size 至少 2，收到 {size}')
    out = []
    step = 1.0 / size * 4.0            # 4 个世界单位铺满一张切片，纹理不糊
    for j in range(size):
        v = (j - size * 0.5) * step
        for i in range(size):
            u = (i - size * 0.5) * step
            if axis == 'xy':
                p = (u, v, 0.0)
            elif axis == 'xz':
                p = (u, 0.0, v)
            else:
                p = (0.0, u, v)
            r, g, b, _r, _m = sample(p[0], p[1], p[2], kind, seed)
            out.append((r, g, b))
    return out
