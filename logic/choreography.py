# -*- coding: utf-8 -*-
"""pyglet_3d_demo 的五幕编排（logic 层，纯数学，无 GL，可无窗口单测）。

时长 4s → **48s**，内容从"环形浮动 + 自旋"升级为一条有剧本的演出线：

===== ========== ==========================================================
幕     时间        内容
===== ========== ==========================================================
一     0 – 10s    **升起**：10 个环形立方体错峰从地面下升起，随高度点亮
二     10 – 20s   **环形波**：相位错开的行波（径向 + 垂直），辉光扫过环带
三     20 – 30s   **机械臂**：转台 + 两段臂解算到取件位，夹起宝石搬到台座
四     30 – 40s   **螺旋**：立方体收拢、抬升成螺旋柱，核心放大脉冲；轨道球退让
五     40 – 48s   **归位**：螺旋展开、回落、沉入地面，姿态精确回到 t=0 帧
===== ========== ==========================================================

**无缝循环的硬约束**：``pose(0) == pose(48)``，且各通道在段边界上连续
（不连续的话循环回绕会看到"跳帧"）。实现手法是每一幕都用"窗口函数"
（``_smooth(_ramp(...))`` 这类 0→1→0 的包络）叠加在基础姿态上：
包络在段边界上恰好为 0，于是拼接处天然连续。自旋则统一取
``360° × 整数圈 × t / DURATION``，首尾相差整数圈 ⇒ 姿态相同。

**机械臂角度是解出来的，不是凑的**：取件位/放件位用两连杆逆运动学
（余弦定理 + 方位角）算出 ``ARM_KEYS`` 里的角度，``tests/run_tests.py``
会断言"夹爪世界坐标落在宝石/台座 0.9 单位内"，角度一旦被改坏测试就红。
"""
import bisect
import math

from pyglet.math import Vec3

from logic.animation import (Channel, Sampler, Timeline, quat_from_axis_angle)

# --------------------------------------------------------------- 常量 --
DURATION = 48.0

A2, A3, A4, A5 = 10.0, 20.0, 30.0, 40.0        # 各幕起点（第五幕 40 → 48）

RING_N = 10
RING_R, RING_Y = 5.0, 0.5
RING_SUNK_Y = -3.4                             # 沉入地面以下（地面不透明，看不见）
CENTER_BASE = Vec3(0.0, 1.0, 0.0)
ORB_N = 4

ARM_BASE = Vec3(-6.0, 0.0, -3.0)               # 机械臂底座（含转台）
SHOULDER_H = 0.6                               # 转台顶面 = 肩关节高度
L1, L2 = 2.4, 1.8                              # 大臂 / 小臂长度（与渲染几何一致）
GEM_REST = Vec3(-3.5, 1.15, -3.5)              # 宝石静置点（八面体中心离地 1.15）
PLINTH = Vec3(-6.5, 0.0, -0.5)                 # 台座位置
PLINTH_H = 1.6                                 # 台座高度
GEM_PLACED = Vec3(PLINTH.x, PLINTH_H + 1.15, PLINTH.z)   # 宝石落座点
GEM_PICK_T, GEM_PLACE_T = 22.0, 26.5           # 取件 / 放件时刻

CAM, CAM_TGT = 'camera', 'cam_target'

# 幕表：(起始时刻, 标题, 副标题)——HUD 与时间轴 UI 直接消费
ACTS = (
    (0.0,  '第一幕 · 升起', '环形立方体错峰升起，随高度点亮'),
    (10.0, '第二幕 · 环形波', '相位错开的行波沿环带扫过'),
    (20.0, '第三幕 · 机械臂', '逆运动学取件：夹起宝石送上台座'),
    (30.0, '第四幕 · 螺旋', '立方体收拢成螺旋柱，核心脉冲放大'),
    (40.0, '第五幕 · 归位', '螺旋展开回落，姿态精确回到开场帧'),
)


# ------------------------------------------------------------ 数学工具 --
def _lerp(a, b, u):
    return a + (b - a) * u


