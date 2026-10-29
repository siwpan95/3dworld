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

from world3d.logic.noise import fbm3

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


# ============================================================ 3D 密度场世界 ==
# 上面那套是 **2.5D 高度图**（每个 (x,z) 列一个高度，柱子必然连成一整根）——
# 它做不出洞穴和悬垂：因为 `y < h` 的判定在 y 方向单调，柱子里不可能有"空-实-空"。
# 这一节补的是三维密度场：实心与否取决于 `density(x, y, z) > 0`，于是同一个 (x,z)
# 列上可以出现多层岩体（悬垂 / 拱 / 洞穴）。
#
# 与既有那套的关系：**并存、互不影响**。`W`/`MAXH`/`build_heights`/`build_mesh`
# 一行没动（`test_voxel_mesh_invariants` 钉住它们的契约），本节全是新增函数。

CAVE_W, CAVE_H, CAVE_D = 32, 16, 32   # 默认尺寸；洞穴世界比 2.5D 那套高，才装得下洞


def _ci(x, y, z, w, d):
    """占用位图下标：``(y·d + z)·w + x``（y 最外层，便于按层扫描）。"""
    return (y * d + z) * w + x


def cave_occupancy(seed, w=CAVE_W, h=CAVE_H, d=CAVE_D, cave=0.11,
                   overhang=0.7, scale=0.075, vscale=1.2):
    """三维密度场 → 占用位图（``bytearray``，1 = 实心；长度 ``w*h*d``）。

    密度 = ``地表高度 - y + overhang · (fbm3 - 0.5)·2``：

    * 第一项给出"地表"，第二项是**让它不再是高度图**的关键 —— 抖动幅度（单位是格）
      超过 1 时，相邻两格的判定可以反号，于是同一列上出现"实-空-实"。这就是悬垂。
    * **隧道 = 两片等值面相交**：``|a−0.5|·2 < cave`` 且 ``|b−0.5|·2 < cave``（a/b 是两组
      独立噪声）。单片等值面是**曲面**，在剖面上切出的是孤立点/短线 —— 看着像"多孔岩石"
      而不是洞（第一版实测就是满屏小孔）；两片曲面**相交**才退化成一条**曲线**，那才是隧道。
    * 最底一层当地基（永远实心），顶层留空。

    ⚠ **``overhang`` 与 ``vscale`` 是一对，别单独调**。密度对 y 的斜率恒为 ``-1``
    （那一项就是 ``-y``），要出现悬垂必须让抖动**沿 y 变化得够快**：

        每格的密度增量 ≈ -1 + 2·overhang·vscale·(噪声斜率≈1.5)

    只有 ``2·overhang·vscale·1.5 > 1`` 才有翻转。实测三档（40×24×40，seed=5）：

    | overhang | vscale | 结果 |
    |---:|---:|---|
    | 2.6 | 1.2 | Δ≈9：几乎每格都翻转 ⇒ **碎石汤**（剖面全是孤立方块，第一版就是这样） |
    | 0.5 | 1.0 | Δ≈0.5：地表连贯，偶有拱 |
    | **0.7** | **1.2** | **默认**：地表连贯 + 明显拱（悬垂 ~500~600 格）+ 封闭洞穴 4~7% |

    ⚠ 成本：每格 3~5 次 ``perlin3``，实测 32×16×32 ≈ **0.46 s**、40×24×40 ≈ **1.1 s**
    （本机，纯 Python 标量）。要么降尺寸，要么等将来有 3D 纹理通道后挪到 GPU。
    """
    if w < 3 or h < 4 or d < 3:
        raise ValueError(f'世界太小（{w}×{h}×{d}），至少 3×4×3')
    if not 0.0 <= cave < 0.5:
        raise ValueError(f'cave 是"隧道半宽"，应在 [0, 0.5)，收到 {cave}')
    if vscale <= 0.0:
        raise ValueError(f'vscale 必须 > 0，收到 {vscale}')
    if overhang <= 0.0:
        raise ValueError(f'overhang 必须 > 0，收到 {overhang}')
    occ = bytearray(w * h * d)

    # 每个 (x,z) 列先算一次地表 —— 只算一次，别在 y 循环里重复算
    surf = [0.0] * (w * d)
    base = 0.55 * h
    amp = 0.22 * h
    for z in range(d):
        for x in range(w):
            n = fbm3(x * scale, 0.0, z * scale, octaves=3, seed=seed)
            surf[z * w + x] = base + amp * (n - 0.5) * 2.0

    for y in range(1, h):                        # 底层是地基，不参与挖洞
        for z in range(d):
            for x in range(w):
                s = surf[z * w + x]
                # 3D 抖动：就是这一项让"柱状"变成"有悬垂"（y 频率见 docstring 的 vscale）
                jitter = fbm3(x * scale * 1.3, y * vscale, z * scale * 1.3,
                              octaves=2, seed=seed + 7)
                dens = (s - y) + overhang * (jitter - 0.5) * 2.0
                solid = dens > 0.0
                if solid and y < h - 1:
                    # 隧道 = **两片等值面相交**。单片等值面在剖面上是孤立的点/短线
                    # （第一版就是这样：看着像"多孔岩石"，不是洞）；两片各自是曲面、
                    # 相交才退化成**曲线** —— 那才是隧道。
                    a = fbm3(x * scale * 0.9, y * vscale * 0.7, z * scale * 0.9,
                             octaves=2, seed=seed + 311)
                    b = fbm3(x * scale * 1.15 + 17.0, y * vscale * 0.85,
                             z * scale * 1.15 - 9.0, octaves=2, seed=seed + 733)
                    if (abs(a - 0.5) * 2.0 < cave and abs(b - 0.5) * 2.0 < cave):
                        solid = False
                if solid:
                    occ[_ci(x, y, z, w, d)] = 1
    for z in range(d):                           # 底层地基
        for x in range(w):
            occ[_ci(x, 0, z, w, d)] = 1
    return occ


