# -*- coding: utf-8 -*-
"""体素网格化 v2：**全 3D 三轴贪心合并 + 逐顶点 AO**。

与 :mod:`world3d.engine.greedy_mesh` 的分工（两者不是替代关系，旧模块保持原样）：

* ``greedy_mesh`` 只处理**单层墙**（``y ∈ [0,1]`` 的墙格，顶面 + 4 侧面）；
* 本模块处理**任意三维体素数组**：六个朝向各沿自己的轴逐层切片，对每层的
  「实心 ↔ 空气」交界掩码做 2D 贪心矩形合并，再发 quad。经典三轴 sweep
  （greedy voxel meshing），合并是真的合并 —— 一个 16³ 的实心块只出 6 个 quad。

## 数组索引顺序（钉死，改动即破坏契约）

``voxels[y][z][x]`` —— **先 y，再 z，最后 x**，与 :mod:`world3d.logic.voxel`
的 ``heights[z * W + x]`` + ``y`` 循环同源。``tests/test_voxel_mesh.py::test_array_index_order_y_z_x``
把这条钉住（只放一个 ``voxels[1][2][3]``，断言 quad 的 x 范围是 3、z 范围是 2）。

内部会把它转置成 ``vol[x, y, z]``（世界轴序），之后所有几何计算都在世界轴序上做，
避免"数组轴 ↔ 世界轴"的隐式映射（那类错误表现为整块地形被压扁/旋转，极难看出来）。

## 面与绕序

法线朝外、从外部看**逆时针**，与 :func:`world3d.engine.geometry.box` 的 ``faces``
表同约定（顶点法线 = 面法线，硬边不共享顶点；``compute_normals`` 的平均法线会把
方块棱角磨圆，所以这里直接给常量法线）。

每个 quad 连续占 4 个顶点，顺序固定为平面内的 ``c0=(u0,v0) c1=(u1,v0)
c2=(u1,v1) c3=(u0,v1)``（``u/v`` 是除扫掠轴之外的另两轴，``(u,v) = (a+1, a+2) % 3``），
索引里再用 0/1/2 的排列决定绕序与对角线 —— 测试可以直接按 ``4*q + k`` 取角点。

## AO（4 档）与对角翻转

每个 quad 角看它所在平面（面朝外那一层）的 3 个邻居 ``side1/side2/corner``::

    ao = 0                     若 side1 and side2（两侧都挡：更强的暗角）
    ao = 3 - (side1 + side2 + corner)   其余情况

取值 0..3，亮度 ``0.55 + ao * 0.15``（0→0.55，3→1.00），写进**顶点色**的 RGB 三通道
（A 保留基色 alpha），所以 ``MeshData`` 不需要任何扩展字段。反过来读回 AO 档位：:

    ao = round((colors[4 * vertex] - 0.55) / 0.15)

当 ``ao[0] + ao[3] > ao[1] + ao[2]`` 时把四边形切成**另一条对角线**（默认 0-2，
翻转后 1-3），消掉各向异性条纹。
"""
from __future__ import annotations

import numpy as np

from world3d.engine.geometry import MeshData

__all__ = [
    'greedy_mesh_3d', 'binary_greedy_mesh', 'mesh_stats', 'naive_face_count',
    'culled_face_count', 'ao_brightness', 'AO_LEVELS', 'AO_BRIGHTNESS_MIN',
    'AO_BRIGHTNESS_STEP',
]

#: AO 档位数（0..3）
AO_LEVELS = 4
#: 亮度映射：``0.55 + ao * 0.15``
AO_BRIGHTNESS_MIN = 0.55
AO_BRIGHTNESS_STEP = 0.15
#: 数组两侧各补多少层空气（AO 要再往角外多取 1 格，2 层足够且不会越界回绕）
_PAD = 2


def ao_brightness(ao):
    """AO 档位（0..3）→ 顶点亮度（0.55 / 0.70 / 0.85 / 1.00）。"""
    return AO_BRIGHTNESS_MIN + AO_BRIGHTNESS_STEP * int(ao)


