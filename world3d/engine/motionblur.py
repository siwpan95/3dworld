# -*- coding: utf-8 -*-
"""运动模糊（④-4）—— 相机式**重投影** + 快门加权累积。

## 为什么用重投影而不是"把整屏糊一下"

屏幕空间的"整屏方向模糊"（拿一个全局向量去采样）看起来像蒙了一层纱：**它让画面所有
像素糊得一样多**，而真实的运动模糊里**近处的东西扫过的像素多得多**。

正确做法是**每像素从自己的深度反推世界坐标，再用上一帧的 view-projection 投回去**，
于是运动矢量天然带上深度：

* 相机**平移**时，运动矢量长度 ∝ **1/z**（近的扫得远、远的几乎不动）；
* 相机**只是旋转**时，运动矢量与深度**无关**（整幅一起转，远处的山也糊）；
* 相机不动时，运动矢量恒为 0（**不需要额外的"静止检测"**）。

这三条同时也是最好的判据 —— 全都可以在 headless 下断言，而且第 2、3 条
**恰好是"整屏模糊"做不到的那两件事**（它连深度都没有）。

## 提供什么

* **纯数学**：`reproject`（屏幕 uv + 距离 → 上一帧 uv）、`motion_vector`、
  `shutter_weights`（快门时间内的采样权重）；
* **可注入的 GLSL**：`mb_glsl()` —— 沿运动矢量采样、按深度剔除前景对背景的"拖影污染"。

**合规**：重投影运动模糊是实时渲染的常规做法；独立实现，不复制任何具体仓库代码。
"""
from __future__ import annotations

import math

from world3d.engine.mathkit import mat4_inverse, mat4_mul

__all__ = ['reproject', 'motion_vector', 'shutter_weights', 'mb_glsl',
           'MB_GLSL_UNIFORMS', 'reconstruct_world']


# ------------------------------------------------------------------ 数学 --
def reconstruct_world(uv, dist: float, inv_vp, cam_position=(0.0, 0.0, 0.0)):
    """屏幕 uv + **线性距离** → 世界坐标（相机位置必须给，默认原点）。

    ``uv`` 是 [0,1] 的屏幕坐标（*不是* NDC）；``inv_vp`` 是当前帧的逆 view-projection
    （列主序 16 元组）。做法是拿 NDC 近平面上的点反投出一条**射线方向**，再从相机沿它走
    ``dist``。

    与 `world3d.engine.volumetrics` 里那条 ``_world`` 完全同式 —— 一张距离图、一套重建公式，
    雾 / 景深 / 运动模糊三处共用（别各写一份，那样迟早会漂开）。

    ⚠ 反投出来的是**世界空间里的近平面点**，射线方向必须 ``normalize(该点 − 相机位置)``。
    我第一版直接对那个点做归一化（等于把相机当成原点）—— 后果是"**静止相机也有运动矢量**、
    平移反而算出 0"，正是"静止相机运动矢量恒为 0"这条不变量把它抓出来的。
    """
    if dist <= 0.0:
        raise ValueError(f'距离必须为正，收到 {dist}')
    if len(cam_position) != 3:
        raise ValueError('cam_position 要三个分量')
    ndc_x, ndc_y = uv[0] * 2.0 - 1.0, uv[1] * 2.0 - 1.0
    p = _xform_w(inv_vp, ndc_x, ndc_y, -1.0)          # 世界空间近平面点
    vx, vy, vz = p[0] - cam_position[0], p[1] - cam_position[1], p[2] - cam_position[2]
    n = math.sqrt(vx * vx + vy * vy + vz * vz)
    if n < 1e-12:
        raise ValueError('射线方向退化（相机贴在近平面上？）')
    return (cam_position[0] + vx / n * dist,
            cam_position[1] + vy / n * dist,
            cam_position[2] + vz / n * dist)


def _xform_w(m, x: float, y: float, z: float):
    """列主序 4x4 变换并做透视除法（返回 xyz）。"""
    w = m[3] * x + m[7] * y + m[11] * z + m[15]
    if abs(w) < 1e-12:
        raise ValueError('退化矩阵：w ≈ 0（投影矩阵不可逆或点落在相机平面上）')
    return ((m[0] * x + m[4] * y + m[8] * z + m[12]) / w,
            (m[1] * x + m[5] * y + m[9] * z + m[13]) / w,
            (m[2] * x + m[6] * y + m[10] * z + m[14]) / w)


