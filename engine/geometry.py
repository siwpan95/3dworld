# -*- coding: utf-8 -*-
"""程序化几何：CPU 端生成顶点数据（纯数据，无 GL）。

产物是 :class:`MeshData`——一份与渲染 API 无关的中间表示，之后由
``engine/mesh.py`` 上传成 GPU 顶点列表，或被 glTF 加载器直接填充。

自带法线：光照（尤其 PBR）没有法线就没有形状；这里用**面积加权**的平均法线
（按三角形叉积的原始长度加权），比"简单相加再归一化"在方块倒角、球极点
处更稳。切线随 UV 生成，供法线贴图使用。
"""
import math

from engine.frustum import AABB
from engine.mathkit import mat4_mul, mat4_inverse, normal_matrix

__all__ = [
    'MeshData', 'box', 'cube', 'sphere', 'plane', 'cylinder', 'torus',
    'ground_grid', 'fullscreen_quad', 'merge_meshes',
    'lathe', 'torus_knot', 'tube',
]


class MeshData:
    """与 API 无关的网格数据。

    Attributes:
        positions: 扁平 [x,y,z, ...]
        normals:   扁平 [nx,ny,nz, ...]（可为空，``compute_normals`` 补）
        uvs:       扁平 [u,v, ...]（可为空）
        colors:    扁平 [r,g,b,a, ...]（可为空）
        indices:   顶点索引（可为空 = 非索引三角形汤）
        tangents:  扁平 [tx,ty,tz, ...]（可为空，``compute_tangents`` 补）
    """

    __slots__ = ('positions', 'normals', 'uvs', 'colors', 'indices', 'tangents',
                 'name')

    def __init__(self, positions, normals=None, uvs=None, colors=None,
                 indices=None, tangents=None, name=''):
        self.positions = list(positions)
        self.normals = list(normals) if normals else []
        self.uvs = list(uvs) if uvs else []
        self.colors = list(colors) if colors else []
        self.indices = list(indices) if indices else []
        self.tangents = list(tangents) if tangents else []
        self.name = name

    # ---- 度量 ----
    @property
    def vertex_count(self):
        return len(self.positions) // 3

    @property
    def triangle_count(self):
        return (len(self.indices) if self.indices else len(self.positions)) // 9

    def aabb(self):
        pts = [(self.positions[i], self.positions[i + 1], self.positions[i + 2])
               for i in range(0, len(self.positions), 3)]
        return AABB.from_points(pts)

    def bounding_sphere(self):
        """包围球（中心 = AABB 中心，半径 = 最远顶点距离）。

        注意不是"AABB 外接球"：那个球总是偏大，长条网格会白剔除不掉。
        """
        b = self.aabb()
        c = b.center
        r = 0.0
        for i in range(0, len(self.positions), 3):
            dx = self.positions[i] - c[0]
            dy = self.positions[i + 1] - c[1]
            dz = self.positions[i + 2] - c[2]
            r = max(r, math.sqrt(dx * dx + dy * dy + dz * dz))
        return c, r

    # ---- 派生属性 ----
    def compute_normals(self, force=False):
        """按三角形面积加权求平均法线（原地修改并返回 self）。"""
        if self.normals and not force:
            return self
        n = self.vertex_count
        acc = [0.0] * (n * 3)
        idx = self.indices if self.indices else list(range(n))
        for t in range(0, len(idx) - 2, 3):
            ia, ib, ic = idx[t] * 3, idx[t + 1] * 3, idx[t + 2] * 3
            ax, ay, az = self.positions[ia], self.positions[ia + 1], self.positions[ia + 2]
            bx, by, bz = self.positions[ib], self.positions[ib + 1], self.positions[ib + 2]
            cx, cy, cz = self.positions[ic], self.positions[ic + 1], self.positions[ic + 2]
            e1x, e1y, e1z = bx - ax, by - ay, bz - az
            e2x, e2y, e2z = cx - ax, cy - ay, cz - az
            # 不归一化：叉积模长 = 2 * 三角形面积，天然做了面积加权
            nx = e1y * e2z - e1z * e2y
            ny = e1z * e2x - e1x * e2z
            nz = e1x * e2y - e1y * e2x
            for i in (ia, ib, ic):
                acc[i] += nx
                acc[i + 1] += ny
                acc[i + 2] += nz
        out = [0.0] * (n * 3)
        for i in range(0, n * 3, 3):
            l = math.sqrt(acc[i] ** 2 + acc[i + 1] ** 2 + acc[i + 2] ** 2)
            if l > 1e-12:
                out[i], out[i + 1], out[i + 2] = acc[i] / l, acc[i + 1] / l, acc[i + 2] / l
            else:
                out[i], out[i + 1], out[i + 2] = 0.0, 1.0, 0.0
        self.normals = out
        return self

    def compute_tangents(self, force=False):
        """按 UV 求切线（Lengyel 法，用于法线贴图）。需要 normals 与 uvs。"""
        if self.tangents and not force:
            return self
        if not self.normals:
            self.compute_normals()
        if not self.uvs:
            return self
        n = self.vertex_count
        tan = [0.0] * (n * 3)
        idx = self.indices if self.indices else list(range(n))
        for t in range(0, len(idx) - 2, 3):
            i0, i1, i2 = idx[t], idx[t + 1], idx[t + 2]
            p = [self.positions[i0 * 3:i0 * 3 + 3],
                 self.positions[i1 * 3:i1 * 3 + 3],
                 self.positions[i2 * 3:i2 * 3 + 3]]
            uv = [self.uvs[i0 * 2:i0 * 2 + 2],
                  self.uvs[i1 * 2:i1 * 2 + 2],
                  self.uvs[i2 * 2:i2 * 2 + 2]]
            e1 = [p[1][k] - p[0][k] for k in range(3)]
            e2 = [p[2][k] - p[0][k] for k in range(3)]
            du1, dv1 = uv[1][0] - uv[0][0], uv[1][1] - uv[0][1]
            du2, dv2 = uv[2][0] - uv[0][0], uv[2][1] - uv[0][1]
            det = du1 * dv2 - du2 * dv1
            if abs(det) < 1e-12:
                continue
            r = 1.0 / det
            tx = (e1[0] * dv2 - e2[0] * dv1) * r
            ty = (e1[1] * dv2 - e2[1] * dv1) * r
            tz = (e1[2] * dv2 - e2[2] * dv1) * r
            for i in (i0, i1, i2):
                tan[i * 3] += tx
                tan[i * 3 + 1] += ty
                tan[i * 3 + 2] += tz
        out = [0.0] * (n * 3)
        for i in range(n):
            # Gram-Schmidt：把切线正交化到法线平面上
            nx, ny, nz = self.normals[i * 3:i * 3 + 3]
            tx, ty, tz = tan[i * 3], tan[i * 3 + 1], tan[i * 3 + 2]
            d = nx * tx + ny * ty + nz * tz
            tx, ty, tz = tx - nx * d, ty - ny * d, tz - nz * d
            l = math.sqrt(tx * tx + ty * ty + tz * tz)
            if l > 1e-12:
                out[i * 3:i * 3 + 3] = [tx / l, ty / l, tz / l]
            else:
                out[i * 3:i * 3 + 3] = [1.0, 0.0, 0.0]
        self.tangents = out
        return self

    # ---- 变换 ----
    def transform(self, matrix):
        """原地施加 4x4 列主序变换（位置 + 法线走逆转置）。"""
        m = matrix
        nm = normal_matrix(m)
        for i in range(0, len(self.positions), 3):
            x, y, z = self.positions[i], self.positions[i + 1], self.positions[i + 2]
            w = m[3] * x + m[7] * y + m[11] * z + m[15]
            self.positions[i] = (m[0] * x + m[4] * y + m[8] * z + m[12]) / (w or 1.0)
            self.positions[i + 1] = (m[1] * x + m[5] * y + m[9] * z + m[13]) / (w or 1.0)
            self.positions[i + 2] = (m[2] * x + m[6] * y + m[10] * z + m[14]) / (w or 1.0)
        for i in range(0, len(self.normals), 3):
            x, y, z = self.normals[i], self.normals[i + 1], self.normals[i + 2]
            nx = nm[0] * x + nm[3] * y + nm[6] * z
            ny = nm[1] * x + nm[4] * y + nm[7] * z
            nz = nm[2] * x + nm[5] * y + nm[8] * z
            l = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            self.normals[i], self.normals[i + 1], self.normals[i + 2] = nx / l, ny / l, nz / l
        return self

    def merge(self, other):
        """原地合并另一个网格（索引网格会先展开成非索引，避免索引重编号）。"""
        a = self.to_non_indexed() if self.indices else self
        b = other.to_non_indexed() if other.indices else other
        a.positions += b.positions
        a.normals = (a.normals + b.normals) if (a.normals and b.normals) else []
        a.uvs = (a.uvs + b.uvs) if (a.uvs and b.uvs) else []
        a.colors = (a.colors + b.colors) if (a.colors and b.colors) else []
        a.tangents = (a.tangents + b.tangents) if (a.tangents and b.tangents) else []
        self.positions, self.normals, self.uvs = a.positions, a.normals, a.uvs
        self.colors, self.tangents, self.indices = a.colors, a.tangents, []
        return self

    def to_non_indexed(self):
        """展开索引，返回新的 MeshData（合并/静态合批前用）。"""
        if not self.indices:
            return self
        idx = self.indices
        out = MeshData([], [], [], [], [], [], self.name)
        for i in idx:
            out.positions += self.positions[i * 3:i * 3 + 3]
            if self.normals:
                out.normals += self.normals[i * 3:i * 3 + 3]
            if self.uvs:
                out.uvs += self.uvs[i * 2:i * 2 + 2]
            if self.colors:
                out.colors += self.colors[i * 4:i * 4 + 4]
            if self.tangents:
                out.tangents += self.tangents[i * 3:i * 3 + 3]
        return out

    def with_color(self, rgb, alpha=1.0):
        """给整块网格刷一个顶点色（返回 self，便于链式）。"""
        n = self.vertex_count
        self.colors = []
        for _ in range(n):
            self.colors += [rgb[0], rgb[1], rgb[2], alpha]
        return self

    def __repr__(self):
        return (f'MeshData(name={self.name!r}, verts={self.vertex_count}, '
                f'tris={self.triangle_count})')