# ------------------------------------------------------------------ 输入 --
def _as_volume(voxels):
    """``voxels[y][z][x]`` → ``vol[x, y, z]`` 的 bool 连续数组（空输入返回 None）。"""
    arr = np.asarray(voxels)
    if arr.size == 0:
        return None
    if arr.ndim != 3:
        raise ValueError(f'voxels 必须是三维数组（voxels[y][z][x]），拿到 ndim={arr.ndim}')
    # 转置成世界轴序 (x, y, z)，后续所有几何计算不再做轴映射
    return np.ascontiguousarray(arr.astype(bool).transpose(2, 0, 1))


def _rgba(c):
    """(r,g,b) / (r,g,b,a) → (r,g,b,a) 四元组。"""
    c = [float(v) for v in c]
    return (c[0], c[1], c[2], c[3] if len(c) > 3 else 1.0)


def _resolve_colors(colors, shape):
    """返回 ``(全局基色 RGBA, 逐体素颜色数组或 None)``。

    接受三种形态：``None``（纯白）、``(r,g,b)`` / ``(r,g,b,a)``、
    或与 ``voxels`` 同形的逐体素颜色 ``(ny, nz, nx, 3|4)``（同样是 ``[y][z][x]`` 序）。
    逐体素色合并时会丢掉矩形内部的色差 —— 取矩形首格的颜色，这是合并的固有代价。
    """
    if colors is None:
        return (1.0, 1.0, 1.0, 1.0), None
    ca = np.asarray(colors, dtype=float)
    if ca.ndim == 1 and ca.size in (3, 4):
        return _rgba(ca), None
    if ca.ndim == 4 and ca.shape[:3] == tuple(shape) and ca.shape[3] in (3, 4):
        vol = np.ascontiguousarray(ca.transpose(2, 0, 1, 3))
        out = np.ones(vol.shape[:3] + (4,), dtype=float)
        out[..., :3] = vol[..., :3]
        if vol.shape[3] == 4:
            out[..., 3] = vol[..., 3]
        return (1.0, 1.0, 1.0, 1.0), out
    raise ValueError('colors 只接受 (r,g,b) / (r,g,b,a) 或与 voxels 同形的 '
                     f'(ny,nz,nx,3|4) 数组，拿到 shape={ca.shape}')


# ------------------------------------------------------------ 2D 贪心 --
def _greedy_rects(mask):
    """布尔掩码 → 覆盖全部 True 格的矩形列表 ``[(i0, j0, i1, j1)]``（半开区间）。

    与 ``greedy_mesh.merge_rects`` 同算法（先向右扩到底、再整行向下扩），
    只是用 numpy 做整行判断。``mask`` 的第 0 维是 u、第 1 维是 v。
    """
    h, w = int(mask.shape[0]), int(mask.shape[1])
    used = np.zeros((h, w), dtype=bool)
    rects = []
    for i in range(h):
        row = mask[i]
        for j in range(w):
            if not row[j] or used[i, j]:
                continue
            j1 = j + 1
            while j1 < w and row[j1] and not used[i, j1]:
                j1 += 1
            i1 = i + 1
            while i1 < h and mask[i1, j:j1].all() and not used[i1, j:j1].any():
                i1 += 1
            used[i, j:j1] = True
            used[i + 1:i1, j:j1] = True
            rects.append((i, j, i1, j1))
    return rects


def _vertex_ao(sol, a, u, v, s, sign, u_ref, v_ref, u_off, v_off):
    """一个 quad 角点的 AO 档位（0..3）。

    ``sol`` 是补齐过空气的实心阵列；``(s, sign)`` 是面所在的格与朝向；
    ``(u_ref, v_ref)`` 是该角在面内紧贴的格，``(u_off, v_off)`` 是朝角外侧的偏移。
    采样点在**面朝外那一层**（``s + sign``）—— 只看"外面有没有挡光"，与真实遮挡一致。
    """
    idx = [0, 0, 0]
    idx[a] = s + sign
    idx[u] = u_ref
    idx[v] = v_ref
    i1 = list(idx)
    i1[u] += u_off
    i2 = list(idx)
    i2[v] += v_off
    i3 = list(idx)
    i3[u] += u_off
    i3[v] += v_off
    side1 = bool(sol[i1[0], i1[1], i1[2]])
    side2 = bool(sol[i2[0], i2[1], i2[2]])
    corner = bool(sol[i3[0], i3[1], i3[2]])
    if side1 and side2:
        return 0
    return 3 - (int(side1) + int(side2) + int(corner))


