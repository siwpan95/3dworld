# -*- coding: utf-8 -*-
"""GLSL 着色器库（#version 330 core，pyglet 2.x 默认档）。

分四组::

    几何      PBR（MRT：HDR 颜色 + 视空间法线/深度） / SKY / BASIC
    IBL 烘焙   ENV_GEN（程序化天空） / IRRADIANCE / PREFILTER / BRDF_LUT
    后处理     SSAO / SSAO_BLUR / BLOOM_* / COMPOSITE / DEBUG
    演示效果   PARTICLES（GPU 实例化粒子）/ TUNNEL（极坐标隧道）/
              RAYMARCH（SDF 光线步进）—— 2026-10-15 demo 剥离时从
              examples/ 上提；terrain/voxel 的着色器随其逻辑层留在
              world3d.logic.terrain / world3d.logic.voxel（自包含）

几条硬约束（踩过的坑都写在对应着色器上方）:

- **不用 sampler 数组 + 动态下标**：GLSL 3.30 要求 sampler 数组下标是常量
  表达式。所以点光源写成 4 个具名 uniform，SSAO 的核**在着色器里**用
  radical-inverse 现算，而不是从 CPU 传数组。
- **天空/环境用等距柱状（equirect）贴图**：pyglet 没有立方体贴图类，
  只能自己用 ``glTexImage2D`` 造；等距柱状的代价是极点会有挤压畸变，
  对"程序化天空 + IBL"这种低频信号完全够用。
- **MRT 的第二张图存视空间法线 + 线性深度**：SSAO 只靠这两样就能重建
  视空间坐标，比再存一张 world position 省一个附件。
"""

# ------------------------------------------------------------------ 公共 --
_SKY_CHUNK = '''
// 程序化天空：天顶/地平/地面三段渐变 + 一个太阳盘 + 地面网格色。
// 与 ENV_GEN / SKY 两个着色器共用，保证"烘焙出的环境光"和"直接看到的天空"
// 是同一个光源，不会出现背景和反射对不上的穿帮。
uniform vec3 uSkyZenith;
uniform vec3 uSkyHorizon;
uniform vec3 uSkyGround;
uniform vec3 uSunDir;
uniform vec3 uSunColor;
uniform float uSunIntensity;

vec3 sky_color(vec3 d) {
    float h = clamp(d.y * 0.5 + 0.5, 0.0, 1.0);
    vec3 base = mix(uSkyGround, uSkyHorizon, smoothstep(0.35, 0.5, h));
    base = mix(base, uSkyZenith, smoothstep(0.5, 1.0, h));
    float sd = max(dot(normalize(d), normalize(uSunDir)), 0.0);
    // 太阳盘 + 一圈柔和光晕（pow 越高盘越小）
    float disc = smoothstep(0.9985, 0.9995, sd);
    float glow = pow(sd, 220.0) * 0.35 + pow(sd, 12.0) * 0.06;
    return base + uSunColor * uSunIntensity * (disc * 12.0 + glow);
}
'''

_EQUIRECT_CHUNK = '''
// 方向 <-> 等距柱状 uv。约定：v=0 是天底（-Y），v=1 是天顶（+Y）。
const float INV_2PI = 0.15915494;
const float INV_PI  = 0.31830989;

vec2 dir_to_uv(vec3 d) {
    d = normalize(d);
    return vec2(atan(d.z, d.x) * INV_2PI + 0.5, acos(clamp(-d.y, -1.0, 1.0)) * INV_PI);
}

vec3 uv_to_dir(vec2 uv) {
    float phi = (uv.x - 0.5) * 6.28318531;
    float y = -cos(uv.y * 3.14159265);
    float r = sqrt(max(0.0, 1.0 - y * y));
    return vec3(r * cos(phi), y, r * sin(phi));
}
'''

_HAMMERSLEY_CHUNK = '''
// 着色器内现算 Hammersley 序列 —— 省掉"CPU 传 uniform 数组"这条路，
// 也就绕开了 GLSL 3.30 对数组下标的常量表达式限制。
float radical_inverse(uint bits) {
    bits = (bits << 16u) | (bits >> 16u);
    bits = ((bits & 0x55555555u) << 1u) | ((bits & 0xAAAAAAAAu) >> 1u);
    bits = ((bits & 0x33333333u) << 2u) | ((bits & 0xCCCCCCCCu) >> 2u);
    bits = ((bits & 0x0F0F0F0Fu) << 4u) | ((bits & 0xF0F0F0F0u) >> 4u);
    bits = ((bits & 0x00FF00FFu) << 8u) | ((bits & 0xFF00FF00u) >> 8u);
    return float(bits) * 2.3283064365386963e-10;
}

vec2 hammersley(uint i, uint n) {
    return vec2(float(i) / float(n), radical_inverse(i));
}
'''

_PBR_CHUNK = '''
const float PI = 3.14159265359;

float D_GGX(float NdotH, float rough) {
    float a  = rough * rough;
    float a2 = a * a;
    float d  = NdotH * NdotH * (a2 - 1.0) + 1.0;
    return a2 / max(PI * d * d, 1e-7);
}

// Smith-Schlick（直接光照版 k=(r+1)²/8；IBL 版 k=r²/2，见 GeometrySmithIBL）
float G_SchlickGGX(float NdotV, float rough) {
    float r = rough + 1.0;
    float k = (r * r) / 8.0;
    return NdotV / (NdotV * (1.0 - k) + k);
}

float G_Smith(float NdotV, float NdotL, float rough) {
    return G_SchlickGGX(NdotV, rough) * G_SchlickGGX(NdotL, rough);
}

vec3 F_Schlick(float cosTheta, vec3 F0) {
    return F0 + (1.0 - F0) * pow(clamp(1.0 - cosTheta, 0.0, 1.0), 5.0);
}

// 粗糙度参与菲涅尔（Karis）：不做这一步，粗糙金属边缘会出现不自然的亮边
vec3 F_SchlickRoughness(float cosTheta, vec3 F0, float rough) {
    vec3 Fr = max(vec3(1.0 - rough), F0);
    return F0 + (Fr - F0) * pow(clamp(1.0 - cosTheta, 0.0, 1.0), 5.0);
}
'''


# ------------------------------------------------------------------ 几何 --
# MRT：location 0 = HDR 颜色，location 1 = 视空间法线 + 线性深度（SSAO 用）
PBR_VERT = '''#version 330 core
in vec3 position;
in vec3 normals;
in vec2 uvs;
in vec3 tangents;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
// 法线矩阵按 mat4 传：pyglet 没有 Mat3 类型，mat3 uniform 的赋值路径没保障；
// 着色器里用 mat3(normal_mat) 取左上 3x3 即可。
uniform mat4 normal_mat;
out vec3 vWorldPos;
out vec3 vNormal;
out vec2 vUV;
out vec3 vTangent;
out vec4 vTint;
void main() {
    vec4 world = model * vec4(position, 1.0);
    vWorldPos = world.xyz;
    vNormal   = normalize(mat3(normal_mat) * normals);
    vTangent  = normalize(mat3(normal_mat) * tangents);
    vUV       = uvs;
    vTint     = vec4(1.0, 1.0, 1.0, 0.0);
    gl_Position = window.projection * window.view * world;
}'''

# 实例化变体：模型矩阵由 4 个 vec4 实例属性拼回来，tint/自发光走 i_tint。
# 为什么是 4 个 vec4 而不是一个 mat4：pyglet 的属性类型表里没有
# GL_FLOAT_MAT4，声明 mat4 属性会在 program introspect 阶段 KeyError。
PBR_INST_VERT = '''#version 330 core
in vec3 position;
in vec3 normals;
in vec2 uvs;
in vec3 tangents;
in vec4 i_m0;
in vec4 i_m1;
in vec4 i_m2;
in vec4 i_m3;
in vec4 i_tint;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
uniform mat4 normal_mat;
out vec3 vWorldPos;
out vec3 vNormal;
out vec2 vUV;
out vec3 vTangent;
out vec4 vTint;
void main() {
    mat4 inst = mat4(i_m0, i_m1, i_m2, i_m3);
    mat4 m = model * inst;
    vec4 world = m * vec4(position, 1.0);
    vWorldPos = world.xyz;
    // 法线矩阵 = normal_mat(模型级逆转置) 与实例矩阵左上 3x3 的合成。
    // 这里必须真的**用上** normal_mat：不用的 uniform 会被驱动优化掉，
    // 之后 pyglet 赋值时会抛 "Uniform not found"。
    mat3 nm = mat3(normal_mat) * mat3(inst);
    vNormal   = normalize(nm * normals);
    vTangent  = normalize(nm * tangents);
    vUV       = uvs;
    vTint     = i_tint;
    gl_Position = window.projection * window.view * world;
}'''