# ------------------------------------------------------------ 基本体 --
def box(sx, sy, sz, offset=(0.0, 0.0, 0.0), color=None):
    """盒体：尺寸 (sx,sy,sz)，中心偏移 offset。

    骨骼段用法：局部原点设在关节（枢轴），几何沿 +Y 展开——
    例如 ``box(0.4, 2.4, 0.4, (0, 1.2, 0))`` 的旋转枢轴在盒体底部。
    """
    hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
    ox, oy, oz = offset
    # 六个面：(法线, 四个角点 CCW, uv)
    faces = [
        ((0, 0, 1), ((-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz))),
        ((0, 0, -1), ((hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz))),
        ((0, 1, 0), ((-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz), (-hx, hy, -hz))),
        ((0, -1, 0), ((-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz))),
        ((1, 0, 0), ((hx, -hy, hz), (hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz))),
        ((-1, 0, 0), ((-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz))),
    ]
    positions, normals, uvs = [], [], []
    for n, quad in faces:
        a, b, c, d = quad
        for x, y, z in (a, b, c, a, c, d):
            positions += [ox + x, oy + y, oz + z]
            normals += list(n)
        uvs += [0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1]
    md = MeshData(positions, normals, uvs, name='box')
    if color is not None:
        md.with_color(color)
    return md


