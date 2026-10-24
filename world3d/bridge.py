# -*- coding: utf-8 -*-
"""3dworld 的 bridge 友好接口（**新增文件，不改既有模块**）。

给「超级引擎」组合仓（superengine）用的稳定入口：把表现层能力按**契约形状**暴露，
尤其是**消费 worldspec / 高程**的那一侧 —— 这正是三仓流水线此前断掉的一腿
（worldbuilder 早已产出 worldspec，an-engine 早已消费，唯独表现层没接）。

设计约定
--------
* 纯适配层，不含算法，只转发到 `world3d.engine.terrain_clipmap` 等既有模块；
* `SUPPORTED_CONTRACTS` 声明能满足的契约与**兼容区间**；
* **headless / 零 GL**：本文件只做几何计算，不建 GL 上下文、不开窗口，
  因此可在 CI 里直接断言（与 3dworld engine 层既有纪律一致）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Sequence, Tuple

from .engine.terrain_clipmap import (ClipmapConfig, build_clipmap,
                                     build_ring_mesh, clipmap_stats,
                                     displace)

__all__ = [
    "BRIDGE_VERSION",
    "SUPPORTED_CONTRACTS",
    "clipmap_config",
    "terrain_rings",
    "terrain_mesh",
    "terrain_from_heightfield",
]

BRIDGE_VERSION = "1.0.0"

#: 本仓声明能满足的契约及兼容区间 —— 由组合仓在装配期校验
SUPPORTED_CONTRACTS = {
    "terrain_sink": ">=1.0,<2.0",
    "render_sink": ">=1.0,<2.0",
}


def clipmap_config(levels: int = 5, grid: int = 32,
                   base_cell: float = 1.0) -> ClipmapConfig:
    """构造几何 clipmap 参数（转发 `ClipmapConfig`）。"""
    return ClipmapConfig(levels=levels, grid=grid, base_cell=base_cell)


def terrain_rings(camera: Tuple[float, float] = (0.0, 0.0),
                  config: ClipmapConfig = None) -> List[Any]:
    """按相机位置生成嵌套 ring 列表（自内向外）。"""
    return build_clipmap(tuple(camera), config or clipmap_config())


def terrain_mesh(height_fn: Callable[[float, float], float],
                 camera: Tuple[float, float] = (0.0, 0.0),
                 config: ClipmapConfig = None,
                 world_size: float = None) -> Dict[str, Any]:
    """把「高度函数」变成 clipmap 网格（**headless**）。

    `height_fn(world_x, world_z) -> height`，通常由 worldbuilder 侧的高程采样器提供
    （见 worldbuilder bridge 的 `make_height_sampler`）——这就是 worldbuilder → 3dworld 的接缝。

    返回 dict：ring 列表、每个 ring 的 MeshData、顶点总数与高度统计。
    顶点数 / 高度非零可作为「这一腿真的通了」的 headless 判据。
    """
    cfg = config or clipmap_config()
    rings = build_clipmap(tuple(camera), cfg)
    meshes = [build_ring_mesh(r, height_fn, world_size=world_size) for r in rings]
    verts = sum(len(m.positions) // 3 for m in meshes)
    # 高度统计：证明 height_fn 真的被采样了（全零说明上游没接通）
    samples = [displace(r, height_fn) for r in rings]
    allh = [float(s.min()) for s in samples] + [float(s.max()) for s in samples]
    return {
        "config": cfg,
        "rings": rings,
        "meshes": meshes,
        "vertex_count": verts,
        "height_min": min(allh) if allh else 0.0,
        "height_max": max(allh) if allh else 0.0,
        # clipmap_stats(rings, cells_per_ring)：不传第二参时它按 rings[0] 反推
        "stats": clipmap_stats(rings) if rings else {},
    }


def terrain_from_heightfield(hm, tile_m: float = 1.0,
                             camera: Tuple[float, float] = (0.0, 0.0),
                             config: ClipmapConfig = None,
                             height_scale: float = 1.0) -> Dict[str, Any]:
    """直接吃**高程阵列**（而非函数），内部包成采样器后走 `terrain_mesh`。

    方便在没有 worldbuilder 侧采样器时直接验证；真实装配推荐用 `terrain_mesh`
    接 worldbuilder 的采样器，保持「谁产出谁解释坐标」。
    """
    import numpy as np

    hm = np.asarray(hm, dtype=float)
    rows, cols = hm.shape
    step = float(tile_m) if tile_m else 1.0

    def sample(x: float, z: float) -> float:
        c = int(min(max(round(x / step), 0), cols - 1))
        r = int(min(max(round(z / step), 0), rows - 1))
        return float(hm[r, c] * height_scale)

    return terrain_mesh(sample, camera=camera, config=config)
