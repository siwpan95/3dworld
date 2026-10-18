# -*- coding: utf-8 -*-
"""体素世界（高度图生成 + 面剔除网格化）。

自 ``examples/voxel_terrain.py`` 上提（2026-10-15 demo 剥离）：demo 删除后，
本模块是「体素面剔除」这项能力的引擎侧落点；``tests/run_tests.py`` 的
``test_voxel_mesh_invariants`` 直接导入本模块的函数做断言。

设计约束（与原 demo 一致，改动即破坏测试契约）
----------------------------------------------
- **纯函数、零依赖**：只 import ``math`` + ``array``，不碰 pyglet / GL；
  同 seed 必然得到同一世界（测试钉住可复现性）。
- **面剔除**：``build_mesh(cull=True)`` 时相邻体素为实心就跳过该面 ——
  只剩表面；``cull=False`` 每个实心体素发 6 面，用于数量对比（HUD 的剔除率）。
- **可分段网格化**：``z0/z1`` 只处理部分列，配合传入的 ``pos/col`` 缓冲追加，
  即可把重建摊到多帧（渐进重建：整套 352ms -> 每帧几毫秒）。
- **面的顶点偏移必须是 ``(dx,dy,dz)`` 三元组**（``FACE_QUAD``）：摊平成一串
  float 后，加体素基坐标时会分不清分量，把 x 加到 y/z 上（实测把整块地形压成
  一条斜线）。
- **逐面亮度**：法线调亮度（顶面最亮），思路同 ``world3d.core.geometry.box_data``。
"""
from __future__ import annotations

import array
import math

# ================================================================ 世界参数 ==
W = 40                 # 世界边长（列数），x/z 都是 0..W-1
MAXH = 14              # 最大层数（1~14）
CX = CZ = 0.5 * W      # 世界中心

# 单位立方体 6 个面：法线 + 4 个角（角按逆时针给，正面朝外）
FACES = (
    ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
    ((0, -1, 0), ((0, 0, 1), (0, 0, 0), (1, 0, 0), (1, 0, 1))),
    ((1, 0, 0), ((1, 0, 1), (1, 0, 0), (1, 1, 0), (1, 1, 1))),
    ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
    ((0, 0, -1), ((1, 0, 0), (0, 0, 0), (0, 1, 0), (1, 1, 0))),
)
NORMALS = tuple(f[0] for f in FACES)
# 每个面的 6 个顶点偏移（两个三角形 = 角 0,1,2 + 0,2,3）。
# 注意：必须是 (dx,dy,dz) 三元组而不是摊平的 float —— 摊平后 x/y/z 分量分不清，
# 加体素基坐标时会把 x 加到 y/z 上（实测直接把地形压成一条斜线）。
FACE_QUAD = tuple((c[0], c[1], c[2], c[0], c[2], c[3]) for _, c in FACES)
# 法线调亮度：顶面最亮、背面最暗（和 box_data 的 _face_shade 同一个意思）
FACE_SHADE = (1.00, 0.45, 0.82, 0.66, 0.90, 0.58)

# 表层生物群系 / 内部岩层配色
C_LOW = (0.22, 0.46, 0.19)      # 低地草
C_MID = (0.33, 0.48, 0.22)      # 中段草坡
C_HIGH = (0.47, 0.43, 0.34)     # 高处岩
C_SNOW = (0.88, 0.91, 0.96)     # 峰顶雪
C_STRATA = ((0.30, 0.29, 0.33), (0.38, 0.34, 0.28),
            (0.45, 0.42, 0.40), (0.56, 0.55, 0.53))

_MASK = 0xFFFFFFFF


def _hash01(x, y, z, seed):
    """确定性哈希 -> [0,1)：色块抖动与高度图都用它，重建必然一致。"""
    h = (x * 0x27D4EB2D) & _MASK
    h = (h ^ (y * 0x165667B1)) & _MASK
    h = (h ^ (z * 0x9E3779B1)) & _MASK
    h = (h ^ (seed * 0x85EBCA6B)) & _MASK
    h = ((h ^ (h >> 15)) * 0x2C1B3C6D) & _MASK
    h = ((h ^ (h >> 13)) * 0x297A2D39) & _MASK
    h ^= h >> 16
    return (h & 0xFFFFFF) / 16777216.0


