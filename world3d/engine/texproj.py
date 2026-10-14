# -*- coding: utf-8 -*-
"""UV 投影与贴图规则：世界 → 纹理坐标的线性映射，及其反解（纯数学，无 GL）。

与 ``proctex``（程序化纹理**像素**）成对：本模块生成纹理**坐标**——
引擎的"纹理规则"双子。设计背景（§18）实证于 CS2 de_dust2 真实世界几何：
世界级 UV 即**面片局部仿射投影**，与 Source 1 ``texinfo`` 公式同构::

    u = tv0 · p + tv0.w          # 本模块：u = (p · u_dir) * scale[0] + offset[0]
    v = tv1 · p + tv1.w          #        v = (p · v_dir) * scale[1] + offset[1]

``u_dir``/``v_dir`` 由**投影轴**（x/y/z 主轴）+ **旋转角**决定；scale 是各向异性
密度（UV / 世界单位），offset 是相位。``gradients()`` 直接给出 texinfo 的 tv 向量。

五个部件
--------
1. :class:`UvProjection` + :func:`project_uv` / :func:`uv_to_world` —— 正反两向；
2. :func:`fit_projection` —— 从**真实数据**最小二乘反解投影（对拍/学习）；
3. :func:`uv_for_faces` —— 给程序化关卡几何一键写合规 UV（含顶点拆分）；
4. :class:`TexWrap` / :class:`Atlas` —— 采样环绕规则与图集映射（含图集内平铺）；
5. :func:`density_report` —— 贴图密度体检（world units per UV 直方图 + 离群面）。

约定（与引擎现状一致）
----------------------
* UV 单位 = "贴图数"：1.0 = 贴满一张纹理，超出部分由 wrap（通常是 repeat）接管；
* 贴图密度用 ``from_tile(size)`` 表达：size = 一张贴图覆盖多少世界单位；
* 轴向基（右手系 ``cross(e1, e2) == d``）::

      z: e1=X, e2=Y      x: e1=Y, e2=Z      y: e1=Z, e2=X

* v 轴与 GL 惯例一致（向上为正），图集的 v 按此换算。
"""
import math

from world3d.engine.geometry import MeshData

__all__ = [
    'AXES', 'axis_vector', 'dominant_axis', 'face_basis',
    'UvProjection', 'project_uv', 'uv_to_world',
    'AffineUv', 'FitResult', 'fit_projection', 'affine_to_projection',
    'uv_for_faces',
    'TexWrap', 'wrap_uv',
    'Atlas', 'texel_scale', 'density_report', 'DensityReport',
]

AXES = ('x', 'y', 'z')

_AXIS_VEC = {
    'x': (1.0, 0.0, 0.0),
    'y': (0.0, 1.0, 0.0),
    'z': (0.0, 0.0, 1.0),
}

#: 轴向基：(e1, e2, d)，满足 cross(e1, e2) == d（右手系）。
_BASIS = {
    'z': ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    'x': ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    'y': ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
}


# ------------------------------------------------------------------ 轴向 --

def axis_vector(axis):
    """投影轴单位向量。"""
    try:
        return _AXIS_VEC[axis]
    except KeyError:
        raise ValueError(f'未知投影轴 {axis!r}（可选 {AXES}）') from None


def face_basis(axis):
    """投影轴的标准面内基 ``(e1, e2)``；``cross(e1, e2) == 轴向量``。"""
    try:
        return _BASIS[axis]
    except KeyError:
        raise ValueError(f'未知投影轴 {axis!r}（可选 {AXES}）') from None


def dominant_axis(normal):
    """面法线的主导轴（|分量| 最大者）；退化法线（全 0）回退 ``'z'``。"""
    ax, ay, az = abs(normal[0]), abs(normal[1]), abs(normal[2])
    if az >= ax and az >= ay:
        return 'z'
    if ax >= ay:
        return 'x'
    return 'y'


# ---------------------------------------------------------------- 投影 --