def _smooth(u):
    """三次 smoothstep：0→1 且两端一阶导为 0（避免机械感启停）。"""
    u = max(0.0, min(1.0, u))
    return u * u * (3.0 - 2.0 * u)


def _ramp(t, t0, t1):
    """t 在 [t0,t1] 内的归一化位置（钳制）。"""
    if t1 <= t0:
        return 1.0 if t >= t1 else 0.0
    return max(0.0, min(1.0, (t - t0) / (t1 - t0)))


def _window(t, t0, t1, ease=2.0):
    """窗口包络：在 [t0, t0+ease] 升到 1、[t1-ease, t1] 降回 0，端点为 0。

    幕间拼接就靠它——包络在段边界上恒为 0，叠加项不会造成跳变。
    """
    return _smooth(_ramp(t, t0, t0 + ease)) * (1.0 - _smooth(_ramp(t, t1 - ease, t1)))


def _spin_deg(t, turns, phase_deg=0.0):
    """整数圈自旋角：t=0 与 t=DURATION 相差整数圈 ⇒ 循环连续。"""
    return 360.0 * turns * t / DURATION + phase_deg


def _ease_keys(keys, subdiv=4):
    """把稀疏关键帧插值成稠密关键帧，用 smoothstep 做缓入缓出。

    ``keys`` = [(t, value), ...]（value 为 Vec3 / float）。
    LINEAR 采样器 + 稠密缓动关键帧 = 手写 easing，无需给采样器加 CUBICSPLINE。
    """
    out = [(keys[0][0], keys[0][1])]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        for k in range(1, subdiv + 1):
            u = _smooth(k / subdiv)
            t = _lerp(t0, t1, k / subdiv)
            out.append((t, _lerp(v0, v1, u) if not isinstance(v0, Vec3)
                        else Vec3(_lerp(v0.x, v1.x, u), _lerp(v0.y, v1.y, u),
                                  _lerp(v0.z, v1.z, u))))
    return out


def _arm_ik(target_world):
    """两连杆逆运动学：返回 (转台 Y 角, 大臂 Z 角, 小臂相对 Z 角)，单位度。

    几何约定（与 presentation/world_renderer.build_arm 一致）：肩关节在
    ``ARM_BASE + (0, SHOULDER_H, 0)``，大臂沿局部 +Y、绕 Z 旋转，小臂接在
    大臂末端再绕 Z 旋转；转台绕 Y 旋转负责方位。绕 Z 转 θ 把 +Y 映到
    ``(-sinθ, cosθ)``，所以"从 +Y 偏向 +X"对应 θ 为负。
    """
    d = Vec3(target_world.x - ARM_BASE.x,
             target_world.y - ARM_BASE.y - SHOULDER_H,
             target_world.z - ARM_BASE.z)
    horiz = math.hypot(d.x, d.z)
    # 转台：局部 +X 转到水平目标方向
    turret = math.degrees(math.atan2(-d.z, d.x)) if horiz > 1e-9 else 0.0
    # 平面内两连杆
    r = min(math.hypot(horiz, d.y), L1 + L2 - 1e-6)
    cos_elbow = max(-1.0, min(1.0, (L1 * L1 + L2 * L2 - r * r) / (2 * L1 * L2)))
    elbow = math.degrees(math.acos(cos_elbow))          # 肘部内角
    theta2 = -(180.0 - elbow)                            # 小臂相对角（向后折）
    beta = math.degrees(math.atan2(
        L2 * math.sin(math.radians(theta2)),
        L1 + L2 * math.cos(math.radians(theta2))))
    theta1 = -math.degrees(math.atan2(horiz, d.y)) - beta
    return turret, theta1, theta2


# ------------------------------------------------------- 机械臂编排 --
def _arm_sway(t):
    """静止时三关节的轻微摆动（周期 12s，48s 内 4 个整周期 ⇒ 循环连续）。"""
    base = 2 * math.pi * t / 12.0
    return (3.0 * math.sin(base + 1.2),          # 转台方位
            6.0 * math.sin(base),                # 大臂
            4.0 * math.sin(base + 0.6))          # 小臂


