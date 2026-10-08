# -*- coding: utf-8 -*-
"""GLSL 着色器库（#version 330 core，pyglet 2.x 默认档）。

分三组::

    几何      PBR（MRT：HDR 颜色 + 视空间法线/深度） / SKY / BASIC
    IBL 烘焙   ENV_GEN（程序化天空） / IRRADIANCE / PREFILTER / BRDF_LUT
    后处理     SSAO / SSAO_BLUR / BLOOM_* / COMPOSITE / DEBUG

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


# ------------------------------------------------------------------ 工厂 --
SOURCES = {
    'pbr': (PBR_VERT, PBR_FRAG),
    'pbr_inst': (PBR_INST_VERT, PBR_FRAG),
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
}


def build(name):
    """编译一个着色器程序（按名字缓存，重复调用返回同一实例）。"""
    from pyglet.graphics.shader import Shader, ShaderProgram
    vert, frag = SOURCES[name]
    return ShaderProgram(Shader(vert, 'vertex'), Shader(frag, 'fragment'))


_CACHE = {}


def get(name):
    """取（并缓存）一个着色器程序。同一 GL 上下文内可复用。"""
    if name not in _CACHE:
        _CACHE[name] = build(name)
    return _CACHE[name]


def invalidate_cache():
    """上下文销毁后调用（缓存的 program id 已失效）。"""
    _CACHE.clear()