# 蒙皮变体（P2-3）：顶点属性 joints/weights（各 vec4）+ 关节矩阵数组。
# SKIN_MAX_JOINTS 必须与 engine.skin.MAX_JOINTS 一致（tests/test_skin.py 有断言守护）。
SKIN_MAX_JOINTS = 64

PBR_SKIN_VERT = ('''#version 330 core
in vec3 position;
in vec3 normals;
in vec2 uvs;
in vec3 tangents;
in vec4 joints;             // 每顶点 4 个关节下标（float 承载，取整）
in vec4 weights;            // 每顶点 4 个权重（CPU 侧已归一化）
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
uniform mat4 normal_mat;
uniform mat4 u_joints[''' + str(SKIN_MAX_JOINTS) + '''];   // 关节矩阵数组
out vec3 vWorldPos;
out vec3 vNormal;
out vec2 vUV;
out vec3 vTangent;
out vec4 vTint;
void main() {
    mat4 skin = weights.x * u_joints[int(joints.x)]
              + weights.y * u_joints[int(joints.y)]
              + weights.z * u_joints[int(joints.z)]
              + weights.w * u_joints[int(joints.w)];
    mat4 m = model * skin;
    vec4 world = m * vec4(position, 1.0);
    vWorldPos = world.xyz;
    // 法线/切线：normal_mat(模型级逆转置) 与 skin 左上 3x3 合成
    mat3 nm = mat3(normal_mat) * mat3(skin);
    vNormal   = normalize(nm * normals);
    vTangent  = normalize(nm * tangents);
    vUV       = uvs;
    vTint     = vec4(1.0, 1.0, 1.0, 0.0);
    gl_Position = window.projection * window.view * world;
}''')

# ============================================================ GPU-Driven（P3-1）
# compute：视锥剔除 + LOD 选择 + **命令生成**。
#
# ⚠️ 设计要点（实测逼出来的）：
#   本机真实窗口上下文是 **GL 3.3**（pyglet 的 shadow context 才报 4.6），
#   `gl_BaseInstanceARB` / `gl_DrawIDARB`（GL_ARB_shader_draw_parameters）**不生效**
#   ——早先"每实例一条命令 + baseInstance 索引实例"的方案会让全部实例塌到 mat[0]。
#   故改为**不依赖 draw parameter** 的形态：
#     · 可见实例按 LOD **固定分区**写入 visible[lv * N + slot]（slot 由 atomic 取）
#     · **每个 LOD 一条命令**；命令的 instanceCount 字段本身用 atomicAdd 累加
#       （⇒ 免第二趟、免 CPU 读回）
#     · 顶点着色器用 gl_InstanceID（GL 3.1+ 核心）查 visible[uLodBase + id]
#
# SSBO 绑定（与 engine/gpu_cull.py 契约一致）：
#   binding 0 InstanceData  vec4 (cx,cy,cz,radius)
#   binding 1 InstanceMat   vec4 mat[]   4 个 vec4 = 列的 4×4 模型矩阵
#   binding 3 Counts        uint counts[]  [0] = 总可见
#   binding 4 Commands      uint cmd[]     MAX_LOD 条 × 4 uint（count, instanceCount, first, 0）
#   binding 5 Visible       uint visible[] MAX_LOD 段 × N（固定分区）
GPU_CULL_COMP = '''#version 430 core
layout(local_size_x = 64) in;

layout(std430, binding = 0) buffer InstanceData { vec4 inst[]; };
layout(std430, binding = 3) buffer Counts       { uint counts[]; };
layout(std430, binding = 4) buffer Commands     { uint cmd[]; };
layout(std430, binding = 5) buffer Visible      { uint visible[]; };

uniform vec4  uPlanes[6];
uniform vec3  uCamPos;
uniform float uLodDists[4];
uniform int   uLodFirst[4];
uniform int   uLodCount[4];
uniform int   uCount;          // 实例总数
uniform int   uMaxLod;         // 实际 LOD 级数

void main() {
    uint i = gl_GlobalInvocationID.x;
    if (i >= uint(uCount)) return;

    vec4 sph = inst[i];
    vec3 c   = sph.xyz;
    float r  = sph.w;

    // ---- 视锥剔除 ----
    for (int k = 0; k < 6; ++k) {
        if (dot(uPlanes[k].xyz, c) + uPlanes[k].w < -r) return;   // 剔除：不写任何表
    }

    // ---- LOD 选择（按到相机距离）----
    float d = distance(c, uCamPos);
    uint lv = 0u;
    for (uint k = 0u; k < 4u; ++k) { if (d >= uLodDists[k]) lv = k + 1u; }
    lv = min(lv, uint(uMaxLod - 1));

    // ---- 分组落表：可见表按 LOD 固定分区；命令的 instanceCount 直接累加 ----
    uint slot = atomicAdd(cmd[lv * 4u + 1u], 1u);
    visible[lv * uint(uCount) + slot] = i;
    atomicAdd(counts[0], 1u);
}'''

# 顶点着色器（#version 430：需要 SSBO 读实例矩阵；**不依赖** draw parameter）
GPU_DRIVEN_VERT = '''#version 430 core
in vec3 position;
in vec3 normals;
layout(std430, binding = 1) buffer InstanceMat { vec4 mat[]; };
layout(std430, binding = 5) buffer Visible { uint visible[]; };
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform int uLodBase;                       // 本此 draw 的 LOD 分区起点 = lod * N
out vec3 vNormal;
out vec3 vWorld;
void main() {
    uint id = visible[uLodBase + uint(gl_InstanceID)];   // 查表得真实实例下标
    mat4 m = mat4(mat[id * 4u + 0u], mat[id * 4u + 1u], mat[id * 4u + 2u], mat[id * 4u + 3u]);
    vec4 world = m * vec4(position, 1.0);
    vWorld  = world.xyz;
    vNormal = normalize(mat3(m) * normals);
    gl_Position = window.projection * window.view * world;
}'''

GPU_DRIVEN_FRAG = '''#version 430 core
in vec3 vNormal;
in vec3 vWorld;
layout(location = 0) out vec4 outColor;
uniform vec3 uBaseColor;
uniform vec3 uLightDir;
void main() {
    vec3 n  = normalize(vNormal);
    float ndl = max(dot(n, normalize(uLightDir)), 0.0);
    float rim = 0.25 * pow(1.0 - max(dot(n, vec3(0.0, 0.0, 1.0)), 0.0), 2.0);
    outColor = vec4(uBaseColor * (0.16 + 0.84 * ndl) + rim, 1.0);
}'''

COMPUTE_SOURCES = {'gpu_cull': GPU_CULL_COMP}

# ============================================================ WBOIT（P2-2）
# Weighted Blended OIT（McGuire 2013）—— **顺序无关透明**，单趟 + 一次合成。
# 透明物写两张 MRT：accum(RGB=Σc·α·w, A=Σα·w) 与 reveal(Π(1-α))。
# per-RT blend 不同：accum 用 (ONE, ONE) 加法；reveal 用 (ZERO, ONE_MINUS_SRC_COLOR) 乘法
# —— 靠 `glBlendFunci(index, ...)`（GL 4.0；本机驱动可用，见 engine/oit.py 的能力探测）。
WBOIT_VERT = '''#version 330 core
in vec3 position;
in vec3 normals;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
uniform mat4 normal_mat;
out vec3 vNormal;
out vec3 vWorld;
void main() {
    vec4 world = model * vec4(position, 1.0);
    vWorld  = world.xyz;
    vNormal = normalize(mat3(normal_mat) * normals);
    gl_Position = window.projection * window.view * world;
}'''

WBOIT_FRAG = '''#version 330 core
in vec3 vNormal;
in vec3 vWorld;
layout(location = 0) out vec4 outAccum;    // Σ c·α·w , Σ α·w
layout(location = 1) out vec4 outReveal;   // Π(1-α)
uniform vec3  uColor;
uniform float uAlpha;
uniform vec3  uLightDir;
// McGuire 的距离权重：近处权重高、极远处迅速衰减（避免远景透明糊成一片）
float wboit_w(float z, float a) {
    return a * max(1e-2, min(3e3,
        10.0 / (1e-5 + pow(z / 200.0, 4.0) + pow(z / 10.0, 2.0))));
}
void main() {
    vec3 n = normalize(vNormal);
    float ndl = max(dot(n, normalize(uLightDir)), 0.0);
    vec3 c = uColor * (0.25 + 0.75 * ndl);
    float a = uAlpha;
    float w = wboit_w(gl_FragCoord.z, a);
    outAccum  = vec4(c * a * w, a * w);
    outReveal = vec4(a, 1.0, 1.0, 1.0);    // 配合 (ZERO, 1-SRC) -> 乘 (1-a)
}'''