def _arm_target_keys():
    """第三幕关键帧：(t, 目标绝对角)，``None`` 表示"该帧偏移归零、姿态=摆动"。

    取件/放件位由 ``_arm_ik`` 解出；中间位是手调的搬运弧线。注意这些是
    **最终姿态角**，构建偏移时会减去该时刻的摆动量，这样姿态才精确落在
    目标上（否则摆动会被叠加成误差——实测放件位会偏 0.44 单位）。
    """
    pick = _arm_ik(GEM_REST)
    place = _arm_ik(GEM_PLACED)
    return (
        (A3,          None),                   # 幕首：姿态交给摆动通道
        (21.2,        pick),                   # 到达取件位
        (22.4,        pick),                   # 夹持保持（GEM_PICK_T=22.0 在区间内）
        (24.2,        (-34.0, -22.0, -74.0)),  # 抬升搬运
        (GEM_PLACE_T, place),                  # 到达放件位（26.5）
        (28.2,        (-52.0, -16.0, -58.0)),  # 收臂
        (A4,          None),                   # 幕尾：偏移归零
    )


def _arm_offset_frames(t):
    """第三幕偏移量（叠加在摆动之上）：关键帧 + smoothstep 缓动。"""
    keys = _arm_target_keys()
    if t <= keys[0][0] or t >= keys[-1][0]:
        return (0.0, 0.0, 0.0)
    frames = []
    for tk, target in keys:
        sway = _arm_sway(tk)
        frames.append((tk, (0.0, 0.0, 0.0) if target is None
                       else tuple(a - b for a, b in zip(target, sway))))
    times = [f[0] for f in frames]
    i = max(0, min(len(frames) - 2, bisect.bisect_right(times, t) - 1))
    t0, v0 = frames[i]
    t1, v1 = frames[i + 1]
    u = _smooth(_ramp(t, t0, t1))
    return tuple(_lerp(a, b, u) for a, b in zip(v0, v1))


def arm_pose(t):
    """机械臂三节点在 t 时刻的绝对角度 (turret_y, seg1_z, seg2_z)，单位度。"""
    sway = _arm_sway(t)
    off = _arm_offset_frames(t)
    return tuple(s + o for s, o in zip(sway, off))


