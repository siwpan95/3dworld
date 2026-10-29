# -*- coding: utf-8 -*-
"""体积雾与光轴（④-2）—— 高度雾的**解析积分** + 单次散射 + Henyey-Greenstein 相函数。

## 为什么值得单独做一层

体积雾最容易写成"沿射线采几步、每步乘 `exp(-密度·dt)`" —— 能跑，但有两个硬问题：

1. **高度雾的解析积分被浪费**。真实雾的密度随高度指数衰减 `d(y) = ρ·exp(-k·(y-base))`，
   沿一条射线做**逐步矩形求和**会在低步数时出现明显的**层状条纹**（每步密度取哪一点的
   值？取起点偏亮、取中点偏向、取终点偏暗）；而这一段的积分**有闭式解**：

       ∫₀¹ ρ·exp(-k·(y₀+Δy·t−base)) dt = ρ·dt·exp(−k·(y₀−base))·(1−exp(−k·Δy))/(k·Δy)

   `height_fog_segment()` 就是这个闭式。**它与步长无关**，于是"调步数只影响散射采样精度、
   不影响雾的浓度"，画面不会因为改步数而整体变亮/变暗。

2. **相函数不能省**。各向同性散射（g=0）看不见"光轴"；真实雾的强前向散射
   （g≈0.7~0.9）才是"太阳在柱子后面出现光柱"的成因。`henyey_greenstein()` 就是它。

3. **"一层均匀灰"不是雾**。前两条做对了，画面仍然是一条从地面往上均匀变淡的灰 ——
   真实雾有团块、有空洞、有被光穿出来的絮。`u_fog_noise` 就是那层 3D 密度调制：
   每个采样段用 `fbm` 乘一个**均值恰为 1** 的因子，于是"开关它不整体改变浓度，
   只把灰变成絮"。它默认 0（关闭），不开就不付噪声的 ALU。

> ⚠ 开噪声后**浓度对步数有温和依赖**（噪声按段中点采样，步数越细越接近真值）——
> 上面第 1 条"改步数不改变浓度"只对纯高度雾成立。这是所有"3D 噪声 + raymarch"
> 的共同性质，不是本实现的缺陷；但把它当成 bug 去查会白查半天，所以写在这里。

## 提供什么

* **纯数学**（全部可 headless 断言）：`height_fog_segment` / `height_fog_column` /
  `henyey_greenstein` / `step_weights` / `dither_offset`；
* **可注入的 GLSL**：`volumetric_glsl()` —— 沿射线推进、在每步查阴影图求"被遮挡多少"、
  按相函数加权累加散射，**并且用的是同一个解析积分**（CPU 与 GPU 同式）；
* **可复用的 GLSL 片段**：`noise3_glsl()` —— Perlin 2002 的 3D 噪声（12 梯度 + 五次 fade），
  本模块自己拼进体积雾，别处（实体纹理 / SDF 密度场）也能直接用。

**合规**：思路来自公开文献（高度雾解析积分是 Frostbite/UE 的经典做法；HG 相函数是
1888 年的老东西），**独立实现**，不复制任何具体仓库代码。3D 噪声的梯度集与 fade
多项式出自 Ken Perlin《Improving Noise》(SIGGRAPH 2002) 的公开定义（借用范围见
`world3d/logic/noise.py` 的借鉴记账；Perlin 未对 classic noise 申请专利）。
"""
from __future__ import annotations

import math

__all__ = ['height_fog_segment', 'height_fog_column', 'henyey_greenstein',
           'step_weights', 'dither_offset', 'volumetric_glsl', 'noise3_glsl',
           'FOG_GLSL_UNIFORMS', 'NOISE3_NORM']