class UvProjection:
    """一次"世界 → UV"的线性投影（Source texinfo 的引擎化表达）。

    ``u = (p · u_dir) * scale[0] + offset[0]``；
    ``v = (p · v_dir) * scale[1] + offset[1]``。

    ``u_dir =  cos(rot)*e1 + sin(rot)*e2``；
    ``v_dir = -sin(rot)*e1 + cos(rot)*e2``（rot=0 时 u=X, v=Y）。

    scale 为负 = 该方向镜像（例如贴图左右翻转）。**视为不可变对象**：
    要改参数请新建（方向与梯度在构造时缓存）。
    """

    __slots__ = ('axis', 'scale', 'rotation', 'offset', '_u', '_v', '_gu', '_gv')

    def __init__(self, axis='z', scale=(1.0, 1.0), rotation=0.0, offset=(0.0, 0.0)):
        if axis not in _BASIS:
            raise ValueError(f'未知投影轴 {axis!r}（可选 {AXES}）')
        self.axis = axis
        self.scale = (float(scale[0]), float(scale[1]))
        self.rotation = float(rotation)
        self.offset = (float(offset[0]), float(offset[1]))
        e1, e2 = _BASIS[axis]
        c, s = math.cos(self.rotation), math.sin(self.rotation)
        self._u = (c * e1[0] + s * e2[0], c * e1[1] + s * e2[1], c * e1[2] + s * e2[2])
        self._v = (-s * e1[0] + c * e2[0], -s * e1[1] + c * e2[1], -s * e1[2] + c * e2[2])
        self._gu = tuple(self._u[i] * self.scale[0] for i in range(3))
        self._gv = tuple(self._v[i] * self.scale[1] for i in range(3))

    @classmethod
    def from_tile(cls, size, axis='z', rotation=0.0, offset=(0.0, 0.0)):
        """按"一张贴图覆盖 size 个世界单位"构造（各向同性）。"""
        s = 1.0 / float(size)
        return cls(axis, (s, s), rotation, offset)

    def directions(self):
        """面内 UV 方向基 ``(u_dir, v_dir)``（单位向量，正交）。"""
        return self._u, self._v

    def gradients(self):
        """UV 对世界坐标的梯度 ``(∇u, ∇v)``——即 texinfo 的 tv0.xyz / tv1.xyz。"""
        return self._gu, self._gv

    def copy(self, **changes):
        """复制（可改字段）——``copy(scale=(0.1, 0.1))``。"""
        axis = changes.get('axis', self.axis)
        scale = changes.get('scale', self.scale)
        rotation = changes.get('rotation', self.rotation)
        offset = changes.get('offset', self.offset)
        return UvProjection(axis, scale, rotation, offset)

    def key(self):
        """顶点拆分用的等价键（同键 = 同投影输出）。"""
        return (self.axis, self.scale[0], self.scale[1], self.rotation,
                self.offset[0], self.offset[1])

    def __repr__(self):
        return (f'UvProjection(axis={self.axis!r}, scale={self.scale}, '
                f'rotation={self.rotation:.4f}, offset={self.offset})')


def project_uv(p, projection):
    """世界点 → UV（按投影）。"""
    gu, gv = projection.gradients()
    x, y, z = p[0], p[1], p[2]
    return (gu[0] * x + gu[1] * y + gu[2] * z + projection.offset[0],
            gv[0] * x + gv[1] * y + gv[2] * z + projection.offset[1])


def uv_to_world(uv, projection, plane=0.0):
    """UV → 世界点（投影的逆；投影只丢失**沿轴**分量，需给定平面高度）。

    ``plane`` = 投影轴方向的坐标（z 轴投影的地板就是 z 高度）。
    恒等式：``uv_to_world(project_uv(p), proj, dot(p, axis_vec)) == p``。
    """
    su, sv = projection.scale
    if abs(su) < 1e-30 or abs(sv) < 1e-30:
        raise ValueError('scale 为 0 的投影不可逆')
    u_dir, v_dir = projection.directions()
    a = (uv[0] - projection.offset[0]) / su
    b = (uv[1] - projection.offset[1]) / sv
    d = _AXIS_VEC[projection.axis]
    return (a * u_dir[0] + b * v_dir[0] + plane * d[0],
            a * u_dir[1] + b * v_dir[1] + plane * d[1],
            a * u_dir[2] + b * v_dir[2] + plane * d[2])