# ------------------------------------------------------------ 主入口 --
def greedy_mesh_3d(voxels, *, size=1.0, origin=(0.0, 0.0, 0.0), colors=None,
                   name='voxel_mesh'):
    """任意三维体素数组 → 合并后的 :class:`MeshData`。

    Args:
        voxels: 三维布尔/整数数组，索引顺序 **``voxels[y][z][x]``**（先 y，再 z，再 x）。
            数组**边界外视为空气**，所以外表面会被画出来（正是一个浮空方块该有的样子）。
        size: 单个体素的边长（世界单位）。
        origin: 体素 ``(0,0,0)`` 角点的世界坐标平移。
        colors: ``None``（纯白）/ ``(r,g,b)`` / ``(r,g,b,a)`` / 同形逐体素颜色。
            亮度（AO）会乘进 RGB。
        name: ``MeshData.name``。

    Returns:
        ``MeshData(positions, normals, uvs, colors, indices, name=...)``：
        位置按 ``origin + 格坐标 * size`` 展开；法线是常量面法线（硬边）；
        uv 用**体素格坐标**（绝对格号，便于材质平铺，跨度 = 合并矩形的宽高）；
        colors 的 RGB = 基色 × AO 亮度（``0.55 + ao * 0.15``），A = 基色 alpha。
        每个 quad 连续 4 个顶点（``c0..c3``）+ 6 个索引，``quads == len(indices) // 6``。
    """
    arr = np.asarray(voxels)
    size = float(size)
    ox, oy, oz = (float(c) for c in origin)
    vol = _as_volume(arr)
    if vol is None:
        return MeshData([], [], [], name=name)
    base, per_voxel = _resolve_colors(colors, arr.shape)

    sol = np.pad(vol, _PAD, mode='constant', constant_values=False)
    br, bg, bb, ba = base
    positions, normals, uvs, vcolors, indices = [], [], [], [], []

    for a in range(3):
        u, v = (a + 1) % 3, (a + 2) % 3
        # np.take 出来的层按"剩余轴的自然顺序"排；只有 a==1（y 轴）与 (u,v)=(z,x) 反序
        need_t = [ax for ax in range(3) if ax != a] != [u, v]
        for sign in (1, -1):
            # 补齐层是空气，np.roll 的首尾回绕不会造出假面（两端恒为 False）
            face = sol & ~np.roll(sol, -sign, axis=a)
            for s in range(sol.shape[a]):
                layer = np.take(face, s, axis=a)
                if not layer.any():
                    continue
                if need_t:
                    layer = layer.T
                plane = s + (1 if sign > 0 else 0) - _PAD
                for (u0, v0, u1, v1) in _greedy_rects(layer):
                    ou0, ou1 = u0 - _PAD, u1 - _PAD
                    ov0, ov1 = v0 - _PAD, v1 - _PAD
                    pts, aos = [], []
                    for du, dv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                        coords = [0.0, 0.0, 0.0]
                        coords[a] = plane
                        coords[u] = ou0 if du == 0 else ou1
                        coords[v] = ov0 if dv == 0 else ov1
                        pts.append((ox + coords[0] * size,
                                    oy + coords[1] * size,
                                    oz + coords[2] * size))
                        aos.append(_vertex_ao(
                            sol, a, u, v, s, sign,
                            u0 if du == 0 else u1 - 1,
                            v0 if dv == 0 else v1 - 1,
                            -1 if du == 0 else 1,
                            -1 if dv == 0 else 1))
                    if per_voxel is None:
                        cr, cg, cb, ca = br, bg, bb, ba
                    else:
                        cell = [0, 0, 0]
                        cell[a] = s - _PAD
                        cell[u] = ou0
                        cell[v] = ov0
                        cr, cg, cb, ca = per_voxel[cell[0], cell[1], cell[2]]
                    nrm = [0.0, 0.0, 0.0]
                    nrm[a] = float(sign)
                    first = len(positions) // 3
                    for k in range(4):
                        x, y, z = pts[k]
                        b = ao_brightness(aos[k])
                        positions += [x, y, z]
                        normals += nrm
                        vcolors += [cr * b, cg * b, cb * b, ca]
                    # uv = 体素格坐标（绝对格号），跨度就是合并矩形的宽高
                    uvs += [ou0, ov0, ou1, ov0, ou1, ov1, ou0, ov1]
                    flip = aos[0] + aos[3] > aos[1] + aos[2]
                    if sign > 0:
                        tri = (1, 2, 3, 1, 3, 0) if flip else (0, 1, 2, 0, 2, 3)
                    else:
                        tri = (1, 3, 2, 1, 0, 3) if flip else (0, 2, 1, 0, 3, 2)
                    indices += [first + t for t in tri]

    return MeshData(positions, normals, uvs, vcolors, indices, name=name)