WBOIT_COMPOSITE_FRAG = '''#version 330 core
in vec2 vNdc;
layout(location = 0) out vec4 outColor;
uniform sampler2D uAccum;
uniform sampler2D uReveal;
uniform sampler2D uScene;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    vec4 accum  = texture(uAccum, uv);
    float reveal = texture(uReveal, uv).r;
    vec3  C  = accum.rgb / max(accum.a, 1e-5);       // 加权平均色
    vec3  bg = texture(uScene, uv).rgb;
    outColor = vec4(mix(bg, C, clamp(1.0 - reveal, 0.0, 1.0)), 1.0);
}'''

PBR_FRAG = '''#version 330 core
in vec3 vWorldPos;
in vec3 vNormal;
in vec2 vUV;
in vec3 vTangent;
in vec4 vTint;
uniform WindowBlock { mat4 projection; mat4 view; } window;
layout(location = 0) out vec4 outColor;
layout(location = 1) out vec4 outNormalDepth;

uniform vec3 uCamPos;
uniform vec3 uBaseColor;
uniform float uMetallic;
uniform float uRoughness;
uniform vec3 uEmissive;
uniform float uAlpha;
uniform int  uUseNormalMap;
uniform sampler2D uBaseTex;
uniform sampler2D uMRTex;       // glTF：B=金属度  G=粗糙度
uniform sampler2D uNormalTex;
uniform sampler2D uOcclusionTex;
uniform sampler2D uEmissiveTex;

uniform vec3  uSunDir;
uniform vec3  uSunColor;
uniform int   uPointCount;
uniform vec3  uPointPos0;   uniform vec3 uPointColor0;
uniform vec3  uPointPos1;   uniform vec3 uPointColor1;
uniform vec3  uPointPos2;   uniform vec3 uPointColor2;
uniform vec3  uPointPos3;   uniform vec3 uPointColor3;

uniform sampler2D uIrradiance;
uniform sampler2D uPrefilter;
uniform sampler2D uBrdfLUT;
uniform float uPrefilterMaxLod;
uniform float uIblIntensity;
uniform int   uDebugMode;      // 0=最终 1=albedo 2=法线 3=仅 IBL 镜面 4=仅直接光
''' + _PBR_CHUNK + _EQUIRECT_CHUNK + '''
void main() {
    vec4 tex = texture(uBaseTex, vUV);
    vec3 albedo = uBaseColor * tex.rgb * vTint.rgb;
    float alpha = uAlpha * tex.a;
    vec2 mr = texture(uMRTex, vUV).gb;
    float metallic  = clamp(uMetallic * mr.x, 0.0, 1.0);
    float roughness = clamp(uRoughness * mr.y, 0.045, 1.0);
    float ao = texture(uOcclusionTex, vUV).r;

    vec3 N = normalize(vNormal);
    if (uUseNormalMap == 1) {
        vec3 t = normalize(texture(uNormalTex, vUV).rgb * 2.0 - 1.0);
        vec3 T = normalize(vTangent - N * dot(N, vTangent));
        vec3 B = cross(N, T);
        N = normalize(mat3(T, B, N) * t);
    }
    vec3 V = normalize(uCamPos - vWorldPos);
    float NdotV = max(dot(N, V), 1e-4);
    vec3 F0 = mix(vec3(0.04), albedo, metallic);

    // ---- 直接光：1 个方向光 + 最多 4 个点光 ----
    vec3 Lo = vec3(0.0);
    {
        vec3 L = normalize(-uSunDir);
        vec3 H = normalize(V + L);
        float NdotL = max(dot(N, L), 0.0);
        if (NdotL > 0.0) {
            vec3 F = F_Schlick(max(dot(H, V), 0.0), F0);
            float D = D_GGX(max(dot(N, H), 0.0), roughness);
            float G = G_Smith(NdotV, NdotL, roughness);
            vec3 spec = (D * G * F) / (4.0 * NdotV * NdotL + 1e-4);
            vec3 kd = (vec3(1.0) - F) * (1.0 - metallic);
            Lo += (kd * albedo / PI + spec) * uSunColor * NdotL;
        }
    }
    // 点光源写成具名 uniform：GLSL 3.30 不允许动态下标取 sampler/数组元素
    #define POINT_LIGHT(POS, COL) { \
        vec3 Lv = (POS) - vWorldPos; float dist = length(Lv); \
        vec3 L = Lv / max(dist, 1e-4); vec3 H = normalize(V + L); \
        float NdotL = max(dot(N, L), 0.0); \
        float atten = 1.0 / (dist * dist + 1e-4); \
        if (NdotL > 0.0) { \
            vec3 F = F_Schlick(max(dot(H, V), 0.0), F0); \
            float D = D_GGX(max(dot(N, H), 0.0), roughness); \
            float G = G_Smith(NdotV, NdotL, roughness); \
            vec3 spec = (D * G * F) / (4.0 * NdotV * NdotL + 1e-4); \
            vec3 kd = (vec3(1.0) - F) * (1.0 - metallic); \
            Lo += (kd * albedo / PI + spec) * (COL) * NdotL * atten; \
        } }
    if (uPointCount > 0) POINT_LIGHT(uPointPos0, uPointColor0)
    if (uPointCount > 1) POINT_LIGHT(uPointPos1, uPointColor1)
    if (uPointCount > 2) POINT_LIGHT(uPointPos2, uPointColor2)
    if (uPointCount > 3) POINT_LIGHT(uPointPos3, uPointColor3)

    // ---- IBL（split-sum）----
    vec3 F = F_SchlickRoughness(NdotV, F0, roughness);
    vec3 kS = F;
    vec3 kD = (1.0 - kS) * (1.0 - metallic);
    // 漫反射：辐照度图里存的是 E/PI（烘焙时已除过），这里直接乘 albedo
    vec3 irradiance = texture(uIrradiance, dir_to_uv(N)).rgb;
    vec3 diffuse = irradiance * albedo;
    vec3 R = reflect(-V, N);
    vec3 prefiltered = textureLod(uPrefilter, dir_to_uv(R), roughness * uPrefilterMaxLod).rgb;
    vec2 brdf = texture(uBrdfLUT, vec2(NdotV, roughness)).rg;
    vec3 specular = prefiltered * (F * brdf.x + brdf.y);
    vec3 ambient = (kD * diffuse + specular) * ao * uIblIntensity;

    vec3 color = Lo + ambient + uEmissive * texture(uEmissiveTex, vUV).rgb;
    // vTint.a = glow：自发光叠加（与旧管线 tint/glow 语义对齐）
    color += vTint.rgb * vTint.a;

    // 调试输出：排查"哪里没光"时不用再猜——直接把单项拎出来看
    if (uDebugMode == 1)      color = albedo;
    else if (uDebugMode == 2) color = N * 0.5 + 0.5;
    else if (uDebugMode == 3) color = specular;
    else if (uDebugMode == 4) color = Lo;
    else if (uDebugMode == 5) color = ambient;

    outColor = vec4(color, alpha);
    // 视空间法线 + 线性深度（SSAO 靠它重建视空间坐标，省一个 G-buffer 附件）
    vec3 viewN = normalize(mat3(window.view) * N);
    float viewZ = -(window.view * vec4(vWorldPos, 1.0)).z;
    outNormalDepth = vec4(viewN, viewZ);
}'''

# 天空：全屏四边形 + 反投影出视线方向，直接采样环境图。
# 必须同时写 location 1 —— MRT 下不写的输出是未定义的，SSAO 会读到垃圾。
SKY_VERT = '''#version 330 core
in vec3 position;
out vec2 vNdc;
void main() {
    vNdc = position.xy;
    gl_Position = vec4(position, 1.0);
}'''

SKY_FRAG = '''#version 330 core
in vec2 vNdc;
layout(location = 0) out vec4 outColor;
layout(location = 1) out vec4 outNormalDepth;
uniform mat4 uInvViewProj;
uniform vec3 uCamPos;
uniform sampler2D uEnv;
uniform float uSkyIntensity;
''' + _EQUIRECT_CHUNK + '''
void main() {
    vec4 far = uInvViewProj * vec4(vNdc, 1.0, 1.0);
    vec3 dir = normalize(far.xyz / far.w - uCamPos);
    outColor = vec4(texture(uEnv, dir_to_uv(dir)).rgb * uSkyIntensity, 1.0);
    // 背景：法线朝 +Z（视空间朝相机），深度给个大值，SSAO 不会去遮蔽它
    outNormalDepth = vec4(0.0, 0.0, 1.0, 1.0e6);
}'''