# ------------------------------------------------------------------ 拟合 --

class AffineUv:
    """一般仿射映射 ``u = a·p + au``，``v = b·p + bv``（不限轴向）。"""

    __slots__ = ('a', 'au', 'b', 'bv')

    def __init__(self, a, au, b, bv):
        self.a = (float(a[0]), float(a[1]), float(a[2]))
        self.au = float(au)
        self.b = (float(b[0]), float(b[1]), float(b[2]))
        self.bv = float(bv)

    def uv(self, p):
        return (self.a[0] * p[0] + self.a[1] * p[1] + self.a[2] * p[2] + self.au,
                self.b[0] * p[0] + self.b[1] * p[1] + self.b[2] * p[2] + self.bv)

    def __repr__(self):
        return f'AffineUv(a={self.a}, au={self.au:.6g}, b={self.b}, bv={self.bv:.6g})'


class FitResult:
    """``fit_projection`` 的结果：仿射解 + 残差 + 可否收敛为轴向投影。"""

    __slots__ = ('count', 'mode', 'affine', 'rms_u', 'rms_v',
                 'projection', 'reason', 'axis_align', 'ortho')

    def __init__(self, count, mode, affine=None, rms_u=0.0, rms_v=0.0,
                 projection=None, reason='', axis_align=0.0, ortho=0.0):
        self.count = count
        self.mode = mode              # 'planar' | 'volume' | 'none'
        self.affine = affine
        self.rms_u = rms_u
        self.rms_v = rms_v
        self.projection = projection  # UvProjection 或 None
        self.reason = reason          # projection 为 None 的原因
        self.axis_align = axis_align  # |cos|（投影面法线 ↔ 最近轴）
        self.ortho = ortho            # |cos|（∇u ↔ ∇v，0 = 完全正交）

    @property
    def ok(self):
        return self.projection is not None

    def __repr__(self):
        p = 'None' if self.projection is None else self.projection
        return (f'FitResult(count={self.count}, mode={self.mode}, '
                f'rms=({self.rms_u:.3g}, {self.rms_v:.3g}), proj={p}, '
                f'reason={self.reason!r})')