def cube(center=(0.0, 0.0, 0.0), size=1.0, color=None):
    """立方体：以 center 为中心、边长 size。"""
    return box(size, size, size, center, color)


def sphere(radius=1.0, segments=24, rings=16, color=None):
    """UV 球：极点处顶点重合（UV 球的固有代价），但网格规则、UV 连续。"""
    positions, normals, uvs, indices = [], [], [], []
    for r in range(rings + 1):
        v = r / rings
        phi = v * math.pi
        for s in range(segments + 1):
            u = s / segments
            theta = u * 2.0 * math.pi
            nx = math.sin(phi) * math.cos(theta)
            ny = math.cos(phi)
            nz = math.sin(phi) * math.sin(theta)
            positions += [nx * radius, ny * radius, nz * radius]
            normals += [nx, ny, nz]
            uvs += [u, 1.0 - v]
    for r in range(rings):
        for s in range(segments):
            a = r * (segments + 1) + s
            b = a + segments + 1
            indices += [a, b, a + 1, a + 1, b, b + 1]
    md = MeshData(positions, normals, uvs, indices=indices, name='sphere')
    if color is not None:
        md.with_color(color)
    return md


def plane(width=1.0, depth=1.0, segs=1, color=None, y=0.0):
    """水平面（XZ 平面），法线 +Y。segs>1 时细分成网格（水面/地形用）。"""
    positions, normals, uvs, indices = [], [], [], []
    for iz in range(segs + 1):
        for ix in range(segs + 1):
            u, v = ix / segs, iz / segs
            x = (u - 0.5) * width
            z = (v - 0.5) * depth
            positions += [x, y, z]
            normals += [0.0, 1.0, 0.0]
            uvs += [u, v]
    for iz in range(segs):
        for ix in range(segs):
            a = iz * (segs + 1) + ix
            b = a + segs + 1
            indices += [a, b, a + 1, a + 1, b, b + 1]
    md = MeshData(positions, normals, uvs, indices=indices, name='plane')
    if color is not None:
        md.with_color(color)
    return md


