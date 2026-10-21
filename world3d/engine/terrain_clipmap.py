# -*- coding: utf-8 -*-
"""几何 clipmap（Geometry Clipmaps，Losasso & Hoppe 2004）—— 纯数学、无 GL。

这是把 `docs/借鉴记账.md` §二里「几何 clipmap」真正落地的版本：
算法取自论文（Geometry Clipmaps: Terrain Rendering Using Nested Regular Grids,
SIGGRAPH 2004），**未搬任何示例代码**，本实现按论文公式自写。

## 它解决什么

传统高度场是一整张固定分辨率的网格；视距一拉远，顶点数线性爆炸，近处又不够细。
clipmap 的核心洞见是：**地形可以用一组「同心、等分辨率、逐层变粗」的方块网格
（论文叫 clip level / 这里叫 ring）来近似**——最内一层（level 0）分辨率最高、
紧贴相机，往外每一层的「格距」翻倍、世界跨度翻倍，于是用一份固定大小的顶点模板
（每个 ring 都是 ``grid × grid`` 个四边形，与层大小无关）就能铺到很远的地形。

真渲染时，ring 的网格是**刚性**的（顶点不随相机平滑移动，只按最细格距吸附），
靠滚动一张高度纹理来"更新"地形——这块（GPU 端纹理更新、裁剪、缝合）属于 GL 层，
本模块**不碰**，只产出：

* 嵌套 ring 的网格生成（每层原点、格距、跨度、以及外层的「方形洞」（annulus））；
* 顶点位移函数 ``displace``：给定高度采样函数，把每个 lattice 顶点抬到正确高度
  （纯数学，CPU 端可单测；真渲染时这一行换成顶点着色器里的纹理采样）；
* ``build_ring_mesh`` 把某层 ring 的 annulus 直接变成可上传的 :class:`MeshData`。

许可
----
论文算法（公开知识，SIGGRAPH 2004）。与 CDLOD（``terrain_lod``）同源思路但不同
结构：CDLOD 按距离四叉树细分、相邻层 morph 消缝；clipmap 用「同心刚性环 + 滚动数据」。
两者都只做纯数学与网格数据，依赖 :class:`world3d.engine.geometry.MeshData`，不引 GL。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from .geometry import MeshData

__all__ = [
    'ClipmapConfig', 'ClipRing', 'build_clipmap', 'ring_cells', 'displace',
    'build_ring_mesh', 'clipmap_stats',
]

#: 高度采样函数：``(world_x, world_z) -> float``（世界高度）。
HeightFn = Callable[[float, float], float]


@dataclass(frozen=True)
class ClipmapConfig:
    """几何 clipmap 参数。

    ``grid`` 是每个 ring 的**顶点数 / 每边**（含两端），四边形数 / 每边 = ``grid - 1``。
    所有 ring 共用这同一份顶点模板——这正是 clipmap「一份网格走天下」的前提
    （与 CDLOD 的 ``LodConfig.grid`` 同构）。

    ``levels`` 是嵌套层数（最内 level 0 最细）。``base_cell`` 是最细层的格距（米），
    第 ``l`` 层的格距 = ``base_cell * 2**l``，世界跨度 = ``(grid-1) * cell_of(l)``。

    相机中心的吸附：所有层都按**最细格距** ``base_cell`` 吸附，保证外层 ring 的洞
    能精准对齐内层 ring 的外缘（否则会裂开/重叠）。
    """

    levels: int = 5
    grid: int = 32
    base_cell: float = 1.0

    def __post_init__(self):
        if self.levels < 1:
            raise ValueError(f'levels 必须 >= 1，收到 {self.levels}')
        if self.grid < 2:
            raise ValueError(f'grid 必须 >= 2，收到 {self.grid}')
        if self.base_cell <= 0:
            raise ValueError(f'base_cell 必须为正，收到 {self.base_cell}')

    @property
    def cells(self) -> int:
        """每个 ring 每边的四边形数（= 顶点数 - 1）。"""
        return self.grid - 1

    def cell_of(self, level: int) -> float:
        """第 ``level`` 层的格距（米）；逐层翻倍。"""
        return self.base_cell * (2 ** level)

    def size_of(self, level: int) -> float:
        """第 ``level`` 层的世界跨度（米）= 格距 × 四边形数。"""
        return self.cell_of(level) * self.cells

    def snap(self, x: float) -> float:
        """把世界坐标吸附到最细格距的格点（所有层共用，保证洞对齐）。"""
        return round(x / self.base_cell) * self.base_cell


@dataclass
class ClipRing:
    """一个 clipmap ring（嵌套方块网格的一层）。

    内层（level 0）是一整张方网格；外层（level > 0）中心挖掉一个方形洞
    （annulus）—— 那个洞正是最内若干层已经覆盖的区域。
    """

    level: int
    center: Tuple[float, float]
    cell: float
    size: float
    hole_half: float

    @property
    def x0(self) -> float:
        return self.center[0] - self.size * 0.5

    @property
    def z0(self) -> float:
        return self.center[1] - self.size * 0.5

    @property
    def x1(self) -> float:
        return self.center[0] + self.size * 0.5

    @property
    def z1(self) -> float:
        return self.center[1] + self.size * 0.5

    def lattice(self) -> np.ndarray:
        """``(grid+1, grid+1, 2)`` 的 world (x, z) 顶点阵列（不含位移 / 不含洞过滤）。"""
        cfg_grid = int(round(self.size / self.cell)) + 1
        xs = self.x0 + self.cell * np.arange(cfg_grid)
        zs = self.z0 + self.cell * np.arange(cfg_grid)
        return np.stack(np.meshgrid(xs, zs, indexing='xy'), axis=-1)

    def in_hole(self, wx: float, wz: float) -> bool:
        """某世界点是否落在「外层洞」里（该区域由更细的层负责）。"""
        if self.hole_half <= 0.0:
            return False
        return (abs(wx - self.center[0]) < self.hole_half
                and abs(wz - self.center[1]) < self.hole_half)

    def contains(self, wx: float, wz: float) -> bool:
        return self.x0 <= wx <= self.x1 and self.z0 <= wz <= self.z1


def build_clipmap(camera: Tuple[float, float],
                  config: ClipmapConfig) -> List[ClipRing]:
    """按相机位置生成嵌套 ring 列表（自内向外，level 0 最细）。

    每个 ring 的中心都吸附到最细格距，因此网格是**刚性**的：相机在「一个最细格距」
    内移动时，ring 的原点/格点完全不变，只有高度数据（``displace`` 的采样结果）滚动。
    这是几何 clipmap 相对「自由漂浮 LOD」的关键不变式，由 :func:`clipmap_stats` /
    单测钉住。
    """
    cx, cz = float(camera[0]), float(camera[1])
    scx = config.snap(cx)
    scz = config.snap(cz)
    rings: List[ClipRing] = []
    for l in range(config.levels):
        cell = config.cell_of(l)
        size = config.size_of(l)
        # 外层挖洞：洞正好是「内一层整张网格的跨度」（半幅 = 内一层 size/2）。
        hole_half = 0.0 if l == 0 else config.size_of(l - 1) * 0.5
        rings.append(ClipRing(level=l, center=(scx, scz), cell=cell,
                              size=size, hole_half=hole_half))
    return rings


def ring_cells(ring: ClipRing) -> List[Tuple[int, int]]:
    """返回该 ring 要绘制的网格单元 ``(r, c)``（0-based，行/列）。

    level 0 发射**全部** ``cells × cells`` 个单元；外层只发射 cell 中心落在洞
    **之外**的单元——于是自然形成 annulus（外层的中心方洞 = 内层覆盖范围）。
    返回的单元集合与 :func:`build_ring_mesh` 用的索引一致。
    """
    cells = int(round(ring.size / ring.cell))
    out: List[Tuple[int, int]] = []
    for r in range(cells):
        for c in range(cells):
            wx = ring.x0 + (c + 0.5) * ring.cell
            wz = ring.z0 + (r + 0.5) * ring.cell
            if not ring.in_hole(wx, wz):
                out.append((r, c))
    return out


def displace(ring: ClipRing, height_fn: HeightFn) -> np.ndarray:
    """对 ring 的全部 lattice 顶点采样高度 → ``(grid+1, grid+1)`` 高度阵列。

    纯数学：高度由 ``height_fn(world_x, world_z)`` 给出（真渲染时这一行被顶点着色器
    里的纹理采样取代）。这里逐顶点调用，结果可直接喂 ``build_ring_mesh``。

    兼容两类高度函数：能直接对 numpy 数组广播的（如
    ``lambda x, z: x + z``），以及只接受标量的（自动用 ``np.vectorize`` 包一层）。
    """
    lat = ring.lattice()
    try:
        out = height_fn(lat[..., 0], lat[..., 1])
        if isinstance(out, np.ndarray) and out.shape == lat.shape[:2]:
            return out.astype(float)
    except Exception:  # noqa: BLE001  (标量函数喂数组会抛，落入 vectorize 分支)
        pass
    f = np.vectorize(height_fn)
    return f(lat[..., 0], lat[..., 1]).astype(float)


def build_ring_mesh(ring: ClipRing, height_fn: HeightFn,
                   world_size: Optional[float] = None,
                   name: Optional[str] = None) -> MeshData:
    """把单个 ring（annulus）变成可上传的 :class:`MeshData`。

    * 顶点 = ring 全部 lattice 点（含洞内被丢弃的点——那些由内层负责，留空无害，
      这正是 clipmap「刚性网格 + 洞」的数据模型）；
    * 索引 = :func:`ring_cells` 选中的 annulus 单元（绕序与 ``terrain_lod`` 一致：
      ``a, d, b / b, d, e``，从上方看逆时针）；
    * uv = 顶点世界坐标相对「整片 clipmap 最大跨度」的归一化，便于材质平铺。
    """
    grid = int(round(ring.size / ring.cell)) + 1
    cell = ring.cell
    lat = ring.lattice()
    hs = displace(ring, height_fn)
    if world_size is None:
        world_size = ring.size
    positions, uvs, indices = [], [], []
    for r in range(grid):
        for c in range(grid):
            wx, wz = lat[r, c, 0], lat[r, c, 1]
            positions += [wx, float(hs[r, c]), wz]
            uvs += [(wx + world_size * 0.5) / world_size,
                    (wz + world_size * 0.5) / world_size]
    for (r, c) in ring_cells(ring):
        a = r * grid + c
        b = a + 1
        d = a + grid
        e = d + 1
        indices += [a, d, b, b, d, e]
    nm = name or f'clipmap_L{ring.level}'
    md = MeshData(positions, uvs=uvs, indices=indices, name=nm)
    md.compute_normals()
    return md


def clipmap_stats(rings: Sequence[ClipRing],
                  cells_per_ring: Optional[int] = None) -> dict:
    """clipmap 统计：层数 / 总顶点 / 总三角 / 各层跨度（帧预算与断言用）。

    ``cells_per_ring`` 不传时按 rings[0] 的世界跨度反推每边四边形数。
    """
    if not rings:
        return {'levels': 0, 'vertices': 0, 'triangles': 0,
                'sizes': [], 'cells_per_ring': 0}
    cells = cells_per_ring if cells_per_ring is not None else \
        int(round(rings[0].size / rings[0].cell))
    tris = sum(cells * cells * 2 for r in rings)   # 全量单元（含洞内未绘制点）
    verts = sum((cells + 1) * (cells + 1) for _ in rings)
    return {
        'levels': len(rings),
        'vertices': verts,
        'triangles': tris,
        'sizes': [round(r.size, 6) for r in rings],
        'cells_per_ring': cells,
    }
