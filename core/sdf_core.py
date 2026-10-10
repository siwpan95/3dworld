# -*- coding: utf-8 -*-
"""SDF/体素共同核 ``sdf``（实现 ``core.contracts.SdfVoxelCore``）。

P0-1 新建（封装）：统一「体素世界读写」与「光线步进着色器程序取用」两件事。
体素以**稀疏 dict** 存储（``(x, y, z) -> 方块 id``，0 视为空气，不入表），
``world_mesh`` 对面剔除后输出 ``(pos, shade)``，与 demo 的顶点色网格同构。

> 依赖：仅 Python 标准库（``math``）+ ``cordis``。headless 安全。
"""
from __future__ import annotations

from core._cordis import Service

AIR = 0

# 方块 id -> 面亮度基准色（顶/侧/底再乘系数）
BLOCK_COLORS = {
    1: (0.55, 0.45, 0.32),   # 土
    2: (0.30, 0.55, 0.26),   # 草
    3: (0.50, 0.50, 0.52),   # 石
    4: (0.78, 0.76, 0.70),   # 沙
}

_FACES = {
    # 方向: (偏移, 四角（逆时针）, 亮度)
    '+y': ((0, 1, 0), [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)], 1.00),
    '-y': ((0, -1, 0), [(0, 0, 1), (0, 0, 0), (1, 0, 0), (1, 0, 1)], 0.45),
    '+x': ((1, 0, 0), [(1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)], 0.78),
    '-x': ((-1, 0, 0), [(0, 0, 1), (0, 0, 0), (0, 1, 0), (0, 1, 1)], 0.78),
    '+z': ((0, 0, 1), [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], 0.86),
    '-z': ((0, 0, -1), [(1, 0, 0), (0, 0, 0), (0, 1, 0), (1, 1, 0)], 0.86),
}


class SdfVoxelService(Service):
    """光线步进墙体、体素方块。"""

    def __init__(self, ctx, config):
        super().__init__(ctx, "sdf")
        cfg = config or {}
        self._vox = {}                       # (x,y,z) -> id（0/空气不入表）
        self._programs = {}                  # 光线步进着色器程序句柄（记账）
        self._acquired = 0
        seed = cfg.get('seed')
        if seed is not None:
            self._gen_flat(int(seed))

    # ---- 体素读写 ----
    def voxel_get(self, x, y, z) -> int:
        return self._vox.get((int(x), int(y), int(z)), AIR)

    def voxel_set(self, x, y, z, v):
        k = (int(x), int(y), int(z))
        v = int(v)
        if v == AIR:
            self._vox.pop(k, None)
        else:
            self._vox[k] = v

    def block_at(self, x, y, z) -> int:
        return self.voxel_get(x, y, z)

    # ---- 网格导出（面剔除）----
    def world_mesh(self):
        """把体素世界导出为 ``(pos, shade)`` 顶点数组（仅暴露的面）。

        每个非空气方块检查 6 邻居，仅当邻居为空气时输出该面（2 三角形 = 6 顶点）。
        """
        pos, shade = [], []
        for (x, y, z), bid in self._vox.items():
            base = BLOCK_COLORS.get(bid, (0.7, 0.7, 0.7))
            for (dx, dy, dz), corners, lit in _FACES.values():
                if (x + dx, y + dy, z + dz) in self._vox:
                    continue
                a, b, c, d = corners
                for (cx, cy, cz) in (a, b, c, a, c, d):     # quad -> 2 三角形（6 顶点）
                    pos.extend((x + cx, y + cy, z + cz))
                    shade.extend((base[0] * lit, base[1] * lit, base[2] * lit))
        return pos, shade

    def voxel_count(self) -> int:
        return len(self._vox)

    # ---- 着色器程序（记账；真实 GL 由渲染共同核执行）----
    def raymarch_program(self, name):
        """取光线步进着色器程序（headless：仅返回记账句柄，可能为 None）。"""
        return self._programs.get(name)

    def register_program(self, name):
        self._acquired += 1
        self._programs[name] = self._acquired
        return self._programs[name]

    # ---- 内部：一片平地（便于 demo/自测起步）----
    def _gen_flat(self, seed, size=8, top=3):
        for x in range(-size, size):
            for z in range(-size, size):
                for y in range(0, top):
                    self._vox[(x, y, z)] = 2 if y == top - 1 else 1

    def __cordis_init__(self):
        return None


__all__ = ['SdfVoxelService', 'AIR', 'BLOCK_COLORS']
