# -*- coding: utf-8 -*-
"""贪心网格化（单层体素 → 合并面片）—— 补齐 ``SdfVoxelCore.world_mesh`` 的留白。

P1-④「归仓 / Boxward」的渲染地基：关卡墙体是单层（y ∈ [0,1]）的单位方块，
逐块画 5 个可见面会有大量冗余三角形；本模块把**同朝向的共面矩形合并成大 quad**——

- 顶面（+y）：对墙格做 2D 贪心矩形合并（走廊/大片墙合成一两个 quad）；
- 侧面（±x / ±z）：面存在于「墙格与开放格的交界」，按行/列贪心合并
  （mask 的结构保证相邻格不会同向共面，矩形只在单一方向延伸）；
- 底面（−y）永远被地板挡住，不生成。

纯函数、零依赖（不 import cordis / pyglet），可独立单测；输出与
``engine/baked_mesh.unit_cube`` 同约定（position + shade 三角汤，
GL_TRIANGLES，每面烘焙固定明暗）。§11.4「自建」列的唯一算法件。
"""
from __future__ import annotations

# 面明暗（与 baked_mesh 的"面烘焙明暗"惯例同族，免法线着色；具体数值各自独立）
SHADE_TOP = 1.00
SHADE_Z = 0.82          # ±z 朝向的侧面
SHADE_X = 0.72          # ±x 朝向的侧面


def merge_rects(mask, h, w):
    """布尔掩码 → 覆盖全部 True 格的极小矩形列表 [(i0, j0, i1, j1)]（半开区间）。

    经典贪心：每个未访问的 True 格先向右扩到头，再整行向下扩；
    保证覆盖、不重叠、矩形数 ≤ 行程压缩的下界量级。
    """
    used = [[False] * w for _ in range(h)]
    rects = []
    for i in range(h):
        for j in range(w):
            if not mask[i][j] or used[i][j]:
                continue
            j1 = j + 1
            while j1 < w and mask[i][j1] and not used[i][j1]:
                j1 += 1
            i1 = i + 1
            while i1 < h and all(mask[i1][jj] and not used[i1][jj]
                                 for jj in range(j, j1)):
                i1 += 1
            for ii in range(i, i1):
                for jj in range(j, j1):
                    used[ii][jj] = True
            rects.append((i, j, i1, j1))
    return rects


def layer_mesh(walls, h, w, cs=1.0, x0=0.0, z0=0.0, ytop=1.0):
    """单层墙格集合 → (pos, shade) 三角汤。

    ``walls``: {(i, j)}，i = 行 ↔ z，j = 列 ↔ x（与 GridService 的格坐标同约定）；
    格 (i,j) 占据 x ∈ [x0+j·cs, x0+(j+1)·cs]，z ∈ [z0+i·cs, z0+(i+1)·cs]，
    y ∈ [0, ytop]。界外邻居视为开放（棋盘外墙的外侧面会被画出来——
    正交旋转相机在外侧能看到它们）。
    """
    def solid(i, j):
        return 0 <= i < h and 0 <= j < w and (i, j) in walls

    pos, shade = [], []

    def quad(a, b, c, d, s):
        for p in (a, b, c, a, c, d):
            pos.extend(p)
            shade.extend((s, s, s))

    # ---- 顶面（+y）：2D 贪心合并 ----
    top_mask = [[solid(i, j) for j in range(w)] for i in range(h)]
    for i0, j0, i1, j1 in merge_rects(top_mask, h, w):
        xa, xb = x0 + j0 * cs, x0 + j1 * cs
        za, zb = z0 + i0 * cs, z0 + i1 * cs
        quad((xa, ytop, zb), (xb, ytop, zb), (xb, ytop, za), (xa, ytop, za), SHADE_TOP)

    # ---- 侧面：四个方向各自建"面存在"掩码后贪心合并 ----
    # +x：墙(i,j) 与开放(i,j+1) 的交界，平面 x = x0+(j+1)·cs（矩形沿 i 延伸）
    side = [[solid(i, j) and not solid(i, j + 1) for j in range(w)] for i in range(h)]
    for i0, j0, i1, _j1 in merge_rects(side, h, w):
        X = x0 + (j0 + 1) * cs
        za, zb = z0 + i0 * cs, z0 + i1 * cs
        quad((X, 0.0, zb), (X, 0.0, za), (X, ytop, za), (X, ytop, zb), SHADE_X)
    # -x：交界 x = x0+j·cs
    side = [[solid(i, j) and not solid(i, j - 1) for j in range(w)] for i in range(h)]
    for i0, j0, i1, _j1 in merge_rects(side, h, w):
        X = x0 + j0 * cs
        za, zb = z0 + i0 * cs, z0 + i1 * cs
        quad((X, 0.0, za), (X, 0.0, zb), (X, ytop, zb), (X, ytop, za), SHADE_X)
    # +z：交界 z = z0+(i+1)·cs（矩形沿 j 延伸）
    side = [[solid(i, j) and not solid(i + 1, j) for j in range(w)] for i in range(h)]
    for i0, _j0, i1, j1 in merge_rects(side, h, w):
        Z = z0 + (i0 + 1) * cs
        xa, xb = x0 + j0 * cs, x0 + j1 * cs
        quad((xa, 0.0, Z), (xb, 0.0, Z), (xb, ytop, Z), (xa, ytop, Z), SHADE_Z)
    # -z：交界 z = z0+i·cs
    side = [[solid(i, j) and not solid(i - 1, j) for j in range(w)] for i in range(h)]
    for i0, _j0, i1, j1 in merge_rects(side, h, w):
        Z = z0 + i0 * cs
        xa, xb = x0 + j0 * cs, x0 + j1 * cs
        quad((xa, 0.0, Z), (xb, 0.0, Z), (xb, ytop, Z), (xa, ytop, Z), SHADE_Z)

    return pos, shade


def quad_count(pos):
    """三角汤里的 quad 数（每 quad = 2 三角 = 6 顶点 = 18 个 float）。"""
    return len(pos) // 18


def naive_quad_count(walls, h, w):
    """不合并时的 quad 数（每墙格：1 顶面 + 各开放邻居 1 侧面；界外算开放）。"""
    def solid(i, j):
        return 0 <= i < h and 0 <= j < w and (i, j) in walls
    n = 0
    for (i, j) in walls:
        n += 1                                   # 顶面
        for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            if not solid(i + di, j + dj):
                n += 1                           # 露出的侧面
    return n
