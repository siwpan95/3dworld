# -*- coding: utf-8 -*-
"""级联阴影贴图（CSM，④-1）—— 把一张阴影图切成 N 级，按视距分配精度。

## 为什么需要它

单张阴影图的精度是**固定**的：它拿正交光盒去罩住整段可见距离，纹素在世界里的尺寸
= 2·半径 / 分辨率。半径 100、分辨率 2048 ⇒ 每纹素约 0.1 世界单位 —— 近处的物体
边缘会糊成锯齿；反过来把半径压到 20 保近处，远处就整片没阴影。

CSM 的做法是**按视距分区**：第 0 级只罩最近的一段（半径小 ⇒ 纹素细 ⇒ 近处锐），
第 1 级往后逐级变大。各级共用同一套正交光方向，所以接缝处的明暗是连续的。

## 本模块提供什么

* **纯数学**（全部可用 Python 直接断言，不需要 GL）：

  - `split_scheme`   —— 分割距离（PSSM 的线性/对数混合，`lambda` 调偏向）
  - `cascade_corners`—— 一段视锥的 8 个世界角点
  - `light_ortho_box`—— 8 个角点 → **紧贴**它们的正交光空间矩阵（可加 padding）
  - `texel_snap`     —— 把光空间平移吸附到纹素栅格（**消抖动的关键**）
  - `select_cascade` —— 视距 → 级下标
  - `csm_matrices`   —— 一站式：一次算出各级的 `(vp, split, radius, center)`
* **可注入的 GLSL**：`csm_glsl(n)` 生成 uniform 声明 + 逐级 PCF 采样 + 选级函数。

## 两个容易静默出错的点（写成代码而不是注释）

1. **GL 3.3 不允许用非常量下标索引 `sampler2D` 数组**（GL 4.0 才放开动态 uniform 索引）
   ⇒ 选级只能展开成 `if / else if` 链。写成 `u_csm_tex[i]` 在多数驱动上**能编译、
   行为未定义**（有的直接整屏黑）—— 这类"能编译但跑错"最难查。
2. **不做纹素吸附**（`texel_snap`）⇒ 相机一动，正交光盒中心跟着漂，同一纹素采样到的
   世界位置随之改变，阴影边缘**闪烁/爬行**。这条在静止截图里完全看不出来。

**合规**：思路来自公开的 PSSM / CSM 文献（Zhang 2006 的分区方案、Engel 的 CSM 实践），
**独立实现**，不复制任何具体仓库的代码。
"""
from __future__ import annotations

import math

from engine.mathkit import (look_at_matrix, mat4_identity, mat4_inverse,
                            mat4_mul, mat4_to_Mat4)

__all__ = ['MAX_CASCADES', 'IDENTITY', 'split_scheme', 'select_cascade',
           'cascade_corners', 'mat4_transform_point', 'light_ortho_box',
           'texel_snap', 'csm_matrices', 'csm_uniforms', 'csm_glsl',
           'mat4_as_Mat4']

#: 级数上限。GLSL 侧是**展开**写死的（见模块 docstring）；4 级够覆盖绝大多数场景：
#: 再往上"每级分到的分辨率"下降得比"半径缩小"快，收益为负。
MAX_CASCADES = 4

#: 单位矩阵（导出给调用方做"没开 CSM"的兜底，免得各自去 import mathkit）
IDENTITY = mat4_identity()


# ------------------------------------------------------------------ 数学基元 --
def mat4_transform_point(m, x: float, y: float, z: float):
    """列主序 4x4 变换一个点（w = 1）。视图/正交矩阵下 w 恒为 1，不做透视除法。"""
    w = m[3] * x + m[7] * y + m[11] * z + m[15]
    if abs(w) < 1e-12:
        w = 1.0
    return ((m[0] * x + m[4] * y + m[8] * z + m[12]) / w,
            (m[1] * x + m[5] * y + m[9] * z + m[13]) / w,
            (m[2] * x + m[6] * y + m[10] * z + m[14]) / w)


def _ortho(left, right, bottom, top, near, far):
    """列主序正交投影（与 `Mat4.orthogonal_projection` 同约定）。

    自己写而不是转一次 pyglet：本模块其余部分都在 `mathkit` 的**元组矩阵**世界里，
    中间混进一个 `Mat4` 只会让调用方也跟着转换。
    """
    rl = (right - left) or 1e-9
    tb = (top - bottom) or 1e-9
    fn = (far - near) or 1e-9
    return (2.0 / rl, 0.0, 0.0, 0.0,
            0.0, 2.0 / tb, 0.0, 0.0,
            0.0, 0.0, -2.0 / fn, 0.0,
            -(right + left) / rl, -(top + bottom) / tb, -(far + near) / fn, 1.0)


