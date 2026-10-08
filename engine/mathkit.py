# -*- coding: utf-8 -*-
"""纯数学工具箱：矩阵 / 四元数 / 插值 / 采样序列。

**不 import pyglet.gl**，因此可在无窗口环境单测（CI 友好）。

矩阵约定（与 pyglet ``Mat4`` 一致）：

- 扁平序列为**列主序**：``m[12], m[13], m[14]`` 是平移分量；
- ``mat4_mul(a, b)`` 表示"先施加 b 再施加 a"（与 ``a @ b`` 同义）；
- 变换组合顺序统一为 ``T * R * S``（glTF 2.0 语义）。

四元数约定：``(w, x, y, z)``，与 pyglet ``Quaternion`` 顺序一致；
但**旋转矩阵一律用本模块的** ``quat_to_mat4`` —— pyglet 的
``Quaternion.to_mat4`` 输出是标准旋转矩阵的转置（实测 90° 绕 Y 会把
+X 映到 +Z），左右手系会反。
"""
import math

from pyglet.math import Mat4, Vec3

__all__ = [
    'clamp', 'saturate', 'lerp', 'smoothstep', 'ease_in_out', 'ease_out_cubic',
    'mat4_identity', 'mat4_from_trs', 'mat4_mul', 'mat4_transpose',
    'mat4_inverse', 'mat4_to_Mat4', 'mat4_from_Mat4', 'normal_matrix',
    'trs_decompose', 'look_at_matrix',
    'quat_from_axis_angle', 'quat_mul', 'quat_normalize', 'slerp',
    'quat_to_mat4', 'mat3_from_mat4',
    'hammersley', 'hemisphere_kernel', 'fibonacci_sphere', 'hash3',
]