# ------------------------------------------------ 各对象姿态函数 --
def ring_pose(i, t, n=RING_N):
    """环形立方体 i 的 (translation, rotation, scale, tint, glow)。"""
    phase = 2 * math.pi * i / n
    rise = _smooth(_ramp(t, 0.4 + 0.42 * i, 2.4 + 0.42 * i))     # 第一幕错峰升起

    # --- 半径：第二幕径向行波 → 第四幕收拢 → 第五幕展开 ---
    if t < A2:
        r = RING_R
    elif t < A3:
        wave = _window(t, A2, A3) * math.sin(2 * math.pi * (t - A2) / 4.0 - phase)
        r = RING_R + 0.45 * wave
    elif t < A4:
        r = RING_R
    elif t < A5:
        r = _lerp(RING_R, 1.4, _smooth(_ramp(t, A4, A4 + 7.0)))
    else:
        r = _lerp(1.4, RING_R, _smooth(_ramp(t, A5 + 1.0, A5 + 5.5)))

    # --- 高度 ---
    if t < A2:
        y = _lerp(RING_SUNK_Y, RING_Y, rise)
    elif t < A3:
        wave = _window(t, A2, A3) * (0.5 + 0.5 * math.sin(2 * math.pi * (t - A2) / 4.0 - phase))
        y = RING_Y + 0.55 * wave
    elif t < A4:
        y = RING_Y + 0.06 * math.sin(2 * math.pi * (t - A3) / 5.0)
    elif t < A5:
        y = _lerp(RING_Y, 3.6 + 0.42 * i, _smooth(_ramp(t, A4, A4 + 7.0)))
    else:
        y = _lerp(3.6 + 0.42 * i, RING_Y, _smooth(_ramp(t, A5 + 1.0, A5 + 5.5)))
        y = _lerp(y, RING_SUNK_Y, _smooth(_ramp(t, A5 + 5.8, A5 + 7.2)))

    # --- 自旋：整数圈 + 第四幕额外整圈（含在幕内，不破坏循环） ---
    turns = 2 + (i % 3)
    deg = _spin_deg(t, turns, 36.0 * i) + 360.0 * _smooth(_ramp(t, A4 + 1, A5 - 1))

    # --- 亮度 / 色相 / 自发光 ---
    # 注意亮度上限压在 ~1.3：tint 是**乘子**，>1.4 会把立方体糊成纯白块，
    # 形状全丢（第一版就是这么翻车的）；"发光"主要靠暖色调偏移 + glow 项。
    if t < A2:
        bright = 0.25 + 0.75 * rise
    elif t < A3:
        wave = _window(t, A2, A3) * (0.5 + 0.5 * math.sin(2 * math.pi * (t - A2) / 4.0 - phase))
        bright = 1.0 + 0.35 * wave
    elif t < A4:
        bright = 1.0 - 0.30 * _smooth(_ramp(t, A3, A3 + 2.0)) \
                     + 0.30 * _smooth(_ramp(t, A4 - 2.0, A4))
    elif t < A5:
        u = _smooth(_ramp(t, A4, A4 + 7.0))
        bright = 1.0 + 0.18 * u + 0.08 * u * math.sin(2 * math.pi * (t - A4) / 5.0)
    else:
        bright = _lerp(1.18, 0.25, _smooth(_ramp(t, A5 + 1.0, A5 + 5.5)))
        bright = _lerp(bright, 0.25, _smooth(_ramp(t, A5 + 5.8, A5 + 7.2)))

    # 暖色偏移：第四幕螺旋起、第五幕退回，白 → 琥珀（warmth 在两端为 0）
    if t < A4:
        warmth = 0.0
    elif t < A5:
        warmth = _smooth(_ramp(t, A4, A4 + 5.0))
    else:
        warmth = 1.0 - _smooth(_ramp(t, A5, A5 + 5.0))
    tint = Vec3(bright, bright * (1.0 - 0.30 * warmth), bright * (1.0 - 0.62 * warmth))

    if t < A4:
        glow = 0.0
    elif t < A5:
        glow = 0.30 * _smooth(_ramp(t, A4, A4 + 3.0))
    else:
        glow = 0.30 * (1.0 - _smooth(_ramp(t, A5 + 1.0, A5 + 4.0)))

    if t < A4:
        scale = 1.0
    elif t < A5:
        scale = _lerp(1.0, 0.75, _smooth(_ramp(t, A4, A4 + 7.0)))
    else:
        scale = _lerp(0.75, 1.0, _smooth(_ramp(t, A5 + 1.0, A5 + 5.5)))

    pos = Vec3(math.cos(2 * math.pi * i / n) * r, y, math.sin(2 * math.pi * i / n) * r)
    return (pos, quat_from_axis_angle((0, 1, 0), deg),
            Vec3(scale, scale, scale), tint, glow)


def center_pose(t):
    """中央大立方体：常驻自旋 + 第四幕放大脉冲（亮度同样压在 ~1.35 以内）。"""
    pulse = _window(t, A4 + 1.0, A5 - 1.0, ease=3.0)
    scale = _lerp(1.0, 1.2, pulse)
    y = CENTER_BASE.y + 0.6 * pulse
    bright = 1.0 + 0.12 * math.sin(2 * math.pi * t / 8.0) + 0.35 * pulse
    # t=48：pulse=0 且 sin(12π)=sin(0)=0 ⇒ bright=1.0，与 t=0 同帧，无需额外回落
    return (Vec3(CENTER_BASE.x, y, CENTER_BASE.z),
            quat_from_axis_angle((0, 1, 0), _spin_deg(t, 2)),
            Vec3(scale, scale, scale),
            Vec3(bright, bright * (1.0 - 0.35 * pulse), bright * (1.0 - 0.65 * pulse)),
            0.55 * pulse)