def build_heights(seed):
    """高度图：白噪声 -> 几轮 3x3 均值（连绵起伏）-> 混回一点原始噪声 -> 量化。"""
    raw = [_hash01(x, 0, z, seed) for z in range(W) for x in range(W)]
    field = raw[:]
    for _ in range(3):
        nxt = []
        for z in range(W):
            for x in range(W):
                s, c = 0.0, 0
                for dz in (-1, 0, 1):
                    zz = z + dz
                    if zz < 0 or zz >= W:
                        continue
                    for dx in (-1, 0, 1):
                        xx = x + dx
                        if 0 <= xx < W:
                            s += field[zz * W + xx]
                            c += 1
                nxt.append(s / c)
        field = nxt
    mixed = [0.72 * field[i] + 0.28 * raw[i] for i in range(W * W)]
    lo, hi = min(mixed), max(mixed)
    span = max(1e-6, hi - lo)
    return [1 + int((v - lo) / span * (MAXH - 1.6) + 0.45) for v in mixed]


def voxel_rgb(x, y, z, h, seed):
    """块色 = 分层色 x 哈希抖动；表层按高度给生物群系，内部给岩层。"""
    if y == h - 1:
        base = (C_LOW if y <= 4 else C_MID if y <= 8
                else C_HIGH if y <= 11 else C_SNOW)
    else:
        base = C_STRATA[min(3, y // 4)]
    k = 0.86 + 0.28 * _hash01(x, y * 7 + 1, z, seed + 31337)
    return base[0] * k, base[1] * k, base[2] * k


def build_mesh(heights, seed, cull, pos=None, col=None, z0=0, z1=None):
    """把世界网格化成三角形汤（可分段：只处理 z ∈ [z0, z1) 的列）。

    ``cull=True`` 时相邻体素为实心就跳过该面——这就是"面剔除"；
    ``cull=False`` 时每个实心体素都发 6 个面，用来做数量对比。
    返回 (位置 array, 颜色 array, 面数)。

    ``pos``/``col`` 传入时会**追加**到已有缓冲，配合 z0/z1 就能把重建摊到多帧
    （实测整套 352ms，同步重建会有一次可见停顿；分段后每帧只花几毫秒）。
    """
    pos = array.array('f') if pos is None else pos
    col = array.array('f') if col is None else col
    faces = 0
    ext_p, ext_c = pos.extend, col.extend
    z1 = W if z1 is None else z1
    for z in range(z0, z1):
        zrow = z * W
        for x in range(W):
            h = heights[zrow + x]
            for y in range(h):
                r, g, b = voxel_rgb(x, y, z, h, seed)
                for ni in range(6):
                    nx, ny, nz = NORMALS[ni]
                    if cull:
                        ax, ay, az = x + nx, y + ny, z + nz
                        if az < 0 or az >= W or ax < 0 or ax >= W:
                            pass                    # 世界外侧 = 空气，要画
                        elif ay < 0:
                            continue                # 地基以下算实心：不画底面
                        elif ay < heights[az * W + ax]:
                            continue                # 被邻居挡住：剔除
                    k = FACE_SHADE[ni]
                    c = (r * k, g * k, b * k, 1.0)
                    ext_p([v for p in FACE_QUAD[ni]
                           for v in (x + p[0], y + p[1], z + p[2])])
                    ext_c(c)
                    ext_c(c)
                    ext_c(c)
                    ext_c(c)
                    ext_c(c)
                    ext_c(c)
                    faces += 1
    return pos, col, faces


# ================================================================== 着色器 ==
# 顶点色已含分层色与逐面亮度；片元直通。
VERTEX_SRC = '''#version 330 core
in vec3 position;
in vec4 colors;          // 已含分层色与逐面亮度
uniform WindowBlock { mat4 projection; mat4 view; } window;
out vec4 v_col;
void main() {
    v_col = colors;
    gl_Position = window.projection * window.view * vec4(position, 1.0);
}'''

FRAGMENT_SRC = '''#version 330 core
in vec4 v_col;
out vec4 final_colors;
void main() { final_colors = v_col; }'''