# 旧管线兼容：顶点色 + tint/glow，单输出（不进 MRT）。
# 3dworld 原有的 48s 演出就是这套材质语义，抽引擎时原样保留。
BASIC_VERT = '''#version 330 core
in vec3 position;
in vec4 colors;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
out vec4 vColor;
void main() {
    vColor = colors;
    gl_Position = window.projection * window.view * model * vec4(position, 1.0);
}'''

BASIC_FRAG = '''#version 330 core
in vec4 vColor;
uniform WindowBlock { mat4 projection; mat4 view; } window;
out vec4 final_colors;
uniform vec3 tint;
uniform float glow;
void main() {
    vec3 c = vColor.rgb * tint + glow * tint * 0.8;
    final_colors = vec4(c, vColor.a);
}'''


# -------------------------------------------------------------- IBL 烘焙 --
FULLSCREEN_VERT = SKY_VERT

ENV_GEN_FRAG = '''#version 330 core
in vec2 vNdc;
out vec4 outColor;
''' + _EQUIRECT_CHUNK + _SKY_CHUNK + '''
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    outColor = vec4(sky_color(uv_to_dir(uv)), 1.0);
}'''

# 漫反射辐照度：以 N 为轴做余弦加权半球积分，存的是 E/PI（即 Li 的均值），
# 于是着色器里 diffuse = albedo * texture(...)，不用再除 PI。
IRRADIANCE_FRAG = '''#version 330 core
in vec2 vNdc;
out vec4 outColor;
uniform sampler2D uEnv;
''' + _EQUIRECT_CHUNK + _HAMMERSLEY_CHUNK + '''
const uint SAMPLES = 128u;

void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    vec3 N = uv_to_dir(uv);
    // 任意正交基（N 与世界上方不平行时直接用 up 叉乘，平行时换一个轴）
    vec3 up = abs(N.y) < 0.999 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0);
    vec3 T = normalize(cross(up, N));
    vec3 B = cross(N, T);

    vec3 sum = vec3(0.0);
    for (uint i = 0u; i < SAMPLES; ++i) {
        vec2 xi = hammersley(i, SAMPLES);
        // 余弦加权：r = sqrt(u)，pdf = cos/PI 与权重 cos 抵消，估计量就是均值
        float r = sqrt(xi.x);
        float phi = 2.0 * 3.14159265 * xi.y;
        vec3 L = T * (r * cos(phi)) + B * (r * sin(phi)) + N * sqrt(max(0.0, 1.0 - xi.x));
        sum += texture(uEnv, dir_to_uv(L)).rgb;
    }
    outColor = vec4(sum / float(SAMPLES), 1.0);
}'''

# 镜面预过滤：按粗糙度逐级做 GGX 重要性采样，结果写进 mip 链的第 N 级，
# 着色器用 textureLod(roughness * maxLod) 一次取到。
PREFILTER_FRAG = '''#version 330 core
in vec2 vNdc;
out vec4 outColor;
uniform sampler2D uEnv;
uniform float uRoughness;
''' + _EQUIRECT_CHUNK + _HAMMERSLEY_CHUNK + '''
const uint SAMPLES = 96u;

void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    vec3 N = uv_to_dir(uv);
    // 标准近似：N = V = R（忽略视线与法线夹角，split-sum 的固定代价）
    vec3 V = N;
    vec3 up = abs(N.y) < 0.999 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0);
    vec3 T = normalize(cross(up, N));
    vec3 B = cross(N, T);
    float a = max(uRoughness * uRoughness, 1e-3);

    vec3 sum = vec3(0.0);
    float weight = 0.0;
    for (uint i = 0u; i < SAMPLES; ++i) {
        vec2 xi = hammersley(i, SAMPLES);
        float phi = 2.0 * 3.14159265 * xi.y;
        float cosT = sqrt((1.0 - xi.x) / (1.0 + (a * a - 1.0) * xi.x));
        float sinT = sqrt(max(0.0, 1.0 - cosT * cosT));
        vec3 H = T * (sinT * cos(phi)) + B * (sinT * sin(phi)) + N * cosT;
        vec3 L = normalize(2.0 * max(dot(V, H), 0.0) * H - V);
        float NdotL = max(dot(N, L), 0.0);
        if (NdotL > 0.0) {
            sum += texture(uEnv, dir_to_uv(L)).rgb * NdotL;
            weight += NdotL;
        }
    }
    outColor = vec4(weight > 0.0 ? sum / weight : vec3(0.0), 1.0);
}'''

# BRDF LUT：split-sum 的第二半（只依赖 BRDF，与环境无关），存 (scale, bias)
BRDF_LUT_FRAG = '''#version 330 core
in vec2 vNdc;
out vec4 outColor;
''' + _HAMMERSLEY_CHUNK + '''
const uint SAMPLES = 256u;

float G_SchlickGGX_IBL(float NdotV, float rough) {
    float k = (rough * rough) / 2.0;
    return NdotV / (NdotV * (1.0 - k) + k);
}

void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    float NdotV = clamp(uv.x, 1e-3, 1.0);
    float rough = clamp(uv.y, 1e-3, 1.0);
    vec3 V = vec3(sqrt(max(0.0, 1.0 - NdotV * NdotV)), 0.0, NdotV);
    vec3 N = vec3(0.0, 0.0, 1.0);
    float A = 0.0, B = 0.0;
    float a = rough * rough;
    for (uint i = 0u; i < SAMPLES; ++i) {
        vec2 xi = hammersley(i, SAMPLES);
        float phi = 2.0 * 3.14159265 * xi.y;
        float cosT = sqrt((1.0 - xi.x) / (1.0 + (a * a - 1.0) * xi.x));
        float sinT = sqrt(max(0.0, 1.0 - cosT * cosT));
        vec3 H = vec3(sinT * cos(phi), sinT * sin(phi), cosT);
        vec3 L = normalize(2.0 * max(dot(V, H), 0.0) * H - V);
        float NdotL = max(L.z, 0.0);
        float NdotH = max(H.z, 0.0);
        float VdotH = max(dot(V, H), 0.0);
        if (NdotL > 0.0) {
            float G = G_SchlickGGX_IBL(NdotV, rough) * G_SchlickGGX_IBL(NdotL, rough);
            float G_Vis = (G * VdotH) / max(NdotH * NdotV, 1e-5);
            float Fc = pow(1.0 - VdotH, 5.0);
            A += (1.0 - Fc) * G_Vis;
            B += Fc * G_Vis;
        }
    }
    outColor = vec4(A / float(SAMPLES), B / float(SAMPLES), 0.0, 1.0);
}'''


# ---------------------------------------------------------------- 后处理 --
# SSAO：从"视空间法线 + 线性深度"重建视空间坐标，用着色器内现算的
# 半球核做遮挡积分，再配一张 4x4 噪声图打散条纹（banding）。
SSAO_FRAG = '''#version 330 core
in vec2 vNdc;
out vec4 outColor;
uniform sampler2D uNormalDepth;
uniform sampler2D uNoise;
uniform mat4 uInvProjection;
uniform vec2 uNoiseScale;
uniform mat4 uProjection;
uniform float uRadius;
uniform float uBias;
uniform float uIntensity;
''' + _HAMMERSLEY_CHUNK + '''
const uint SAMPLES = 24u;

// 视空间坐标重建：把 NDC 反投影到远平面得到方向，归一化到 z=-1 后乘线性深度
vec3 view_pos(vec2 uv, float linearZ) {
    vec4 p = uInvProjection * vec4(uv * 2.0 - 1.0, 1.0, 1.0);
    vec3 ray = p.xyz / p.w;
    return (ray / max(-ray.z, 1e-5)) * linearZ;
}

void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    vec4 nd = texture(uNormalDepth, uv);
    if (nd.a > 1.0e5) {                       // 背景：不参与遮蔽
        outColor = vec4(1.0);
        return;
    }
    vec3 N = normalize(nd.xyz);
    vec3 P = view_pos(uv, nd.a);

    vec3 rvec = texture(uNoise, uv * uNoiseScale).xyz * 2.0 - 1.0;
    vec3 T = normalize(rvec - N * dot(rvec, N));   // Gram-Schmidt
    vec3 B = cross(N, T);
    mat3 tbn = mat3(T, B, N);

    float occlusion = 0.0;
    for (uint i = 0u; i < SAMPLES; ++i) {
        vec2 xi = hammersley(i, SAMPLES);
        // 半球内、向中心聚集（z 抬一点，避免平地自遮挡噪点）
        float r = sqrt(xi.x);
        float phi = 2.0 * 3.14159265 * xi.y;
        vec3 s = vec3(r * cos(phi), r * sin(phi), sqrt(max(0.02, 1.0 - xi.x)));
        vec3 sp = P + (tbn * s) * uRadius;
        vec4 clip = uProjection * vec4(sp, 1.0);
        vec2 suv = clip.xy / clip.w * 0.5 + 0.5;
        if (suv.x < 0.0 || suv.x > 1.0 || suv.y < 0.0 || suv.y > 1.0) continue;
        float sampleDepth = texture(uNormalDepth, suv).a;
        float rangeCheck = smoothstep(0.0, 1.0, uRadius / max(abs(nd.a - sampleDepth), 1e-4));
        occlusion += rangeCheck * step(sampleDepth, -sp.z + uBias);
    }
    float ao = 1.0 - occlusion / float(SAMPLES);
    outColor = vec4(clamp(pow(ao, uIntensity), 0.0, 1.0));
}'''