def cylinder(radius=0.5, height=1.0, segments=24, capped=True, color=None):
    """圆柱：轴沿 Y，底面在 y=0，顶面在 y=height。"""
    md_side_pos, md_side_n, md_side_uv, idx = [], [], [], []
    for s in range(segments + 1):
        u = s / segments
        theta = u * 2.0 * math.pi
        nx, nz = math.cos(theta), math.sin(theta)
        md_side_pos += [nx * radius, 0.0, nz * radius, nx * radius, height, nz * radius]
        md_side_n += [nx, 0.0, nz, nx, 0.0, nz]
        md_side_uv += [u, 1.0, u, 0.0]
    for s in range(segments):
        a = s * 2
        idx += [a, a + 1, a + 2, a + 2, a + 1, a + 3]
    md = MeshData(md_side_pos, md_side_n, md_side_uv, indices=idx, name='cylinder')
    if capped:
        for sign, yy in ((1.0, height), (-1.0, 0.0)):
            base = md.vertex_count
            md.positions += [0.0, yy, 0.0]
            md.normals += [0.0, sign, 0.0]
            md.uvs += [0.5, 0.5]
            for s in range(segments + 1):
                theta = (s / segments) * 2.0 * math.pi
                md.positions += [math.cos(theta) * radius, yy, math.sin(theta) * radius]
                md.normals += [0.0, sign, 0.0]
                md.uvs += [0.5 + 0.5 * math.cos(theta), 0.5 + 0.5 * math.sin(theta)]
            for s in range(segments):
                if sign > 0:
                    md.indices += [base, base + 1 + s, base + 2 + s]
                else:
                    md.indices += [base, base + 2 + s, base + 1 + s]
    if color is not None:
        md.with_color(color)
    return md


