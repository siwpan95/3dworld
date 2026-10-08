# -*- coding: utf-8 -*-
"""诺德之境时间线（logic 层）：60 秒五幕"显卡演示片"式电影化演出。

运镜参考了显卡测试类动画（鲁大师 UE5 3D 评测那类"硬件演示片"）的语言：
**一镜到底式穿透运动**——高空俯冲扎进山谷 → 贴地掠行擦过篝火 →
正对准穿进石拱门 → 急转身仰拍锁定巨龙 → 环绕追击 → 低角度仰拍龙焰
喷墙 → 推轨贴近符文 → 冲天拉升追龙远去 → 缓落回开场帧（循环闭合）。

所有周期都整除 60s（1/1.5/2/2.5/3/4/5/6/10/12/15/20/30），整圈自旋走
``_spin(t, 整数圈)``，保证 t=60 与 t=0 逐帧一致；龙在首尾都远在画面外。

与 core/nordic_scene.py 的布局常量对齐（导入而非复写），
与 logic/nordic_audio.py 的事件时刻共用常量（FIRE/RUNE/TAKEOFF）。
"""
import math
from bisect import bisect_right

from pyglet.math import Vec3

from core.nordic_scene import DRAGON_HOVER
from engine.mathkit import (quat_from_axis_angle, quat_mul,
                            quat_normalize, trs_decompose)

DURATION = 60.0

ACTS = (
    (0.0, '第一幕 · 荒原'),
    (9.5, '第二幕 · 进发'),
    (21.0, '第三幕 · 龙临'),
    (40.0, '第四幕 · 龙语'),
    (50.0, '第五幕 · 黎明'),
)

# 与音频/视觉共用的事件时刻（logic/nordic_audio.py 导入这些常量）
FIRE_T0, FIRE_T1 = 44.8, 48.4        # 龙焰喷向龙语墙
RUNE_T0, RUNE_T1 = 45.8, 49.8        # 符文逐字点亮
RUNE_FADE = (54.5, 59.5)             # 黎明降临，魔法微光渐熄
TAKEOFF_T = 48.3                     # 龙抬头起飞
ROAR_T = 24.2                        # 龙入画第一声吼（音频用）

_TAU = 2.0 * math.pi

# ---------------------------------------------------------------- 相机 --
# (时间, eye, target)：一镜到底的"演示片"运镜（详见模块 docstring）
_CAM_KEYS = (
    (0.0,  (44.0, 30.0, 26.0),  (0.0, 12.0, -60.0)),   # 高空全景（极光入画）
    (3.5,  (24.0, 16.0, 8.0),   (0.0, 8.0, -52.0)),    # 俯冲
    (6.5,  (8.0, 5.2, -6.0),    (0.0, 4.5, -40.0)),    # 压低高度
    (9.5,  (1.5, 3.0, -13.0),   (2.0, 3.2, -34.0)),    # 拉平贴地
    (11.8, (2.6, 3.3, -13.4),   (5.8, 1.1, -15.6)),    # 低角度扫过篝火
    (14.0, (0.5, 2.7, -18.0),   (-2.5, 3.8, -32.0)),   # 回中轴
    (16.2, (-3.5, 3.6, -22.5),  (-9.0, 5.2, -33.0)),   # 摆向拱门（过门准备）
    (18.2, (-11.6, 4.6, -25.6), (-11.6, 5.0, -34.0)),  # 正对拱门洞
    (19.8, (-11.6, 4.7, -30.6), (-9.5, 5.0, -38.0)),   # 一穿而过（t≈19.0 在门洞）
    (20.9, (-11.4, 4.5, -26.8), (-8.0, 5.0, -36.0)),   # 出拱门直行（北向）
    (22.3, (-8.6, 4.8, -26.0),  (-3.0, 6.5, -42.0)),   # 向东转（回中轴）
    (23.2, (-4.0, 5.6, -30.0),  (22.0, 14.0, -60.0)),  # 急转仰拍右侧天空（龙入场）
    (25.8, (0.5, 6.0, -29.5),   (26.0, 13.0, -64.0)),  # 锁定龙（follow 窗口）
    (28.6, (2.0, 6.6, -27.5),   (6.0, 11.0, -52.0)),
    (31.2, (0.0, 5.2, -23.5),   (-14.0, 9.0, -34.0)),  # 龙左掠，镜头左摆
    (33.4, (-6.0, 4.8, -23.5),  (-16.0, 8.4, -28.0)),  # 跟拍左翼转弯
    (35.8, (5.5, 4.2, -19.0),   (-2.0, 8.0, -33.0)),   # 绕到广场南侧（龙在前方高空）
    (38.2, (-1.5, 4.6, -19.5),  (0.5, 7.6, -31.0)),    # 龙减速逼近悬停位
    (40.6, (-0.5, 4.8, -24.5),  (0.0, 6.9, -34.0)),    # 收敛姿态（仰角）
    (42.8, (5.0, 5.4, -19.0),   (0.0, 6.6, -35.0)),    # 中轴南侧：巨龙+墙同框
    (45.0, (1.5, 5.0, -25.0),   (0.0, 6.2, -36.0)),    # 喷火起（仰视巨龙喷焰）
    (47.6, (2.6, 4.9, -27.0),   (0.0, 5.2, -40.0)),    # 缓推（符文点亮中）
    (49.3, (3.5, 4.6, -28.8),   (0.0, 4.8, -43.0)),    # 停在墙前（符文全亮）
    (50.6, (0.6, 4.8, -34.0),   (2.0, 8.5, -34.0)),    # 龙抬头拉升
    (52.6, (-2.0, 12.5, -30.0), (-6.0, 21.0, -52.0)),  # 冲天追龙（大仰角）
    (55.2, (6.0, 20.0, -17.0),  (-15.0, 27.0, -62.0)), # 半空回望（龙远去）
    (57.6, (21.0, 18.0, 4.0),   (-6.0, 12.0, -52.0)),  # 缓落
    (60.0, (44.0, 30.0, 26.0),  (0.0, 12.0, -60.0)),   # 归位 = 开场帧
)