# ------------------------------------------------------------------ 分割 --
def split_scheme(z_near: float, z_far: float, n: int, lambda_: float = 0.75):
    """PSSM 混合分割：返回 ``n + 1`` 个**视距**（从 ``z_near`` 到 ``z_far``）。

    ``lambda_ = 0`` → 纯线性（各级等宽）；``lambda_ = 1`` → 纯对数（近处极密）。
    实践中取 0.7~0.8：既让近处足够密，又不至于把最后一级拉得太远。

    ``split_i = λ·near·(far/near)^(i/n) + (1-λ)·(near + (far-near)·i/n)``
    """
    if n < 1:
        raise ValueError(f'级数至少要 1，收到 {n}')
    if not 0.0 <= lambda_ <= 1.0:
        raise ValueError(f'lambda 必须在 [0,1]，收到 {lambda_}')
    if not 0.0 < z_near < z_far:
        raise ValueError(f'要求 0 < z_near < z_far，收到 {z_near} / {z_far}')
    ratio = z_far / z_near
    out = []
    for i in range(n + 1):
        f = i / n
        log_part = z_near * (ratio ** f)
        lin_part = z_near + (z_far - z_near) * f
        out.append(lambda_ * log_part + (1.0 - lambda_) * lin_part)
    return tuple(out)


def select_cascade(z_view: float, splits) -> int:
    """视空间 z → 级下标。

    视空间里相机朝 **-z**，所以距离 = ``-z_view``。超出最后一级的按最后一级算
    （宁可精度不够，也不能"没有阴影"—— 那是更刺眼的表现错误）。
    """
    dist = -float(z_view)
    for i in range(len(splits) - 1):
        if dist <= splits[i + 1]:
            return i
    return len(splits) - 2


# ------------------------------------------------------------------ 视锥角点 --
def cascade_corners(view, z0: float, z1: float, tan_half_fov_y: float,
                    aspect: float):
    """把视锥 ``[z0, z1]`` 这段的 8 个角点算到**世界坐标**（返回 8 个三元组）。

    ``view`` 是列主序视图矩阵（`engine.mathkit` 约定），内部取逆。
    视空间角点：``(±tan·aspect·d, ±tan·d, -d)``，``d ∈ {z0, z1}``。
    """
    if not 0.0 < z0 < z1:
        raise ValueError(f'要求 0 < z0 < z1，收到 {z0} / {z1}')
    if tan_half_fov_y <= 0.0 or aspect <= 0.0:
        raise ValueError('tan_half_fov_y 与 aspect 必须为正')
    inv = mat4_inverse(view)
    hx, hy = tan_half_fov_y * aspect, tan_half_fov_y
    out = []
    for d in (z0, z1):
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                out.append(mat4_transform_point(inv, sx * hx * d, sy * hy * d, -d))
    return out