SSAO_BLUR_FRAG = '''#version 330 core
in vec2 vNdc;
out vec4 outColor;
uniform sampler2D uAO;
uniform vec2 uTexel;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    float sum = 0.0;
    // 4x4 盒式模糊：够用且便宜；双边（保边）模糊更贵，收益在动态场景才明显
    for (int x = -2; x < 2; ++x)
        for (int y = -2; y < 2; ++y)
            sum += texture(uAO, uv + vec2(float(x), float(y)) * uTexel).r;
    outColor = vec4(sum / 16.0);
}'''

# Bloom 第 0 级：软膝盖阈值 + Karis average。
# Karis average 用 1/(1+luma) 给每个采样加权——不这么做，法线贴图碰巧
# NdotL≈1 的单个超亮像素会在下采样链里"闪烁"（firefly）。
BLOOM_PREFILTER_FRAG = '''#version 330 core
in vec2 vNdc;
out vec4 outColor;
uniform sampler2D uScene;
uniform vec3 uThresholdKnee;   // x=threshold  y=threshold-knee  z=knee*2
uniform float uKaris;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    vec3 c = texture(uScene, uv).rgb;
    float luma = dot(c, vec3(0.2126, 0.7152, 0.0722));
    float knee = uThresholdKnee.z * max(luma - uThresholdKnee.y, 0.0);
    float soft = uThresholdKnee.x + knee;
    float contrib = max(luma - soft, 0.0) / max(luma, 1e-4);
    vec3 bright = c * contrib;
    // Karis：把高亮像素按 1/(1+luma) 压回去，只在这一级做
    float w = 1.0 / (1.0 + luma);
    outColor = vec4(mix(bright, bright * w, uKaris), 1.0);
}'''

# 13 抽样降采样（借双线性把 36 个 texel 合成 13 次取样），COD AW 的滤波盒
BLOOM_DOWN_FRAG = '''#version 330 core
in vec2 vNdc;
out vec4 outColor;
uniform sampler2D uSrc;
uniform vec2 uTexel;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    vec3 a = texture(uSrc, uv + uTexel * vec2(-2.0,  2.0)).rgb;
    vec3 b = texture(uSrc, uv + uTexel * vec2( 0.0,  2.0)).rgb;
    vec3 c = texture(uSrc, uv + uTexel * vec2( 2.0,  2.0)).rgb;
    vec3 d = texture(uSrc, uv + uTexel * vec2(-2.0,  0.0)).rgb;
    vec3 e = texture(uSrc, uv).rgb;
    vec3 f = texture(uSrc, uv + uTexel * vec2( 2.0,  0.0)).rgb;
    vec3 g = texture(uSrc, uv + uTexel * vec2(-2.0, -2.0)).rgb;
    vec3 h = texture(uSrc, uv + uTexel * vec2( 0.0, -2.0)).rgb;
    vec3 i = texture(uSrc, uv + uTexel * vec2( 2.0, -2.0)).rgb;
    vec3 j = texture(uSrc, uv + uTexel * vec2(-1.0,  1.0)).rgb;
    vec3 k = texture(uSrc, uv + uTexel * vec2( 1.0,  1.0)).rgb;
    vec3 l = texture(uSrc, uv + uTexel * vec2(-1.0, -1.0)).rgb;
    vec3 m = texture(uSrc, uv + uTexel * vec2( 1.0, -1.0)).rgb;
    vec3 down = e * 0.125;
    down += (a + c + g + i) * 0.03125;
    down += (b + d + f + h) * 0.0625;
    down += (j + k + l + m) * 0.125;
    outColor = vec4(down, 1.0);
}'''

# 3x3 帐篷上采样，加回上一级（逐级累加才不会出现"中心亮、边缘跳变"）
BLOOM_UP_FRAG = '''#version 330 core
in vec2 vNdc;
out vec4 outColor;
uniform sampler2D uSrc;
uniform vec2 uTexel;
uniform float uRadius;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    vec2 t = uTexel * uRadius;
    vec3 a = texture(uSrc, uv + t * vec2(-1.0,  1.0)).rgb;
    vec3 b = texture(uSrc, uv + t * vec2( 0.0,  1.0)).rgb;
    vec3 c = texture(uSrc, uv + t * vec2( 1.0,  1.0)).rgb;
    vec3 d = texture(uSrc, uv + t * vec2(-1.0,  0.0)).rgb;
    vec3 e = texture(uSrc, uv).rgb;
    vec3 f = texture(uSrc, uv + t * vec2( 1.0,  0.0)).rgb;
    vec3 g = texture(uSrc, uv + t * vec2(-1.0, -1.0)).rgb;
    vec3 h = texture(uSrc, uv + t * vec2( 0.0, -1.0)).rgb;
    vec3 i = texture(uSrc, uv + t * vec2( 1.0, -1.0)).rgb;
    vec3 up = (e * 4.0 + (b + d + f + h) * 2.0 + (a + c + g + i)) / 16.0;
    outColor = vec4(up, 1.0);
}'''

# 合成：HDR + Bloom -> 曝光 -> ACES -> 暗角 -> gamma
# 顺序是有讲究的：Bloom 必须在 tone-map **之前**叠加（之后就不是光能了），
# FXAA/暗角这类"显示空间"的效果才放在 tone-map 之后。
COMPOSITE_FRAG = '''#version 330 core
in vec2 vNdc;
out vec4 outColor;
uniform sampler2D uScene;
uniform sampler2D uBloom;
uniform sampler2D uAO;
uniform float uBloomIntensity;
uniform float uExposure;
uniform float uAOStrength;
uniform float uVignette;
uniform int   uToneMap;        // 0=Reinhard  1=ACES
uniform int   uDebug;          // 0=最终  1=仅 AO  2=仅 Bloom  3=法线/深度

// ACES 电影级 tone-map（Narkowicz 近似）：比 Reinhard 更好地保住高光颜色
vec3 aces(vec3 x) {
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}

void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    vec3 hdr = texture(uScene, uv).rgb;
    float ao = mix(1.0, texture(uAO, uv).r, uAOStrength);
    vec3 color = hdr * ao;

    vec3 bloom = texture(uBloom, uv).rgb;
    if (uDebug == 2) { outColor = vec4(bloom / (1.0 + bloom), 1.0); return; }
    if (uDebug == 1) { outColor = vec4(vec3(ao), 1.0); return; }

    color += bloom * uBloomIntensity;
    color *= uExposure;

    vec3 mapped = (uToneMap == 1) ? aces(color) : color / (color + vec3(1.0));
    // 暗角（显示空间效果，放 tone-map 之后）
    float d = length(uv - 0.5);
    mapped *= mix(1.0, smoothstep(0.85, 0.25, d), uVignette);
    // 线性 -> sRGB（窗口 framebuffer 不是 sRGB 格式，得自己编码）
    outColor = vec4(pow(mapped, vec3(1.0 / 2.2)), 1.0);
}'''

DEBUG_FRAG = '''#version 330 core
in vec2 vNdc;
out vec4 outColor;
uniform sampler2D uTex;
uniform int uChannel;      // 0=rgb  1=r  2=法线（*0.5+0.5）  3=深度（归一化）
uniform float uScale;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    vec4 t = texture(uTex, uv);
    if (uChannel == 1)      outColor = vec4(vec3(t.r), 1.0);
    else if (uChannel == 2) outColor = vec4(normalize(t.xyz) * 0.5 + 0.5, 1.0);
    else if (uChannel == 3) outColor = vec4(vec3(clamp(t.a * uScale, 0.0, 1.0)), 1.0);
    else                    outColor = vec4(t.rgb, 1.0);
}'''