# ------------------------------------------------------------- 飞龙轨迹 --
_DRAGON_KEYS = (
    (20.0, (52.0, 24.0, -86.0)),    # 远山之后（画外右上方）
    (24.0, (30.0, 15.0, -66.0)),    # 入场（配合 ROAR_T 一声吼）
    (27.0, (10.0, 11.0, -54.0)),    # 压低逼近
    (30.0, (-10.0, 8.6, -44.0)),    # 从龙语墙顶掠过（腹底 ≈ 9.1 > 墙顶 6.5）
    (33.0, (-18.0, 8.2, -30.0)),    # 左翼低掠
    (36.0, (-6.0, 8.0, -24.0)),     # 掉头
    (39.0, (3.0, 7.8, -27.5)),      # 减速对准悬停位
    (41.0, DRAGON_HOVER),           # 悬停到位（= 场景的悬停点）
    (46.5, DRAGON_HOVER),           # 悬停保持（旋翼喷火）
    (48.3, (0.0, 8.6, -30.5)),      # 抬头拉升（= TAKEOFF_T）
    (50.8, (4.0, 14.0, -37.0)),     # 起飞
    (53.0, (-6.0, 24.0, -58.0)),    # 高空越墙
    (56.0, (-30.0, 33.0, -92.0)),   # 远去
    (59.0, (-58.0, 42.0, -132.0)),
    (60.0, (-68.0, 46.0, -150.0)),  # 出画
)

_HOVER_T0, _HOVER_T1 = 40.0, 48.3    # 悬停微振窗口
_FOLLOW_T0, _FOLLOW_T1 = 23.0, 41.5  # 相机锁定跟随龙的窗口

# 翼扇动：频率 Hz（60·f(60)=60 整圈 → 循环闭合）与振幅（度）
_WING_FREQ = ((0.0, 0.85), (20.0, 0.85), (26.0, 1.30), (34.0, 1.00),
              (40.0, 1.55), (48.0, 1.85), (51.0, 2.20), (54.0, 1.50),
              (60.0, 1.00))
_WING_AMP = ((0.0, 18.0), (20.0, 18.0), (26.0, 34.0), (34.0, 26.0),
             (40.0, 42.0), (48.0, 50.0), (51.0, 55.0), (54.0, 40.0),
             (60.0, 22.0))


# ------------------------------------------------------------ 数学工具 --
def _lerp(a, b, u):
    return a + (b - a) * u


def _smooth(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3.0 - 2.0 * u)


def _ramp(t, t0, t1):
    if t1 <= t0:
        return 1.0 if t >= t1 else 0.0
    return max(0.0, min(1.0, (t - t0) / (t1 - t0)))


def _step(t, t0, t1):
    return _smooth(_ramp(t, t0, t1))


def _window(t, t0, t1, ease=2.0):
    return _smooth(_ramp(t, t0, t0 + ease)) * (1.0 - _smooth(_ramp(t, t1 - ease, t1)))


def _spin(t, turns, extra=0.0, t0=0.0, t1=0.0):
    return 360.0 * (turns * t / DURATION + extra * _step(t, t0, t1))


def _pulse(t, period, phase=0.0):
    return 0.5 + 0.5 * math.sin(_TAU * t / period + phase)


