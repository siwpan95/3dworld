# -*- coding: utf-8 -*-
"""kkrieger 致敬时间轴（logic 层，纯数学，无 GL，可无窗口单测）。

参考 .kkrieger（.theprodukkt / Farbrausch，2004 Breakpoint 96k 冠军）：
程序化生成的科幻工业走廊 + 中央纪念碑大厅。本模块只含编排数据：
- 相机漫游路径（CAM / CAM_TGT 两条 translation 通道，look-at 由表现层合成）
- 场景动画通道（旋转环 / 脉冲核心 / 能量束 / 全息框 / 升降台 / 舱门 /
  灯带与频闪 / 风扇 / 无人机）

时长 24s → **96s**，四幕结构：

===== =========== =====================================================
幕     时间         内容
===== =========== =====================================================
一     0 – 24s     **走廊**：高空俯冲进走廊，三道舱门依次开启，管道/货箱/频闪
二     24 – 48s    **大厅**：进入大厅，纪念碑唤醒，全息框扫描，平台升起
三     48 – 72s    **核心**：过载——环加速、核心脉冲、能量束增亮，镜头逼近
四     72 – 96s    **撤离**：镜头上撤，灯带逐段熄灭，拉回开场高空帧
===== =========== =====================================================

**循环连续性**：``pose(0) == pose(96)``。所有周期项写成
``2πn·t/DURATION``（n 为整数）或整数圈自旋；一次性动作（开门、平台升降、
灯带熄灭）都用"窗口包络"在幕内完成并归零。自旋角一律写成
``360°×(整圈数×t/DURATION + 额外整圈数×S(t))``，其中 ``S`` 是 0→1 的
smoothstep——这样加速段之后**不会倒转**（早期写成"加一圈再减一圈"，
视觉上就是风扇突然反转，已修正）。
"""
import bisect
import math

from pyglet.math import Vec3

from logic.animation import Channel, Sampler, Timeline, quat_from_axis_angle

DURATION = 96.0
A2, A3, A4 = 24.0, 48.0, 72.0            # 第二/三/四幕起点

CAM = 'camera'
CAM_TGT = 'cam_target'

ACTS = (
    (0.0,  '第一幕 · 走廊', '俯冲进走廊：三道舱门依次开启'),
    (24.0, '第二幕 · 大厅', '纪念碑唤醒：全息扫描 + 升降平台'),
    (48.0, '第三幕 · 核心', '过载：环加速、核心脉冲、能量束增亮'),
    (72.0, '第四幕 · 撤离', '撤退：灯带逐段熄灭，拉回开场高空帧'),
)

# ---- 场景常量（与 core/kkrieger_scene.py 对齐；测试会拿这里的半径校验几何） ----
DOOR_OPEN_T = (5.0, 10.0, 15.0)          # 三道舱门开始开启的时刻
# 撤离时反向关闭，但必须晚于镜头通过的时刻（镜头 t≈8.8 / 13.7 / 19.6 往里，
# t≈86.5 过 z=-32、t≈90 过 z=-14）——关早了镜头会从门板里穿过去。
DOOR_CLOSE_T = (92.5, 88.5, 85.0)
DOOR_SLIDE = 1.75

STRIP_N = 14
STRIP_BLACKOUT_T0, STRIP_BLACKOUT_STEP = 76.0, 0.85   # 灯带逐段熄灭
ALIGHT_N = 4
LIFT_REST_Y, LIFT_TOP_Y = 0.15, 3.4
ATRIUM_LIGHTS = ((-5, -61), (5, -61), (-5, -71), (5, -71))

