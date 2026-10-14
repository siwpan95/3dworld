# -*- coding: utf-8 -*-
"""景深（DoF，④-3）—— 弥散圆 / 超焦距 / 景深范围 / 散景采样盘。

## 这一层在算什么

真实相机的"虚化"不是"高斯模糊背景"，而是**每一点按弥散圆（CoC）大小摊成一个圆盘**。
弥散圆直径（薄透镜近似）::

    CoC = A·f·|z − z_f| / ( z·(z_f − f) )        A = 光圈直径 = f/N

* ``z = z_f``（合焦面）⇒ CoC = 0；
* ``z < z_f``（前景）⇒ **负**；``z > z_f``（背景）⇒ **正**；
* ``z → ∞`` ⇒ CoC 趋于渐近值 ``A·f/(z_f − f)``（**不会无限增大**）；
* ``z → 0`` ⇒ CoC → ∞。

把"多大算清晰"定成一个**容限** ``c``（通常取 0.03mm 的"焦深"），就得到两个工程量：

* **超焦距** ``H = f²/(N·c) + f``：对焦在 H 上时，从 ``H/2`` 到无穷远全部"清晰"；
* **景深范围** ``near/far``：给定对焦距离与光圈，真正清晰的那一段。

这三个量之间有**可断言的恒等式**（本模块的测试就钉它们）：
``focus = H`` ⇒ ``far = ∞`` 且 ``near = H/2``；且在 near/far 两处算出的 CoC 正好等于容限 ``c``。

## 散景（bokeh）

采样盘必须**面积均匀**（否则圆盘中心偏亮、虚化看起来像"脏点"）。用黄金角螺旋，
点数与光圈叶片数可调（``blades ≥ 3`` 时把点夹到正多边形内 ⇒ 六边形散景）。

## 一个必须写进代码的坑

**前景的弥散圆会"糊到"它后面的物体上**（前景的圆盘半径比后景大得多，单纯 gather
会把前景颜色抹到背景上，边缘出现一圈"晕"）。标准的做法是：采样时按**双方 CoC**
判断这个采样点是否真的覆盖到当前像素，不满足就剔除。

**合规**：CoC / 超焦距是 19 世纪的光学公式；散景采样是实时渲染的常规做法。
**独立实现**，不复制任何具体仓库代码。
"""
from __future__ import annotations

import math

__all__ = ['circle_of_confusion', 'coc_pixels', 'hyperfocal', 'dof_limits',
           'bokeh_disk', 'dof_glsl', 'DOF_GLSL_UNIFORMS']


# ------------------------------------------------------------------ 光学 --
def circle_of_confusion(z: float, focus: float, aperture: float, focal: float):
    """薄透镜近似的弥散圆**直径**（单位与 ``z/focus/focal`` 一致，``aperture`` 是光圈**直径**）。

    带上符号：``z < focus`` 为负（前景）、``z > focus`` 为正（背景）、合焦面为 0。
    符号在着色器里有用（前景要 clamp，见模块 docstring 的坑）。
    """
    if z <= 0.0:
        raise ValueError(f'物距必须为正，收到 {z}')
    if focus <= 0.0 or focal <= 0.0 or aperture <= 0.0:
        raise ValueError('focus / focal / aperture 都必须为正')
    # ⚠ 必须先拦这条：`focus < focal` 时公式**还能算出一个数**（分母不为 0），
    # 但那对应"合焦面在镜头内部"这种物理上不存在的配置 —— 数值不会报错，
    # 只会给出一个符号相反的 CoC。测试里最早就是用它把 z=2 判成"背景"的。
    if focus <= focal:
        raise ValueError(f'对焦距离必须大于焦距（{focus} <= {focal}）')
    denom = z * (focus - focal)
    if abs(denom) < 1e-12:
        raise ValueError('分母退化（z 或 focus−focal 太接近 0）')
    return aperture * focal * (z - focus) / denom


def coc_pixels(z: float, focus: float, aperture: float, focal: float,
               sensor_height: float, image_height: int) -> float:
    """弥散圆的**像素**直径（工程上用得上：超过 ~1 像素才看得出虚化）。"""
    if sensor_height <= 0.0 or image_height <= 0:
        raise ValueError('sensor_height / image_height 必须为正')
    return (circle_of_confusion(z, focus, aperture, focal)
            / sensor_height * image_height)