def _ease_keys(keys, subdiv=8):
    out = [(keys[0][0], keys[0][1])]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        for k in range(1, subdiv + 1):
            u = _smooth(k / subdiv)
            if isinstance(v0, Vec3):
                out.append((_lerp(t0, t1, k / subdiv),
                            Vec3(_lerp(v0.x, v1.x, u), _lerp(v0.y, v1.y, u),
                                 _lerp(v0.z, v1.z, u))))
            else:
                out.append((_lerp(t0, t1, k / subdiv), _lerp(v0, v1, u)))
    return out


def _sample(keys, t):
    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]
    times = [k[0] for k in keys]
    i = bisect_right(times, t) - 1
    t0, v0 = keys[i]
    t1, v1 = keys[i + 1]
    u = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
    if isinstance(v0, Vec3):
        return Vec3(_lerp(v0.x, v1.x, u), _lerp(v0.y, v1.y, u),
                    _lerp(v0.z, v1.z, u))
    return _lerp(v0, v1, u)


_CAM_EYE, _CAM_TGT = None, None
_DRG_POS = None
_WF, _WA = None, None


def _cache():
    global _CAM_EYE, _CAM_TGT, _DRG_POS, _WF, _WA
    if _CAM_EYE is None:
        _CAM_EYE = _ease_keys([(k[0], Vec3(*k[1])) for k in _CAM_KEYS])
        _CAM_TGT = _ease_keys([(k[0], Vec3(*k[2])) for k in _CAM_KEYS])
        _DRG_POS = _ease_keys([(k[0], Vec3(*k[1])) for k in _DRAGON_KEYS])
        _WF = _ease_keys(list(_WING_FREQ))
        _WA = _ease_keys(list(_WING_AMP))


# ---------------------------------------------------------------- 相机 --
def camera_pose(t):
    """相机 (eye, target)：追龙窗口内 target 平滑跟随龙位。"""
    _cache()
    eye = _sample(_CAM_EYE, t)
    tgt = _sample(_CAM_TGT, t)
    w = _window(t, _FOLLOW_T0, _FOLLOW_T1, ease=2.8)
    if w > 1e-4:
        d = dragon_position(t)
        tgt = Vec3(_lerp(tgt.x, d.x * 0.94, w), _lerp(tgt.y, d.y * 0.74 + 1.4, w),
                   _lerp(tgt.z, d.z, w))
    return eye, tgt


# ---------------------------------------------------------------- 飞龙 --
def _dragon_base(t):
    _cache()
    return _sample(_DRG_POS, t)


def dragon_position(t):
    """龙的节点位置：基础轨迹 + 悬停段的扇翼微振（周期 3/5s 整除 60）。"""
    p = _dragon_base(t)
    w = _window(t, _HOVER_T0, _HOVER_T1, ease=1.4)
    if w > 1e-4:
        jx = 0.10 * math.sin(_TAU * t / 5.0)
        jy = 0.16 * math.sin(_TAU * t / 3.0 + 0.7)
        return Vec3(p.x + jx * w, p.y + jy * w, p.z)
    return p


def _horizontal(d):
    return (d[0], 0.0, d[2])


def dragon_heading(t):
    """运动方向（单位向量）；悬停段特判为"面向龙语墙"。"""
    tc = max(20.0, min(t, 59.6))
    a, b = _dragon_base(tc - 0.35), _dragon_base(tc + 0.35)
    d = (b.x - a.x, b.y - a.y, b.z - a.z)
    ln = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
    if ln < 1e-3:                                # 悬停：面向墙（-z），微低头
        return (0.0, -0.10, -1.0)
    return (d[0] / ln, d[1] / ln, d[2] / ln)


def _look_quat(fwd, up=(0.0, 1.0, 0.0)):
    """前向 + 世界上 → 四元数（局部 +z 对准 fwd）。"""
    fx, fy, fz = fwd
    ln = math.sqrt(fx * fx + fy * fy + fz * fz) or 1.0
    fx, fy, fz = fx / ln, fy / ln, fz / ln
    rx = up[1] * fz - up[2] * fy
    ry = up[2] * fx - up[0] * fz
    rz = up[0] * fy - up[1] * fx
    rl = math.sqrt(rx * rx + ry * ry + rz * rz)
    if rl < 1e-4:                                # 视线与 up 共线：换参考轴
        return _look_quat(fwd, (1.0, 0.0, 0.0))
    rx, ry, rz = rx / rl, ry / rl, rz / rl
    ux = fy * rz - fz * ry
    uy = fz * rx - fx * rz
    uz = fx * ry - fy * rx
    m = [rx, ry, rz, 0.0, ux, uy, uz, 0.0, fx, fy, fz, 0.0, 0.0, 0.0, 0.0, 1.0]
    return quat_normalize(trs_decompose(m)[1])