# 相机漫游关键帧：(时间, eye, target)——与 core/kkrieger_scene.py 布局对齐
_CAM_KEYS = (
    (0.0,  (0.0, 9.0, 12.0),    (0.0, 1.6, -24.0)),   # 第一幕：高空俯瞰走廊入口
    (3.0,  (0.0, 4.2, 6.0),     (0.0, 1.6, -30.0)),   # 俯冲
    (5.0,  (0.0, 1.9, 1.0),     (0.0, 1.5, -30.0)),   # 降人视高度（门 0 将开）
    (8.0,  (0.6, 1.8, -9.0),    (-0.6, 1.5, -34.0)),  # 过第一道门
    (11.0, (-0.7, 1.9, -22.0),  (0.7, 1.5, -44.0)),   # 蛇形前进（门 1 将开）
    (14.0, (0.7, 2.0, -33.0),   (-0.5, 1.6, -52.0)),
    (17.0, (-0.5, 2.1, -43.0),  (0.0, 1.8, -60.0)),   # 过第三道门
    (20.0, (0.0, 2.3, -51.0),   (0.0, 2.4, -64.0)),   # 望见大厅
    (24.0, (0.0, 2.6, -56.5),   (0.0, 3.0, -66.0)),   # 第二幕：进大厅
    # 环绕半径受限：大厅六根立柱在 (±5.5,-60)/(±7,-66)/(±5.5,-72)，
    # 早期把机位放在 (±4.6,-60) 时离柱面不足 1 米，画面被柱子糊住/穿模。
    (28.0, (4.2, 3.2, -58.5),   (0.0, 3.6, -66.0)),   # 绕纪念碑
    (33.0, (0.0, 3.6, -56.5),   (0.0, 3.6, -66.0)),
    (38.0, (-4.2, 3.4, -58.5),  (0.0, 3.8, -66.0)),
    (43.0, (0.0, 4.4, -56.5),   (0.0, 4.0, -66.0)),   # 平台升起后抬视
    (48.0, (0.0, 4.6, -57.5),   (0.0, 4.2, -66.0)),   # 第三幕：正面
    (53.0, (3.6, 4.6, -61.0),   (0.0, 4.4, -66.0)),   # 过载：加速环绕
    (58.0, (-3.6, 4.8, -61.0),  (0.0, 4.6, -66.0)),
    (63.0, (2.4, 5.2, -62.5),   (0.0, 4.8, -66.0)),
    (67.0, (0.0, 5.0, -63.5),   (0.0, 4.8, -66.0)),   # 逼近核心
    (71.0, (0.0, 4.4, -64.6),   (0.0, 4.6, -66.0)),   # 贴脸
    (72.0, (0.0, 4.2, -65.2),   (0.0, 4.6, -66.0)),   # 第四幕：撤离起点
    (75.0, (0.0, 3.6, -62.0),   (0.0, 4.0, -64.0)),   # 后撤
    (79.0, (0.0, 2.6, -56.0),   (0.0, 1.8, -50.0)),   # 退出大厅
    (84.0, (0.0, 2.2, -40.0),   (0.0, 1.6, -20.0)),   # 走廊后撤
    (89.0, (0.0, 2.0, -18.0),   (0.0, 1.6, 4.0)),
    (93.0, (0.0, 3.0, -4.0),    (0.0, 2.0, 8.0)),     # 出走廊
    (96.0, (0.0, 9.0, 12.0),    (0.0, 1.6, -24.0)),   # 回到开场帧（硬切回开头）
)


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
    """0 → 1 的单调 smoothstep（用于"加速后不回退"的自旋）。"""
    return _smooth(_ramp(t, t0, t1))


def _window(t, t0, t1, ease=2.0):
    """0 → 1 → 0 的窗口包络（端点为 0，幕间拼接不会跳变）。"""
    return _smooth(_ramp(t, t0, t0 + ease)) * (1.0 - _smooth(_ramp(t, t1 - ease, t1)))


def _ease_keys(keys, subdiv=6):
    """关键帧 → 稠密缓动关键帧（LINEAR 采样器 + smoothstep = 手写 easing）。"""
    out = [(keys[0][0], keys[0][1])]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        for k in range(1, subdiv + 1):
            u = _smooth(k / subdiv)
            out.append((_lerp(t0, t1, k / subdiv),
                        _lerp(v0, v1, u) if not isinstance(v0, Vec3)
                        else Vec3(_lerp(v0.x, v1.x, u), _lerp(v0.y, v1.y, u),
                                  _lerp(v0.z, v1.z, u))))
    return out