def orb_pose(i, t):
    """四颗轨道球：绕中心公转，第四幕向外退让并抬升（给螺旋柱让位）。

    所有周期项都写成 ``2πn·t/DURATION``（n 为整数）——否则 t=48 与 t=0
    的相位对不上，循环回绕会看到跳变。
    """
    phase = 2 * math.pi * i / ORB_N
    ang = 2 * math.pi * 2.0 * t / DURATION + phase          # 2 圈 ⇒ 循环连续
    retreat = _window(t, A4, A5, ease=2.0)
    r = 3.4 + 0.35 * i + 2.2 * retreat
    y = (2.2 + 0.42 * i + 1.4 * retreat
         + 0.18 * math.sin(2 * math.pi * 3.0 * t / DURATION + phase))
    bright = (1.15 + 0.18 * math.sin(2 * math.pi * 4.0 * t / DURATION + phase)
              + 0.25 * retreat)
    return (Vec3(math.cos(ang) * r, y, math.sin(ang) * r),
            quat_from_axis_angle((0, 1, 0), _spin_deg(t, 3, 45.0 * i)),
            Vec3(0.42, 0.42, 0.42), Vec3(bright, bright * 0.78, bright * 0.42),
            0.25 + 0.35 * retreat)


def gem_pose(t):
    """宝石（外部 OBJ 模型）：地面静置 → 悬浮 → 被夹持 → 落座台座 → 归位。

    返回 (translation, rotation)。被夹持期间位置由夹爪世界矩阵给出（见
    ``carry_weight``），本通道此时保持取件点，视觉不会跳。
    """
    if t < A3:
        rise = _window(t, 8.0, A3, ease=3.0)
        bob = 0.12 * math.sin(2 * math.pi * t / 4.0) * (1.0 - _smooth(_ramp(t, 6.0, 9.0)))
        pos = Vec3(GEM_REST.x, GEM_REST.y + 0.35 * rise + bob, GEM_REST.z)
    elif t < GEM_PLACE_T:
        pos = Vec3(GEM_REST.x, GEM_REST.y, GEM_REST.z)
    elif t < GEM_PLACE_T + 1.1:
        u = _smooth(_ramp(t, GEM_PLACE_T, GEM_PLACE_T + 1.1))
        pos = Vec3(_lerp(GEM_REST.x, GEM_PLACED.x, u),
                   _lerp(GEM_REST.y, GEM_PLACED.y, u),
                   _lerp(GEM_REST.z, GEM_PLACED.z, u))
    elif t < 42.0:
        pos = Vec3(GEM_PLACED.x, GEM_PLACED.y, GEM_PLACED.z)
    elif t < 46.5:
        u = _smooth(_ramp(t, 42.0, 46.5))
        pos = Vec3(_lerp(GEM_PLACED.x, GEM_REST.x, u),
                   _lerp(GEM_PLACED.y, GEM_REST.y, u),
                   _lerp(GEM_PLACED.z, GEM_REST.z, u))
    else:
        pos = Vec3(GEM_REST.x, GEM_REST.y, GEM_REST.z)
    return pos, quat_from_axis_angle((0, 1, 0), _spin_deg(t, 2))


def carry_weight(t):
    """宝石被夹爪携带的权重 0→1→0（表现层用它混合"静置姿态 / 夹爪姿态"）。"""
    return _smooth(_ramp(t, GEM_PICK_T - 0.6, GEM_PICK_T - 0.1)) * \
        (1.0 - _smooth(_ramp(t, GEM_PLACE_T, GEM_PLACE_T + 0.8)))