#: 顶点/片段着色器里**必须**声明的 uniform 名字（demo 与测试共用，避免两边漂开）
FOG_GLSL_UNIFORMS = (
    'u_fog_depth', 'u_inv_vp', 'u_fog_cam', 'u_fog_light_dir', 'u_fog_light_color',
    'u_fog_density', 'u_fog_falloff', 'u_fog_base', 'u_fog_ambient',
    'u_fog_phase_g', 'u_fog_steps', 'u_fog_max_dist', 'u_fog_analytic', 'u_fog_shafts',
    'u_fog_noise', 'u_fog_noise_scale',
)

#: 交错梯度噪声的两个魔数（CPU 侧 `dither_offset` 与 GLSL 辅助函数**必须同式**，
#: 测试里逐字对比 —— 两处漂开的话"抖动量"会悄无声息地不等价）
IGN_A, IGN_B, IGN_C = 0.06711056, 0.00583715, 52.9829189


# ------------------------------------------------------------------ 解析积分 --
def height_fog_segment(y0: float, y1: float, base: float, density: float,
                       falloff: float, dt: float = 1.0):
    """一段射线的**光学厚度**（闭式解）。

    ``d(y) = density·exp(-falloff·(y-base))``，射线从 ``y0`` 到 ``y1``（``dt`` 是这段的
    世界长度；只影响"乘以长度"，不影响形状）。

    ``falloff → 0`` 时闭式退化成 ``density·dt``（密度与高度无关 ⇒ 均匀雾），
    这里用判据分支处理，**不能让 0/0 出现 NaN**。
    """
    if dt <= 0.0:
        return 0.0
    dy = y1 - y0
    k = falloff * dy
    at0 = density * math.exp(-falloff * (y0 - base))
    if abs(k) < 1e-6:
        # 均匀雾（含 falloff=0 与水平段 dy=0）—— 用级数首项，避免 1-exp(-k) 相消
        return at0 * dt
    return at0 * dt * (1.0 - math.exp(-k)) / k


def height_fog_column(y0: float, y1: float, base: float, density: float,
                      falloff: float, steps: int = 64):
    """**数值参考实现**：把 [y0, y1] 均分成 ``steps`` 段逐步矩形求和。

    存在的唯一理由：给 `height_fog_segment` 当**对照**（测试里两者必须一致到
    步长的二阶量级）。着色器里用的是闭式，不是这个。
    """
    if steps < 1:
        raise ValueError(f'steps 至少 1，收到 {steps}')
    total = 0.0
    dy = (y1 - y0) / steps
    for i in range(steps):
        a = y0 + dy * i
        total += height_fog_segment(a, a + dy, base, density, falloff, abs(dy))
    return total


# ------------------------------------------------------------------ 相函数 --
def henyey_greenstein(cos_theta: float, g: float) -> float:
    """Henyey-Greenstein 相函数（**已除以 4π 归一化**，即 ∫dω = 1）。

    * ``g = 0``   → 各向同性，返回 ``1/(4π) ≈ 0.0796``；
    * ``g > 0``   → 前向散射（朝向光源看更亮 ⇒ 光轴）；
    * ``g < 0``   → 后向散射。
    """
    if not -0.999 < g < 0.999:
        raise ValueError(f'g 必须在 (-0.999, 0.999)，收到 {g}（±1 时分母为 0）')
    c = max(-1.0, min(1.0, cos_theta))
    denom = 1.0 + g * g - 2.0 * g * c
    return (1.0 - g * g) / (4.0 * math.pi * denom * math.sqrt(denom))


def step_weights(n: int, mode: str = 'linear') -> tuple:
    """把 ``[0,1]`` 切成 ``n`` 段的**步长权重**（和为 1）。

    * ``linear``    —— 等分；
    * ``exp``       —— 近处密、远处疏（远处像素覆盖的立体角大，密采样是浪费）。

    权重归一化到和为 1：调用方乘上总长度即可得到世界步长，
    这样"换分布不改变总的采样跨度"。
    """
    if n < 1:
        raise ValueError(f'n 至少 1，收到 {n}')
    if mode == 'linear':
        w = [1.0 / n] * n
    elif mode == 'exp':
        raw = [2.0 ** (-3.0 * i / max(1, n - 1)) for i in range(n)]
        s = sum(raw)
        w = [v / s for v in raw]
    else:
        raise ValueError(f'未知分布 {mode!r}（只支持 linear / exp）')
    return tuple(w)