def _spin(t, turns, extra=0.0, t0=0.0, t1=0.0):
    """自旋角（度）：``整圈 × t/DURATION`` + 额外整圈 × 单调 S(t)。

    S 是 0→1 的 smoothstep，所以额外圈数是**加上去就不再退回**的——
    角速度先减慢后加快，但方向恒定。首尾相差 ``turns + extra`` 个整圈，
    循环回绕时姿态一致。
    """
    return 360.0 * (turns * t / DURATION + extra * _step(t, t0, t1))


def _pulse(t, period, phase=0.0):
    """周期脉冲 0..1，周期整除 DURATION ⇒ 循环连续。"""
    return 0.5 + 0.5 * math.sin(2 * math.pi * t / period + phase)


# -------------------------------------------------------- 场景动画表 --
# 命名约定由 core/kkrieger_scene.py 定义；未知名返回 None（静默跳过），
# 这样场景可以自由增删对象而不必同步改本模块。
def _ring_pose(i, t):
    axis = ((0, 1, 0), (1, 0, 0), (0, 0, 1))[i % 3]
    turns = (i + 1) * 2
    # 过载幕：额外 (i+1) 整圈在 [46, 70] 内加完，之后维持高速滑行
    deg = _spin(t, turns, extra=(i + 1) * 2, t0=46.0, t1=70.0)
    return {'rotation': quat_from_axis_angle(axis, deg)}


def _core_pose(t):
    # 尺寸脉冲周期 6s（整除 96s）；过载幕幅度放大、频率不变
    amp = 0.14 + 0.16 * _window(t, A3 - 4.0, A4, ease=6.0)
    s = 1.0 + amp * (2 * _pulse(t, 6.0) - 1.0)
    bright = 1.0 + 0.5 * _window(t, A3 - 6.0, A4, ease=6.0)
    return {'rotation': quat_from_axis_angle((0, 1, 0), _spin(t, 2)),
            'scale': Vec3(s, s, s),
            'tint': Vec3(bright, bright * 0.72, bright * 0.34),
            'glow': 0.25 + 0.65 * _window(t, A3 - 6.0, A4, ease=6.0)}


def _beam_pose(t):
    over = _window(t, A3 - 6.0, A4, ease=6.0)          # 过载包络
    w = 1.0 + 1.4 * over + 0.10 * (2 * _pulse(t, 4.0) - 1.0)
    h = 1.0 + 0.22 * over
    return {'scale': Vec3(w, h, w),
            'tint': Vec3(1.0 + 0.3 * over, 0.70, 0.30),
            'glow': 0.15 + 0.85 * over}


def _holo_pose(t):
    # 扫描：周期 16s（整除 96s），在纪念碑上下往复 6 次
    y = 4.4 + 2.6 * math.sin(2 * math.pi * t / 16.0)
    flick = 0.55 + 0.45 * (2 * _pulse(t, 2.0) - 1.0)
    return {'translation': Vec3(0.0, y, -66.0),
            'rotation': quat_from_axis_angle((0, 1, 0), _spin(t, 3)),
            'tint': Vec3(0.45, 0.90, 1.0),
            'glow': 0.35 + 0.45 * flick}


def _lift_pose(t):
    # 第二幕升起 → 第三幕过载时轻微下沉 → 第四幕落回原位
    # （必须回到 REST_Y：否则 t=96 与 t=0 差 3 米多，循环回绕会看到平台瞬移）
    up = _step(t, 26.0, 36.0)
    down = _step(t, 70.0, 78.0)
    dip = _window(t, A3 + 4.0, A4 - 6.0, ease=5.0) * 0.6
    y = LIFT_REST_Y + (LIFT_TOP_Y - LIFT_REST_Y) * up * (1.0 - down) - dip
    return {'translation': Vec3(0.0, y, -58.0)}


def _atrium_fan_pose(t):
    return {'rotation': quat_from_axis_angle(
        (0, 1, 0), _spin(t, 4, extra=8, t0=A3 - 6.0, t1=A4))}


