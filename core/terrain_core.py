# -*- coding: utf-8 -*-
"""地形共同核 ``terrain``（实现 ``core.contracts.TerrainCore``）。

自 ``games/godhand/kernel`` 上提的**跨内核共享实现**（P0-1 去重）：
可编辑高度场（抬山 + / 挖海 −）。headless 安全：纯数值网格，无 GL / 无随机依赖；
``rebuild_mesh`` 仅生成顶点数组，供 demo 渲染与 GL 记账使用。

> 依赖：仅 Python 标准库（``math``）+ ``cordis``（vendored）。
> 默认格数/格距/海平面/掩埋线与原 godhand 常量一致，保证行为不变。
"""
from __future__ import annotations

import math

from core._cordis import Service

# 默认参数（与原 godhand 常量 GRID_T / CELL_T / SEA / BURY 一致，内核可经 config 覆盖）
DEFAULT_GRID = 24          # 高度场格数（世界 48×48）
DEFAULT_CELL = 2.0
DEFAULT_SEA = 0.0          # 海平面
DEFAULT_BURY = 4.0         # 高于此高度 = 被掩埋（压垮）


class TerrainService(Service):
    """可编辑高度场（实现 TerrainCore 契约：height_at / color_at / regenerate /
    edit_height / rebuild_mesh / ground_height）。

    headless 安全：纯数值网格，无 GL / 无随机依赖；``rebuild_mesh`` 仅生成顶点数组，
    供 demo 渲染与 GL 记账使用。
    """

    def __init__(self, ctx, config):
        super().__init__(ctx, "terrain")
        cfg = config or {}
        self.g = int(cfg.get('grid', DEFAULT_GRID))
        self.cell = float(cfg.get('cell', DEFAULT_CELL))
        self.half = self.g * self.cell / 2.0
        self.sea = float(cfg.get('sea', DEFAULT_SEA))
        self.bury = float(cfg.get('bury', DEFAULT_BURY))
        # 确定性初始起伏（无随机依赖，便于离线复现）
        self.h = [[0.0] * self.g for _ in range(self.g)]
        self.regenerate(seed=0)

    # -- TerrainCore 契约 --
    def height_at(self, x: float, z: float, seed: int = None) -> float:
        i = int((x + self.half) / self.cell)
        j = int((z + self.half) / self.cell)
        i = max(0, min(self.g - 1, i))
        j = max(0, min(self.g - 1, j))
        return self.h[i][j]

    def color_at(self, h, slope=0.0, lambert=1.0, jitter=0.0):
        if h < self.sea:
            return (0.20, 0.45, 0.70)                 # 水下
        if h > self.bury:
            return (0.55, 0.50, 0.45)                 # 高山裸岩
        t = (h - self.sea) / max(0.001, self.bury - self.sea)
        return (0.20 + 0.25 * t, 0.45 + 0.25 * t, 0.16 + 0.10 * t)  # 草地梯度

    def regenerate(self, seed: int = 0) -> None:
        """用确定性相位重建整片地形（seed 改变起伏，但不引入随机）。"""
        for i in range(self.g):
            for j in range(self.g):
                self.h[i][j] = 1.0 + 0.8 * math.sin(i * 0.6 + seed * 0.13) \
                                     * math.cos(j * 0.5 - seed * 0.07)
        # 边界略低，便于「挖海」形成环水
        for i in range(self.g):
            for j in range(self.g):
                edge = min(i, j, self.g - 1 - i, self.g - 1 - j)
                if edge == 0:
                    self.h[i][j] -= 1.5

    def edit_height(self, x: float, z: float, delta: float) -> None:
        """神之手命门：在 (x, z) 处**增量修改**高度（抬山 + / 挖海 −）。"""
        i = int((x + self.half) / self.cell)
        j = int((z + self.half) / self.cell)
        if 0 <= i < self.g and 0 <= j < self.g:
            self.h[i][j] = max(-6.0, min(8.0, self.h[i][j] + delta))

    def ground_height(self, x: float, z: float, from_y: float = 100.0) -> float:
        return self.height_at(x, z)

    def rebuild_mesh(self, *args, **kw):
        """把当前高度场导出为地表网格（pos, shade），按高度上色。"""
        pos, shade = [], []
        g, cell, half = self.g, self.cell, self.half
        for i in range(g - 1):
            for j in range(g - 1):
                x0 = -half + i * cell
                z0 = -half + j * cell
                x1, z1 = x0 + cell, z0 + cell
                a = (x0, self.h[i][j], z0)
                b = (x1, self.h[i + 1][j], z0)
                c = (x1, self.h[i + 1][j + 1], z1)
                d = (x0, self.h[i][j + 1], z1)
                for p in (a, b, c, a, c, d):
                    pos.extend(p)
                    col = self.color_at(p[1])
                    shade.extend(col)
        return pos, shade

    def __cordis_init__(self):
        return None


__all__ = ['TerrainService', 'DEFAULT_GRID', 'DEFAULT_CELL', 'DEFAULT_SEA', 'DEFAULT_BURY']