# ------------------------------------------------------- 粒子（GPU 实例化）--
# 自 examples/particles_gpu.py 上提（2026-10-15 demo 剥离）。
# 逐帧动画 100% 在顶点着色器里算：CPU 每帧只更新 u_time 一个标量，
# 粒子位置/大小/颜色全部由 i_seed（每实例种子）+ u_time 现推出来。
# 三个坑位（构建封装见 world3d.engine.particles.ParticleSystem）：
#   1. i_seed 初始数据必须按**基础顶点数**给长度（四边形 6 顶点 × vec4 = 24 float）；
#   2. add_instance 内部 _instances += 1 后写下标 _instances-1 —— 0 号槽保留初始数据；
#   3. 顶点着色器里**用到**的属性才活着（GLSL 优化掉未用的 in，pyglet 按名找不到会炸）。
PARTICLES_VERT = '''#version 330 core

in vec3 position;      // 基础四边形角点（[-1,1]^2，z=0）
in vec4 colors;        // 基础四边形顶点色（静态色调乘子）
in vec4 i_seed;        // 每实例种子 [0,1)^4 —— 逐帧动画的唯一输入
in vec4 i_color;       // 每实例基色（色彩抖动源）

uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform float u_time;      // 累加相位（CPU 每帧只更新这一个标量）
uniform float u_energy;    // 整体能量/速度
uniform float u_size_scale; // 粒子尺寸倍率
uniform float u_alpha_scale; // alpha 补偿 = 1/size_scale²：放大后单颗要变淡，否则加性混合直接饱和
uniform int   u_preset;    // 0 星系漩涡 / 1 喷泉烟花 / 2 湍流云

out vec2 v_corner;
out vec4 v_col;

const float TAU = 6.28318531;

void main() {
    vec3  p    = vec3(0.0);
    float size = 0.10;
    float life = 0.0;                  // 0..1：寿命/强度，用于颜色与尺寸渐变
    float alpha = 0.5;
    vec3  tint = i_color.rgb;

    if (u_preset == 0) {
        // ---- 0：星系漩涡 —— 盘状分布 + 绕 Y 轴差速旋转 ----
        float r   = 0.6 + 9.4 * pow(i_seed.x, 0.62);   // pow<1 让中心更密（盘状）
        float om  = 1.7 / (0.8 + r);                   // 差速：内圈角速度大（开普勒味）
        float ang = i_seed.y * TAU + u_time * om;      // 同一个 ang 里既有随机相位也有旋转
        float y   = (i_seed.z - 0.5) * (0.22 + 0.9 * r / 9.4);   // 薄盘，中心略鼓
        p = vec3(cos(ang) * r, y, sin(ang) * r);
        // 密度波：两条**固定在世界坐标**里的旋臂（粒子转、臂不动）
        float arm = 0.55 + 0.45 * sin(2.0 * ang - 2.6 * r);
        tint *= mix(vec3(1.00, 0.86, 0.60), vec3(0.40, 0.62, 1.00),
                    clamp(r / 9.5, 0.0, 1.0));         // 核球暖黄 -> 旋臂冷蓝
        tint *= 0.30 + 1.00 * arm;
        size  = (0.050 + 0.045 * i_seed.w) * (1.15 - 0.35 * r / 9.5);
        life  = arm;
        alpha = 0.22 + 0.55 * arm;

    } else if (u_preset == 1) {
        // ---- 1：喷泉/烟花 —— 抛物线弹道 + 重力 + 寿命循环 ----
        float rate = 0.45;
        float grp  = floor(i_seed.z * 3.0);            // 分成 3 组错峰发射
        life = fract(u_time * rate + grp * 0.3333 + i_seed.z * 0.05);
        float vy = 9.0 + 6.0 * i_seed.w;               // 竖直初速 9~15
        float G  = 11.0;                               // 重力
        float T  = 2.0 * vy / G;                       // 恰好落回地面的时刻
        float tt = life * T;
        float az = i_seed.y * TAU;
        float sp = 0.16 * vy * (0.35 + i_seed.x);      // 水平初速 ∝ vy -> 束形稳定
        p = vec3(cos(az) * sp * tt,
                 0.15 + vy * tt - 0.5 * G * tt * tt,
                 sin(az) * sp * tt);
        // 颜色随寿命渐变：白热 -> 橙 -> 暗红
        tint  = mix(vec3(1.00, 0.96, 0.78), vec3(1.00, 0.42, 0.10),
                    smoothstep(0.0, 0.55, life));
        tint  = mix(tint, vec3(0.42, 0.06, 0.02), smoothstep(0.55, 1.0, life));
        size  = (0.20 - 0.10 * life) * (0.55 + 0.75 * i_seed.w);
        alpha = 0.45 * (1.0 - life) * (1.0 - life);

    } else {
        // ---- 2：湍流云 —— 多频正弦伪噪声位移 ----
        vec3 q = (i_seed.xyz - 0.5) * vec3(12.0, 5.0, 12.0);
        float t = u_time * 0.6;
        vec3 d = 1.00 * sin(q.yzx * 0.55 + t * 1.00)
               + 0.50 * sin(q.zxy * 1.13 - t * 1.37)
               + 0.25 * sin(q.xyz * 2.31 + t * 0.73);
        p = q + d * (1.25 * u_energy);
        float spin = u_time * 0.09;                    // 整团云极慢自转
        float cs = cos(spin), sn = sin(spin);
        p.xz = mat2(cs, -sn, sn, cs) * p.xz;
        float m = clamp(length(d) / 1.6, 0.0, 1.0);
        tint  = mix(vec3(0.24, 0.42, 0.85), vec3(0.88, 0.96, 1.00), m);
        size  = (0.12 + 0.13 * i_seed.w) * (0.7 + 0.5 * m);
        life  = m;
        alpha = 0.10 + 0.26 * m;
    }

    // --- 朝向相机的公告板：先在视图空间定位，再按像素尺寸铺角点 ---
    // 能量只影响尺度/位移，不影响相位（推进速率由 CPU 的累加相位负责），
    // 这样按 +/- 时画面是"变大变小"而不是"跳帧"。
    size *= (0.80 + 0.25 * u_energy) * u_size_scale;
    vec4 view_pos = window.view * vec4(p, 1.0);
    view_pos.xy += position.xy * size;
    gl_Position = window.projection * view_pos;

    v_corner = position.xy;
    // 能量守恒式缩放：面积涨 k² 倍，单颗亮度就除以 k²。
    // 少了这一步，放大后大量粒子在加性混合下会糊成一整块色团。
    v_col = vec4(tint, alpha * u_alpha_scale) * colors;
}'''

PARTICLES_FRAG = '''#version 330 core
in vec2 v_corner;
in vec4 v_col;
out vec4 final_colors;

void main() {
    // 径向软衰减。**不能用 (1-d²)²**：那个剖面太"平"（d=0.5 处还有 56% 亮度），
    // 加性混合下大量粒子一叠，中心直接饱和到 1.0 —— 于是每颗粒子变成一个
    // 亮方块，只有最外圈还在衰减（实测就是这个现象）。
    // 换成高斯剖面：中心亮、尾巴平滑、在四边形边界 d=1 处恰好为 0，
    // 无论怎么叠都读得出"圆点"。减去边界值是为了让 d=1 严格为 0（不裁剪出硬边）。
    float d = sqrt(dot(v_corner, v_corner));
    const float K = 4.0;
    float edge = exp(-K);
    float falloff = max(0.0, exp(-K * d * d) - edge) / (1.0 - edge);
    final_colors = vec4(v_col.rgb, v_col.a * falloff);
}'''


# ------------------------------------------------- 隧道（demoscene 全屏片元）--
# 自 examples/demoscene_tunnel.py 上提（2026-10-15 demo 剥离）。
# 纯片元着色器：几何只有一个 NDC 全屏四边形，画面 100% 由极坐标隧道公式算出
# （无贴图、无外部素材）。片元着色器里必须声明 in vec3 position; in vec4 colors;
# —— pyglet 按名字校验顶点属性，声明了却不用会被 GLSL 编译器优化掉，导致创建
# 顶点列表报错。
TUNNEL_VERT = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec2 v_uv;
out vec3 v_tint;
void main() {
    v_uv = position.xy * 0.5 + 0.5;
    v_tint = colors.rgb;
    gl_Position = vec4(position.xy, 0.0, 1.0);
}'''

TUNNEL_FRAG = '''#version 330 core
in vec2 v_uv;
in vec3 v_tint;
out vec4 final_colors;