# ------------------------------------------------------------------ 标量 --
def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def saturate(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(edge0, edge1, x):
    """GLSL 语义的 smoothstep：边界外钳制，边界内 3t²-2t³。"""
    t = saturate((x - edge0) / (edge1 - edge0)) if edge1 != edge0 else 0.0
    return t * t * (3.0 - 2.0 * t)


def ease_in_out(t):
    """平滑起停（默认缓动）。"""
    t = saturate(t)
    return t * t * (3.0 - 2.0 * t)


def ease_out_cubic(t):
    t = saturate(t)
    return 1.0 - (1.0 - t) ** 3


# ------------------------------------------------------------------ 矩阵 --
def mat4_identity():
    return (1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0)


def mat4_mul(a, b):
    """列主序 4x4 相乘（先 b 后 a），O(64) 朴素实现，可读优先。"""
    out = [0.0] * 16
    for c in range(4):                       # b 的列
        b0, b1, b2, b3 = b[c * 4:c * 4 + 4]
        for r in range(4):                   # a 的行
            out[c * 4 + r] = (a[r] * b0 + a[4 + r] * b1
                              + a[8 + r] * b2 + a[12 + r] * b3)
    return tuple(out)


def mat4_transpose(m):
    return tuple(m[(i % 4) * 4 + (i // 4)] for i in range(16))


def mat4_from_trs(translation, rotation, scale):
    """TRS -> 列主序矩阵（T * R * S）。rotation 为 (w,x,y,z) 四元数。"""
    w, x, y, z = rotation
    tx, ty, tz = translation
    sx, sy, sz = scale
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    r00, r10, r20 = 1 - 2 * (yy + zz), 2 * (xy + wz), 2 * (xz - wy)
    r01, r11, r21 = 2 * (xy - wz), 1 - 2 * (xx + zz), 2 * (yz + wx)
    r02, r12, r22 = 2 * (xz + wy), 2 * (yz - wx), 1 - 2 * (xx + yy)
    return (r00 * sx, r10 * sx, r20 * sx, 0.0,
            r01 * sy, r11 * sy, r21 * sy, 0.0,
            r02 * sz, r12 * sz, r22 * sz, 0.0,
            tx, ty, tz, 1.0)


def mat4_inverse(m):
    """通用 4x4 求逆（高斯-约当消元）。奇异矩阵返回 None。

    用途：法线矩阵（inverse-transpose）、屏幕射线反投影、glTF 蒙皮的逆绑定
    矩阵。手写消元而不是抄公式表——抄表的版本曾在索引约定上翻过车
    （列主序下 a01 到底是哪一位，两种命名流派正好相反）。
    """
    # 取行：列主序里第 j 行 = [m[j], m[4+j], m[8+j], m[12+j]]
    rows = [[m[j], m[4 + j], m[8 + j], m[12 + j],
             1.0 if j == 0 else 0.0, 1.0 if j == 1 else 0.0,
             1.0 if j == 2 else 0.0, 1.0 if j == 3 else 0.0]
            for j in range(4)]
    for col in range(4):
        # 选主元（部分选主元，避免小除数放大误差）
        pivot = max(range(col, 4), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-12:
            return None
        if pivot != col:
            rows[col], rows[pivot] = rows[pivot], rows[col]
        inv_p = 1.0 / rows[col][col]
        rows[col] = [v * inv_p for v in rows[col]]
        for r in range(4):
            if r == col:
                continue
            f = rows[r][col]
            if f == 0.0:
                continue
            rows[r] = [a - f * b for a, b in zip(rows[r], rows[col])]
    # 右半部分即逆矩阵；按列主序回填：inv[col*4 + row] = aug[row][4 + col]
    return tuple(rows[row][4 + col] for col in range(4) for row in range(4))


def mat3_from_mat4(m):
    """取左上 3x3（列主序 9 元组）。"""
    return (m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10])


def normal_matrix(model):
    """法线矩阵 = 左上 3x3 的逆转置。

    非均匀缩放（glTF 节点的 scale 允许非均匀）下，直接用模型矩阵变换法线
    会把法线拉歪；必须走逆转置。返回 9 元组（列主序 mat3）。
    """
    inv = mat4_inverse(model)
    if inv is None:
        return mat3_from_mat4(model)
    # 逆转置：先转置再取左上 3x3
    t = mat4_transpose(inv)
    return mat3_from_mat4(t)


def trs_decompose(m):
    """列主序矩阵 -> (translation, rotation(wxyz), scale)。

    用于 glTF ``node.matrix`` 与 TRS 的统一：规范规定 matrix 必须可分解为
    TRS，这里就是那个分解（无斜切的常规仿射变换）。
    """
    tx, ty, tz = m[12], m[13], m[14]
    cx = (m[0], m[1], m[2])
    cy = (m[4], m[5], m[6])
    cz = (m[8], m[9], m[10])
    sx = math.sqrt(sum(c * c for c in cx))
    sy = math.sqrt(sum(c * c for c in cy))
    sz = math.sqrt(sum(c * c for c in cz))
    # 负行列式 = 镜像，规范约定由 scale.x 取负承载
    if (m[0] * (m[5] * m[10] - m[6] * m[9])
            - m[4] * (m[1] * m[10] - m[2] * m[9])
            + m[8] * (m[1] * m[6] - m[2] * m[5])) < 0.0:
        sx = -sx
    if sx == 0 or sy == 0 or sz == 0:
        return (tx, ty, tz), (1.0, 0.0, 0.0, 0.0), (sx, sy, sz)
    rx = (cx[0] / sx, cx[1] / sx, cx[2] / sx)
    ry = (cy[0] / sy, cy[1] / sy, cy[2] / sy)
    rz = (cz[0] / sz, cz[1] / sz, cz[2] / sz)
    # 矩阵 -> 四元数（Shepperd 法，取最大迹分支避免除零）
    trace = rx[0] + ry[1] + rz[2]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * s
        qx = (ry[2] - rz[1]) / s
        qy = (rz[0] - rx[2]) / s
        qz = (rx[1] - ry[0]) / s
    elif rx[0] > ry[1] and rx[0] > rz[2]:
        s = math.sqrt(1.0 + rx[0] - ry[1] - rz[2]) * 2.0
        qw = (ry[2] - rz[1]) / s
        qx = 0.25 * s
        qy = (ry[0] + rx[1]) / s
        qz = (rz[0] + rx[2]) / s
    elif ry[1] > rz[2]:
        s = math.sqrt(1.0 + ry[1] - rx[0] - rz[2]) * 2.0
        qw = (rz[0] - rx[2]) / s
        qx = (ry[0] + rx[1]) / s
        qy = 0.25 * s
        qz = (rz[1] + ry[2]) / s
    else:
        s = math.sqrt(1.0 + rz[2] - rx[0] - ry[1]) * 2.0
        qw = (rx[1] - ry[0]) / s
        qx = (rz[0] + rx[2]) / s
        qy = (rz[1] + ry[2]) / s
        qz = 0.25 * s
    return (tx, ty, tz), quat_normalize((qw, qx, qy, qz)), (sx, sy, sz)


def mat4_to_Mat4(m):
    """列主序 16 元组 -> pyglet ``Mat4``。"""
    return Mat4(*m)


def mat4_from_Mat4(m):
    """pyglet ``Mat4`` -> 列主序 16 元组。"""
    return tuple(m)


def look_at_matrix(eye, target, up):
    """手写 look-at 视图矩阵（纯标量运算，跨 pyglet 2.x 版本稳定）。

    不用 ``Mat4.look_at``：部分 pyglet 2.x 过渡版的 Vec3.cross / cross_product
    改名会让它在运行期炸掉。参数支持 Vec3 或三元组。
    """
    ex, ey, ez = eye[0], eye[1], eye[2]
    fx = target[0] - ex
    fy = target[1] - ey
    fz = target[2] - ez
    fl = math.sqrt(fx * fx + fy * fy + fz * fz) or 1.0
    fx, fy, fz = fx / fl, fy / fl, fz / fl
    # s = normalize(f × up)
    sx = fy * up[2] - fz * up[1]
    sy = fz * up[0] - fx * up[2]
    sz = fx * up[1] - fy * up[0]
    sl = math.sqrt(sx * sx + sy * sy + sz * sz) or 1.0
    sx, sy, sz = sx / sl, sy / sl, sz / sl
    # u = s × f
    ux, uy, uz = sy * fz - sz * fy, sz * fx - sx * fz, sx * fy - sy * fx
    return (sx, ux, -fx, 0.0,
            sy, uy, -fy, 0.0,
            sz, uz, -fz, 0.0,
            -(sx * ex + sy * ey + sz * ez),
            -(ux * ex + uy * ey + uz * ez),
            (fx * ex + fy * ey + fz * ez),
            1.0)


# -------------------------------------------------------------- 四元数 --
def quat_from_axis_angle(axis, angle_deg):
    """轴角 -> 四元数 (w,x,y,z)。axis 会被归一化。"""
    ax, ay, az = axis
    norm = math.sqrt(ax * ax + ay * ay + az * az) or 1.0
    ax, ay, az = ax / norm, ay / norm, az / norm
    half = math.radians(angle_deg) * 0.5
    s = math.sin(half)
    return (math.cos(half), ax * s, ay * s, az * s)


def quat_mul(a, b):
    """四元数相乘（先 b 后 a）。"""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def quat_normalize(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def slerp(q0, q1, t):
    """四元数球面插值：点积为负取最短弧，近平行退化为 nlerp。"""
    d = q0[0] * q1[0] + q0[1] * q1[1] + q0[2] * q1[2] + q0[3] * q1[3]
    if d < 0.0:
        q1 = (-q1[0], -q1[1], -q1[2], -q1[3])
        d = -d
    if d > 0.9995:
        q = tuple(a + (b - a) * t for a, b in zip(q0, q1))
        return quat_normalize(q)
    theta = math.acos(min(1.0, d))
    s = math.sin(theta)
    w0, w1 = math.sin((1 - t) * theta) / s, math.sin(t * theta) / s
    return tuple(a * w0 + b * w1 for a, b in zip(q0, q1))


def quat_to_mat4(q):
    """四元数 -> 列主序旋转矩阵（右手法，glTF 语义）。"""
    return mat4_from_trs((0.0, 0.0, 0.0), q, (1.0, 1.0, 1.0))


# ------------------------------------------------------------ 采样序列 --
def hammersley(i, n):
    """Hammersley 序列第 i 点（ Radical Inverse base-2），用于重要性采样。"""
    bits = i
    bits = (bits << 16) | (bits >> 16)
    bits = ((bits & 0x55555555) << 1) | ((bits & 0xAAAAAAAA) >> 1)
    bits = ((bits & 0x33333333) << 2) | ((bits & 0xCCCCCCCC) >> 2)
    bits = ((bits & 0x0F0F0F0F) << 4) | ((bits & 0xF0F0F0F0) >> 4)
    bits = ((bits & 0x00FF00FF) << 8) | ((bits & 0xFF00FF00) >> 8)
    radical = float(bits) * 2.3283064365386963e-10
    return (float(i) / float(n), radical)


def hemisphere_kernel(n, up_bias=0.05):
    """SSAO 半球采样核：切空间半球内、靠近中心的样本更密（衰减权重）。

    返回 [(x,y,z), ...]，z >= 0（沿法线方向）。``up_bias`` 把最靠近切面的
    那一层样本抬起来一点，避免平地自遮挡产生的噪点。
    """
    out = []
    for i in range(n):
        u, v = hammersley(i, n)
        # 按 1-t² 分布半径：靠近原点更密（cos 加权半球采样的简化）
        r = math.sqrt(max(0.0, 1.0 - u * u))
        phi = 2.0 * math.pi * v
        x, y = math.cos(phi) * r, math.sin(phi) * r
        z = math.sqrt(max(1e-6, 1.0 - x * x - y * y))
        z = max(z, up_bias)
        out.append((x, y, z))
    return out


def fibonacci_sphere(n):
    """斐波那契球面点集（确定性、近似均匀），用于 IBL 卷积/环境积分。"""
    out = []
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(n):
        y = 1.0 - (2.0 * i + 1.0) / n
        r = math.sqrt(max(0.0, 1.0 - y * y))
        theta = golden * i
        out.append((math.cos(theta) * r, y, math.sin(theta) * r))
    return out


def hash3(x, y, z):
    """确定性 3D 哈希 -> [0,1)。用于生成可复现的噪声纹理。"""
    h = (x * 374761393 + y * 668265263 + z * 2147483647) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFFFF) / float(0xFFFFFF)


def vec3(v):
    """三元组 -> pyglet Vec3（顺手工具，避免到处解包）。"""
    return Vec3(v[0], v[1], v[2])