def hyperfocal(focal: float, f_number: float, coc_limit: float) -> float:
    """超焦距 ``H = f²/(N·c) + f``（对焦在 H 上时，``H/2`` 到 ∞ 都"清晰"）。"""
    if focal <= 0.0 or f_number <= 0.0 or coc_limit <= 0.0:
        raise ValueError('focal / f_number / coc_limit 都必须为正')
    return focal * focal / (f_number * coc_limit) + focal


def dof_limits(focus: float, focal: float, f_number: float, coc_limit: float):
    """景深范围 ``(near, far)``；``focus >= H`` 时 ``far = inf``。

    标准式（Wikipedia「景深」条目同款）::

        near = s·(H − f) / (H + s − 2f)
        far  = s·(H − f) / (H − s)          s ≥ H 时 far = ∞

    ⚠ 分母里的 **``2f`` 不能省**。我第一版写成 ``H + (s − f)``，在 ``s = H`` 时
    得到 ``H(H−f)/(2H−f)`` —— 比正确的 ``H/2`` 差 0.08%，而"在 near/far 处算出的
    CoC 正好等于容限"这条恒等式也只剩 0.17% 的精度。改成标准式后**两边都精确成立**，
    测试因此可以从"差不多"收紧到 1e-9。
    """
    if focus <= focal:
        raise ValueError(f'对焦距离必须大于焦距（{focus} <= {focal}）')
    H = hyperfocal(focal, f_number, coc_limit)
    near_den = H + focus - 2.0 * focal
    near = focus * (H - focal) / near_den if near_den > 0 else focus
    far_den = H - focus
    far = (focus * (H - focal) / far_den) if far_den > 0 else math.inf
    return near, far


# ------------------------------------------------------------------ 散景盘 --
def bokeh_disk(n: int = 16, blades: int = 0, rotation: float = 0.0):
    """单位圆盘内的**面积均匀**采样点（黄金角螺旋）；``blades >= 3`` 时夹到正多边形内。

    ``rotation`` 是整盘旋转（度）—— 让"转动镜头"的动画可复现（不用随机数）。

    面积均匀很重要：均匀取**极角**会让盘心堆点 ⇒ 虚化的亮斑中心偏亮、看起来像脏点。
    这里取 ``r = sqrt((i+0.5)/n)``（半径按面积反变换）。
    """
    if n < 1:
        raise ValueError(f'n 至少 1，收到 {n}')
    if blades and blades < 3:
        raise ValueError(f'光圈叶片数要么 0（圆形）要么 ≥3，收到 {blades}')
    ga = math.pi * (3.0 - math.sqrt(5.0))          # 黄金角
    rad = math.radians(rotation)
    out = []
    for i in range(n):
        r = math.sqrt((i + 0.5) / n)
        a = i * ga + rad
        x, y = r * math.cos(a), r * math.sin(a)
        if blades:
            x, y = _clamp_to_polygon(x, y, blades, rad)
        out.append((x, y))
    return out


def _clamp_to_polygon(x: float, y: float, blades: int, phase: float):
    """把点夹进"外接圆半径 1、边数 blades、旋转 phase"的正多边形内。

    正多边形的内切圆半径 = cos(π/blades)；实现方式是**按角度找最近边的法向外推**，
    而不是逐边裁剪（后者会把点推来推去、破坏面积均匀）。
    """
    ang = math.atan2(y, x) - phase
    sect = 2.0 * math.pi / blades
    k = round(ang / sect)
    # 该边中点方向（法向）与内切圆半径
    nx, ny = math.cos(k * sect + phase), math.sin(k * sect + phase)
    inr = math.cos(math.pi / blades)
    proj = x * nx + y * ny
    if proj > inr:
        x, y = x + (inr - proj) * nx, y + (inr - proj) * ny
    return x, y


# ------------------------------------------------------------------ GLSL --
#: 顶点/片段里必须声明的 uniform（demo 与测试共用，避免两边漂开）
DOF_GLSL_UNIFORMS = (
    'u_dof_depth', 'u_dof_color', 'u_dof_focus', 'u_dof_aperture',
    'u_dof_focal', 'u_dof_max_coc', 'u_dof_coc_scale', 'u_dof_on',
)