def reproject(uv, dist: float, inv_vp_current, vp_previous, cam_position):
    """当前屏幕 uv（+ 像素自己的距离）→ **上一帧**的 uv。

    ``cam_position`` 是当前帧的相机世界坐标（重建射线要用它当原点）。
    返回 ``(prev_uv, valid)``：``valid=False`` 表示投到了屏幕外（调用方可据此不采样）。

    ⚠ 这一步**必须逐像素带自己的深度**：只用"一个全局向量"就退化成整屏模糊了。
    """
    if dist <= 0.0:
        raise ValueError(f'距离必须为正，收到 {dist}')
    world = reconstruct_world(uv, dist, inv_vp_current, cam_position)
    w = vp_previous[3] * world[0] + vp_previous[7] * world[1] \
        + vp_previous[11] * world[2] + vp_previous[15]
    if abs(w) < 1e-9:
        return (uv[0], uv[1]), False
    cx = (vp_previous[0] * world[0] + vp_previous[4] * world[1]
          + vp_previous[8] * world[2] + vp_previous[12]) / w
    cy = (vp_previous[1] * world[0] + vp_previous[5] * world[1]
          + vp_previous[9] * world[2] + vp_previous[13]) / w
    prev = (cx * 0.5 + 0.5, cy * 0.5 + 0.5)
    valid = -0.5 <= prev[0] <= 1.5 and -0.5 <= prev[1] <= 1.5
    return prev, valid


def motion_vector(uv, dist: float, inv_vp_current, vp_previous, cam_position):
    """``上一帧 uv − 当前 uv``。**内容在屏幕上移动的方向**（相机右移 ⇒ 内容是往左）。

    ⚠ 这是"把**当前帧**像素投到**上一帧**"（backward reprojection）——
    采样方向取负号即可，别把两个方向搞反（反了画面会糊成一团、方向还看不出错）。
    """
    prev, valid = reproject(uv, dist, inv_vp_current, vp_previous, cam_position)
    return (prev[0] - uv[0], prev[1] - uv[1]), valid


def shutter_weights(n: int, mode: str = 'box') -> tuple:
    """快门开启期间 ``n`` 个采样的权重（和为 1）。

    * ``box``  —— 快门是"开-等-关"，理想情况下每段时间相等（等权）；
    * ``tent`` —— 三角权重，中心最重。用来压"采样数不足时的条带"，代价是
      高频运动会被轻微压低（真实相机的机械快门本来也近似这样）。

    ⚠ **不要**用"每帧只往前抖一点点的随机抖动"来凑等效——那需要时域累积（TAA），
    没有累积就是一张张不同的噪声图，静止画面会"沙沙响"。
    """
    if n < 1:
        raise ValueError(f'采样数至少 1，收到 {n}')
    if mode == 'box':
        w = [1.0 / n] * n
    elif mode == 'tent':
        if n == 1:
            w = [1.0]
        else:
            raw = [1.0 - abs(2.0 * (i + 0.5) / n - 1.0) for i in range(n)]
            s = sum(raw) or 1.0
            w = [v / s for v in raw]
    else:
        raise ValueError(f'未知权重模式 {mode!r}（只支持 box / tent）')
    return tuple(w)


# ------------------------------------------------------------------ GLSL --
#: 必须声明的 uniform（demo 与测试共用）
MB_GLSL_UNIFORMS = (
    'u_mb_depth', 'u_mb_color', 'u_mb_inv_vp_cur', 'u_mb_vp_prev',
    'u_mb_cam', 'u_mb_shutter', 'u_mb_samples', 'u_mb_max_len', 'u_mb_on',
)