def torus(major=1.0, minor=0.3, major_segs=32, minor_segs=16, color=None):
    """圆环（甜甜圈）：主环在 XZ 平面。"""
    positions, normals, uvs, indices = [], [], [], []
    for i in range(major_segs + 1):
        u = i / major_segs
        theta = u * 2.0 * math.pi
        cx, cz = math.cos(theta), math.sin(theta)
        for j in range(minor_segs + 1):
            v = j / minor_segs
            phi = v * 2.0 * math.pi
            nx = cx * math.cos(phi)
            ny = math.sin(phi)
            nz = cz * math.cos(phi)
            positions += [(major + minor * math.cos(phi)) * cx, minor * ny,
                          (major + minor * math.cos(phi)) * cz]
            normals += [nx, ny, nz]
            uvs += [u, v]
    for i in range(major_segs):
        for j in range(minor_segs):
            a = i * (minor_segs + 1) + j
            b = a + minor_segs + 1
            indices += [a, b, a + 1, a + 1, b, b + 1]
    md = MeshData(positions, normals, uvs, indices=indices, name='torus')
    if color is not None:
        md.with_color(color)
    return md


def ground_grid(half=10, tile=1.0, color_a=(0.50, 0.53, 0.47),
                color_b=(0.32, 0.34, 0.30)):
    """棋盘格地面（XZ 平面，y=0），两色交替。

    颜色是**冷灰**而不是偏绿的灰：早期版本用 (g*0.8, g, g*0.8)，
    与天空蓝对冲，满屏地面时很晃眼；改冷灰后暖色物体才跳得出来。
    """
    positions, normals, uvs, colors = [], [], [], []
    for ix in range(-half, half):
        for iz in range(-half, half):
            x0, z0 = ix * tile, iz * tile
            x1, z1 = x0 + tile, z0 + tile
            quad = [(x0, 0.0, z0), (x1, 0.0, z0), (x1, 0.0, z1),
                    (x0, 0.0, z0), (x1, 0.0, z1), (x0, 0.0, z1)]
            for x, y, z in quad:
                positions += [x, y, z]
                normals += [0.0, 1.0, 0.0]
                uvs += [x, z]
            c = color_a if (ix + iz) % 2 == 0 else color_b
            colors += list(c) + [1.0]
    return MeshData(positions, normals, uvs, colors, name='ground')


def fullscreen_quad():
    """NDC 全屏四边形（两个三角形），供纯片元着色器 pass 使用。

    顶点直接落在裁剪空间 [-1,1]，不依赖 WindowBlock；着色器里必须同时声明
    ``in vec3 position;``（pyglet 按名字校验属性）。
    """
    verts = (-1.0, -1.0, 0.0, 1.0, -1.0, 0.0, 1.0, 1.0, 0.0,
             -1.0, -1.0, 0.0, 1.0, 1.0, 0.0, -1.0, 1.0, 0.0)
    uvs = (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 1.0)
    return MeshData(list(verts), [], list(uvs), name='fullscreen_quad')


def merge_meshes(meshes):
    """把一批网格合并成一个（静态合批：N 次 draw call 变 1 次）。

    注意（踩坑记录）：不能用一个"空 MeshData"作累加器——``MeshData.merge``
    对属性是"两边都有才保留"，空累加器的 uvs/normals 为空 ⇒ 整个合并结果
    的 UV/法线列被静默丢弃。后果是"合并后的网格贴图全失效、变成单点采样
    的纯色"（雪原/石墙全成了光板，排查了很久）。这里改为逐属性显式拼接，
    并在缺失时本地补齐（缺法线先算、缺 UV 补 0），保证列宽一致。
    """
    meshes = list(meshes)
    if not meshes:
        return MeshData([], [], [], name='merged')
    out = MeshData([], [], [], name='merged')
    all_colors = all(m.colors for m in meshes)
    all_tangents = all(m.tangents for m in meshes)
    for m in meshes:
        mm = m.to_non_indexed() if m.indices else m
        n = mm.vertex_count
        if not mm.normals:
            mm.compute_normals()
        if not mm.uvs:
            mm.uvs = [0.0] * (n * 2)
        out.positions += mm.positions
        out.normals += mm.normals
        out.uvs += mm.uvs
        if all_colors:
            out.colors += mm.colors
        if all_tangents:
            out.tangents += mm.tangents
    out.name = meshes[0].name
    return out