def dither_offset(px: int, py: int, frame: int = 0, size: int = 8) -> float:
    """二维**交错梯度噪声**（Interleaved Gradient Noise）的抖动偏移（∈ [0,1)）。

    用途：体积雾的采样起点若每像素都一样，低步数下会出现**层状条纹**；按像素抖一下
    就变成了细颗粒噪点（再靠时间上的微小偏移让它"活"起来）。

    为什么不是随机数：随机抖动会让**静止画面沙沙响**，而且剧照/录屏不可复现。
    交错梯度噪声是纯确定性的，且分布均匀（``mean ≈ 0.5``）。

    ``size`` 只影响空间周期（默认 8×8 一个 tile，够用）。
    """
    if size < 2:
        raise ValueError(f'size 至少 2，收到 {size}')
    fx = (px + frame * 5) % size
    fy = (py + frame * 3) % size
    inner = IGN_A * fx + IGN_B * fy
    return (IGN_C * (inner - math.floor(inner))) % 1.0


# ------------------------------------------------------------------ GLSL --
#: 3D 噪声的归一化常数。**必须与 `world3d.logic.noise._PERLIN3_NORM` 相等** ——
#: 两处漂开后"CPU 侧算出 0.62、GPU 侧算出 0.55"不会报错，只会让离线烘焙的
#: 噪声体和实时采样的噪声体长得不一样。这里不 import 逻辑层（engine 不该反向
#: 依赖 logic 的私有量），改由 `tests/test_volumetrics.py` 断言两处相等。
NOISE3_NORM = 1.0

#: 3D 噪声的 GLSL 片段（`{fn}` / `{norm}` 是文本占位符，用 `str.replace` 填，
#: 不走 `.format` —— 片段里全是花括号，走 format 会被当成字段名）。
_NOISE3_GLSL = '''// ---- 3D 密度噪声（Perlin 2002：12 梯度集 + 五次 fade）----
// 为什么内联算、不采样 3D 纹理：本引擎没有 3D 纹理通道（pyglet 侧只走 2D）；
// 内联只有 ALU 成本、不占显存，代价是每个采样点几十条指令 —— 所以它**默认关闭**
// （u_fog_noise = 0 时下面那个 if 整段跳过，不开就不付钱）。
// ⚠ 与 CPU 侧 `world3d.logic.noise.perlin3` **结构同式、数值不同**：GPU 只有
//   32 位 uint 哈希，CPU 侧是 int64 回绕语义。两边都确定性，但不要跨端比数值。
const float {fn}_NORM3 = {norm};

uint {fn}_hash3(ivec3 c) {
    uint h = uint(c.x) * 374761393u + uint(c.y) * 668265263u
           + uint(c.z) * 2654435761u + 1013904223u;
    h ^= h >> 13;
    h *= 1274126177u;
    h ^= h >> 16;
    return h;
}

// 12 个梯度方向（立方体 12 条棱的中点）用 16 个槽位 + 位掩码选中，
// 省掉 `% 12` 的整除（论文里"顺手也更快"的那部分：分量只有 -1/0/1 ⇒ 点积退化成加法）
float {fn}_grad3(uint h, vec3 d) {
    uint i = h & 15u;
    float u = (i < 8u) ? d.x : d.y;
    float v = (i < 4u) ? d.y : ((i == 12u || i == 14u) ? d.x : d.z);
    return ((i & 1u) == 0u ? u : -u) + ((i & 2u) == 0u ? v : -v);
}

// 单层 3D Perlin（≈[-1,1]；**整数格点上恰好 0**，与 CPU 侧同一性质）
float {fn}_noise3(vec3 p) {
    vec3 fl = floor(p);
    ivec3 c = ivec3(fl);
    vec3 d = p - fl;
    vec3 w = d * d * d * (d * (d * 6.0 - 15.0) + 10.0);
    float n[8];
    for (int k = 0; k < 8; ++k) {
        ivec3 o = ivec3(k & 1, (k >> 1) & 1, (k >> 2) & 1);
        n[k] = {fn}_grad3({fn}_hash3(c + o), d - vec3(o));
    }
    float lo = mix(mix(n[0], n[1], w.x), mix(n[2], n[3], w.x), w.y);
    float hi = mix(mix(n[4], n[5], w.x), mix(n[6], n[7], w.x), w.y);
    return mix(lo, hi, w.z) / {fn}_NORM3;
}

// 3 层 fBm 并重映射到 0..1（**均值 ≈ 0.5**，下面的调制依赖这条：均值不是 0.5
// 的话，一开噪声整幅雾就会整体变浓或变淡，而画面只表现为"好像变通透了"）
float {fn}_fbm3(vec3 p) {
    float sum = 0.0, amp = 1.0, norm = 0.0;
    vec3 q = p;
    for (int i = 0; i < 3; ++i) {
        sum += amp * {fn}_noise3(q);
        norm += amp;
        amp *= 0.5;
        q *= 2.0;
    }
    return clamp(sum / norm * 0.5 + 0.5, 0.0, 1.0);
}
'''


