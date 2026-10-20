"""CDLOD 地形：四叉树选层 + 单张网格模板 + 顶点 morph 消缝。

解决什么
--------
``geometry.heightfield()`` 是**全分辨率静态网格**：1024² 的高度图就是 100 万顶点，
视距一拉远面数线性爆炸。CDLOD（Filip Strugar, 2010）的做法是：

- 把地形切成四叉树，**每层节点用同一张 ``grid x grid`` 网格模板**（大小不同而已）；
- 按"节点包围盒到相机的 3D 距离"决定这一层要不要继续细分；
- 相邻层在交界处**把细网格顶点插值到粗网格的格点上**（morph），
  于是不需要 skirts、也不需要缝合网格。

本模块只做**纯数学与网格数据**（无 GL、可单测）：
``TerrainLod`` 负责选层与采样，``build_node_mesh`` 产出可上传的 ``MeshData``，
``MORPH_GLSL`` 给出等价的顶点着色器写法（真正渲染时用它，而不是每帧重建网格）。

坐标系
------
与 ``geometry.heightfield`` 一致：行主序 ``hm[row][col]``，row 对应 +z，
地形以原点为中心铺开 ``size x size`` 米，y = ``y_base + hm * height_scale``。

许可
----
算法出自 CDLOD 论文（Strugar 2010）。**未取任何示例代码**（该仓许可记载两说，
见 docs/借鉴记账.md）；本实现按论文公式自写。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .geometry import MeshData

#: 顶点着色器里的 morph 片段（与 ``TerrainLod.morph_factor`` 同一口径）。
MORPH_GLSL = """
// uniforms: mat4 u_node_mvp; vec2 u_node_origin; float u_node_size;
//           float u_morph_k; sampler2D u_height; vec2 u_height_texel;
// v_morph_k 传下去给片元做调试可视化
float snap_even(float v, float spacing) {
    // 把坐标吸附到"偶数格点"（= 父层格点）
    float s = spacing * 2.0;
    return floor(v / s + 0.5) * s;
}
vec3 terrain_vertex(vec2 grid_xy) {
    vec2 uv = grid_xy / u_grid;                       // grid_xy ∈ [0, u_grid]
    vec2 local = uv * u_node_size;                    // 节点内局部坐标（米）
    vec2 world = u_node_origin + local;
    // morph：向父层格点插值，k=1 时完全落在粗网格上（消缝）
    vec2 snapped = vec2(snap_even(local.x, u_cell), snap_even(local.y, u_cell));
    vec2 morphed = mix(local, snapped, u_morph_k);
    vec2 wpos = u_node_origin + morphed;
    float h = texture(u_height, (wpos + 0.5 * u_world_size) / u_world_size).r;
    return vec3(wpos.x, h * u_height_scale, wpos.y);
}
"""


@dataclass(frozen=True)
class LodConfig:
    """CDLOD 参数。

    ``grid`` 是每个节点的四边形数（每边）—— 顶点数固定为 ``(grid+1)^2``，
    与节点大小无关，这正是 CDLOD"一张模板走天下"的前提。
    ``leaf_size`` 是最细一层的边长（米），``levels`` 是层数，
    根节点边长 = ``leaf_size * 2**(levels-1)``。
    ``leaf_range`` 是最细一层的可见半径，第 l 层的半径 = ``leaf_range * 2**l``。
    """

    grid: int = 16
    levels: int = 5
    leaf_size: float = 32.0
    leaf_range: float = 48.0
    morph_start: float = 0.7

    def __post_init__(self):
        if self.grid < 2 or self.grid % 2:
            # 偶数格是 morph 的前提：细层顶点要能落在父层格点上，
            # 奇数格的最远一条边吸不过去（会直接产生裂缝）。
            raise ValueError(f"grid 必须是 >=2 的偶数，收到 {self.grid}")
        if not 1 <= self.levels <= 12:
            raise ValueError(f"levels 必须在 1..12，收到 {self.levels}")
        if self.leaf_size <= 0 or self.leaf_range <= 0:
            raise ValueError("leaf_size / leaf_range 必须为正")
        if not 0.0 <= self.morph_start < 1.0:
            raise ValueError(f"morph_start 必须在 [0,1)，收到 {self.morph_start}")

    @property
    def root_size(self) -> float:
        return self.leaf_size * (2 ** (self.levels - 1))

    @property
    def cell_size(self) -> float:
        """最细一层的格距（米）。"""
        return self.leaf_size / self.grid

    def range_of(self, level: int) -> float:
        """第 ``level`` 层（0=最细）的可见半径。"""
        return self.leaf_range * (2 ** level)

    def size_of(self, level: int) -> float:
        return self.leaf_size * (2 ** level)


@dataclass
class LodNode:
    """选层结果：一个待渲染的节点。"""

    level: int
    x0: float
    z0: float
    size: float
    morph: float = 0.0

    @property
    def x1(self) -> float:
        return self.x0 + self.size

    @property
    def z1(self) -> float:
        return self.z0 + self.size

    @property
    def center(self) -> Tuple[float, float]:
        return (self.x0 + self.size * 0.5, self.z0 + self.size * 0.5)

    def contains(self, x: float, z: float) -> bool:
        return self.x0 <= x <= self.x1 and self.z0 <= z <= self.z1

    def distance_to(self, x: float, z: float) -> float:
        """点到节点包围盒（XZ）的距离；点在盒内为 0。"""
        dx = max(self.x0 - x, 0.0, x - self.x1)
        dz = max(self.z0 - z, 0.0, z - self.z1)
        return math.hypot(dx, dz)


class TerrainLod:
    """高度场 + 四叉树选层。

    典型用法（无窗口可跑）：

        t = TerrainLod(hm, size=1024.0, height_scale=180.0)
        nodes = t.select(camera=(0.0, 0.0), view_range=256.0)
        for n in nodes:
            md = t.build_node_mesh(n)        # 调试/CPU 端；真渲染走模板 + MORPH_GLSL
    """

    def __init__(self, heightmap: np.ndarray, size: float = 1024.0,
                 height_scale: float = 180.0, y_base: float = 0.0,
                 config: Optional[LodConfig] = None):
        hm = np.asarray(heightmap, dtype=np.float64)
        if hm.ndim != 2 or min(hm.shape) < 2:
            raise ValueError(f"heightmap 必须是 >=2x2 的二维数组，收到 {hm.shape}")
        self.hm = hm
        self.size = float(size)
        self.height_scale = float(height_scale)
        self.y_base = float(y_base)
        self.config = config or LodConfig()
        if self.config.root_size > self.size + 1e-9:
            raise ValueError(
                f"根节点边长 {self.config.root_size} 超过地形尺寸 {self.size}；"
                f"请减小 leaf_size 或 levels"
            )

    # ── 采样 ──
    def height_at(self, x: float, z: float) -> float:
        """世界坐标 → 地形高度（双线性，越界按边缘钳制）。"""
        return self._bilinear(x, z) * self.height_scale + self.y_base

    def _bilinear(self, x: float, z: float) -> float:
        rows, cols = self.hm.shape
        u = (x + self.size * 0.5) / self.size
        v = (z + self.size * 0.5) / self.size
        c = min(max(u, 0.0), 1.0) * (cols - 1)
        r = min(max(v, 0.0), 1.0) * (rows - 1)
        c0, r0 = int(c), int(r)
        c1, r1 = min(c0 + 1, cols - 1), min(r0 + 1, rows - 1)
        fc, fr = c - c0, r - r0
        return float(
            self.hm[r0, c0] * (1 - fc) * (1 - fr)
            + self.hm[r0, c1] * fc * (1 - fr)
            + self.hm[r1, c0] * (1 - fc) * fr
            + self.hm[r1, c1] * fc * fr
        )

    # ── 选层 ──
    def morph_factor(self, level: int, distance: float) -> float:
        """顶点 morph 系数 0..1。

        距离在 ``range * morph_start`` 以内为 0（用原网格），
        到 ``range`` 处为 1（完全落到父层格点）。这样节点被替换成更粗的一层时，
        交界处的顶点已经在同一位置上，不会跳变、也不会裂开。
        """
        rng = self.config.range_of(level)
        start = rng * self.config.morph_start
        if distance <= start:
            return 0.0
        if distance >= rng:
            return 1.0
        return (distance - start) / max(rng - start, 1e-9)

    def select(self, camera: Tuple[float, float],
               view_range: Optional[float] = None) -> List[LodNode]:
        """按相机位置选出要渲染的节点（自顶向下细分）。

        ``view_range`` 缺省取根节点边长（即整片地形）。
        """
        cx, cz = float(camera[0]), float(camera[1])
        cfg = self.config
        root = LodNode(level=cfg.levels - 1,
                       x0=-cfg.root_size * 0.5, z0=-cfg.root_size * 0.5,
                       size=cfg.root_size)
        limit = cfg.root_size if view_range is None else float(view_range)
        out: List[LodNode] = []

        stack: List[LodNode] = [root]
        while stack:
            node = stack.pop()
            dist = node.distance_to(cx, cz)
            if dist > limit:
                continue
            can_split = node.level > 0
            if can_split and dist < cfg.range_of(node.level - 1):
                half = node.size * 0.5
                for dz in (0.0, half):
                    for dx in (0.0, half):
                        stack.append(LodNode(node.level - 1,
                                             node.x0 + dx, node.z0 + dz, half))
                continue
            node.morph = self.morph_factor(node.level, dist)
            out.append(node)

        out.sort(key=lambda n: (-n.level, n.z0, n.x0))
        return out

    # ── 网格 ──
    def build_template(self) -> MeshData:
        """单位模板：``grid x grid`` 四边形、y=0，供 GPU 复用（每节点只改 uniform）。"""
        g = self.config.grid
        positions, uvs, indices = [], [], []
        for r in range(g + 1):
            for c in range(g + 1):
                positions += [c / g, 0.0, r / g]
                uvs += [c / g, r / g]
        for r in range(g):
            for c in range(g):
                a = r * (g + 1) + c
                b = a + 1
                d = a + (g + 1)
                e = d + 1
                indices += [a, d, b, b, d, e]
        return MeshData(positions, uvs=uvs, indices=indices, name='terrain_lod_template')

    def node_grid(self, node: LodNode,
                  morph: Optional[float] = None) -> Tuple[list, list, list]:
        """节点在**世界坐标**下的顶点/UV/索引（morph 已应用，CPU 端用）。"""
        g = self.config.grid
        k = node.morph if morph is None else float(morph)
        # 父层格距 = 本层格距的两倍；morph=1 时顶点吸附到父层格点
        cell = node.size / g
        positions, uvs, indices = [], [], []
        for r in range(g + 1):
            for c in range(g + 1):
                lx, lz = c * cell, r * cell
                if k > 0.0:
                    snap_x = round(lx / (2 * cell)) * (2 * cell)
                    snap_z = round(lz / (2 * cell)) * (2 * cell)
                    snap_x = min(max(snap_x, 0.0), node.size)
                    snap_z = min(max(snap_z, 0.0), node.size)
                    lx += (snap_x - lx) * k
                    lz += (snap_z - lz) * k
                wx, wz = node.x0 + lx, node.z0 + lz
                positions += [wx, self.height_at(wx, wz), wz]
                uvs += [(wx + self.size * 0.5) / self.size,
                        (wz + self.size * 0.5) / self.size]
        for r in range(g):
            for c in range(g):
                a = r * (g + 1) + c
                b = a + 1
                d = a + (g + 1)
                e = d + 1
                indices += [a, d, b, b, d, e]
        return positions, uvs, indices

    def build_node_mesh(self, node: LodNode,
                        morph: Optional[float] = None) -> MeshData:
        """节点的可上传网格（CPU 版；真渲染请用模板 + ``MORPH_GLSL``）。"""
        positions, uvs, indices = self.node_grid(node, morph=morph)
        md = MeshData(positions, uvs=uvs, indices=indices,
                      name=f'terrain_lod_L{node.level}')
        md.compute_normals()
        return md

    # ── 统计 ──
    def stats(self, nodes: Sequence[LodNode]) -> dict:
        """节点数/三角数/覆盖面积/最大层（帧预算与断言用）。"""
        g = self.config.grid
        tris = sum(g * g * 2 for _ in nodes)
        area = sum(n.size * n.size for n in nodes)
        levels = sorted({n.level for n in nodes})
        return {
            "nodes": len(nodes),
            "triangles": tris,
            "area": area,
            "max_level": max(levels) if levels else -1,
            "levels": levels,
        }

    def coverage_report(self, nodes: Sequence[LodNode],
                        view_range: Optional[float] = None,
                        bins: int = 64) -> dict:
        """覆盖检查：把视野正方形栅格化，统计每格被几个节点覆盖。

        正常结果应为``min_cov == max_cov == 1``（无缝、无重叠）。
        """
        limit = self.config.root_size if view_range is None else float(view_range)
        cx, cz = 0.0, 0.0
        if nodes:
            # 以节点集合的包围盒为准，检查它是否被恰好铺满
            x0 = min(n.x0 for n in nodes)
            z0 = min(n.z0 for n in nodes)
            x1 = max(n.x1 for n in nodes)
            z1 = max(n.z1 for n in nodes)
        else:
            return {"min": 0, "max": 0, "holes": bins * bins}
        cov = np.zeros((bins, bins), dtype=np.int32)
        for n in nodes:
            c0 = int((n.x0 - x0) / (x1 - x0) * bins)
            c1 = int(math.ceil((n.x1 - x0) / (x1 - x0) * bins))
            r0 = int((n.z0 - z0) / (z1 - z0) * bins)
            r1 = int(math.ceil((n.z1 - z0) / (z1 - z0) * bins))
            cov[r0:r1, c0:c1] += 1
        return {"min": int(cov.min()), "max": int(cov.max()),
                "holes": int((cov == 0).sum()), "bins": bins}