# ------------------------------------------------------------ 高级建模 --
def lathe(profile, segments=24, closed_top=False, closed_bottom=False,
          color=None, name='lathe'):
    """旋转体（车床曲面）：把 2D 轮廓 ``(r, y)`` 列表绕 Y 轴旋转一周。

    这是"盒子拼装"之外的第一种真正的曲面建模——花瓶、凹槽柱、涡轮壳、
    卵形体都是一条轮廓线的事。法线解析计算：轮廓切线 (dr, dy) 旋转后取
    垂直方向，比 compute_normals 的面平均在棱角处更干净（顶点共享时
    面平均会把折角磨圆）。

    profile 必须按 y 升序或降序排列；r=0 的端点会退化成极点（顶点重合，
    与 UV 球同样的固有代价）。``closed_*`` 补一个中心扇形盖。
    """
    positions, normals, uvs, indices = [], [], [], []
    n = len(profile)
    for s in range(segments + 1):
        u = s / segments
        theta = u * 2.0 * math.pi
        ct, st = math.cos(theta), math.sin(theta)
        for k, (r, y) in enumerate(profile):
            positions += [r * ct, y, r * st]
            # 轮廓切线（沿 k 增大方向），法线 = (dy, -dr) 旋转到该经角
            k0, k1 = max(0, k - 1), min(n - 1, k + 1)
            dr = profile[k1][0] - profile[k0][0]
            dy = profile[k1][1] - profile[k0][1]
            ln = math.hypot(dr, dy) or 1.0
            nx, ny = dy / ln, -dr / ln
            normals += [nx * ct, ny, nx * st]
            uvs += [u, k / (n - 1)]
    for s in range(segments):
        for k in range(n - 1):
            a = s * n + k
            b = a + n
            indices += [a, b, a + 1, a + 1, b, b + 1]
    md = MeshData(positions, normals, uvs, indices=indices, name=name)

    def _cap(idx_end, sign):
        base = md.vertex_count
        md.positions += [0.0, profile[idx_end][1], 0.0]
        md.normals += [0.0, sign, 0.0]
        md.uvs += [0.5, 0.5]
        for s in range(segments + 1):
            theta = (s / segments) * 2.0 * math.pi
            r, y = profile[idx_end]
            md.positions += [math.cos(theta) * r, y, math.sin(theta) * r]
            md.normals += [0.0, sign, 0.0]
            md.uvs += [0.5 + 0.5 * math.cos(theta), 0.5 + 0.5 * math.sin(theta)]
        for s in range(segments):
            if sign > 0:
                md.indices += [base, base + 1 + s, base + 2 + s]
            else:
                md.indices += [base, base + 2 + s, base + 1 + s]

    if closed_bottom and profile[0][0] > 1e-9:
        _cap(0, -1.0)
    if closed_top and profile[-1][0] > 1e-9:
        _cap(n - 1, 1.0)
    if color is not None:
        md.with_color(color)
    return md


def _parallel_transport(points):
    """折线顶点处的平行传输标架：[(tangent, normal, binormal), ...]。

    沿曲线扫掠管道时用平行传输而不是 Frenet——Frenet 在拐点/直段处
    法线会翻转或未定义（曲率=0），管道表面出现可憎的接缝扭转。
    """
    frames = []
    # 初始法线：取与首切线最不平行的坐标轴叉乘
    t0 = _sub(points[1], points[0])
    t0 = _normalize(t0)
    axis = (0.0, 1.0, 0.0) if abs(t0[1]) < 0.9 else (1.0, 0.0, 0.0)
    n = _normalize(_cross(axis, t0))
    b = _cross(t0, n)
    frames.append((t0, n, b))
    for i in range(1, len(points)):
        t1 = _normalize(_sub(points[min(i + 1, len(points) - 1)],
                             points[i - 1]))
        # 把上一标架的法线投影到当前切线的垂直平面（双反射法的简化）
        n = _sub(n, _scale(t1, _dot(n, t1)))
        if math.sqrt(_dot(n, n)) < 1e-9:
            axis = (0.0, 1.0, 0.0) if abs(t1[1]) < 0.9 else (1.0, 0.0, 0.0)
            n = _normalize(_cross(axis, t1))
        else:
            n = _normalize(n)
        b = _cross(t1, n)
        frames.append((t1, n, b))
    return frames


