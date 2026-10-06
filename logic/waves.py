# -*- coding: utf-8 -*-
"""Gerstner 波数学（纯三角函数，不 import pyglet.gl，可无窗口单测）。

公式来源：Gerstner wave（= Trotchoidal wave），GPU Gems 1 第 1 章
"Effective Water Simulation from Physical Models"（Finch）给出的是它的
四个叠加波常见写法；这里用的是**解析高度场**的等价形式：

    对每个波 i（单位方向 d_i、波数 k_i、角频率 w_i、振幅 A_i）：
        phase_i(x, z, t) = k_i * dot(d_i, (x, z)) - w_i * t
        h(x, z, t)      = Σ A_i * sin(phase_i)                       ← 高度
        ∂h/∂x           = Σ A_i * k_i * d_i.x * cos(phase_i)         ← x 向斜率
        ∂h/∂z           = Σ A_i * k_i * d_i.y * cos(phase_i)         ← z 向斜率

    深水色散关系：w = sqrt(g * k)，g = 9.81；由它把波长换算成波速，
    所以同一份波长参数在 CPU 与 GPU 上得到的相位推进速度完全一致。

法线用高度场梯度显式构造（不是有限差分，避免步长误差）：

    n = normalize((-∂h/∂x, 1, -∂h/∂z))

踩坑记录（写给未来的自己）：

- **水平位移不做**。完整 Gerstner 还会把顶点沿 d_i 做水平位移
  （Q_i * A_i * cos(phase)），但那个映射不可逆：给定世界坐标 (x, z) 要反解
  出"未位移坐标"只能牛顿迭代。demo 里 CPU 侧（浮标吃水/倾斜）必须能和
  GPU 侧（顶点着色器位移）用**同一个解析函数**，所以这里只保留竖直高度项，
  两边严格一致；代价是波峰不会向前翻卷（不做白沫翻卷也确实够用）。
- **相位里用 z 而不是 y**，因为世界坐标的"水平面"是 XZ 平面（Y 轴朝上），
  方向向量按 (dx, dz) 给。写错轴的话波看起来会像贴在竖直墙上。
- 单位与量级：A 是米级振幅（0.1~1.2），波长 3~30，g=9.81。
  振幅超过约 0.6 * 波长 / (2π) 时解析高度场会出现自交（波峰互相穿插），
  预设值都留了余量，调参时注意。
"""
import math

__all__ = [
    'GRAVITY', 'Wave', 'clamp01', 'gerstner_height', 'gerstner_gradient',
    'gerstner_normal', 'sample_surface', 'default_waves', 'wave_presets',
    'scaled_preset',
]

GRAVITY = 9.81


class Wave:
    """单个波分量：振幅 A、波长 L、方向 d（会被归一化）、陡度 Q。

    陡度 Q 目前只作为"谱形"文档字段保留（见模块 docstring：不做水平位移），
    调参时用它记录各分量相对权重，不参与计算。
    """

    __slots__ = ('amplitude', 'wavelength', 'direction', 'steepness')

    def __init__(self, amplitude, wavelength, direction=(1.0, 0.0), steepness=0.25):
        if wavelength <= 0.0:
            raise ValueError('wavelength 必须为正')
        dx, dz = float(direction[0]), float(direction[1])
        norm = math.hypot(dx, dz)
        if norm <= 1e-12:
            raise ValueError('direction 不能是零向量')
        self.amplitude = float(amplitude)
        self.wavelength = float(wavelength)
        self.direction = (dx / norm, dz / norm)   # 归一化：方向只表示朝向
        self.steepness = float(steepness)

    # ---- 派生量（每次访问重算，避免对象间状态不同步） ----
    @property
    def wavenumber(self):
        """k = 2π / L（弧度 / 米）。"""
        return 2.0 * math.pi / self.wavelength

    @property
    def omega(self):
        """深水色散：w = sqrt(g * k)，波速 c = w / k = sqrt(g / k)。"""
        return math.sqrt(GRAVITY * self.wavenumber)

    def phase(self, x, z, t=0.0):
        """相位 k * dot(d, (x, z)) - w * t。"""
        k = self.wavenumber
        return k * (self.direction[0] * x + self.direction[1] * z) - self.omega * t

    def __repr__(self):
        return (f'Wave(A={self.amplitude:.3f}, L={self.wavelength:.2f}, '
                f'd=({self.direction[0]:+.2f},{self.direction[1]:+.2f}), Q={self.steepness:.2f})')