def _solve(mat, rhs):
    """高斯消元（部分主元）。``mat`` 行主序 n×n，``rhs`` 长 n；奇异返回 None。"""
    n = len(mat)
    m = [list(mat[i]) + [rhs[i]] for i in range(n)]
    for col in range(n):
        p = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[p][col]) < 1e-14:
            return None
        m[col], m[p] = m[p], m[col]
        pv = m[col][col]
        for r in range(col + 1, n):
            f = m[r][col] / pv
            if f:
                row_c, row_r = m[col], m[r]
                for c in range(col, n + 1):
                    row_r[c] -= f * row_c[c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / m[r][r]
    return x


def _sym_eigen3(c):
    """3×3 对称矩阵 Jacobi 特征分解。返回 (特征值, 特征向量列列表)。"""
    a = [[c[0][0], c[0][1], c[0][2]],
         [c[1][0], c[1][1], c[1][2]],
         [c[2][0], c[2][1], c[2][2]]]
    v = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in range(64):
        off, p, q = 0.0, 0, 1
        for i in range(3):
            for j in range(i + 1, 3):
                if abs(a[i][j]) > off:
                    off, p, q = abs(a[i][j]), i, j
        if off < 1e-13 * (abs(a[0][0]) + abs(a[1][1]) + abs(a[2][2]) + 1e-300):
            break
        theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
        t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
        cs = 1.0 / math.sqrt(t * t + 1.0)
        sn = t * cs
        for k in range(3):          # 旋转行
            akp, akq = a[k][p], a[k][q]
            a[k][p] = cs * akp - sn * akq
            a[k][q] = sn * akp + cs * akq
        for k in range(3):          # 旋转列
            apk, aqk = a[p][k], a[q][k]
            a[p][k] = cs * apk - sn * aqk
            a[q][k] = sn * apk + cs * aqk
        for k in range(3):          # 累积特征向量
            vkp, vkq = v[k][p], v[k][q]
            v[k][p] = cs * vkp - sn * vkq
            v[k][q] = sn * vkp + cs * vkq
    vals = [a[i][i] for i in range(3)]
    vecs = [[v[r][c] for r in range(3)] for c in range(3)]
    return vals, vecs


def fit_projection(points, uvs, planar_ratio=0.02):
    """从真实数据反解 UV 投影（最小二乘）。

    * 点集**近似共面**（厚度/直径 < ``planar_ratio``）→ 先在拟合平面内做 2D
      仿射（数值稳、梯度严格落在平面内）；
    * 否则做全 3D 仿射（4 参数 × 2 组方程；要求点集不退化）。

    返回 :class:`FitResult`：仿射解 + 残差 + 能否收敛为轴向投影及原因。
    """
    n = len(points)
    if n < 4:
        return FitResult(n, 'none', reason='点数不足（需 ≥4）')
    # 均值与协方差
    mx = my = mz = 0.0
    for p in points:
        mx += p[0]; my += p[1]; mz += p[2]
    mx /= n; my /= n; mz /= n
    c = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    for p in points:
        dx, dy, dz = p[0] - mx, p[1] - my, p[2] - mz
        c[0][0] += dx * dx; c[0][1] += dx * dy; c[0][2] += dx * dz
        c[1][1] += dy * dy; c[1][2] += dy * dz; c[2][2] += dz * dz
    c[1][0], c[2][0], c[2][1] = c[0][1], c[0][2], c[1][2]
    vals, vecs = _sym_eigen3(c)
    order = sorted(range(3), key=lambda i: vals[i])
    l_min, l_mid, l_max = vals[order[0]], vals[order[1]], vals[order[2]]
    l_max = max(l_max, 1e-300)
    spread = math.sqrt(max(l_mid, 0.0) / l_max)      # 面内散布（共线则 ~0）
    if spread < 1e-6:
        return FitResult(n, 'none', reason='点集共线，投影不可定')

    normal = vecs[order[0]]
    flat = math.sqrt(max(l_min, 0.0) / l_max)        # 厚度 / 直径
    mean = (mx, my, mz)

    if flat <= planar_ratio:
        mode = 'planar'
        nrm = _normalize(normal)
        # 面内基
        t = (1.0, 0.0, 0.0) if abs(nrm[0]) < 0.9 else (0.0, 1.0, 0.0)
        e1 = _normalize(_cross(nrm, t))
        e2 = _cross(nrm, e1)
        # 2D 仿射：u = a1 x + a2 y + cu（x,y 为面内坐标）
        m3 = [[0.0] * 3 for _ in range(3)]
        b3u = [0.0] * 3
        b3v = [0.0] * 3
        for p, uv in zip(points, uvs):
            dx, dy, dz = p[0] - mx, p[1] - my, p[2] - mz
            x = dx * e1[0] + dy * e1[1] + dz * e1[2]
            y = dx * e2[0] + dy * e2[1] + dz * e2[2]
            m3[0][0] += x * x; m3[0][1] += x * y; m3[0][2] += x
            m3[1][1] += y * y; m3[1][2] += y
            m3[2][2] += 1.0
            b3u[0] += x * uv[0]; b3u[1] += y * uv[0]; b3u[2] += uv[0]
            b3v[0] += x * uv[1]; b3v[1] += y * uv[1]; b3v[2] += uv[1]
        m3[1][0], m3[2][0], m3[2][1] = m3[0][1], m3[0][2], m3[1][2]
        sol_u = _solve(m3, b3u)
        sol_v = _solve(m3, b3v)
        if sol_u is None or sol_v is None:
            return FitResult(n, 'none', reason='法方程奇异（点集退化）')
        a = tuple(sol_u[0] * e1[k] + sol_u[1] * e2[k] for k in range(3))
        b = tuple(sol_v[0] * e1[k] + sol_v[1] * e2[k] for k in range(3))
        au = sol_u[2] - (a[0] * mx + a[1] * my + a[2] * mz)
        bv = sol_v[2] - (b[0] * mx + b[1] * my + b[2] * mz)
    else:
        mode = 'volume'
        # 全 3D 4 参数正规方程（带微小正则，抗秩亏）
        m4 = [[0.0] * 4 for _ in range(4)]
        b4u = [0.0] * 4
        b4v = [0.0] * 4
        for p, uv in zip(points, uvs):
            w = (p[0], p[1], p[2], 1.0)
            for i in range(4):
                for j in range(4):
                    m4[i][j] += w[i] * w[j]
                b4u[i] += w[i] * uv[0]
                b4v[i] += w[i] * uv[1]
        for i in range(4):
            m4[i][i] += 1e-9 * (m4[0][0] + m4[1][1] + m4[2][2] + 1.0)
        sol_u = _solve(m4, b4u)
        sol_v = _solve(m4, b4v)
        if sol_u is None or sol_v is None:
            return FitResult(n, 'none', reason='法方程奇异（点集退化）')
        a = (sol_u[0], sol_u[1], sol_u[2]); au = sol_u[3]
        b = (sol_v[0], sol_v[1], sol_v[2]); bv = sol_v[3]

    aff = AffineUv(a, au, b, bv)
    # 残差
    mse_u = mse_v = 0.0
    for p, uv in zip(points, uvs):
        pu, pv = aff.uv(p)
        mse_u += (pu - uv[0]) ** 2
        mse_v += (pv - uv[1]) ** 2
    rms_u = math.sqrt(mse_u / n)
    rms_v = math.sqrt(mse_v / n)

    proj, reason = affine_to_projection(aff)
    nrm = _normalize(_cross(a, b))
    nax = dominant_axis(nrm)
    align = abs(nrm[('x', 'y', 'z').index(nax)])
    su = _len3(a); sv = _len3(b)
    ortho = abs(_dot3(a, b) / (su * sv)) if su > 1e-30 and sv > 1e-30 else 1.0
    return FitResult(n, mode, aff, rms_u, rms_v, proj, reason, align, ortho)


def affine_to_projection(aff, angle_tol=0.966, tol=0.02):
    """把一般仿射解收敛为轴向 :class:`UvProjection`（可则返回，否则给原因）。

    ``angle_tol``：投影面法线与最近轴的 ``|cos|`` 下限（默认 ≈15°）；
    ``tol``：梯度方向残差 / 梯度模长 的上限（默认 2%）。
    """
    a, b = aff.a, aff.b
    su, sv = _len3(a), _len3(b)
    if su < 1e-12 or sv < 1e-12:
        return None, 'UV 梯度退化（尺度为零）'
    n = _cross(a, b)
    ln = _len3(n)
    if ln < 1e-12 * su * sv:
        return None, f'∇u 与 ∇v 平行（|cos|={abs(_dot3(a, b) / (su * sv)):.3f}）'
    axis = dominant_axis(n)
    align = abs(n[('x', 'y', 'z').index(axis)]) / ln
    if align < angle_tol:
        return None, f'投影面非轴对齐（cos={align:.3f}）'
    e1, e2 = _BASIS[axis]
    theta = math.atan2(_dot3(a, e2), _dot3(a, e1))
    cs, sn = math.cos(theta), math.sin(theta)
    u_dir = (cs * e1[0] + sn * e2[0], cs * e1[1] + sn * e2[1], cs * e1[2] + sn * e2[2])
    v_dir = (-sn * e1[0] + cs * e2[0], -sn * e1[1] + cs * e2[1], -sn * e1[2] + cs * e2[2])
    su_s = _dot3(a, u_dir)              # 目标 u 方向上的分量（≥0）
    sv_s = _dot3(b, v_dir)              # 可为负（镜像 v）
    ru = _len3((a[0] - su_s * u_dir[0], a[1] - su_s * u_dir[1], a[2] - su_s * u_dir[2]))
    rv = _len3((b[0] - sv_s * v_dir[0], b[1] - sv_s * v_dir[1], b[2] - sv_s * v_dir[2]))
    if ru > tol * su or rv > tol * sv:
        return None, (f'UV 梯度含面外分量/剪切（残差 {ru / su:.3f}, {rv / sv:.3f}）')
    return UvProjection(axis, (su_s, sv_s), theta, (aff.au, aff.bv)), ''


# ------------------------------------------------------------ 几何接线 --

def uv_for_faces(mesh, rules, split=True):
    """按面法线选投影，为网格生成 UV（返回**新** :class:`MeshData`）。

    ``rules`` 三种形态：

    * :class:`UvProjection` —— 所有面同一条规则；
    * ``{'x': proj, 'y': proj, 'z': proj}`` —— 按主导轴分派（推荐）；
    * ``callable(normal) -> UvProjection`` —— 自定义。

    ``split=True``：同一顶点被**不同投影**的面引用时拆成多份（每份归一组，
    这是烘焙世界投影 UV 的标准做法——盒子一个角属于 3 张不同面）。
    ``split=False``：原地写（顶点共享、后写覆盖），仅适用于已按面拆点的网格。
    """
    vc = mesh.vertex_count
    pos = mesh.positions
    idx = list(mesh.indices) if mesh.indices else list(range(vc))

    def resolve(normal):
        if callable(rules):
            return rules(normal)
        if isinstance(rules, dict):
            ax = dominant_axis(normal)
            try:
                return rules[ax]
            except KeyError:
                raise KeyError(f'rules 缺少轴 {ax!r} 的投影（现有键 {sorted(rules)}）') from None
        return rules

    def face_normal(a, b, c):
        e1 = (pos[b * 3] - pos[a * 3], pos[b * 3 + 1] - pos[a * 3 + 1],
              pos[b * 3 + 2] - pos[a * 3 + 2])
        e2 = (pos[c * 3] - pos[a * 3], pos[c * 3 + 1] - pos[a * 3 + 1],
              pos[c * 3 + 2] - pos[a * 3 + 2])
        return (e1[1] * e2[2] - e1[2] * e2[1],
                e1[2] * e2[0] - e1[0] * e2[2],
                e1[0] * e2[1] - e1[1] * e2[0])

    def project_at(vi, proj):
        return project_uv((pos[vi * 3], pos[vi * 3 + 1], pos[vi * 3 + 2]), proj)

    if not split:
        out = MeshData(list(pos), list(mesh.normals) or None, [0.0] * (vc * 2),
                       list(mesh.colors) or None, idx, list(mesh.tangents) or None,
                       mesh.name, mesh.joints, mesh.weights)
        for t in range(0, len(idx) - 2, 3):
            proj = resolve(face_normal(idx[t], idx[t + 1], idx[t + 2]))
            for k in range(3):
                vi = idx[t + k]
                out.uvs[vi * 2], out.uvs[vi * 2 + 1] = project_at(vi, proj)
        return out

    npos, nuv, nidx = [], [], []
    nnorm = [] if mesh.normals else None
    ncol = [] if mesh.colors else None
    ntan = [] if mesh.tangents else None
    njoint = [] if mesh.joints else None
    nweight = [] if mesh.weights else None
    mapping = {}

    def new_vertex(vi, proj):
        key = (vi, proj.key())
        ni = mapping.get(key)
        if ni is not None:
            return ni
        ni = len(npos) // 3
        mapping[key] = ni
        npos.extend(pos[vi * 3:vi * 3 + 3])
        u, v = project_at(vi, proj)
        nuv.extend((u, v))
        if nnorm is not None:
            nnorm.extend(mesh.normals[vi * 3:vi * 3 + 3])
        if ncol is not None:
            ncol.extend(mesh.colors[vi * 4:vi * 4 + 4])
        if ntan is not None:
            ntan.extend(mesh.tangents[vi * 3:vi * 3 + 3])
        if njoint is not None:
            njoint.extend(mesh.joints[vi * 4:vi * 4 + 4])
        if nweight is not None:
            nweight.extend(mesh.weights[vi * 4:vi * 4 + 4])
        return ni

    for t in range(0, len(idx) - 2, 3):
        proj = resolve(face_normal(idx[t], idx[t + 1], idx[t + 2]))
        for k in range(3):
            nidx.append(new_vertex(idx[t + k], proj))
    return MeshData(npos, nnorm, nuv, ncol, nidx, ntan, mesh.name, njoint, nweight)


# ------------------------------------------------------------ 贴图规则 --

class TexWrap:
    """采样环绕规则（值即 GL 采样器语义；字符串便于直接映射）。"""

    REPEAT = 'repeat'
    CLAMP = 'clamp'
    MIRROR = 'mirror'

    GL_NAMES = {REPEAT: 'GL_REPEAT', CLAMP: 'GL_CLAMP_TO_EDGE',
                MIRROR: 'GL_MIRRORED_REPEAT'}

    @classmethod
    def gl_name(cls, wrap):
        return cls.GL_NAMES[wrap]


def wrap_uv(u, wrap=TexWrap.REPEAT):
    """单分量环绕（CPU 端口径校验/软件采样用；GPU 交给采样器）。"""
    if wrap == TexWrap.REPEAT:
        return u - math.floor(u)
    if wrap == TexWrap.CLAMP:
        return 0.0 if u < 0.0 else (1.0 if u > 1.0 else u)
    if wrap == TexWrap.MIRROR:
        t = abs(u) % 2.0
        return t if t <= 1.0 else 2.0 - t
    raise ValueError(f'未知 wrap 模式 {wrap!r}')


class Atlas:
    """网格图集：名字 → 归一化矩形；``remap`` 支持**图集格内平铺**。

    行优先排布，第 0 行在图片**顶部**；矩形按 GL 惯例（v 向上）输出。
    """

    __slots__ = ('names', 'cols', 'rows', '_rects')

    def __init__(self, names, cols=None, rows=None):
        names = list(names)
        if not names:
            raise ValueError('图集至少要有一个名字')
        n = len(names)
        if cols is None and rows is None:
            cols = max(1, int(math.ceil(math.sqrt(n))))
        if rows is None:
            rows = int(math.ceil(n / cols))
        cols = max(1, cols)
        rows = max(1, rows)
        self.names = names
        self.cols, self.rows = cols, rows
        self._rects = {}
        for i, nm in enumerate(names):
            col, row = i % cols, i // cols
            u0, u1 = col / cols, (col + 1) / cols
            v0, v1 = 1.0 - (row + 1) / rows, 1.0 - row / rows
            self._rects[nm] = (u0, v0, u1, v1)

    def rect(self, name):
        """归一化矩形 ``(u0, v0, u1, v1)``。"""
        try:
            return self._rects[name]
        except KeyError:
            raise KeyError(f'图集没有 {name!r}（现有 {self.names}）') from None

    def remap(self, u, v, name, padding=0.0):
        """UV（可为任意大，先取小数=平铺）→ 图集内坐标。

        ``padding`` 为格宽/格高的比例（防相邻格渗色），在格内四周内缩。
        """
        u0, v0, u1, v1 = self.rect(name)
        w, h = u1 - u0, v1 - v0
        iu0, iu1 = u0 + padding * w, u1 - padding * w
        iv0, iv1 = v0 + padding * h, v1 - padding * h
        fu = u - math.floor(u)
        fv = v - math.floor(v)
        return (iu0 + fu * (iu1 - iu0), iv0 + fv * (iv1 - iv0))

    def __repr__(self):
        return f'Atlas({self.names!r}, cols={self.cols}, rows={self.rows})'


def texel_scale(pixels_per_world, texsize):
    """由"像素密度"求 scale：``scale = pixels_per_world / texsize``。

    例：256 px/单位、512 像素贴图 → scale=0.5（UV/世界单位），
    即一张贴图覆盖 2 个世界单位。
    """
    return float(pixels_per_world) / float(texsize)


# ------------------------------------------------------------ 密度体检 --

_DEFAULT_BINS = (0.0, 32.0, 64.0, 96.0, 128.0, 160.0, 192.0, 224.0,
                 256.0, 320.0, 384.0, 512.0, 768.0, 1024.0, float('inf'))


class DensityReport:
    """贴图密度体检报告（world units per UV 口径）。"""

    __slots__ = ('faces', 'valid', 'skipped', 'ratios', 'bins',
                 'median', 'p10', 'p90', 'histogram', 'outliers')

    def __init__(self, faces, ratios, bins, outliers):
        self.faces = faces
        self.valid = len(ratios)
        self.skipped = faces - len(ratios)
        self.ratios = ratios
        self.bins = bins
        self.outliers = outliers
        s = sorted(ratios)
        self.median = _quantile(s, 0.5)
        self.p10 = _quantile(s, 0.1)
        self.p90 = _quantile(s, 0.9)
        hist = {}
        for i in range(len(bins) - 1):
            lo, hi = bins[i], bins[i + 1]
            label = f'{int(lo)}-{int(hi)}' if hi != float('inf') else f'{int(lo)}+'
            hist[label] = 0
        for r in ratios:
            for i in range(len(bins) - 1):
                if bins[i] <= r < bins[i + 1]:
                    lo, hi = bins[i], bins[i + 1]
                    label = f'{int(lo)}-{int(hi)}' if hi != float('inf') else f'{int(lo)}+'
                    hist[label] += 1
                    break
        self.histogram = hist

    def __repr__(self):
        return (f'DensityReport(faces={self.faces}, valid={self.valid}, '
                f'median={self.median:.1f}, p10={self.p10:.1f}, p90={self.p90:.1f}, '
                f'outliers={len(self.outliers)})')


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    f = pos - lo
    return sorted_vals[lo] * (1.0 - f) + sorted_vals[hi] * f


def density_report(mesh, uvs=None, bins=None, outlier_factor=3.0):
    """逐面统计贴图密度（世界单位/UV），返回 :class:`DensityReport`。

    * ``uvs`` 可覆盖（对比两套 UV 时用）；默认取 ``mesh.uvs``；
    * 密度 = ``1 / sqrt(area_uv / area_world)``（正交 UV 时两方向的几何均值）；
    * 离群面 = 密度偏离中位数超过 ``outlier_factor`` 倍的面索引列表。
    """
    uvs = uvs if uvs is not None else mesh.uvs
    if not uvs:
        raise ValueError('网格没有 UV，也没有传入 uvs')
    pos = mesh.positions
    idx = list(mesh.indices) if mesh.indices else list(range(mesh.vertex_count))
    ratios, outliers = [], []
    faces = 0
    for t in range(0, len(idx) - 2, 3):
        faces += 1
        i0, i1, i2 = idx[t], idx[t + 1], idx[t + 2]
        ax, ay, az = pos[i0 * 3], pos[i0 * 3 + 1], pos[i0 * 3 + 2]
        e1 = (pos[i1 * 3] - ax, pos[i1 * 3 + 1] - ay, pos[i1 * 3 + 2] - az)
        e2 = (pos[i2 * 3] - ax, pos[i2 * 3 + 1] - ay, pos[i2 * 3 + 2] - az)
        cx = e1[1] * e2[2] - e1[2] * e2[1]
        cy = e1[2] * e2[0] - e1[0] * e2[2]
        cz = e1[0] * e2[1] - e1[1] * e2[0]
        area = 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
        du1 = uvs[i1 * 2] - uvs[i0 * 2]
        dv1 = uvs[i1 * 2 + 1] - uvs[i0 * 2 + 1]
        du2 = uvs[i2 * 2] - uvs[i0 * 2]
        dv2 = uvs[i2 * 2 + 1] - uvs[i0 * 2 + 1]
        area_uv = 0.5 * abs(du1 * dv2 - du2 * dv1)
        if area <= 1e-9 or area_uv <= 1e-15:
            continue
        ratios.append(1.0 / math.sqrt(area_uv / area))
    med = _quantile(sorted(ratios), 0.5)
    if med > 0.0:
        lo, hi = med / outlier_factor, med * outlier_factor
        outliers = [i for i, r in enumerate(ratios) if r < lo or r > hi]
    return DensityReport(faces, ratios, tuple(bins or _DEFAULT_BINS), outliers)


# ------------------------------------------------------------------ 内部 --

def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot3(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _len3(a):
    return math.sqrt(_dot3(a, a))


def _normalize(a):
    l = _len3(a)
    if l < 1e-300:
        return (0.0, 0.0, 1.0)
    return (a[0] / l, a[1] / l, a[2] / l)