def _door_pose(i, t):
    open_ = _step(t, DOOR_OPEN_T[i], DOOR_OPEN_T[i] + 2.2)
    shut = _step(t, DOOR_CLOSE_T[i], DOOR_CLOSE_T[i] + 2.2)
    a = open_ * (1.0 - shut)
    return a


def _door_translation(i, tag, t):
    a = _door_pose(i, t)
    sign = -1.0 if tag == 'a' else 1.0
    return Vec3(sign * DOOR_SLIDE * a, 0.0, 0.0)


def _strip_pose(i, t):
    """天花板灯带：第二幕转暖、第三幕过载增亮、第四幕依次熄灭后回亮到开场态。"""
    over = _window(t, A3 - 8.0, A4, ease=8.0)
    # 熄灭：第 i 段在 76 + 0.85i 秒变黑；撤离尾声（t>94）统一回位到开场亮度
    dark = _step(t, STRIP_BLACKOUT_T0 + STRIP_BLACKOUT_STEP * i,
                 STRIP_BLACKOUT_T0 + STRIP_BLACKOUT_STEP * i + 1.0)
    restore = _step(t, 94.0, 96.0)
    level = _lerp(1.0, 0.04, dark * (1.0 - restore))
    warm = 0.55 * over
    return {'tint': Vec3(level, level * (1.0 - 0.18 * warm), level * (1.0 - 0.45 * warm)),
            'glow': (1.15 + 0.5 * over) * level}


def _alight_pose(i, t):
    """大厅悬浮灯环：第四幕熄灭（比走廊灯带更早，衬托撤离）。"""
    dark = _step(t, 74.0 + 1.2 * i, 75.5 + 1.2 * i)
    restore = _step(t, 94.5, 96.0)
    level = _lerp(1.0, 0.05, dark * (1.0 - restore))
    return {'tint': Vec3(level, level, level), 'glow': 1.0 * level}


def _strobe_pose(i, t):
    """琥珀警示频闪：锯齿脉冲周期 3s（整除 96s），撤离幕熄灭。"""
    phase = 0.7 * i
    blink = max(0.0, math.sin(2 * math.pi * t / 3.0 + phase)) ** 6
    alive = 1.0 - _step(t, 80.0, 84.0) + _step(t, 95.0, 96.0)
    alive = max(0.0, min(1.0, alive))
    g = 1.2 * blink * alive
    return {'tint': Vec3(1.0, 0.62, 0.22), 'glow': g}


def _fan_pose(i, t):
    return {'rotation': quat_from_axis_angle(
        (1, 0, 0), _spin(t, 8 + 2 * i, extra=6 + i, t0=A3 - 4.0, t1=A4))}


def _drone_pose(i, t):
    """三架无人机：两架巡逻走廊（折返），一架环绕大厅。"""
    if i < 2:
        period = 24.0
        u = (t % period) / period
        tri = 2 * u if u < 0.5 else 2 * (1 - u)          # 0→1→0 折返
        z = -4.0 - (44.0 if i == 0 else 30.0) * tri - (0.0 if i == 0 else 8.0)
        x = (1.4 if i == 0 else -1.2) * math.sin(2 * math.pi * t / 12.0)
        y = 2.6 + 0.35 * math.sin(2 * math.pi * t / 3.0)
    else:
        ang = 2 * math.pi * 2.0 * t / DURATION
        r = 5.6 + 0.8 * math.sin(2 * math.pi * t / 16.0)
        z = -66.0 + r * math.sin(ang)
        x = r * math.cos(ang)
        y = 5.2 + 0.6 * math.sin(2 * math.pi * t / 8.0)
    return {'translation': Vec3(x, y, z),
            'rotation': quat_from_axis_angle((0, 1, 0), _spin(t, 6))}