def clamp01(v):
    """钳到 [0, 1]（预设强度用）。"""
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def gerstner_height(x, z, t=0.0, waves=()):
    """波面高度 h(x, z, t) = Σ A_i sin(k_i·(d_i·(x,z)) - w_i t)。"""
    total = 0.0
    for w in waves:
        total += w.amplitude * math.sin(w.phase(x, z, t))
    return total


def gerstner_gradient(x, z, t=0.0, waves=()):
    """波面梯度 (∂h/∂x, ∂h/∂z)：逐项 A k cos(phase)。"""
    gx = gz = 0.0
    for w in waves:
        k = w.wavenumber
        c = w.amplitude * k * math.cos(w.phase(x, z, t))
        gx += c * w.direction[0]
        gz += c * w.direction[1]
    return gx, gz


def gerstner_normal(x, z, t=0.0, waves=()):
    """解析法线 n = normalize((-∂h/∂x, 1, -∂h/∂z))，返回三元组。

    注意：这是"未位移坐标处的波面法线"。因为 CPU/GPU 两侧都用未位移坐标
    求值（见模块 docstring），浮标倾斜与水面着色不会出现几何错位。
    """
    gx, gz = gerstner_gradient(x, z, t, waves)
    nx, ny, nz = -gx, 1.0, -gz
    inv = 1.0 / math.sqrt(nx * nx + ny * ny + nz * nz)
    return nx * inv, ny * inv, nz * inv


def sample_surface(x, z, t=0.0, waves=()):
    """一次给出 (高度, 法线)，省掉浮标逐帧的重复遍历。"""
    return gerstner_height(x, z, t, waves), gerstner_normal(x, z, t, waves)


# ---------------------------------------------------------------- 预设 --
def _dirs(n):
    """均匀撒 n 个方向（黄金角错开，避免出现整齐的网格状波峰）。"""
    out = []
    for i in range(n):
        a = math.pi * (3.0 - math.sqrt(5.0)) * i          # 黄金角
        out.append((math.cos(a), math.sin(a)))
    return out


def default_waves(scale=1.0):
    """默认波组：8 个分量，波长 2.6~24，振幅按 1/L 衰减（自然海况谱形）。

    ``scale`` 只缩放振幅，不改波长——波长决定波速与"浪的尺度"，
    缩放振幅才是"浪变大"，两者混在一起调会让画面节奏跟着乱。
    """
    dirs = _dirs(8)
    # (振幅, 波长) 从大到小：长波给大起伏，短波给细节
    spec = [(0.46, 24.0), (0.34, 15.0), (0.26, 9.5), (0.18, 6.2),
            (0.13, 4.3), (0.09, 3.4), (0.07, 2.9), (0.05, 2.6)]
    return [Wave(a * scale, L, dirs[i], 0.22) for i, (a, L) in enumerate(spec)]


# 三档强度预设：(名称, 放大系数, 波数)——给 demos/water_waves.py 的 1/2/3 键用。
# 放大系数别调太猛：振幅到 2.0 时波高（峰谷 ~4m）相对相机眼高（~9m）太大，
# 画面上会变成一片"白色冰川"而不是浪——白沫覆盖 + 菲涅尔掠射 + 雾一起作用，
# 亮部直接糊成整片。1.15 这一档的峰谷约 2.2m，仍然明显是"大浪"但画面读得出来。
WAVE_PRESETS = (
    ('微风', 0.42, 6),
    ('中浪', 1.00, 8),
    ('风暴', 1.15, 12),
)


def wave_presets():
    """返回 [(名称, 放大系数, 波数)] —— 供 demo 显示与切换。"""
    return list(WAVE_PRESETS)


def scaled_preset(index):
    """按预设编号生成波组（越强：振幅越大 + 分量越多）。"""
    name, amp_scale, count = WAVE_PRESETS[index % len(WAVE_PRESETS)]
    dirs = _dirs(count)
    spec = [(0.46, 24.0), (0.34, 15.0), (0.26, 9.5), (0.18, 6.2),
            (0.13, 4.3), (0.09, 3.4), (0.07, 2.9), (0.05, 2.6),
            (0.05, 2.2), (0.04, 1.9), (0.035, 1.7), (0.03, 1.5)]
    waves = [Wave(a * amp_scale, L, dirs[i], 0.22)
             for i, (a, L) in enumerate(spec[:count])]
    return name, waves