def tube(points, radius=0.1, radial=8, color=None, caps=True, name='tube',
         frames=None):
    """沿 3D 折线扫掠的管体：导管/线缆/扶手/轨迹可视化。

    截面圆用平行传输标架定向（见 :func:`_parallel_transport`），
    端点各补一个中心扇形封盖（``caps=False`` 时省略——闭合曲线扫掠的
    管体首尾环重合，盖子只会藏在管内还可能 z-fighting）。
    ``radius`` 可以是标量（等粗）或长度 == len(points) 的序列（渐变粗细，
    做"脖子细-躯干粗-尾尖细"这类有机体）。
    ``frames`` 可外部传入预计算标架（闭合曲线要做和乐角补偿，见
    :func:`torus_knot`）。
    """
    pts = [(p[0], p[1], p[2]) for p in points]
    if len(pts) < 2:
        raise ValueError('tube 至少需要两个路径点')
    if isinstance(radius, (list, tuple)):
        if len(radius) != len(pts):
            raise ValueError('radius 序列长度必须与路径点数一致')
        radii = [float(r) for r in radius]
    else:
        radii = None
    frames = frames or _parallel_transport(pts)
    positions, normals, uvs, indices = [], [], [], []
    for i, (p, (t, n, b)) in enumerate(zip(pts, frames)):
        v = i / (len(pts) - 1)
        ri = radii[i] if radii else radius
        for s in range(radial + 1):
            u = s / radial
            phi = u * 2.0 * math.pi
            c, sn = math.cos(phi), math.sin(phi)
            dx = n[0] * c + b[0] * sn
            dy = n[1] * c + b[1] * sn
            dz = n[2] * c + b[2] * sn
            positions += [p[0] + dx * ri, p[1] + dy * ri, p[2] + dz * ri]
            normals += [dx, dy, dz]
            uvs += [u, v]
    stride = radial + 1
    for i in range(len(pts) - 1):
        for s in range(radial):
            a = i * stride + s
            bidx = a + stride
            indices += [a, bidx, a + 1, a + 1, bidx, bidx + 1]
    md = MeshData(positions, normals, uvs, indices=indices, name=name)

    def _cap(idx, sign):
        t, n, b = frames[idx]
        ri = radii[idx] if radii else radius
        base = md.vertex_count
        md.positions += list(pts[idx])
        md.normals += [t[0] * sign, t[1] * sign, t[2] * sign]
        md.uvs += [0.5, 0.5]
        for s in range(radial + 1):
            phi = (s / radial) * 2.0 * math.pi
            c, sn = math.cos(phi), math.sin(phi)
            dx = n[0] * c + b[0] * sn
            dy = n[1] * c + b[1] * sn
            dz = n[2] * c + b[2] * sn
            md.positions += [pts[idx][0] + dx * ri,
                             pts[idx][1] + dy * ri,
                             pts[idx][2] + dz * ri]
            md.normals += [t[0] * sign, t[1] * sign, t[2] * sign]
            md.uvs += [0.5 + 0.5 * c, 0.5 + 0.5 * sn]
        for s in range(radial):
            if sign > 0:
                md.indices += [base, base + 1 + s, base + 2 + s]
            else:
                md.indices += [base, base + 2 + s, base + 1 + s]

    if caps:
        _cap(0, -1.0)
        _cap(len(pts) - 1, 1.0)
    if color is not None:
        md.with_color(color)
    return md