def noise3_glsl(fn: str) -> str:
    """把 3D 噪声片段渲染成带前缀的 GLSL（`fn_hash3` / `fn_grad3` / `fn_noise3` / `fn_fbm3`）。

    `fn` **必填**（没有默认值）：前缀就是命名空间，给个默认值会让调用方拿到
    `vol_fog_noise_hash3` 这种既不像雾又不像噪声的名字。本模块自己用 `vol_fog`，
    别处（实体纹理 / SDF 密度场）各给各的前缀。

    单独暴露是为了让它能用在别处而不用把整段体积雾拖过去。
    """
    if fn.isidentifier() is False:
        raise ValueError(f'函数名必须是合法标识符，收到 {fn!r}')
    return _NOISE3_GLSL.replace('{fn}', fn).replace('{norm}', repr(NOISE3_NORM))


def volumetric_glsl(fn: str = 'volumetric_fog', shadow_fn: str = None) -> str:
    """生成可注入的 GLSL 片段。

    约定：

    * 调用方已把**线性化的场景距离**写进 `u_fog_depth`（一张 R32F 深度纹理），
      或者直接用 `volumetric_fog_apply(scene_color)` 的形式在合成阶段叠加；
    * `u_inv_vp` 是**屏幕 → 世界**的逆矩阵（用来从 UV 反推世界射线方向）；
    * 需要单次散射（光轴）时给 `shadow_fn` 一个"世界坐标 + 视空间 z + 法线点积 → 0/1"
      的函数名（例如 `world3d.engine.csm` 的 `csm_shadow`），否则只算高度雾。
      **阴影图由那个函数自己带**（它知道自己的采样器和级选择）—— 本模块**不再重复声明**
      一个 `u_fog_shadow`：声明了如果没被采样就会被驱动优化掉，而 pyglet 给"不存在的
      uniform"赋值会抛异常（症状与体积雾毫无字面关系）。
    * 密度噪声要 `u_fog_noise`（强度，0 = 关闭）与 `u_fog_noise_scale`（频率，世界单位⁻¹）
      **配套给**；只给强度不给频率等于在 `(0,0,0)` 固定点采样（噪声退化成常数）。
      两个都默认 0，所以老调用方不设它们也安全 —— 只是没有团块。

    ⚠ 两条与 `engine/csm.py` 同源的坑（那里踩过一次，这里照抄注释）：

    1. **选级/多张纹理不许用非常量下标**（GL 3.3 的 `sampler2D` 数组限制）——
       本函数只吃一张深度图 + 一张阴影图，所以天然没有这个问题；要接 CSM 的 N 级时
       由调用方把选级写进 `shadow_fn`，那本来就是展开的。
    2. **`uniform block` 必须在每个用到它的着色器阶段各声明一遍** —— 本片段**不使用**
       `window` 块（它只用传给它的 uniform），所以调用方不必为它多声明一次。
    """
    if fn.isidentifier() is False:
        raise ValueError(f'函数名必须是合法标识符，收到 {fn!r}')
    need_shadow = bool(shadow_fn)
    # ⚠ **约定必须取反**：`world3d.engine.csm` 的 `csm_shadow` 返回 **1 = 在阴影里**，
    # 而这里要的是"能被照到多少" ⇒ `1 − shadow`。写成直接用会把光轴画反
    # （被照亮的空气不加光、被挡住的空气反而发亮），而画面整体只是"雾很平"，
    # 极难联想到是符号问题（实测：光轴开关切换只差 1.5/255）。
    scatter = (f'        float lit = 1.0 - {shadow_fn}(pos, z_here, ndl_here);'
               if need_shadow else
               '        float lit = 1.0;   // 不接阴影函数 ⇒ 只有高度雾，没有光轴')
    noise = noise3_glsl(fn)
    return f'''// ---- 体积雾 / 光轴（生成自 `world3d.engine.volumetrics`，不要手改）----
// ⚠ 调用方负责写版本声明（片段里重复声明版本号是编译错误；本片段**不含**它）
// ⚠ 本片段**只声明一个采样器**（u_fog_depth）。光轴要用到的阴影图由 `shadow_fn`
//    自己带（它知道自己的采样器与级选择）—— 早先这里另声明了一个 u_fog_shadow，
//    结果在不接光轴时它被驱动优化掉，pyglet 赋值直接抛异常（与体积雾毫无字面关系）。
uniform sampler2D u_fog_depth;     // 场景**线性距离**（R32F；不是深度缓冲的原始值）
uniform mat4  u_inv_vp;            // 屏幕(NDC) → 世界
uniform vec3  u_fog_cam;           // 相机世界坐标
uniform vec3  u_fog_light_dir;     // 指向光源
uniform vec3  u_fog_light_color;
uniform vec3  u_fog_ambient;
uniform float u_fog_density;
uniform float u_fog_falloff;
uniform float u_fog_base;
uniform float u_fog_phase_g;
uniform float u_fog_steps;         // 采样步数（用 float 传，省得再声明 int uniform）
uniform float u_fog_max_dist;
uniform float u_fog_analytic;      // 1 = 闭式高度雾积分；0 = 每步矩形（对照组）
uniform float u_fog_shafts;        // 1 = 记入单次散射（光轴）；0 = 只有雾
uniform float u_fog_noise;         // 密度噪声强度：**0 = 关闭（默认，不付 ALU）**，1 = 常规
uniform float u_fog_noise_scale;   // 噪声采样频率（世界单位⁻¹，常见 0.05~0.3；与上一项配套给）

// 一段射线的光学厚度 —— **闭式解**，与 CPU 侧 `height_fog_segment` 同式。
// 逐步矩形求和会在低步数时出现层状条纹，而且"改步数会让整幅雾变浓变淡"。
float {fn}_segment(float y0, float y1, float dt) {{
    float dy = y1 - y0;
    float k = u_fog_falloff * dy;
    float at0 = u_fog_density * exp(-u_fog_falloff * (y0 - u_fog_base));
    if (abs(k) < 1e-4) return at0 * dt;                 // 均匀雾：避开 1-exp(-k) 的相消
    return at0 * dt * (1.0 - exp(-k)) / k;
}}

// 相位函数（与 CPU 侧 `henyey_greenstein` 同式，已除 4π 归一化）
float {fn}_phase(float c) {{
    float g = u_fog_phase_g;
    float d = 1.0 + g * g - 2.0 * g * c;
    return (1.0 - g * g) / (4.0 * 3.14159265359 * d * sqrt(d));
}}

// 交错梯度噪声（与 CPU 侧 `dither_offset` **同式**，魔数由同一组常量生成）：
// 采样起点逐像素抖一下，低步数下的层状条纹就变成细颗粒，而不是一条条横带。
float {fn}_dither(vec2 frag) {{
    float inner = {IGN_A} * frag.x + {IGN_B} * frag.y;
    return fract({IGN_C} * fract(inner));
}}

// 屏幕 UV + 线性距离 → 世界坐标（逆 view-projection 反推，不需要法线/几何缓冲）
vec3 {fn}_world(vec2 uv, float dist) {{
    vec4 c = u_inv_vp * vec4(uv * 2.0 - 1.0, -1.0, 1.0);
    vec3 dir = normalize(c.xyz / c.w - u_fog_cam);
    return u_fog_cam + dir * dist;
}}

// 场景距离：从深度图取**线性距离**（调用方在深度趟里按同约定写进去）。
// 单独抽成一个函数是为了让 `u_fog_depth` **真的被采样到** —— 只声明不采样会被驱动
// 优化掉，而 pyglet 给"不存在的 uniform"赋值会抛异常。
float {fn}_scene_dist(vec2 uv) {{
    return min(texture(u_fog_depth, uv).r, u_fog_max_dist);
}}

{noise}
// 沿射线积分：返回 (散射 RGB, 透射率)。调用方用它合成 scene*trans + scatter。
vec4 {fn}(vec2 uv, float scene_dist, float dither) {{
    float steps = max(2.0, u_fog_steps);
    float far = min(scene_dist, u_fog_max_dist);
    vec3 sum = vec3(0.0);
    float trans = 1.0;
    float jitter = dither / steps;              // 抖动起点：打散层状条纹
    for (int i = 0; i < 512; ++i) {{
        if (float(i) >= steps) break;           // GLSL 330 的循环边界必须是常量
        float t0 = jitter + float(i) / steps;
        float t1 = min(1.0, jitter + float(i + 1) / steps);
        if (t0 >= 1.0) break;
        float d0 = t0 * far, d1 = t1 * far;
        float dt = d1 - d0;
        if (dt <= 0.0) break;
        vec3 p0 = {fn}_world(uv, d0);
        vec3 p1 = {fn}_world(uv, d1);
        float ext = (u_fog_analytic > 0.5)
            ? {fn}_segment(p0.y, p1.y, dt)                       // 闭式
            : u_fog_density * exp(-u_fog_falloff * (p0.y - u_fog_base)) * dt;  // 逐步矩形
        if (ext <= 0.0) {{ jitter = t1; continue; }}
        vec3 pos = (p0 + p1) * 0.5;             // 散射源项变化平缓，中点即可
        // 密度噪声：绕开 0 之外什么都不做（u_fog_noise 默认 0 ⇒ 整段跳过，
        // 不开噪声的场景不付这几十条指令）。乘子的均值是 1 ⇒ 开关它不整体改变
        // 雾的浓度，只把"一层均匀灰"打散成团块。
        // ⚠ 这一项是**按段中点采样**的：步数越多噪声越细（收敛到真值），
        //   所以"改步数不改变浓度"那条性质只对纯高度雾成立，开了噪声就有温和依赖。
        if (u_fog_noise > 0.0) {{
            float nz = {fn}_fbm3(pos * u_fog_noise_scale);
            ext *= max(1.0 + (nz - 0.5) * 2.0 * u_fog_noise, 0.0);
        }}
        trans *= exp(-ext);                     // 先衰减，再加本段散射（能量守恒的顺序）
        float z_here = -(d0 + dt * 0.5);
        float ndl_here = 1.0;
{scatter}
        float ph = {fn}_phase(dot(normalize(pos - u_fog_cam), u_fog_light_dir));
        sum += (u_fog_ambient + u_fog_light_color * ph * lit * u_fog_shafts)
               * ext * trans;
        jitter = t1;
        if (trans < 0.003) break;               // 已不透光，后面不必再算
    }}
    return vec4(sum, trans);
}}
'''