uniform float u_time;
uniform vec2  u_res;
uniform vec3  u_palette;   // 灯条基色（1/2/3 键切换）
uniform float u_twist;     // 隧道扭动强度（T 键）
uniform float u_scan;      // 扫描线开关（G 键）

float hash21(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
}

void main() {
    // --- 以短边归一化的中心坐标 ---
    vec2 p = (v_uv - 0.5) * vec2(u_res.x / u_res.y, 1.0) * 2.0;

    // --- 相机滚转 + 隧道扭动：整体旋转采样坐标 ---
    float roll = u_time * 0.25 + u_twist * 0.6 * sin(u_time * 0.37);
    float cr = cos(roll), sr = sin(roll);
    p = mat2(cr, -sr, sr, cr) * p;

    float r = max(length(p), 1e-4);
    float ang = atan(p.y, p.x);

    // --- 极坐标隧道：z 为"深度"，与 r 成反比 ---
    float z = 1.0 / r;
    // 行进速度带心跳式脉动（模拟 demo 的节拍）
    float beat = 1.0 + 0.35 * sin(u_time * 3.14159);
    vec2 uv = vec2(ang / 3.14159265, z * 0.55 + u_time * 1.7 * beat);

    // --- 程序化"金属面板"：格子哈希 + 缝隙 ---
    vec2 grid = uv * vec2(9.0, 5.0);
    vec2 cell = floor(grid);
    vec2 f = fract(grid);
    float h = hash21(cell);
    vec3 col = mix(vec3(0.045, 0.055, 0.085), vec3(0.13, 0.17, 0.26), h);
    col += 0.05 * h * vec3(0.4, 0.6, 1.0);                 // 轻微冷色噪声

    float gap = smoothstep(0.0, 0.045, f.x) * smoothstep(1.0, 0.955, f.x)
              * smoothstep(0.0, 0.06, f.y) * smoothstep(1.0, 0.94, f.y);
    col *= 0.22 + 0.78 * gap;                               // 缝隙压暗

    // --- 琥珀灯条：稀疏格子自发光，随机闪烁 ---
    float lit = step(0.90, hash21(cell * 0.71 + vec2(3.1, 7.7)));
    float flick = 0.65 + 0.35 * sin(u_time * 7.0 + h * 33.0);
    col += lit * u_palette * flick * 1.35;

    // --- 行进光带：沿深度方向扫过的亮环 ---
    float band = fract(z * 0.45 - u_time * 0.9);
    float ring = pow(1.0 - abs(band * 2.0 - 1.0), 12.0);
    col += ring * vec3(0.35, 0.75, 1.0) * 0.5 * smoothstep(0.6, 4.0, z);

    // --- 深度雾：远处（r 小 = z 大）融入黑暗 ---
    col *= exp(-z * 0.30);

    // --- 中心辉光：隧道尽头的光 ---
    float core = pow(max(0.0, 1.0 - r * 1.9), 4.0);
    col += core * (u_palette * 0.9 + vec3(0.25, 0.35, 0.6));

    // --- 色散：按半径轻微错位红蓝通道（伪·镜头色差）---
    col.r *= 1.0 + 0.18 * core;
    col.b *= 1.0 + 0.10 * (1.0 - core);

    // --- 扫描线 + 暗角 ---
    float scan = 1.0 - u_scan * 0.12 * (0.5 + 0.5 * sin(v_uv.y * u_res.y * 3.14159));
    float vig = smoothstep(1.35, 0.25, length(v_uv - 0.5) * 1.6);
    col *= scan * mix(0.55, 1.0, vig);

    // --- 色调映射 + 轻微 gamma ---
    col = col / (col + vec3(0.85));
    col = pow(max(col, 0.0), vec3(0.85));

    final_colors = vec4(col * v_tint, 1.0);
}'''


# --------------------------------------------- SDF 光线步进（全屏片元渲染）--
# 自 examples/raymarch_sdf.py 上提（2026-10-15 demo 剥离）。
# 球/盒/环 smooth-min 融合 + 软阴影 + AO + 地面反射雾；数学与 world3d.core.sdf_core
# 的距离场同源（该核供 games/fps 的迷宫距离场使用），本着色器是其"渲染集成"形态。
RAYMARCH_VERT = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec2 v_uv;
void main() {
    v_uv = position.xy * 0.5 + 0.5;     // NDC → [0,1]²，片元里再转成屏幕坐标
    gl_Position = vec4(position.xy, 0.0, 1.0);
}'''