# ------------------------------------------------------- 二进制贪心（算法） --
def _binary_greedy_cover(grid):
    """uint64 位掩码 + 位计数驱动的 quadtree 全覆盖合并。

    把每一行打包成 **uint64 位掩码**，用 ``np.bitwise_count`` 在 O(1) 内判定
    "一个矩形整块是否全是面"，自顶向下递归合并。返回覆盖全部 True 单元、互不重叠的
    矩形列表 ``[(i0,j0,i1,j1)]``（半开区间，第 0 维 = u、第 1 维 = v）。

    与 :func:`_greedy_rects`（逐行右扩 + 整列下扩的 2D 贪心扫描）不同：本函数生成的是
    **2 的幂对齐**矩形（binary greedy meshing，cgerikj / Rust ``binary-greedy-meshing``，
    MIT / Apache-2.0 双许可；本实现为自研 numpy 版，**不搬 Rust 的 chunk 布局**）。
    合并数据结构的代价更低，且大块（尤其 2 的幂尺寸）合并得同样干净。
    """
    h, w = int(grid.shape[0]), int(grid.shape[1])
    # 每行补到 64 的整数倍，再打包成 uint64 位掩码（MSB 优先，列 0 → 最高位）。
    wp = ((w + 63) // 64) * 64
    padded = np.zeros((h, wp), dtype=np.uint8)
    padded[:, :w] = grid.astype(np.uint8)
    # packbits 默认 big-order：列 0 是首字节的 MSB。但 `.view(uint64)` 在小端机上把
    # 字节按 LSB 排，导致列 0 落到 bit 7 而非 bit 63，与下方 `_col_mask`（MSB 优先）错位。
    # byteswap 让"列 c → bit 63-c"，位计数与矩形判定才一致。
    words = np.packbits(padded, axis=1).view(np.uint64).byteswap().reshape(h, wp // 64)
    # 每个 uint64 词的 1 的个数 → 积分图（按"词列"累加），用于 O(1) 矩形查总数。
    cnt = np.bitwise_count(words).astype(np.int64)
    S = np.zeros((h + 1, cnt.shape[1] + 1), dtype=np.int64)
    S[1:, 1:] = cnt.cumsum(0).cumsum(1)

    def _col_mask(lo, hi):
        """词内列区间 ``[lo, hi)``（MSB 优先）对应的 64 位掩码。"""
        mask = 0
        for c in range(lo, hi):
            mask |= 1 << (63 - c)
        return np.uint64(mask)

    def rect_count(x, y, sw, sh):
        """矩形 ``[x,x+sw) × [y,y+sh)`` 内 True 单元数（uint64 位计数）。"""
        if sw <= 0 or sh <= 0:
            return 0
        w0 = x // 64
        w1 = (x + sw + 63) // 64
        if w1 - w0 == 1:
            mask = _col_mask(x % 64, x % 64 + sw)
            return int(np.bitwise_count(words[y:y + sh, w0] & mask).sum())
        total = S[y + sh, w1] - S[y, w1] - S[y + sh, w0] + S[y, w0]
        lo = x % 64
        if lo:
            total -= int(np.bitwise_count(
                words[y:y + sh, w0] & _col_mask(0, lo)).sum())
        rem = w1 * 64 - (x + sw)
        if rem:
            start = x + sw - (w1 - 1) * 64
            total -= int(np.bitwise_count(
                words[y:y + sh, w1 - 1] & _col_mask(start, 64)).sum())
        return total

    def full(x, y, sw, sh):
        return rect_count(x, y, sw, sh) == sw * sh

    quads = []

    def rec(x, y, sw, sh):
        # 约定：内部 x = 列起点、y = 行起点（与 rect_count / _col_mask 一致）；
        # 但返回的 rect 元组用 ``(行, 列, 行, 列)``（== 层的第 0/1 轴序），由外层按
        # ``grid[i0:i1, j0:j1]`` 切片。否则会覆盖转置后的错误格子——曾导致"覆盖到空气 /
        # 漏掉实心格"。
        if sw <= 0 or sh <= 0:
            return
        if sw == 1 and sh == 1:
            if grid[y, x]:
                quads.append((y, x, y + 1, x + 1))
            return
        if full(x, y, sw, sh):
            quads.append((y, x, y + sh, x + sw))
            return
        sw2, sw3 = sw // 2, sw - sw // 2
        sh2, sh3 = sh // 2, sh - sh // 2
        rec(x, y, sw2, sh2)
        rec(x + sw2, y, sw3, sh2)
        rec(x, y + sh2, sw2, sh3)
        rec(x + sw2, y + sh2, sw3, sh3)

    rec(0, 0, w, h)
    return quads


def binary_greedy_mesh(voxels, *, size=1.0, origin=(0.0, 0.0, 0.0), colors=None,
                       ao=True, name='voxel_binary_mesh'):
    """任意三维体素数组 → **二进制贪心**合并后的 :class:`MeshData`。

    与 :func:`greedy_mesh_3d` 都做"面剔除 + 合并"，但合并策略不同：本函数用
    :func:`_binary_greedy_cover`（uint64 位掩码 + quadtree 全覆盖合并）生成 **2 的幂对齐**
    矩形。面数**恒 ≤ 朴素面剔除** ``culled_face_count``，在块状体素 / 2 的幂尺寸上通常
    **等于甚至少于**三轴贪心（个别 L 形碎块可能多 1~2 个 quad——属算法取舍）。

    输入/输出约定与 :func:`greedy_mesh_3d` 一致：``voxels[y][z][x]``、``size``、
    ``origin``、``colors``、逐顶点 AO 与对角翻转、``MeshData`` 布局（每 quad 4 顶点、
    6 索引）。AO 与三轴版同式（4 邻居查表 + 翻转对角线）。
    """
    arr = np.asarray(voxels)
    size = float(size)
    ox, oy, oz = (float(c) for c in origin)
    vol = _as_volume(arr)
    if vol is None:
        return MeshData([], [], [], name=name)
    base, per_voxel = _resolve_colors(colors, arr.shape)

    sol = np.pad(vol, _PAD, mode='constant', constant_values=False)
    br, bg, bb, ba = base
    positions, normals, uvs, vcolors, indices = [], [], [], [], []

    for a in range(3):
        u, v = (a + 1) % 3, (a + 2) % 3
        # 只有 a==1（y 轴）时剩余两轴顺序 (z,x) 与 (u,v)=(z=2,x=0) 反序，需要转置
        need_t = [ax for ax in range(3) if ax != a] != [u, v]
        for sign in (1, -1):
            # 与 greedy_mesh_3d 同式的"实心 → 空气"交界掩码（每层一个 2D 网格）。
            face = sol & ~np.roll(sol, -sign, axis=a)
            for s in range(sol.shape[a]):
                layer = np.take(face, s, axis=a)
                if not layer.any():
                    continue
                if need_t:
                    layer = layer.T
                # 裁掉两端的 _PAD 空气边界，让真实体素区从 0 起算——
                # 否则 2 格 pad 边会破坏 2 的幂对齐，使大块无法合成一个 quad。
                layer = layer[_PAD:-_PAD, _PAD:-_PAD]
                rects = _binary_greedy_cover(layer)
                plane = s + (1 if sign > 0 else 0) - _PAD
                for (i0, j0, i1, j1) in rects:
                    # 裁掉 pad 后，层内索引就是体素坐标（0 起）；AO 采样仍要回 sol 坐标。
                    ou0, ou1 = i0, i1
                    ov0, ov1 = j0, j1
                    pts, aos = [], []
                    for du, dv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                        coords = [0.0, 0.0, 0.0]
                        coords[a] = plane
                        coords[u] = ou0 if du == 0 else ou1
                        coords[v] = ov0 if dv == 0 else ov1
                        pts.append((ox + coords[0] * size,
                                    oy + coords[1] * size,
                                    oz + coords[2] * size))
                        if ao:
                            aos.append(_vertex_ao(
                                sol, a, u, v, s, sign,
                                (i0 if du == 0 else i1 - 1) + _PAD,
                                (j0 if dv == 0 else j1 - 1) + _PAD,
                                -1 if du == 0 else 1,
                                -1 if dv == 0 else 1))
                        else:
                            aos.append(3)
                    if per_voxel is None:
                        cr, cg, cb, ca = br, bg, bb, ba
                    else:
                        cell = [0, 0, 0]
                        cell[a] = s - _PAD
                        cell[u] = ou0
                        cell[v] = ov0
                        cr, cg, cb, ca = per_voxel[cell[0], cell[1], cell[2]]
                    nrm = [0.0, 0.0, 0.0]
                    nrm[a] = float(sign)
                    first = len(positions) // 3
                    for k in range(4):
                        x, y, z = pts[k]
                        b = ao_brightness(aos[k]) if ao else 1.0
                        positions += [x, y, z]
                        normals += nrm
                        vcolors += [cr * b, cg * b, cb * b, ca]
                    uvs += [ou0, ov0, ou1, ov0, ou1, ov1, ou0, ov1]
                    flip = (aos[0] + aos[3] > aos[1] + aos[2]) if ao else False
                    if sign > 0:
                        tri = (1, 2, 3, 1, 3, 0) if flip else (0, 1, 2, 0, 2, 3)
                    else:
                        tri = (1, 3, 2, 1, 0, 3) if flip else (0, 2, 1, 0, 3, 2)
                    indices += [first + t for t in tri]

    return MeshData(positions, normals, uvs, vcolors, indices, name=name)


# ------------------------------------------------------------------ 统计 --
def naive_face_count(voxels):
    """完全不剔除时的面数：每个实心体 6 面。"""
    return 6 * int(np.count_nonzero(np.asarray(voxels)))


def culled_face_count(voxels):
    """**只剔除、不合并**时的面数（= 表面积，单位：体素面）。``merged_ratio`` 的分母。"""
    vol = _as_volume(voxels)
    if vol is None:
        return 0
    p = np.pad(vol, 1, mode='constant', constant_values=False)
    n = 0
    for a in range(3):
        for step in (-1, 1):
            # 每个「实心 → 空气」交界恰好从实心一侧被数到一次
            n += int(np.count_nonzero(p & ~np.roll(p, -step, axis=a)))
    return n


def mesh_stats(mesh, voxels=None):
    """网格统计：``{"quads", "triangles", "vertices", "merged_ratio"}``。

    ``merged_ratio = 1 - quads / culled_face_count(voxels)``（相对"剔除但不合并"）。
    ``MeshData`` 不携带源数组，所以不传 ``voxels`` 时 ``merged_ratio`` 为 ``None``
    —— 宁可空着，也不拿 ``naive_face_count``（不剔除）当分母去虚报一个更好看的数。
    """
    quads = len(mesh.indices) // 6 if mesh.indices else mesh.triangle_count // 2
    stats = {'quads': quads, 'triangles': mesh.triangle_count,
             'vertices': mesh.vertex_count, 'merged_ratio': None}
    if voxels is not None:
        culled = culled_face_count(voxels)
        if culled:
            stats['merged_ratio'] = 1.0 - quads / float(culled)
    return stats