def dof_glsl(n: int = 16, fn: str = 'dof_apply', blades: int = 0,
             disk=None) -> str:
    """生成可注入的 GLSL：按弥散圆做 gather（散景盘采样）并返回虚化后的颜色。

    约定：

    * `u_dof_depth` 是**线性距离**（与 `world3d.engine.volumetrics` 用的是同一张图 —— 一张距离图
      给"雾 + 景深"两用，别各画一张）；
    * `u_dof_color` 是场景 HDR 色；
    * 采样点由 **CPU 侧** `bokeh_disk(n, blades)` 生成后**烘进着色器常量**
      （不用随机数：静止画面不该"沙沙响"，剧照要可复现）。

    ⚠ 两条容易写错、且**只在特定画面上才看得出来**的：

    1. **前景不能糊到背景上**：前景的 CoC 远大于背景，单纯 gather 会把前景颜色抹到
       背景上（物体边缘出现一圈"晕"）。这里按**采样点自己的 CoC 是否能够到当前像素**
       来剔除（`_reach`）。
    2. **近场 CoC 要夹住上限**：`CoC ∝ 1/z`，贴近相机时它会爆掉 ⇒ 采样半径失控。
       必须 `clamp(max_coc)`。
    """
    if n < 1:
        raise ValueError(f'采样数至少 1，收到 {n}')
    pts = bokeh_disk(n, blades) if disk is None else list(disk)
    if len(pts) != n:
        raise ValueError(f'disk 点数 {len(pts)} 与 n={n} 不符')
    body = '\n'.join(f'    vec2({x:.6f}, {y:.6f}),' for x, y in pts).rstrip(',')
    return f'''// ---- 景深 DoF（生成自 `world3d.engine.dof`，不要手改）----
// ⚠ 调用方负责写版本声明（片段里重复声明版本号是编译错误；本片段**不含**它）
uniform sampler2D u_dof_depth;     // 线性距离（与体积雾共用同一张，别各画一张）
uniform sampler2D u_dof_color;     // 场景 HDR 色
uniform float u_dof_focus;         // 对焦距离（世界单位）
uniform float u_dof_aperture;      // 光圈直径（世界单位；越大景深越浅）
uniform float u_dof_focal;         // 焦距（世界单位）
uniform float u_dof_max_coc;       // 弥散圆上限（像素）—— 近场不夹会失控
uniform float u_dof_coc_scale;     // "世界单位 → 像素"的换算系数
uniform float u_dof_on;            // 0/1

// 带符号的弥散圆（像素）：<0 前景、>0 背景、=0 合焦
float {fn}_coc(float z) {{
    float denom = z * (u_dof_focus - u_dof_focal);
    float coc = (abs(denom) < 1e-6)
        ? 0.0
        : u_dof_aperture * u_dof_focal * (z - u_dof_focus) / denom;
    return clamp(coc * u_dof_coc_scale, -u_dof_max_coc, u_dof_max_coc);
}}

// 散景盘（CPU 侧烘进来的常量；圆形 or 正多边形）
const int   {fn}_N = {n};
const vec2  {fn}_DISK[{n}] = vec2[{n}](
{body}
);

vec3 {fn}(vec2 uv) {{
    float z0 = texture(u_dof_depth, uv).r;
    vec3 base = texture(u_dof_color, uv).rgb;
    if (u_dof_on < 0.5) return base;
    float coc0 = {fn}_coc(z0);
    float radius = abs(coc0);
    if (radius < 0.75) return base;              // 不到 3/4 像素不算虚化（省一半采样）

    vec2 texel = 1.0 / vec2(textureSize(u_dof_color, 0));
    vec3 sum = base;
    float wsum = 1.0;
    for (int i = 0; i < {fn}_N; ++i) {{
        vec2 off = {fn}_DISK[i] * radius * texel;
        vec2 suv = clamp(uv + off, vec2(0.0), vec2(1.0));
        float sz = texture(u_dof_depth, suv).r;
        float cocs = {fn}_coc(sz);
        // ① 前景不能糊到背景上：只有"采样点自己的圆盘能够到当前像素"才计入。
        //    漏掉这条 ⇒ 前景物体边缘在背景上留一圈晕。
        float reach = float(i + 1) / float({fn}_N) * radius;
        if (abs(cocs) < reach) continue;
        // ② 权重按 CoC 大小加权（大 CoC 的样本更"虚"，理应占更大权重）
        float w = max(abs(cocs), 1e-3);
        sum += texture(u_dof_color, suv).rgb * w;
        wsum += w;
    }}
    return sum / wsum;
}}
'''