RAYMARCH_FRAG = '''#version 330 core
in vec2 v_uv;
out vec4 final_colors;

uniform vec2  u_res;
uniform float u_time;        // 演示时间（已按 DURATION 取模）
uniform vec3  u_eye;         // 相机位置
uniform vec3  u_target;      // 相机注视点
uniform vec3  u_palette;     // 材质主色（1/2/3 键）
uniform float u_shadow;      // 软阴影开关
uniform float u_morph;       // 0..1 形态进度

const int   MAX_STEPS = 96;  // 主步进预算（硬上限，绝不放开）
const int   SHADOW_STEPS = 20;
const float T_MAX = 26.0;    // 最远步进距离（远超场景尺寸，靠雾收尾）

// 场景尺寸（世界单位）：地面在 y = GROUND，图元悬在地面之上
const float GROUND = -1.15;

// ---------------------------------------------------------------- SDF --
float sdSphere(vec3 p, float r) { return length(p) - r; }

float sdRoundBox(vec3 p, vec3 b, float r) {
    vec3 q = abs(p) - b + r;
    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0) - r;
}

float sdTorus(vec3 p, vec2 t) {          // t = (主半径, 管半径)
    vec2 q = vec2(length(p.xz) - t.x, p.y);
    return length(q) - t.y;
}

float smin(float a, float b, float k) {  // 多项式平滑并集
    float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

vec2 scene(vec3 p) {
    float m = u_morph;                                   // 0..1 形态进度
    float grow = 0.85 + 0.30 * sin(u_time * 0.9);        // 整体呼吸
    float k = 0.20 + 0.30 * (0.5 - 0.5 * cos(m * 3.14159));  // 融合半径呼吸

    // 图元都抬高 1.05：这样它们"坐"在地面上（底部刚好蹭到 y=GROUND），
    // 阴影是**接地阴影**——悬空 1 米以上时软阴影会飘在物体正下方老远，
    // 看起来像贴纸而不是影子。
    vec3 q0 = p - vec3(0.0, 1.05, 0.0);

    // 球：沿 X 来回平移（幅度 0.55，随 morph 从"贴住"到"分开"）
    vec3  sp = q0 - vec3(0.55 * cos(m * 3.14159), 0.10, 0.0);
    float d = sdSphere(sp, 0.72 * grow);

    // 圆角盒：YZ 截面按 morph 缩放（方 → 扁），并整体轻微倾斜
    float ca = cos(0.45 * m), sa = sin(0.45 * m);
    vec3 rp = vec3(q0.x * ca - q0.z * sa, q0.y, q0.x * sa + q0.z * ca);
    vec3 bsize = vec3(0.55, 0.48 + 0.18 * sin(m * 6.28318), 0.55);
    d = smin(d, sdRoundBox(rp, bsize, 0.20), k);

    // 圆环：绕 X 轴翻转（幅度 0.9 rad，够看出"立起来"但不至于倒平）
    float tb = 0.55 + 0.90 * m;
    float ct = cos(tb), st = sin(tb);
    mat3 rx = mat3(1.0, 0.0, 0.0,
                   0.0, ct, -st,
                   0.0, st, ct);
    d = smin(d, sdTorus(rx * q0, vec2(1.40, 0.26)), k);

    // 地面：加进来而不是 max 求交，几何上完全等价（物体落入地面下时平面更近），
    // 但**不会毁掉远处的步进**——max 版本会让"物体轮廓在地面上的投影"
    // 变成硬边，远处地面细看全是台阶。
    float ground = p.y - GROUND;
    d = min(d, ground);

    // 材质编号：地面用 0.65 的缓坡过渡，避免 1 像素宽的判定抖动。
    // 注意**不能**用 `ground < 0.0001` 判：物体内部 ground 是负的，
    // 那个条件在物体里也成立，会把整块物体染成地面色。
    float id = mix(2.0, 1.0, clamp((ground - 0.03) / 0.65, 0.0, 1.0));
    return vec2(d, id);
}

// ------------------------------------------------------------- 法线/AO --
// eps 随命中距离缩放：固定 eps 在"擦地"的像素上会取到地面/物体两侧的样本，
// 法线直接翻向、边缘出现一条亮线（第一版就是这么翻车的）。
vec3 calcNormal(vec3 p, float t) {
    float e = 0.0012 + 0.0016 * t;
    vec2 h = vec2(e, 0.0);
    return normalize(vec3(
        scene(p + h.xyy).x - scene(p - h.xyy).x,
        scene(p + h.yxy).x - scene(p - h.yxy).x,
        scene(p + h.yyx).x - scene(p - h.yyx).x));
}

float calcAO(vec3 p, vec3 n) {
    float occ = 0.0, sca = 1.0;
    for (int i = 0; i < 5; i++) {
        float h = 0.02 + 0.14 * float(i);
        float d = scene(p + n * h).x;
        occ += (h - d) * sca;
        sca *= 0.80;
    }
    return clamp(1.0 - 1.8 * occ, 0.0, 1.0);
}

float softShadow(vec3 ro, vec3 rd) {
    float res = 1.0, t = 0.06;
    for (int i = 0; i < SHADOW_STEPS; i++) {
        float h = scene(ro + rd * t).x;
        res = min(res, 10.0 * h / t);   // 离遮挡物越近越黑；t 越大半影越宽
        t += clamp(h, 0.05, 0.6);
        if (res < 0.005 || t > 7.0) break;
    }
    return clamp(res, 0.0, 1.0);
}

float checker(vec2 q) {         // 地面棋盘格（解析，不吃贴图）
    vec2 c = floor(q);
    return mod(c.x + c.y, 2.0);
}

void main() {
    // --------- 相机射线 ---------
    vec2 uv = (gl_FragCoord.xy - 0.5 * u_res) / u_res.y;   // 以短边归一化
    vec3 fw = normalize(u_target - u_eye);
    vec3 rt = normalize(cross(vec3(0.0, 1.0, 0.0), fw));
    vec3 up = cross(fw, rt);
    vec3 rd = normalize(uv.x * rt + uv.y * up + 1.55 * fw);  // 1.55 ≈ 焦距

    // --------- 主步进 ---------
    float t = 0.0;
    float id = 0.0;
    for (int i = 0; i < MAX_STEPS; i++) {
        vec3 p = u_eye + rd * t;
        vec2 r = scene(p);
        if (r.x < 0.0016 * t + 0.0006) { id = r.y; break; }
        t += r.x * 0.85;                 // 0.85 是安全系数：宁多走几步也不穿模
        if (t > T_MAX) break;
    }

    // --------- 天空 / 背景 ---------
    // 天空按射线**俯仰角**分层：地平线偏暖、头顶深蓝、往下逐渐变成地面色。
    // 第一版写成 mix(mix(...)) 的纯水平渐变，结果地平线是一条亮白带，
    // 整个画面发灰发白——天空的"上蓝下暖"必须靠 rd.y 才立得住。
    vec3 sky_up = vec3(0.055, 0.105, 0.235);
    vec3 sky_hz = vec3(0.44, 0.42, 0.40);
    vec3 sky_dn = vec3(0.14, 0.125, 0.115);
    float up_t = clamp(rd.y, 0.0, 1.0);
    float dn_t = clamp(-rd.y, 0.0, 1.0);
    vec3 col = mix(sky_hz, sky_up, pow(up_t, 0.55));
    col = mix(col, sky_dn, pow(dn_t, 0.45));
    vec3 sun_dir = normalize(vec3(-0.55, 0.42, 0.70));
    float sdot = max(dot(rd, sun_dir), 0.0);
    col += vec3(1.0, 0.86, 0.62) * pow(sdot, 256.0) * 1.4;  // 太阳盘
    col += vec3(1.0, 0.80, 0.55) * pow(sdot, 6.0) * 0.10;   // 太阳周围辉光

    if (id > 0.5) {
        vec3 p = u_eye + rd * t;
        vec3 n = calcNormal(p, t);
        vec3 base = u_palette;
        if (id > 1.5) {                                  // 地面
            float c = checker(p.xz * 0.55);
            base = mix(vec3(0.11), vec3(0.26), c);
            // 靠近原点的地面染一点主色，让物体与地面有色彩呼应
            base = mix(base, u_palette * 0.40, 0.20 * exp(-0.05 * dot(p.xz, p.xz)));
        }
        float ao = calcAO(p, n);
        float diff = max(dot(n, sun_dir), 0.0);
        float sh = (u_shadow > 0.5) ? softShadow(p + n * 0.02, sun_dir) : 1.0;
        vec3  sky_amb = mix(vec3(0.08, 0.10, 0.16), sky_up, 0.5);
        vec3  lit = base * (sky_amb * ao * 1.5 + vec3(0.98, 0.88, 0.74) * diff * sh * 0.95);
        // 轮廓微光：视线与法线接近垂直处补一点主色，避免边缘糊进背景
        lit += u_palette * pow(1.0 - max(dot(n, -rd), 0.0), 3.0) * 0.10;
        // 指数雾：把远处地面融进天空，藏掉 T_MAX 截断的硬边。
        // 别调太狠——第一版雾在近处就有 2 成强度，整个画面灰成一片。
        float fog = 1.0 - exp(-0.0022 * t * t - 0.02 * t);
        col = mix(lit, col, fog);
    }

    // --------- 曝光 + 色调映射（ACES 近似）+ gamma ---------
    col *= 0.85;
    col = (col * (2.51 * col + 0.03)) / (col * (2.43 * col + 0.59) + 0.14);
    col = pow(clamp(col, 0.0, 1.0), vec3(1.0 / 2.2));
    final_colors = vec4(col, 1.0);
}'''


# ------------------------------------------------------------------ 工厂 --
SOURCES = {
    'pbr': (PBR_VERT, PBR_FRAG),
    'pbr_inst': (PBR_INST_VERT, PBR_FRAG),
    'pbr_skin': (PBR_SKIN_VERT, PBR_FRAG),
    'gpu_driven': (GPU_DRIVEN_VERT, GPU_DRIVEN_FRAG),
    'wboit': (WBOIT_VERT, WBOIT_FRAG),
    'wboit_composite': (FULLSCREEN_VERT, WBOIT_COMPOSITE_FRAG),
    'sky': (SKY_VERT, SKY_FRAG),
    'basic': (BASIC_VERT, BASIC_FRAG),
    'env_gen': (FULLSCREEN_VERT, ENV_GEN_FRAG),
    'irradiance': (FULLSCREEN_VERT, IRRADIANCE_FRAG),
    'prefilter': (FULLSCREEN_VERT, PREFILTER_FRAG),
    'brdf_lut': (FULLSCREEN_VERT, BRDF_LUT_FRAG),
    'ssao': (FULLSCREEN_VERT, SSAO_FRAG),
    'ssao_blur': (FULLSCREEN_VERT, SSAO_BLUR_FRAG),
    'bloom_prefilter': (FULLSCREEN_VERT, BLOOM_PREFILTER_FRAG),
    'bloom_down': (FULLSCREEN_VERT, BLOOM_DOWN_FRAG),
    'bloom_up': (FULLSCREEN_VERT, BLOOM_UP_FRAG),
    'composite': (FULLSCREEN_VERT, COMPOSITE_FRAG),
    'debug': (FULLSCREEN_VERT, DEBUG_FRAG),
    'particles': (PARTICLES_VERT, PARTICLES_FRAG),
    'tunnel': (TUNNEL_VERT, TUNNEL_FRAG),
    'raymarch': (RAYMARCH_VERT, RAYMARCH_FRAG),
}


def build(name):
    """编译一个着色器程序（按名字缓存，重复调用返回同一实例）。"""
    from pyglet.graphics.shader import Shader, ShaderProgram
    vert, frag = SOURCES[name]
    return ShaderProgram(Shader(vert, 'vertex'), Shader(frag, 'fragment'))


_CACHE = {}

_COMPUTE_CACHE = {}


def build_compute(name):
    """编译一个 **compute** 程序（P3-1：GPU 剔除/命令生成用）。"""
    from pyglet.graphics.shader import Shader, ShaderProgram
    return ShaderProgram(Shader(COMPUTE_SOURCES[name], 'compute'))


def get_compute(name):
    """取（并缓存）一个 compute 程序。"""
    if name not in _COMPUTE_CACHE:
        _COMPUTE_CACHE[name] = build_compute(name)
    return _COMPUTE_CACHE[name]


def get(name):
    """取（并缓存）一个着色器程序。同一 GL 上下文内可复用。"""
    if name not in _CACHE:
        _CACHE[name] = build(name)
    return _CACHE[name]


def invalidate_cache():
    """上下文销毁后调用（缓存的 program id 已失效）。"""
    _CACHE.clear()
    _COMPUTE_CACHE.clear()