# -------------------------------------------------------------- 相机 --
# (时间, eye, target)：五幕运镜脚本，t=0 与 t=48 同帧 ⇒ 无缝循环
#
# 取景约束（第一版踩过坑）：立柱在半径 13、门拱在 z=±14，**机位一律留在
# 半径 12 以内**，否则镜头会被柱子挡脸；第三幕的取件/放件特写距离目标
# 约 9 个单位，太近会把宝石怼满画面、机械臂反被挤出去。
_CAM_KEYS = (
    (0.0,  (0.0, 15.0, 20.0),   (0.0, 2.0, 0.0)),      # 第一幕：高空全景
    (4.0,  (7.0, 10.0, 15.0),   (0.0, 1.5, 0.0)),
    (8.0,  (2.0, 5.0, 12.5),    (0.0, 1.2, 0.0)),
    (10.0, (0.0, 4.2, 11.5),    (0.0, 1.6, 0.0)),      # 第二幕：降到人视高度
    (13.0, (8.5, 4.0, 7.5),     (0.0, 1.6, 0.0)),      # 环绕环带
    (16.0, (9.0, 4.2, -6.0),    (0.0, 1.6, 0.0)),
    (19.0, (3.0, 4.4, -10.5),   (0.0, 1.6, 0.0)),
    (20.0, (0.5, 4.6, -11.0),   (0.0, 1.6, 0.0)),      # 第三幕：转向机械臂
    # 机械臂特写一律**从环外往里看**（目标半径 6.7 > 环半径 5）：站在环内拍，
    # 环形立方体会怼到镜头前糊住画面；机位离臂座 ≥6.5，免得被摆动的臂扫到。
    (22.0, (-10.5, 4.0, -6.0),  (-4.2, 1.6, -2.8)),    # 取件瞬间（22.0 = 夹持时刻）
    (26.5, (-11.5, 4.4, -2.0),  (-6.4, 2.6, -0.6)),    # 放件特写（26.5 = 落座时刻）
    (29.0, (-9.0, 4.6, 2.5),    (-4.5, 2.0, -0.5)),
    (30.0, (-5.0, 5.2, 7.5),    (-2.0, 2.4, 0.0)),     # 第四幕：随螺旋上升
    (36.0, (7.0, 7.5, 9.0),     (0.0, 5.0, 0.0)),      # 螺旋柱全高入画
    (39.5, (8.0, 9.0, 5.5),     (0.0, 5.5, 0.0)),
    (40.0, (8.5, 9.5, 5.0),     (0.0, 5.6, 0.0)),      # 第五幕：拉远升高
    (44.0, (6.0, 12.0, 12.0),   (0.0, 3.0, 0.0)),
    (48.0, (0.0, 15.0, 20.0),   (0.0, 2.0, 0.0)),      # 回到开场帧
)


def camera_pose(t):
    """相机 (eye, target)：关键帧 + smoothstep 缓动烘焙（缓动表缓存一次）。"""
    return _sample_baked(_cam_eye_keys(), t), _sample_baked(_cam_tgt_keys(), t)


_CAM_EYE = None
_CAM_TGT = None


def _cam_eye_keys():
    global _CAM_EYE
    if _CAM_EYE is None:
        _CAM_EYE = _ease_keys([(k[0], Vec3(*k[1])) for k in _CAM_KEYS], subdiv=6)
    return _CAM_EYE


def _cam_tgt_keys():
    global _CAM_TGT
    if _CAM_TGT is None:
        _CAM_TGT = _ease_keys([(k[0], Vec3(*k[2])) for k in _CAM_KEYS], subdiv=6)
    return _CAM_TGT


def _sample_baked(keys, t):
    """在烘焙关键帧上线性采样（与 Sampler 同语义，就地实现避免建对象）。"""
    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]
    times = [k[0] for k in keys]
    i = bisect.bisect_right(times, t) - 1
    t0, v0 = keys[i]
    t1, v1 = keys[i + 1]
    u = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
    return Vec3(_lerp(v0.x, v1.x, u), _lerp(v0.y, v1.y, u), _lerp(v0.z, v1.z, u))


# -------------------------------------------------------- 幕查询工具 --
def act_index(t):
    """t 落在第几幕（0 起）。"""
    times = [a[0] for a in ACTS]
    return max(0, min(len(ACTS) - 1, bisect.bisect_right(times, t) - 1))


def act_at(t):
    """返回 (起始时刻, 标题, 副标题)。"""
    return ACTS[act_index(t)]