def dragon_rotation(t):
    """朝向 = 运动方向 + 转弯侧倾（bank）。"""
    h = dragon_heading(t)
    q = _look_quat(h)
    h0 = _horizontal(dragon_heading(t - 0.45))
    h1 = _horizontal(dragon_heading(t + 0.45))
    turn = h0[0] * h1[2] - h0[2] * h1[0]       # 水平转向率（叉积 y 分量符号）
    bank = max(-36.0, min(36.0, turn * 300.0))
    if abs(bank) < 0.2:
        return q
    return quat_mul(q, quat_from_axis_angle((0.0, 0.0, 1.0), bank))


def wing_angle(t):
    """双翼扑动角（度，正 = 上抬）。"""
    _cache()
    amp = _sample(_WA, t)
    freq = _sample(_WF, t)
    return amp * math.sin(_TAU * t * freq)


def dragon_alpha(t):
    """龙的透明淡入淡出：出场时远处淡入、末段飞远后淡出。

    首尾 alpha=0 ⇒ t=0 与 t=60 帧完全一致（龙在开场机位也看不见），
    淡入发生在 100m 外的远空，配合 23.2s 才转向天空的镜头——观感是
    "从山后飞来"，而不是"凭空出现"。
    """
    return _step(t, 21.0, 23.6) * (1.0 - _step(t, 58.5, 60.0))


def fire_window(t):
    """龙焰强度窗口（0..1，与 fire_scale 同源时刻）。"""
    return _window(t, FIRE_T0, FIRE_T1, ease=0.9)


def fire_scale(t):
    """龙焰缩放（沿 +z 拉长；窗口外缩到 0.02 藏起来）。"""
    w = _window(t, FIRE_T0, FIRE_T1, ease=0.9)
    s = w * (0.88 + 0.30 * _pulse(t, 0.6))     # 0.6s 抖动整除 60
    return 0.02 + 1.75 * max(0.0, s)


# ------------------------------------------------------------ 场景物件 --
def campfire_scale(t):
    """篝火跳动（0.75/0.5s 双频叠加，均整除 60）。"""
    return 1.0 + 0.20 * math.sin(_TAU * t / 0.75) \
        + 0.10 * math.sin(_TAU * t / 0.5 + 1.1)


def campfire_rotation(t):
    return quat_from_axis_angle((0.0, 1.0, 0.0),
                                _spin(t, 1) + 6.0 * math.sin(_TAU * t / 2.5))


def rune_glow(t):
    """龙语墙符文强度：喷火期间逐字点亮 → 持续辉光 → 黎明渐熄。"""
    rise = _step(t, RUNE_T0, RUNE_T1)
    fade = _step(t, RUNE_FADE[0], RUNE_FADE[1])
    a = rise * (1.0 - fade)
    # 逐个点亮的四连脉冲（每 0.95s 一下，与音频上行音阶对齐）
    pulse = 0.0
    for k in range(4):
        tk = 46.0 + k * 0.95
        pulse += _step(t, tk, tk + 0.22) * math.exp(-max(0.0, t - tk) * 2.4)
    breathe = 1.0 + 0.12 * (2.0 * _pulse(t, 6.0) - 1.0)
    return a * breathe * 1.90 + pulse * 0.70


def rune_beam(t):
    """光柱升起比例 0..1（配合符文强度）。"""
    return _step(t, 46.4, 49.4) * (1.0 - _step(t, 54.0, 58.5))


def aurora_pose(k, t):
    """第 k 条极光帘幕：整圈慢旋 + 漂移 + 呼吸亮度。"""
    node = {
        'rotation': quat_from_axis_angle((0.0, 1.0, 0.0),
                                         _spin(t, 1) * (1.0 if k % 2 == 0 else -1.0)
                                         + k * 47.0),
        'translation': Vec3(3.0 * math.sin(_TAU * t / 30.0 + k * 1.7),
                            1.6 * math.sin(_TAU * t / 20.0 + k * 2.1),
                            1.2 * math.sin(_TAU * t / 15.0 + k * 0.9)),
        'bright': 0.62 + 0.28 * _pulse(t, (15.0, 20.0, 12.0, 30.0)[k], k * 1.3),
    }
    return node


def stars_bright(t):
    return 0.90 + 0.10 * _pulse(t, 6.0)


def window_bright(t):
    """木屋暖窗：炉火摇晃感。"""
    return 0.88 + 0.12 * _pulse(t, 5.0)


def moon_bright(t):
    return 1.0 + 0.05 * _pulse(t, 30.0)


def fire_light(t):
    """篝火点光源强度系数（含跳动）。"""
    return 0.88 + 0.22 * _pulse(t, 0.75) + 0.10 * _pulse(t, 0.5, 1.1)