# ------------------------------------------------------------------ 光盒拟合 --
def light_ortho_box(corners, light_dir, up=(0.0, 1.0, 0.0), pad: float = 0.0,
                    z_extra=None, depth: float = 400.0):
    """8 个世界角点 → 紧贴它们的正交光空间矩阵。

    做法（Engel 的 "fit to frustum"）：

    1. 取 8 点中心作光轴目标，从 ``中心 + 方向 × depth`` 处 look-at；
    2. 把 8 点投到光空间，取 x/y 包围盒 —— 取**两轴里大的那个半宽**做成**正方盒**
       （长方盒会让纹素在 x/y 上尺寸不等，PCF 的采样半径就没法用一个标量表达）；
    3. z 方向按实际包围盒再向两侧各撑 ``z_extra``（默认 = 半宽），好把"在光与视锥
       **之间**"的投影物也包进来 —— 否则处在光盒外的柱子不投影，表现为
       "影子在远处凭空断掉"。

    返回 ``(vp, radius, center, (near, far))``：`vp` 是列主序 `投影·视图`，
    `radius` 是正交半宽（纹素在世界里的尺寸 = ``2·radius / 分辨率``），
    `center` 是光盒中心。
    """
    if len(corners) != 8:
        raise ValueError(f'要 8 个角点，收到 {len(corners)}')
    cx = sum(c[0] for c in corners) / 8.0
    cy = sum(c[1] for c in corners) / 8.0
    cz = sum(c[2] for c in corners) / 8.0
    dl = math.sqrt(sum(c * c for c in light_dir)) or 1.0
    lx, ly, lz = (c / dl for c in light_dir)
    eye = (cx + lx * depth, cy + ly * depth, cz + lz * depth)
    view = look_at_matrix(eye, (cx, cy, cz), up)
    xs, ys, zs = [], [], []
    for c in corners:
        p = mat4_transform_point(view, c[0], c[1], c[2])
        xs.append(p[0])
        ys.append(p[1])
        zs.append(p[2])
    mx = (min(xs) + max(xs)) / 2.0
    my = (min(ys) + max(ys)) / 2.0
    half = max((max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0) + pad
    half = max(half, 1e-3)                     # 退化（点状视锥）时别给出 0 半径
    extra = half if z_extra is None else z_extra
    z_lo, z_hi = min(zs), max(zs)              # 光空间里两者都是负数
    near = max(1e-3, -(z_hi + extra))
    far = max(near + 1e-3, -(z_lo - extra))
    ortho = _ortho(mx - half, mx + half, my - half, my + half, near, far)
    return mat4_mul(ortho, view), half, (cx, cy, cz), (near, far)


def texel_snap(light_vp, size: int):
    """把光空间平移**吸附到纹素栅格**，返回新的 `vp`。

    相机一动，正交光盒中心就跟着动一点点，同一个纹素采样到的世界位置随之漂移 ⇒
    阴影边缘**闪烁/爬行**（shimmer）。做法：把世界原点投到光空间 NDC，算出"离最近纹素
    中心的偏移"，再把这段偏移加回投影矩阵的平移分量。正交投影 w 恒为 1，所以直接加。

    **这是 CSM 里最容易漏、但一漏就整片抖的一步** —— 而且静止截图完全看不出来。
    """
    if size < 1:
        raise ValueError(f'分辨率至少要 1，收到 {size}')
    ox, oy, _oz = mat4_transform_point(light_vp, 0.0, 0.0, 0.0)
    texel = 2.0 / size                         # NDC 跨度是 2
    dx = round(ox / texel) * texel - ox
    dy = round(oy / texel) * texel - oy
    m = list(light_vp)
    m[12] += dx
    m[13] += dy
    return tuple(m)


def csm_matrices(view, z_near: float, z_far: float, tan_half_fov_y: float,
                 aspect: float, light_dir, n: int = 3, lambda_: float = 0.75,
                 size: int = 2048, pad: float = 0.0, snap: bool = True,
                 up=(0.0, 1.0, 0.0)):
    """一站式：一次算出全部级联。

    返回 ``list[dict]``，每项：``{'index', 'z_near', 'z_far', 'split', 'vp',
    'radius', 'center', 'texel', 'ortho_near', 'ortho_far'}``
    （`vp` 已是列主序元组，`snap=True` 时已吸附到纹素栅格）。
    """
    if n > MAX_CASCADES:
        raise ValueError(f'级数上限 {MAX_CASCADES}（GLSL 侧是展开写死的），收到 {n}')
    if n < 1:
        raise ValueError(f'级数至少要 1，收到 {n}')
    splits = split_scheme(z_near, z_far, n, lambda_)
    out = []
    for i in range(n):
        corners = cascade_corners(view, splits[i], splits[i + 1],
                                 tan_half_fov_y, aspect)
        vp, radius, center, zr = light_ortho_box(corners, light_dir, up=up, pad=pad)
        if snap:
            vp = texel_snap(vp, size)
        out.append(dict(index=i, z_near=splits[i], z_far=splits[i + 1],
                        split=splits[i + 1], vp=vp, radius=radius,
                        center=center, texel=2.0 * radius / size,
                        ortho_near=zr[0], ortho_far=zr[1]))
    return out


def csm_uniforms(cascades, prefix: str = 'u_csm_vp'):
    """把 `csm_matrices` 的结果摊成可直接上传的形状。

    返回 ``(uniforms, splits)``：``uniforms`` 是 ``{'u_csm_vp0': [16 个 float], ...}``，
    ``splits`` 是各级远端视距。

    ⚠ **每级一个独立的 `mat4` uniform，不用 `mat4[]` 数组**。原因是实测出来的：
    pyglet 2.1.16 对 `uniform mat4 u_vp[4]` 的 `_c_array` 只按 4 个 float 分配
    （把 GL 报的"数组长度 4"当成了"分量数"），于是 `prog['u_csm_vp'] = [64 个 float]`
    直接 `AssertionError: Size of data (64) does not match size of the uniform: 4.`。
    `float u_csm_split[4]` 这类**标量数组**没这个问题（长度 1 × 数组 4 = 4 ✓）。
    选级本来就是展开的 if/else 链，独立矩阵正好和它一致。
    """
    uniforms = {}
    splits = []
    for i, c in enumerate(cascades):
        uniforms[f'{prefix}{i}'] = list(c['vp'])
        splits.append(c['split'])
    return uniforms, splits


def mat4_as_Mat4(m):
    """列主序元组 → ``pyglet.math.Mat4``（给 ``prog[...] = ...`` 用）。"""
    return mat4_to_Mat4(m)


# ------------------------------------------------------------------ GLSL --
def csm_glsl(n: int = 3, fn: str = 'csm_shadow', samples: int = 3) -> str:
    """生成可注入的 GLSL 片段。

    约定（调用方只要满足这几条就能用）：

    * 顶点着色器把世界坐标与视空间 z 传下来（本模块不关心你怎么传）；
    * 深度图是 **N 张独立的 `sampler2D`**（不是 `sampler2DArray` —— 那样要么用几何
      着色器写 `gl_Layer`，要么用 pyglet 没暴露的 `FramebufferTextureLayer`）；
    * 调用 ``csm_shadow(world_pos, z_view, ndl)`` 拿到 ``0（全亮）…1（全暗）``。

    ⚠ **选级必须是 if/else 链**：见模块 docstring 第 1 条。
    """
    if not 1 <= n <= MAX_CASCADES:
        raise ValueError(f'级数必须在 1~{MAX_CASCADES}，收到 {n}')
    k = samples if samples % 2 else samples + 1        # PCF 核必须是奇数边
    r = k // 2
    lines = [f'#define CSM_CASCADES {n}']
    # 每级一个独立 mat4（不用 mat4[] 数组，理由见 `csm_uniforms` 的 docstring）
    lines += [f'uniform mat4 u_csm_vp{i};' for i in range(n)]
    lines += [f'uniform float u_csm_split[{n}];     // 各级远端（视距，正数）',
              'uniform float u_csm_pcf;              // PCF 采样半径（单位：纹素）']
    lines += [f'uniform sampler2D u_csm_tex{i};' for i in range(n)]
    lines.append(f'''
// 单级：投到光空间 → {k}x{k} PCF。返回 1 = 处于阴影。
float _{fn}_one(sampler2D tex, mat4 vp, vec3 wp, float ndl) {{
    vec4 lp = vp * vec4(wp, 1.0);
    vec3 p = lp.xyz * 0.5 + 0.5;
    if (p.x < 0.0 || p.x > 1.0 || p.y < 0.0 || p.y > 1.0 || p.z > 1.0) {{
        return 0.0;                       // 光盒外：宁可漏，也不整片黑
    }}
    float bias = max(0.0012, 0.006 * (1.0 - ndl));   // 斜率偏移，压自阴影 acne
    float sz = 1.0 / float(textureSize(tex, 0).x);
    float lit = 0.0;
    for (int i = -{r}; i <= {r}; ++i) {{
        for (int j = -{r}; j <= {r}; ++j) {{
            float d = texture(tex, p.xy + vec2(i, j) * sz * u_csm_pcf).r;
            lit += (p.z - bias > d) ? 0.0 : 1.0;
        }}
    }}
    return 1.0 - lit / float({k * k});
}}''')
    lines.append(f'''
// 选级：**展开**成 if/else 链（GL 3.3 不允许动态下标索引 sampler2D 数组）
float {fn}(vec3 wp, float z_view, float ndl) {{
    float dist = -z_view;                 // 视空间里相机朝 -z
    if (dist <= u_csm_split[0]) return _{fn}_one(u_csm_tex0, u_csm_vp0, wp, ndl);''')
    for i in range(1, n - 1):
        lines.append(f'    else if (dist <= u_csm_split[{i}]) '
                     f'return _{fn}_one(u_csm_tex{i}, u_csm_vp{i}, wp, ndl);')
    if n > 1:
        lines.append(f'    return _{fn}_one(u_csm_tex{n - 1}, u_csm_vp{n - 1}, '
                     f'wp, ndl);')
    lines.append('}')
    return '\n'.join(lines)