def next_act_time(t):
    """下一幕起点（已是最后一幕则回到 0，便于循环跳转）。"""
    idx = act_index(t)
    return ACTS[idx + 1][0] if idx + 1 < len(ACTS) else 0.0


def prev_act_time(t):
    """上一幕起点（带 0.15s 回退容差，避免刚跳到幕首又跳回上一幕）。"""
    idx = act_index(t)
    if idx == 0:
        return ACTS[0][0]
    return ACTS[idx][0] if t - ACTS[idx][0] > 0.15 else ACTS[idx - 1][0]


# ------------------------------------------------------ 时间轴构建 --
def _axis_times(step, duration):
    n = int(round(duration / step))
    times = [i * step for i in range(n)]
    times.append(float(duration))
    return times


def build_world_timeline(cube_meta, model_meta=(), duration=DURATION, step=0.25):
    """把五幕编排烘焙成时间轴（供 app.py 采样驱动渲染）。

    Args:
        cube_meta:  [(name, Vec3)] 立方体节点；``ring_i`` / ``center`` / ``orb_i``
                    三类命名各有编排；未知命名不建通道（静止在 base，便于增删对象）。
        model_meta: [(name, Vec3)] 外部模型节点（宝石），走 ``gem_pose``。
        duration:   循环时长，默认 48s。
        step:       常规通道烘焙步长（秒）；机械臂与相机用 1/4 步长换更顺的曲线。

    Returns:
        Timeline（含 translation/rotation/scale/tint/glow 五类通道）。
    """
    coarse = _axis_times(step, duration)
    fine = _axis_times(step / 4.0, duration)          # 机械臂 / 相机：更密
    ratio = duration / DURATION                       # 支持自定义时长（测试用）

    def T(t):
        return t * ratio

    channels = []

    def bake_object(name, pose_fn):
        """一次算完该对象所有时刻的位姿，再拆成 5 条通道（省 4/5 计算量）。"""
        poses = [pose_fn(T(t)) for t in coarse]
        for prop, idx in (('translation', 0), ('rotation', 1),
                          ('scale', 2), ('tint', 3), ('glow', 4)):
            if prop in ('scale',) and not name.startswith(('ring_', 'center', 'orb_')):
                continue
            channels.append(Channel(name, prop,
                                    Sampler(coarse, [p[idx] for p in poses], 'LINEAR')))

    for name, _base in cube_meta:
        if name.startswith('ring_'):
            i = int(name.rsplit('_', 1)[1])
            bake_object(name, lambda t, i=i: ring_pose(i, t))
        elif name == 'center':
            bake_object(name, center_pose)
        elif name.startswith('orb_'):
            i = int(name.rsplit('_', 1)[1])
            bake_object(name, lambda t, i=i: orb_pose(i, t))

    for name, _base in model_meta:
        poses = [gem_pose(T(t)) for t in coarse]
        channels.append(Channel(name, 'translation',
                                Sampler(coarse, [p[0] for p in poses], 'LINEAR')))
        channels.append(Channel(name, 'rotation',
                                Sampler(coarse, [p[1] for p in poses], 'LINEAR')))

    # 机械臂：转台绕 Y、两段绕 Z（角度由 arm_pose 给出，含第三幕逆解偏移）
    arm_frames = [arm_pose(T(t)) for t in fine]
    for idx, (node, axis) in enumerate((('turret', (0, 1, 0)),
                                        ('seg1', (0, 0, 1)),
                                        ('seg2', (0, 0, 1)))):
        channels.append(Channel(
            node, 'rotation',
            Sampler(fine, [quat_from_axis_angle(axis, f[idx]) for f in arm_frames],
                    'LINEAR')))

    # 相机：eye / target（表现层合成 look-at）
    cam_frames = [camera_pose(T(t)) for t in fine]
    channels.append(Channel(CAM, 'translation',
                            Sampler(fine, [c[0] for c in cam_frames], 'LINEAR')))
    channels.append(Channel(CAM_TGT, 'translation',
                            Sampler(fine, [c[1] for c in cam_frames], 'LINEAR')))
    return Timeline(duration, channels)