def _scene_props(name, t):
    """按对象名分派动画（返回 {属性: 值}；未知名返回 None）。"""
    if name.startswith('monument_ring_'):
        return _ring_pose(int(name.rsplit('_', 1)[1]), t)
    if name == 'monument_core':
        return _core_pose(t)
    if name == 'beam':
        return _beam_pose(t)
    if name == 'holo_frame':
        return _holo_pose(t)
    if name == 'lift':
        return _lift_pose(t)
    if name == 'atrium_fan':
        return _atrium_fan_pose(t)
    if name.startswith('door'):
        idx, tag = name[4], name[-1]
        return {'translation': _door_translation(int(idx), tag, t)}
    if name.startswith('strip_'):
        return _strip_pose(int(name.rsplit('_', 1)[1]), t)
    if name.startswith('alight_'):
        return _alight_pose(int(name.rsplit('_', 1)[1]), t)
    if name.startswith('strobe_'):
        return _strobe_pose(int(name.rsplit('_', 1)[1]), t)
    if name.startswith('fan_'):
        return _fan_pose(int(name.rsplit('_', 1)[1]), t)
    if name.startswith('drone_'):
        return _drone_pose(int(name.rsplit('_', 1)[1]), t)
    return None


# ------------------------------------------------------------ 相机 --
_CAM_EYE, _CAM_TGT = None, None


def _cam_keys():
    global _CAM_EYE, _CAM_TGT
    if _CAM_EYE is None:
        _CAM_EYE = _ease_keys([(k[0], Vec3(*k[1])) for k in _CAM_KEYS])
        _CAM_TGT = _ease_keys([(k[0], Vec3(*k[2])) for k in _CAM_KEYS])
    return _CAM_EYE, _CAM_TGT


def _sample(keys, t):
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


def camera_pose(t):
    """相机 (eye, target)。"""
    eye, tgt = _cam_keys()
    return _sample(eye, t), _sample(tgt, t)


# -------------------------------------------------------- 幕查询工具 --
def act_index(t):
    times = [a[0] for a in ACTS]
    return max(0, min(len(ACTS) - 1, bisect.bisect_right(times, t) - 1))


def act_at(t):
    return ACTS[act_index(t)]


def next_act_time(t):
    idx = act_index(t)
    return ACTS[idx + 1][0] if idx + 1 < len(ACTS) else 0.0


def prev_act_time(t):
    idx = act_index(t)
    if idx == 0:
        return ACTS[0][0]
    return ACTS[idx][0] if t - ACTS[idx][0] > 0.2 else ACTS[idx - 1][0]


# ------------------------------------------------------ 时间轴构建 --
def _axis_times(step, duration):
    n = int(round(duration / step))
    times = [i * step for i in range(n)]
    times.append(float(duration))
    return times


def build_camera_channels(duration=DURATION, step=0.25):
    times = _axis_times(step, duration)
    ratio = duration / DURATION
    frames = [camera_pose(t * ratio) for t in times]
    return [
        Channel(CAM, 'translation', Sampler(times, [f[0] for f in frames], 'LINEAR')),
        Channel(CAM_TGT, 'translation', Sampler(times, [f[1] for f in frames], 'LINEAR')),
    ]


def build_scene_channels(animated_meta, duration=DURATION, step=0.25):
    """按场景元数据生成动画通道（只建对象真正用到的属性通道）。

    每个对象的姿态函数对任意 t 返回**同一批属性键**（键集合在时间上恒定），
    所以这里直接取第一帧的键集合建通道，不做缺键回填——若某对象的键集合
    随时间变化，说明姿态函数写得有问题，测试会先炸。
    """
    times = _axis_times(step, duration)
    ratio = duration / DURATION
    channels = []
    for name, _base in animated_meta:
        poses = [_scene_props(name, t * ratio) for t in times]
        if not any(poses):
            continue                              # 未命名对象：静止，无需通道
        props = set(poses[0].keys())
        for p in poses:
            assert set(p.keys()) == props, f'{name} 的属性键随时间变化: {p.keys()}'
        for prop in sorted(props):
            channels.append(Channel(
                name, prop, Sampler(times, [p[prop] for p in poses], 'LINEAR')))
    return channels


def build_kkrieger_timeline(animated_meta, duration=DURATION):
    """相机 + 场景动画合一时间轴（96s 四幕循环）。"""
    return Timeline(duration,
                    build_camera_channels(duration) +
                    build_scene_channels(animated_meta, duration))