def mb_glsl(n: int = 12, fn: str = 'mb_apply', mode: str = 'box') -> str:
    """生成可注入的 GLSL：沿**每像素自己的**运动矢量做加权累积。

    约定：

    * `u_mb_depth` 是场景**线性距离**（与体积雾 / 景深同一张图）；
    * `u_mb_inv_vp_cur` 当前帧逆 view-projection；`u_mb_vp_prev` 上一帧的
      view-projection；`u_mb_cam` 当前帧相机位置；
    * `u_mb_shutter` 是快门比例（0 = 关，1 = 全开）；`u_mb_max_len` 是**像素**上限
      （防止贴脸的物体把整屏拉花）。

    ⚠ 两条必须在代码里说清的事：

    1. **必须逐像素重投影**（用自己的深度）。写成一个全局向量就退化成整屏模糊，
       近处不扫、远处反而糊 —— 一眼假，但"能跑"。
    2. **要按深度剔除"前景拖影污染"**：前景像素的运动矢量很长，采样时会扫到背景，
       于是前景物体在背景上拖出一条亮痕。判据同景深：拿采样点自己的运动量去比
       "它离当前像素还有多远"。
    """
    if n < 1:
        raise ValueError(f'采样数至少 1，收到 {n}')
    w = shutter_weights(n, mode)
    body = ', '.join(f'{v:.6f}' for v in w)
    return f'''// ---- 运动模糊（生成自 `world3d.engine.motionblur`，不要手改）----
// ⚠ 调用方负责写版本声明（本片段**不含**它）
uniform sampler2D u_mb_depth;      // 线性距离（与雾/景深共用同一张）
uniform sampler2D u_mb_color;      // 场景 HDR 色
uniform mat4  u_mb_inv_vp_cur;     // 当前帧逆 view-projection
uniform mat4  u_mb_vp_prev;        // 上一帧 view-projection
uniform vec3  u_mb_cam;            // 当前帧相机位置
uniform float u_mb_shutter;        // 快门比例：0 = 关，1 = 全开
uniform float u_mb_samples;        // 实际采样数（≤ 烘进来的 {n}）
uniform float u_mb_max_len;        // 模糊长度上限（像素）
uniform float u_mb_on;             // 0/1

// 每像素自己的运动矢量（屏幕 UV 空间）。**逐像素带深度**是关键，
// 写成一个全局向量就退化成"整屏模糊"，近处不扫、远处反而糊。
// ⚠ 反投出来的是**世界空间近平面点**（必须先除以 w），射线方向要减去相机位置；
//    直接对它做 normalize 等于把相机当原点 ⇒ 静止相机也会算出一堆假运动矢量。
vec2 {fn}_prev_uv(vec2 uv, float dist) {{
    vec4 np = u_mb_inv_vp_cur * vec4(uv * 2.0 - 1.0, -1.0, 1.0);
    vec3 dir = normalize(np.xyz / max(abs(np.w), 1e-6) - u_mb_cam);
    vec3 world = u_mb_cam + dir * dist;
    vec4 pc = u_mb_vp_prev * vec4(world, 1.0);
    return pc.xy / max(abs(pc.w), 1e-6) * 0.5 + 0.5;
}}

// 快门权重（CPU 侧 `shutter_weights` 同式，逐字对应）
const int   {fn}_N = {n};
const float {fn}_W[{n}] = float[{n}]({body});

vec3 {fn}(vec2 uv) {{
    float z0 = texture(u_mb_depth, uv).r;
    vec3 base = texture(u_mb_color, uv).rgb;
    if (u_mb_on < 0.5 || u_mb_shutter < 0.001) return base;
    vec2 prev = {fn}_prev_uv(uv, z0);
    vec2 mv = (prev - uv) * u_mb_shutter;

    // 长度上限（**像素**）：贴脸物体的矢量能横跨整屏，不夹会把画面拉花。
    // ⚠ 像素长度 = `length(mv * 分辨率)`。我第一版写成 `length(mv) / height * 1000`
    // —— 量纲就是错的（uv 长度除高度再乘 1000 什么都不是），夹取因此形同虚设。
    float plen = length(mv * vec2(textureSize(u_mb_color, 0)));
    if (plen > u_mb_max_len) mv *= u_mb_max_len / max(plen, 1e-6);

    vec3 sum = vec3(0.0);
    float wsum = 0.0;
    int count = int(min(u_mb_samples, float({fn}_N)));
    for (int i = 0; i < {fn}_N; ++i) {{
        if (i >= count) break;                       // GLSL 330：循环边界必须是常量
        float t = (float(i) + 0.5) / float(count) - 0.5;   // 以当前帧为中心
        vec2 suv = clamp(uv + mv * t, vec2(0.0), vec2(1.0));
        // 前景拖影污染：采样点的距离若明显近于当前像素，它本来不该"扫"到这里
        float sz = texture(u_mb_depth, suv).r;
        float reach = abs(t) * length(mv) * 2.0;
        float depth_gap = max(0.0, z0 - sz) / max(z0, 1e-3);
        if (depth_gap > reach + 0.35) continue;
        float w = {fn}_W[i];
        sum += texture(u_mb_color, suv).rgb * w;
        wsum += w;
    }}
    return wsum > 0.0 ? sum / wsum : base;
}}
'''