def _frames_closed(pts):
    """闭合曲线的扫掠标架（pts 首尾点重合，返回长度 == len(pts)）。

    与开曲线的两点不同：
    1. 切线用**周期中央差分**——首末点的前向/后向差分会引入一阶误差，
       闭合处切线跳变 ~14°，接缝直接裂开；
    2. 平行传输绕一圈后标架差一个**和乐角** φ（曲面 holonomy），
       把 ``-φ·i/N`` 的渐进旋转匀给沿途标架，末环才能与首环重合。
    """
    n_pts = len(pts) - 1                    # 去掉回绕点的独立点数
    tangents = []
    for i in range(n_pts):
        tp = pts[(i + 1) % n_pts]
        tm = pts[(i - 1) % n_pts]
        tangents.append(_normalize(_sub(tp, tm)))
    t0 = tangents[0]
    axis = (0.0, 1.0, 0.0) if abs(t0[1]) < 0.9 else (1.0, 0.0, 0.0)
    n = _normalize(_cross(axis, t0))
    frames = []
    for i in range(n_pts):
        t = tangents[i]
        n = _sub(n, _scale(t, _dot(n, t)))
        if math.sqrt(_dot(n, n)) < 1e-9:
            axis = (0.0, 1.0, 0.0) if abs(t[1]) < 0.9 else (1.0, 0.0, 0.0)
            n = _normalize(_cross(axis, t))
        else:
            n = _normalize(n)
        frames.append((t, n, _cross(t, n)))
    # 传输一整圈回到起始切线平面，测和乐角
    n_looped = _sub(frames[-1][1], _scale(t0, _dot(frames[-1][1], t0)))
    n_looped = _normalize(n_looped)
    phi = math.atan2(_dot(_cross(frames[0][1], n_looped), t0),
                     _dot(frames[0][1], n_looped))
    if abs(phi) > 1e-9:
        for i in range(n_pts):
            t, ni, _bi = frames[i]
            a = -phi * i / n_pts
            ni = _rotate_about(ni, t, a)
            frames[i] = (t, ni, _cross(t, ni))
    frames.append(frames[0])                # 回绕环 == 首环（精确重合）
    return frames


def torus_knot(p=2, q=3, major=1.0, tube_r=0.22, tubular=96, radial=10,
               color=None, name='torus_knot'):
    """(p,q) 环面结：绕环面 p 圈穿 q 次的管状结。

    参数曲线 ``r = major*(2+cos(q·t))/3``，z = sin(q·t)·major/3，
    t ∈ [0, 2πp]。采样**含回绕点**（首尾管环重合 → 曲面闭合，不需要
    封盖），标架用 :func:`_frames_closed`（周期切线 + 和乐角补偿）——
    结在空间里反复扭转，正是 Frenet 标架会翻车的场合。
    """
    pts = []
    for i in range(tubular + 1):
        t = 2.0 * math.pi * p * i / tubular
        r = major * (2.0 + math.cos(q * t)) / 3.0
        pts.append((r * math.cos(t),
                    math.sin(q * t) * major / 3.0,
                    r * math.sin(t)))
    md = tube(pts, tube_r, radial, color=color, caps=False, name=name,
              frames=_frames_closed(pts))
    md.name = name
    return md


# ---- 向量小工具（几何模块内部用，避免依赖 mathkit 的四元数体系） ----
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _normalize(a):
    l = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / l, a[1] / l, a[2] / l)


def _rotate_about(v, axis, ang):
    """Rodrigues 旋转：v 绕单位轴 axis 转 ang 弧度。"""
    c, s = math.cos(ang), math.sin(ang)
    cr = _cross(axis, v)
    d = _dot(axis, v)
    return (v[0] * c + cr[0] * s + axis[0] * d * (1 - c),
            v[1] * c + cr[1] * s + axis[1] * d * (1 - c),
            v[2] * c + cr[2] * s + axis[2] * d * (1 - c))