def open_air_mask(occ, w=CAVE_W, h=CAVE_H, d=CAVE_D):
    """从**天空**（顶层空气）做 6 邻域洪水填充，返回"通天的空气"掩码。

    为什么要区分：同一列上的"实-空-实"有两种成因 ——
    **拱/悬垂**（空腔通着天，抬头看得见）与**封闭洞穴**（空腔被岩石包住）。
    两者在占用位图上长得一样，只有连通性分得开；`cave_stats` 的统计靠它。
    """
    seen = bytearray(w * h * d)
    stack = []
    for z in range(d):
        for x in range(w):
            i = _ci(x, h - 1, z, w, d)
            if not occ[i] and not seen[i]:
                seen[i] = 1
                stack.append((x, h - 1, z))
    while stack:
        x, y, z = stack.pop()
        for nx, ny, nz in NORMALS:            # 复用六面法线当 6 邻域方向
            ax, ay, az = x + nx, y + ny, z + nz
            if ax < 0 or ax >= w or az < 0 or az >= d or ay < 0 or ay >= h:
                continue
            i = _ci(ax, ay, az, w, d)
            if not occ[i] and not seen[i]:
                seen[i] = 1
                stack.append((ax, ay, az))
    return seen


def cave_stats(occ, w=CAVE_W, h=CAVE_H, d=CAVE_D):
    """统计：实心占比、**悬垂**（头顶岩石 + 脚下通天空气）、**封闭洞穴**、夹层列。

    三个数字的定义（都刻意取"能被打穿"的口径）：

    * ``overhang``：实心格，其正下方是**与天空连通**的空气 —— 也就是"人站得进去的
      拱/悬崖底"。对 2.5D 高度图恒为 0（柱子里下方必然是实心），所以它是
      "这个世界不再是高度图"的判据。
    * ``sealed_air``：空气格，但**不与天空连通** —— 封闭洞穴（挖得到、走不进去）。
    * ``gap_cols``：列上出现"实-空-实"的列数（不计连通性，只说明非单调）。

    只给测试与 HUD 用；不参与网格化。
    """
    total = w * h * d
    solid = sum(occ)
    open_air = open_air_mask(occ, w, h, d)
    open_cnt = sum(1 for i in range(total) if open_air[i])
    sealed = sum(1 for i in range(total) if not occ[i] and not open_air[i])
    overhang = 0
    for y in range(1, h):
        for z in range(d):
            for x in range(w):
                if occ[_ci(x, y, z, w, d)]:
                    below = _ci(x, y - 1, z, w, d)
                    if not occ[below] and open_air[below]:
                        overhang += 1
    gap_cols = 0
    for z in range(d):
        for x in range(w):
            col = [occ[_ci(x, y, z, w, d)] for y in range(h)]
            if any(col[y] == 0 and col[y + 1] for y in range(h - 1)):
                gap_cols += 1
    return {'total': total, 'solid': solid, 'ratio': solid / total,
            'open_air': open_cnt, 'sealed_air': sealed,
            'sealed_ratio': sealed / total, 'overhang': overhang,
            'gap_cols': gap_cols}


def build_cave_mesh(occ, seed=0, w=CAVE_W, h=CAVE_H, d=CAVE_D,
                    cull=True, kind='rock', color_fn=None, pos=None, col=None):
    """三维占用位图 → 三角形汤（六向面剔除 + 实体纹理顶点色）。

    * ``cull=True`` 时相邻格实心就跳过该面（只剩表面）；``cull=False`` 每格发 6 面。
    * 颜色默认取 ``logic/solidtex`` 的实体纹理（**按世界坐标**求值，所以岩层随
      高度自然分层，不需要 UV、也不需要调色板表）；``color_fn(x, y, z) -> (r,g,b)``
      可覆盖，用于调试或固定配色。
    * 底面（y=0 的下方）视为实心 / 不画，与世界外侧（空气）区分开 —— 与
      ``build_mesh`` 的既有约定一致。

    返回 ``(位置 array, 颜色 array, 面数)``，与 ``build_mesh`` 同形，可直接喂同一个着色器。
    """
    from world3d.logic.solidtex import sample as _solid_sample
    pos = array.array('f') if pos is None else pos
    col = array.array('f') if col is None else col
    faces = 0
    ext_p, ext_c = pos.extend, col.extend
    for y in range(h):
        for z in range(d):
            for x in range(w):
                if not occ[_ci(x, y, z, w, d)]:
                    continue
                rgb = None
                for ni in range(6):
                    nx, ny, nz = NORMALS[ni]
                    if cull:
                        ax, ay, az = x + nx, y + ny, z + nz
                        if ay < 0:
                            continue                     # 地基以下算实心：不画底面
                        if 0 <= ax < w and 0 <= az < d and 0 <= ay < h:
                            if occ[_ci(ax, ay, az, w, d)]:
                                continue                 # 被邻居挡住：剔除
                    if rgb is None:
                        if color_fn is not None:
                            rgb = color_fn(x, y, z)
                        else:
                            rgb = _solid_sample(x + 0.5, y + 0.5, z + 0.5,
                                                kind, seed)[:3]
                    k = FACE_SHADE[ni]
                    c = (rgb[0] * k, rgb[1] * k, rgb[2] * k, 1.0)
                    ext_p([v for p in FACE_QUAD[ni]
                           for v in (x + p[0], y + p[1], z + p[2])])
                    for _ in range(6):
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
