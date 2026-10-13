# -*- coding: utf-8 -*-
"""**可视化导演台**（design note `docs/3_可视化导演台_design_note.md`）。

把 3dworld 已有的底层零件串成"编排 → 预览 → 导出 → 录制"的闭环：

| 零件 | 位置 |
|------|------|
| 演出模型（关键帧/插值/JSON） | `engine/shot.py`（`Shot` / `ShotPlayer`） |
| 时间轴/关键帧/插值底层 | `engine/timeline.py`（`Sampler`/`Channel`/`Timeline`） |
| 时间轴面板 | `presentation/timeline_ui.py`（`TimelineUI`） |
| 场景 / 渲染 | `engine/scene_build.py` + `engine/renderer.py` |

**编辑对象**（`T` 循环切换）：

- ``$camera`` —— 相机运镜（``position`` / ``target`` / ``fov``）
- ``$env``    —— 环境光（``sun_intensity`` / ``sun_dir`` / ``ibl_intensity`` …）
- 场景节点    —— 物件（``translation`` / ``rotation`` / ``scale``）

用法::

    python examples/director.py                          # 打开默认演出（祭坛环绕）
    python examples/director.py --shot-json shots/x.shot.json
    python examples/director.py --smoke 4

操作::

    SPACE          播放 / 暂停
    拖时间轴        手动 seek（同时暂停）
    拖视口          环绕相机（滚轮推拉）
    T              切换编辑目标（相机 → 环境光 → 场景对象 → …）
    K              在当前播头把**编辑目标的当前值**记为关键帧
    DEL / BACKSPACE 删除编辑目标上离播头最近的关键帧
    [ / ]          选中通道上/下（看关键帧、改插值用）
    I              切换选中通道的插值（LINEAR → CUBICSPLINE → STEP）
    M              在当前播头加一个幕标记
    F2 / CTRL+S    导出演出 JSON（写 shots/<名字>.shot.json）
    O              从当前路径重新载入演出
    N              新建空演出（只清通道，保留场景）
    R              重新载入场景 JSON
    ESC            退出

编辑键（T 切到对象/环境光时；相机请用鼠标）
    W A S D        XZ 平面移动
    Q E            上下
    Z X            统一缩放（对象）/ 日照强度（环境光）
    C V            绕 Y 旋转（对象）/ 日照方位（环境光）
    SHIFT          与 W/A/S/D/Q/E 同按 = 0.25 倍步长（微调）

命令行特殊项：``--shot-json <path>``（要打开的演出；不存在就按默认演出起步）。
"""
from __future__ import annotations

import math
import os
import sys

# 项目根进 sys.path（`platform` / `engine` / `presentation` / `host` 都是顶层包）。
#
# ⚠️ 导入路径必须**全仓一致**：一律 `from world3d.platform.app import ...`。
# 历史教训（P0-3 实踩）：曾写成 `from demos._harness import ...`，于是同一个文件被当成
# 两个模块导入、`DemoApp` 成了两个不同的类对象，`scripts/record_all.py` /
# `scripts/verify_demos.py` 的 `issubclass(cls, DemoApp)` 判定为假 ——
# 症状是"验收表里这个 demo 的截图数恒为 0"、"录制脚本走进裸 App 分支后
# TypeError: unexpected keyword 'shots'"。P2-1 起真身在 `world3d/platform/app.py`，
# 旧路径 `examples/_harness.py` 只是薄兼容层（不要再用）。
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pyglet  # noqa: E402
from pyglet import shapes, text  # noqa: E402

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError: No module named 'world3d'（P2-1 全量验收实测）。

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.platform.app import DemoApp, PROJECT_ROOT, run  # noqa: E402
from engine.mathkit import quat_mul, quat_normalize  # noqa: E402
from engine.shot import CAMERA_NODE, ENV_NODE, Shot, ShotPlayer  # noqa: E402
from engine.timeline import INTERPOLATIONS, quat_from_axis_angle  # noqa: E402
from presentation.timeline_ui import PANEL_HEIGHT, PANEL_MARGIN, TimelineUI  # noqa: E402

DEFAULT_SCENE = os.path.join(PROJECT_ROOT, 'docs', 'llm_scene_demo.json')
SHOT_DIR = os.path.join(PROJECT_ROOT, 'shots')
DEFAULT_SHOT_NAME = 'altar_dolly'
DEFAULT_SHOT_PATH = os.path.join(SHOT_DIR, DEFAULT_SHOT_NAME + '.shot.json')

# 允许出现在场景 JSON environment 字段里的 LightEnvironment 构造参数
_ENV_KEYS = ('sun_dir', 'sun_color', 'sun_intensity', 'sky_zenith',
             'sky_horizon', 'sky_ground', 'ibl_intensity', 'sky_intensity',
             'env_size', 'prefilter_levels')

_ABBR = {'LINEAR': 'LIN', 'CUBICSPLINE': 'CUB', 'STEP': 'STP'}

# ---- 关键帧面板配色（与 TimelineUI 同一套暗底/亮字） ----
COL_PANEL = (18, 20, 26, 235)
COL_PANEL_BORDER = (185, 192, 205, 235)
COL_ROW = (30, 33, 41, 255)
COL_ROW_SEL = (36, 86, 132, 255)
COL_TEXT = (245, 247, 250, 255)
COL_TEXT_DIM = (152, 157, 165, 255)
COL_KEY = (255, 196, 110, 255)
COL_KEY_SEL = (255, 235, 190, 255)
COL_PLAYHEAD = (0, 165, 255, 255)
COL_ENV = (140, 220, 170, 255)
COL_CAM = (120, 190, 255, 255)
COL_NODE = (230, 180, 255, 255)


# ==================================================================== 默认演出
# 相机弧形闭环环绕的参数。整条是**闭环**（末帧 = 首帧），录出来的一段 mp4
# 首尾相接可以无缝循环（发行版附件会循环播）。
#
# 半径/高度是**量出来的**，不是拍的：
#
# * `moonlit-altar` 的主体是"低矮平台"（台阶半径 6.6、含拱顶总高 7），放在
#   17~18 单位外就缩成地平线上一条扁带（实测 t=3/13 两帧几乎看不出内容）；
# * 但**抬太高、俯角太大也不行**：实测眼高 5~6.8 时平台顶面与地面亮度几乎
#   相同（把地面藏掉全局均值只变 2.0），平台糊成一片、眼看着像"没画出来"
#   （抬高台阶能改 10.3 → 阶梯确实在画，是**对比度**没了）；
# * 结论：贴地的**近水平**机位才对——台阶的立面（riser）此时正对光，
#   明暗层次清楚（对照实验里眼高 4.2、注视 3.2、距离 13 的一帧最好）。
_ORBIT_R = 12.5
_ORBIT_H0, _ORBIT_H1 = 3.8, 5.2        # 眼高 3.8↔5.2（小幅抬升，保持近水平）
_ORBIT_TGT = (0.0, 3.5, 0.0)           # 注视平台面往上一点（主体略高于画面中心）
_ORBIT_FOV0, _ORBIT_FOV1 = 52.0, 46.0
_ORBIT_AZ0 = 45.0                      # 起始方位（度）
_ORBIT_SEGMENTS = 8                    # 一整圈分 8 段（每段 45°）


def _orbit_cam_keys():
    """生成闭环环绕的相机关键帧 ``[(t, pos, target, fov), ...]``（含末帧回原点）。"""
    keys = []
    for i in range(_ORBIT_SEGMENTS + 1):
        u = i / float(_ORBIT_SEGMENTS)          # 0..1
        arc = math.sin(math.pi * u)             # 0 → 1 → 0（抬升再回落）
        az = math.radians(_ORBIT_AZ0 + 360.0 * u)
        pos = (_ORBIT_R * math.cos(az), _ORBIT_H0 + (_ORBIT_H1 - _ORBIT_H0) * arc,
               _ORBIT_R * math.sin(az))
        fov = _ORBIT_FOV0 + (_ORBIT_FOV1 - _ORBIT_FOV0) * arc
        keys.append((round(20.0 * u, 4), pos, _ORBIT_TGT, fov))
    return keys


_CAM_KEYS = _orbit_cam_keys()


def _sun_from_cam(cam_pos, lift=0.52, skew=0.42):
    """把太阳方位**跟着相机**摆：太阳落在相机的同一水平方位上（略微偏一点）。

    这是导演里的普通常识，但踩过才知道必须做：3dworld 的 `moonlit-altar` 是
    深色石作 + 夜空，相机一旦绕到太阳**对面**，主体就整片发黑、整帧几乎只剩
    天空（实测 t=14/16/19.5 就是这样"什么都看不见"）。让太阳跟着相机走，
    既保证主体始终有受光面，又因为 skew 而保留侧向明暗变化（不然就是死板的
    "正打光"，看不出体积）。
    """
    x, z = cam_pos[0], cam_pos[2]
    a = math.atan2(z, x) + skew
    c, s = math.cos(lift), math.sin(lift)
    return (c * math.cos(a), s, c * math.sin(a))


def default_shot(name=DEFAULT_SHOT_NAME, duration=20.0) -> Shot:
    """默认演出：绕祭坛一整圈（闭环）+ 主体自转/呼吸 + 太阳随相机扫过。

    三条线同时动，**每一秒画面都在变**（"录出来必须看得到内容"的硬要求）：

    1. 相机：半径 12.5 绕祭坛一整圈，眼高 4.0→6.8→4.0 抬升回落，fov 52↔44；
    2. 物件：`core_knot` 自转 270°、`gold_ring` 缩放呼吸、三个浮空球升降；
    3. 灯光（`$env`）：太阳方位跟着相机扫一圈（主体始终受光）+ 强度起伏。
    """
    s = Shot(name=name, duration=duration, scene=DEFAULT_SCENE,
             acts=[{'t': 0.0, 'label': '开场'}, {'t': 2.5, 'label': '环绕'},
                   {'t': 7.5, 'label': '升镜'}, {'t': 12.5, 'label': '高点'},
                   {'t': 17.5, 'label': '回落'}],
             notes='绕祭坛一整圈（闭环可无缝循环）；太阳随相机扫过，主体始终受光')

    # ---- 1. 相机（控制点直接用关键帧时间，函数里不再插值，避免"双重平滑"）----
    ts = [k[0] for k in _CAM_KEYS]
    s.add_channel(CAMERA_NODE, 'position', ts, [k[1] for k in _CAM_KEYS])
    s.add_channel(CAMERA_NODE, 'target', ts, [k[2] for k in _CAM_KEYS])
    s.add_channel(CAMERA_NODE, 'fov', ts, [k[3] for k in _CAM_KEYS])

    # ---- 2. 物件（核心结自转 + 金环呼吸 + 三球浮沉）----
    spin = [(0, 0.0), (7, 90.0), (14, 180.0), (20, 270.0)]
    s.add_channel('core_knot', 'rotation', [t for t, _ in spin],
                  [tuple(quat_from_axis_angle((0, 1, 0), a)) for _, a in spin])
    s.add_channel('gold_ring', 'scale', [0, 5, 10, 15, 20],
                  [(1, 1, 1), (1.22, 1.22, 1.22), (0.94, 0.94, 0.94),
                   (1.30, 1.30, 1.30), (1, 1, 1)])
    for node, base, amp, phase in (
            ('orb_a', (2.1, 4.4, 0.6), 1.05, 0.0),
            ('orb_b', (-1.7, 6.1, 1.2), 0.85, 2.1),
            ('orb_c', (0.5, 7.5, -1.0), 1.25, 4.2)):
        ots, ovs = [], []
        for k in range(13):                 # 0..20s，每 ~1.67s 一个关键帧（浮沉更顺）
            t = 20.0 * k / 12.0
            ots.append(round(t, 4))
            ovs.append((base[0], base[1] + amp * math.sin(phase + t * 1.1), base[2]))
        s.add_channel(node, 'translation', ots, ovs)

    # ---- 3. 灯光（太阳随相机扫过；强度不下潜太深，免得整帧发黑）----
    s.add_channel(ENV_NODE, 'sun_dir', ts, [_sun_from_cam(k[1]) for k in _CAM_KEYS])
    s.add_channel(ENV_NODE, 'sun_intensity',
                  [0, 4, 8, 12, 16, 20],
                  [1.05, 1.45, 1.30, 1.10, 1.35, 1.05])
    s.add_channel(ENV_NODE, 'ibl_intensity', [0, 8, 16, 20], [0.72, 0.92, 0.86, 0.72])
    return s


# ================================================================ 关键帧面板
class KeyframePanel:
    """右侧关键帧检查器：每个通道一行，行内画关键帧刻度；点行选中、点键 seek。"""

    WIDTH = 366
    ROW_H = 24
    TITLE_H = 26
    MAX_ROWS = 14

    def __init__(self):
        self.batch = pyglet.graphics.Batch()
        self._ready = False
        self.shot = None
        self.selected = 0
        self.scroll = 0
        self.playhead = 0.0
        self._rows = []                # [(x, y, w, h, channel_index)]

    # ------------------------------------------------------------ 布局 --
    def layout(self, width, height):
        self._width, self._height = width, height
        self.batch = pyglet.graphics.Batch()
        self.x0 = width - self.WIDTH - PANEL_MARGIN
        self.x1 = width - PANEL_MARGIN
        self.y1 = height - 40                     # 让开 HUD 顶条
        self.y0 = PANEL_MARGIN + PANEL_HEIGHT + 12
        n_vis = min(self.MAX_ROWS, max(1, int((self.y1 - self.y0 - self.TITLE_H) / self.ROW_H)))
        self._n_vis = n_vis
        self.panel = shapes.BorderedRectangle(
            self.x0, self.y0, self.WIDTH, self.y1 - self.y0, 2,
            color=COL_PANEL, border_color=COL_PANEL_BORDER, batch=self.batch)
        self._font = {}
        from engine.fonts import resolve_cjk_font
        cjk = resolve_cjk_font()
        if cjk:
            self._font['font_name'] = cjk
        self.lbl_title = text.Label('关键帧', font_size=13, weight='bold',
                                    x=self.x0 + 12, y=self.y1 - 18,
                                    anchor_x='left', anchor_y='center',
                                    color=COL_TEXT, batch=self.batch, **self._font)
        self._ready = True
        self.rebuild()

    # -------------------------------------------------------- 内容重建 --
    def rebuild(self):
        """通道/关键帧变了就重建图元（一次性批，不在每帧重建）。

        ⚠️ **必须把每个图元存进 ``self._keep``**：pyglet 2.1.16 的
        ``Batch`` 不持有 ``Shape``/``Label`` 对象的引用，只被批创建、无人引用的
        图元会被 GC 回收，**表现为"代码跑了但屏幕上什么都没有"**（实测：最小
        探针里持有引用的红块渲染、不持有引用的绿块全黑）。这是本工程踩过的
        第 N 个 pyglet 静默陷阱，见 `examples/README.md` 的坑位表。
        """
        if not self._ready:
            return
        self.batch = pyglet.graphics.Batch()
        self._keep = []
        self.panel = shapes.BorderedRectangle(
            self.x0, self.y0, self.WIDTH, self.y1 - self.y0, 2,
            color=COL_PANEL, border_color=COL_PANEL_BORDER, batch=self.batch)
        self._keep.append(self.panel)
        self.lbl_title = text.Label('关键帧', font_size=13, weight='bold',
                                   x=self.x0 + 12, y=self.y1 - 18,
                                   anchor_x='left', anchor_y='center',
                                   color=COL_TEXT, batch=self.batch, **self._font)
        self._keep.append(self.lbl_title)
        self._rows = []
        self.progress = None
        self.track_x0 = self.track_x1 = float(self.x0)
        if self.shot is None:
            return

        n = len(self.shot.channels)
        self.scroll = max(0, min(self.scroll, max(0, n - self._n_vis)))
        first = self.scroll
        last = min(n, first + self._n_vis)
        name_w = 152                          # 名字列宽（"core_knot·rotation" 也放得下）
        track_x0 = self.x0 + name_w + 96
        track_x1 = self.x1 - 12
        self.track_x0, self.track_x1 = track_x0, track_x1

        for row, idx in enumerate(range(first, last)):
            ch = self.shot.channels[idx]
            y = self.y1 - self.TITLE_H - (row + 1) * self.ROW_H + 4
            sel = (idx == self.selected)
            bg = shapes.Rectangle(self.x0 + 4, y, self.WIDTH - 8, self.ROW_H - 3,
                                  color=COL_ROW_SEL if sel else COL_ROW, batch=self.batch)
            col = (COL_CAM if ch['node'] == CAMERA_NODE else
                   COL_ENV if ch['node'] == ENV_NODE else COL_NODE)
            lbl = text.Label(f"{ch['node']}·{ch['prop']}", font_size=11,
                             x=self.x0 + 10, y=y + self.ROW_H / 2 - 1,
                             anchor_x='left', anchor_y='center',
                             color=col if sel else COL_TEXT_DIM,
                             batch=self.batch, **self._font)
            tag = text.Label(f"{_ABBR.get(ch['interpolation'], ch['interpolation'])} "
                             f"{len(ch['times'])}键", font_size=10,
                             x=self.x0 + name_w + 14, y=y + self.ROW_H / 2 - 1,
                             anchor_x='left', anchor_y='center',
                             color=COL_KEY if sel else COL_TEXT_DIM,
                             batch=self.batch, **self._font)
            self._keep.extend((bg, lbl, tag))
            # 通道轨道 + 关键帧刻度
            ty = y + self.ROW_H / 2 - 3
            bar = shapes.Rectangle(track_x0, ty, track_x1 - track_x0, 4,
                                   color=(58, 63, 76, 255), batch=self.batch)
            self._keep.append(bar)
            for kt in ch['times']:
                frac = 0.0 if self.shot.duration <= 0 else min(1.0, kt / self.shot.duration)
                dot = shapes.Circle(track_x0 + (track_x1 - track_x0) * frac,
                                    ty + 2, 3.2 if sel else 2.6,
                                    color=COL_KEY_SEL if sel else COL_KEY,
                                    batch=self.batch)
                self._keep.append(dot)
            self._rows.append((self.x0 + 4, y, self.WIDTH - 8, self.ROW_H, idx))

        # 播头竖线（每帧刷 x，不重建批）
        if n:
            self.progress = shapes.Rectangle(
                track_x0, self.y1 - self.TITLE_H - (last - first) * self.ROW_H + 4,
                2, (last - first) * self.ROW_H - 4,
                color=COL_PLAYHEAD, batch=self.batch)
            self._keep.append(self.progress)
        if n > self._n_vis:
            self.lbl_more = text.Label(
                f'{self.selected + 1}/{n}   [ ] 换行', font_size=10,
                x=self.x0 + 12, y=self.y0 + 12, anchor_x='left', anchor_y='center',
                color=COL_TEXT_DIM, batch=self.batch, **self._font)
            self._keep.append(self.lbl_more)

    def update(self, playhead):
        self.playhead = playhead
        if self.progress is not None and self.shot is not None and self.shot.duration > 0:
            frac = max(0.0, min(1.0, playhead / self.shot.duration))
            self.progress.x = self.track_x0 + (self.track_x1 - self.track_x0) * frac

    def draw(self):
        self.batch.draw()

    # ------------------------------------------------------------ 交互 --
    def hit(self, x, y):
        """返回 (kind, payload)：('row', 通道下标) / ('key', (通道下标, 时刻)) / None。"""
        if not self._ready or self.shot is None:
            return None
        for rx, ry, rw, rh, idx in self._rows:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                if x >= self.track_x0 - 6:
                    ch = self.shot.channels[idx]
                    best, bd = None, 1e9
                    span = max(1e-6, self.track_x1 - self.track_x0)
                    for kt in ch['times']:
                        fx = self.track_x0 + span * min(1.0, kt / max(1e-6, self.shot.duration))
                        d = abs(fx - x)
                        if d < bd:
                            best, bd = kt, d
                    if best is not None and bd <= 8.0:
                        return ('key', (idx, best))
                return ('row', idx)
        return None


# ==================================================================== 导演台
class DirectorApp(DemoApp):
    TITLE = 'director · 可视化导演台'
    HELP = ('SPACE 播放/暂停  |  T 换目标  K 记帧  DEL 删帧  [ ] 选通道  I 换插值  '
            'M 加幕  F2 导出  O 载入  N 新建  R 重载  ESC 退出')
    HUD = True
    FOV = 52.0
    Z_NEAR, Z_FAR = 0.1, 700.0
    DURATION = 20.0
    SHOTS = ((3.0, 'docs/director_a.png'), (9.0, 'docs/director_b.png'),
             (13.0, 'docs/director_c.png'), (18.0, 'docs/director_d.png'))
    SHOT_PATH = DEFAULT_SHOT_PATH              # main() 可覆盖
    SCENE_PATH = DEFAULT_SCENE

    # ------------------------------------------------------------- setup --
    def setup(self):
        from engine.camera import Camera, PITCH_MAX, PITCH_MIN
        from engine.env import LightEnvironment
        from engine.renderer import Renderer
        from engine.scene_build import build_scene, load_json
        from engine.shaders import get

        self._cam_pitch_lim = (PITCH_MIN, PITCH_MAX)
        self.msg = 'ready'
        self.playing = True
        self.looping = True
        self.playhead = 0.0
        self.edit_i = 0                        # 编辑目标下标（0=相机, 1=环境光, 2+=节点）
        self.selected = 0                      # 选中的通道下标（面板高亮）

        # ---- 场景 ----
        self._scene_path = os.path.abspath(self.SCENE_PATH)
        self._data = load_json(self._scene_path)
        env_def = self._data.get('environment') or {}
        self.env = LightEnvironment(
            **{k: v for k, v in env_def.items() if k in _ENV_KEYS}).bake()
        self.renderer = Renderer(self.window.width, self.window.height,
                                 environment=self.env)
        self.scene = build_scene(self._data, get('pbr'))

        # ---- 相机（基类 Camera：position/target/fov 全由演出驱动）----
        self.camera = Camera(position=(13.0, 3.2, 13.0), target=(0.0, 2.0, 0.0),
                             fov=self.FOV, near=self.Z_NEAR, far=self.Z_FAR)
        self._orb = None                       # (yaw, pitch, dist)，每次由 position/target 现算

        # ---- 演出 ----
        self.shot = None
        self._load_shot_file()
        if self.shot is None:
            self.shot = default_shot(duration=float(self.DURATION))
            self.msg = '默认演出（未见 shot json）'
        if self.args.duration is not None:
            self.shot.duration = float(self.args.duration)
        self.DURATION = self.shot.duration
        self.rebuild_shot_views()

        self.player = ShotPlayer(self.shot, scene=self.scene,
                                 camera=self.camera, env=self.env)
        if self.args.flags & {'auto', 'play'}:
            self.playing = True

        # ---- 面板 ----
        self.timeline_ui = TimelineUI(
            self.shot.duration, self._toggle_play, self._toggle_loop, self._seek,
            acts=[(a['t'], a['label'], '') for a in self.shot.acts])
        self.panel = KeyframePanel()
        self.panel.shot = self.shot
        self.panel.selected = self.selected
        w, h = self.window.width, self.window.height
        self.timeline_ui.layout(w, h)
        self.panel.layout(w, h)
        self.window.push_handlers(on_mouse_press=self._on_mouse_press,
                                  on_mouse_release=lambda *a: self._on_release())
        # 起始姿态 = t=0 的演出（而不是构造函数里的初值）
        self.player.apply(0.0)
        self._place_help()
        print(f'[director] scene={os.path.basename(self._scene_path)} '
              f'({len(self.scene.drawables())} drawables)  '
              f'shot={self.shot.status_text()}')

    def _place_help(self):
        """把 HUD 的帮助行抬到**时间轴面板 + 幕标签之上**，并让出右侧关键帧面板。

        `Hud.layout` 把它固定在 y=16，而 `TimelineUI` 的面板占 y=14..78 ——
        默认位置会正好压在时间轴下沿（实测糊成一片）。抬到 94 又会和
        `TimelineUI` 画在 (x0+14, 90) 的**当前幕名**叠在一起（实测"▶ 升镜"被
        "SPACE" 压住），所以再抬一档到 108。
        """
        if self.hud is not None:
            self.hud.help.position = (PANEL_MARGIN + 6,
                                      PANEL_MARGIN + PANEL_HEIGHT + 30, 0)

    # -------------------------------------------------------- 演出装载 --
    def _load_shot_file(self):
        p = os.path.abspath(self.SHOT_PATH)
        if not os.path.exists(p):
            return
        try:
            self.shot = Shot.load(p)
            if self.shot.scene and not os.path.isabs(self.shot.scene):
                cand = os.path.join(PROJECT_ROOT, self.shot.scene)
                self.shot.scene = cand
            self.msg = f'载入 {os.path.basename(p)}'
        except Exception as e:
            self.msg = f'载入失败：{e}'
            print(f'[director] shot load failed: {e}')

    def rebuild_shot_views(self):
        """演出结构变了：重建 Timeline（录制脚本读 app.timeline.duration）与面板。"""
        self.timeline = self.shot.build_timeline()
        self.DURATION = self.shot.duration
        if getattr(self, 'timeline_ui', None) is not None:
            acts = [(a['t'], a['label'], '') for a in self.shot.acts]
            self.timeline_ui.duration = self.shot.duration
            self.timeline_ui.acts = acts
            self.timeline_ui.layout(self.window.width, self.window.height)
        if getattr(self, 'panel', None) is not None:
            self.panel.shot = self.shot
            self.panel.selected = min(self.selected, max(0, len(self.shot.channels) - 1))
            self.panel.layout(self.window.width, self.window.height)

    # ------------------------------------------------------- 编辑目标 --
    @property
    def _targets(self):
        """编辑目标序列：``[$camera, $env, <场景节点>…]``。"""
        return [CAMERA_NODE, ENV_NODE] + self.player.animatable_nodes()

    @property
    def target(self):
        ts = self._targets
        return ts[self.edit_i % len(ts)]

    def _cycle_target(self, step=1):
        ts = self._targets
        self.edit_i = (self.edit_i + step) % len(ts)
        # 顺手把选中通道跳到该目标的第一个通道（没有就把选中留在原处）
        t = self.target
        for i, c in enumerate(self.shot.channels):
            if c['node'] == t:
                self._select(i)
                break
        self.msg = f'编辑目标 → {t}'

    def _select(self, idx):
        if not self.shot.channels:
            return
        self.selected = max(0, min(idx, len(self.shot.channels) - 1))
        vis = max(1, self.panel._n_vis)
        if self.selected < self.panel.scroll:
            self.panel.scroll = self.selected
        elif self.selected >= self.panel.scroll + vis:
            self.panel.scroll = self.selected - vis + 1
        self.panel.selected = self.selected
        self.panel.rebuild()

    def _cycle_channel(self, step):
        if not self.shot.channels:
            self.msg = '没有通道（先按 K 记一个关键帧）'
            return
        self._select((self.selected + step) % len(self.shot.channels))
        c = self.shot.channels[self.selected]
        self.msg = f'通道 {self.selected + 1}/{len(self.shot.channels)}：{c["node"]}·{c["prop"]}'

    # ----------------------------------------------------- 播放/时间轴 --
    def _toggle_play(self):
        self.playing = not self.playing
        self.msg = '播放' if self.playing else '暂停'

    def _toggle_loop(self):
        self.looping = not self.looping
        self.msg = f'循环 {"开" if self.looping else "关"}'

    def _seek(self, t):
        self.playhead = max(0.0, min(float(t), self.shot.duration))
        self.time = self.playhead
        self.playing = False
        self.msg = f'seek {self.playhead:.2f}s'

    # --------------------------------------------------------- 关键帧 --
    def _props_of(self, target):
        if target == CAMERA_NODE:
            return self.player.capture_camera(self.playhead)
        if target == ENV_NODE:
            return self.player.capture_env()
        return self.player.capture_node(target)

    def _record(self, at=None):
        """在当前播头把**编辑目标的当前值**记为关键帧。"""
        t = self.playhead if at is None else float(at)
        target = self.target
        vals = self._props_of(target)
        if not vals:
            self.msg = f'{target} 没有可记录的属性'
            return
        made = []
        for prop, v in vals.items():
            try:
                i = self.shot.ensure_channel(target, prop)
                r = self.shot.set_key(i, t, v)
                made.append(f'{prop}{"改" if r["replaced"] else "+"}')
            except Exception as e:
                self.msg = f'{prop} 记录失败：{e}'
                return
        self.msg = f'K @{t:.2f}s  {target}: ' + ' '.join(made)
        self.rebuild_shot_views()
        for i, c in enumerate(self.shot.channels):
            if c['node'] == target:
                self._select(i)
                break

    def _delete_key(self):
        if not self.shot.channels:
            return
        # 优先删"编辑目标上的通道"，否则删当前选中通道
        idx = self.selected
        for i, c in enumerate(self.shot.channels):
            if c['node'] == self.target:
                idx = i
                break
        if self.shot.del_key(idx, self.playhead):
            c = self.shot.channels[idx]
            self.msg = f'删帧 {c["node"]}·{c["prop"]} @{self.playhead:.2f}s（剩 {len(c["times"])}）'
            self.rebuild_shot_views()
        else:
            self.msg = f'@{self.playhead:.2f}s 附近没有关键帧'

    def _cycle_interp(self):
        if not self.shot.channels:
            return
        ch = self.shot.channels[self.selected]
        k = INTERPOLATIONS.index(ch['interpolation'])
        ch['interpolation'] = INTERPOLATIONS[(k + 1) % len(INTERPOLATIONS)]
        self.msg = f'{ch["node"]}·{ch["prop"]} 插值 → {ch["interpolation"]}'
        self.rebuild_shot_views()

    def _add_act(self):
        label = f'幕{len(self.shot.acts) + 1}'
        self.shot.acts.append({'t': round(self.playhead, 3), 'label': label})
        self.shot.acts.sort(key=lambda a: a['t'])
        self.msg = f'加幕「{label}」@{self.playhead:.2f}s'
        self.rebuild_shot_views()

    # ----------------------------------------------------------- 导出 --
    def _save_shot(self, path=None):
        p = path or self.SHOT_PATH
        try:
            if self.shot.scene:
                # 场景只存**相对仓库根**的路径引用，跨机可搬
                try:
                    self.shot.scene = os.path.relpath(self.shot.scene, PROJECT_ROOT).replace('\\', '/')
                except ValueError:
                    pass
            self.shot.save(p)
            self.msg = f'导出 {os.path.relpath(p, PROJECT_ROOT)}'
            print(f'[director] saved {p}')
        except Exception as e:
            self.msg = f'导出失败：{e}'
            print(f'[director] save failed: {e}')

    def _new_shot(self):
        self.shot = Shot(name=self.shot.name, duration=self.shot.duration,
                         scene=self.shot.scene)
        self.selected = 0
        self.panel.scroll = 0
        self.rebuild_shot_views()
        self.player.shot = self.shot
        self.msg = f'新建空演出「{self.shot.name}」（{self.shot.duration:.1f}s）'

    # ------------------------------------------------- 对象/环境 编辑 --
    def _edit_target(self, key, mods):
        """对象/环境光的键盘编辑（相机不在此列：它用鼠标）。"""
        target = self.target
        if target == CAMERA_NODE:
            self.msg = '相机请用鼠标（拖动环绕 / 滚轮推拉）'
            return False
        step = 0.25 if (mods & pyglet.window.key.MOD_SHIFT) else 1.0
        if target == ENV_NODE:
            if key in (pyglet.window.key.Z, pyglet.window.key.X):
                d = 0.15 * (1 if key == pyglet.window.key.Z else -1)
                self.env.sun_intensity = max(0.0, self.env.sun_intensity + d)
                self.msg = f'sun_intensity = {self.env.sun_intensity:.3f}'
                return True
            if key in (pyglet.window.key.C, pyglet.window.key.V):
                a = 0.12 * (1 if key == pyglet.window.key.C else -1)
                x, y, z = self.env.sun_dir
                ca, sa = math.cos(a), math.sin(a)
                nx, nz = x * ca - z * sa, x * sa + z * ca   # 绕 Y 转
                self.env.sun_dir = (nx, y, nz)
                self.msg = f'sun_dir = ({nx:.2f}, {y:.2f}, {nz:.2f})'
                return True
            return False

        node = self.player._node(target)
        if node is None:
            return False
        x, y, z = node.translation
        if key == pyglet.window.key.W:
            z -= step
        elif key == pyglet.window.key.S:
            z += step
        elif key == pyglet.window.key.A:
            x -= step
        elif key == pyglet.window.key.D:
            x += step
        elif key == pyglet.window.key.Q:
            y += step
        elif key == pyglet.window.key.E:
            y -= step
        elif key in (pyglet.window.key.Z, pyglet.window.key.X):
            s = 1.0 + 0.04 * (1 if key == pyglet.window.key.Z else -1)
            node.scale = tuple(v * s for v in node.scale)
            self.msg = f'{target}.scale = ({node.scale[0]:.2f}, …)'
            node.mark_dirty()
            return True
        elif key in (pyglet.window.key.C, pyglet.window.key.V):
            a = 6.0 * (1 if key == pyglet.window.key.C else -1)
            dq = quat_from_axis_angle((0, 1, 0), a)
            node.rotation = quat_normalize(quat_mul(tuple(dq), tuple(node.rotation)))
            self.msg = f'{target} 绕 Y {"+" if a > 0 else ""}{a:.0f}°'
            node.mark_dirty()
            return True
        else:
            return False
        node.translation = (x, y, z)
        node.mark_dirty()
        self.msg = f'{target}.translation = ({x:.2f}, {y:.2f}, {z:.2f})'
        return True

    # ------------------------------------------------------------ 相机 --
    def _orbit_state(self):
        """(yaw, pitch, dist)：从当前 position/target 反算，保证鼠标编辑连续。"""
        px, py, pz = self.camera.position
        tx, ty, tz = self.camera.target
        dx, dy, dz = px - tx, py - ty, pz - tz
        dist = math.sqrt(dx * dx + dy * dy + dz * dz) or 1e-3
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, dy / dist))))
        yaw = math.degrees(math.atan2(dz, dx))
        return yaw, pitch, dist

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        if self.timeline_ui.scrubbing:                 # 拖时间轴 = seek
            self.timeline_ui.on_drag(x, y)
            self.playhead = self.timeline_ui.playhead
            self.time = self.playhead
            return
        yaw, pitch, dist = self._orbit_state()         # 其余位置 = 环绕相机
        lo, hi = self._cam_pitch_lim
        yaw += dx * 0.4
        pitch = max(lo, min(hi, pitch - dy * 0.4))
        self._set_orbit(yaw, pitch, dist)
        self.playing = False
        px, py, pz = self.camera.position
        self.msg = f'相机 pos=({px:.1f}, {py:.1f}, {pz:.1f})  按 K 记帧'

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        yaw, pitch, dist = self._orbit_state()
        self._set_orbit(yaw, pitch, max(2.0, dist - scroll_y * 1.5))
        self.playing = False

    def _set_orbit(self, yaw, pitch, dist):
        yr, pr = math.radians(yaw), math.radians(pitch)
        tx, ty, tz = self.camera.target
        self.camera.position = (tx + dist * math.cos(pr) * math.cos(yr),
                                ty + dist * math.sin(pr),
                                tz + dist * math.cos(pr) * math.sin(yr))

    # ----------------------------------------------------------- 输入 --
    def _on_mouse_press(self, x, y, button, modifiers):
        if self.timeline_ui.on_press(x, y):            # 时间轴面板：按钮/seek 已由回调处理
            return pyglet.event.EVENT_HANDLED
        hit = self.panel.hit(x, y)
        if hit is not None:
            kind, payload = hit
            if kind == 'row':
                self._select(payload)
                c = self.shot.channels[payload]
                self.msg = f'选中 {c["node"]}·{c["prop"]}'
            else:
                idx, t = payload
                self._select(idx)
                self._seek(t)
        return pyglet.event.EVENT_HANDLED

    def _on_release(self):
        self.timeline_ui.on_release()

    def _on_key_press(self, symbol, modifiers):
        K = pyglet.window.key
        if symbol == K.ESCAPE:
            self.window.close()
            return pyglet.event.EVENT_HANDLED
        if symbol == K.SPACE:
            self._toggle_play()
            return pyglet.event.EVENT_HANDLED
        self.on_key(symbol, modifiers)
        return pyglet.event.EVENT_HANDLED

    def on_key(self, symbol, modifiers):
        K = pyglet.window.key
        if symbol == K.K:
            self._record()
        elif symbol in (K.DELETE, K.BACKSPACE):
            self._delete_key()
        elif symbol == K.T:
            self._cycle_target()
        elif symbol == K.BRACKETLEFT:
            self._cycle_channel(-1)
        elif symbol == K.BRACKETRIGHT:
            self._cycle_channel(1)
        elif symbol == K.I:
            self._cycle_interp()
        elif symbol == K.M:
            self._add_act()
        elif symbol == K.F2 or (symbol == K.S and (modifiers & K.MOD_CTRL)):
            self._save_shot()
        elif symbol in (K.W, K.A, K.S, K.D, K.Q, K.E, K.Z, K.X, K.C, K.V):
            self._edit_target(symbol, modifiers)
        elif symbol == K.O:
            self._load_shot_file()
            self.player.shot = self.shot
            self.rebuild_shot_views()
        elif symbol == K.N:
            self._new_shot()
        elif symbol == K.R:
            self._reload_scene()
        else:
            return
        self.panel.shot = self.shot

    # ------------------------------------------------------- 场景重载 --
    def _reload_scene(self):
        from engine.scene_build import build_scene, load_json, validate
        from engine.shaders import get
        try:
            data = load_json(self._scene_path)
        except Exception as e:
            self.msg = f'场景载入失败：{e}'
            return
        errors = [i for i in validate(data) if i[0] == 'error']
        if errors:
            self.msg = f'场景被拒（{len(errors)} 个错误）'
            for _lvl, path, msg in errors[:4]:
                print(f'    {path}: {msg}')
            return
        for d in self.scene.drawables():
            v = getattr(d, 'vlist', None)
            if v is not None:
                try:
                    v.delete()
                except Exception:
                    pass
        self.scene = build_scene(data, get('pbr'))
        self._data = data
        self.player.scene = self.scene
        self.player._node_cache = {}
        self.msg = f'场景重载 OK（{len(self.scene.drawables())} drawables）'

    # ------------------------------------------------------- 主循环 --
    def update(self, dt):
        dur = max(1e-6, self.shot.duration)
        if self.playing:
            t = self.time % dur
        else:
            t = self.playhead
            self.time = t
        self.playhead = t
        self.player.apply(t)                  # **先**写回（mark_dirty）
        self.scene.update(dt)                 # 再刷新世界矩阵 → 绘制物拿新 model
        self.timeline_ui.update(t, self.playing, self.looping)
        self.panel.update(t)

    def render(self):
        w, h = self.window.width, self.window.height
        self.renderer.render(self.camera, self.scene, w / float(h), self.window)

    def _draw_hud(self):
        from pyglet.gl import (GL_BLEND, GL_DEPTH_TEST, GL_ONE_MINUS_SRC_ALPHA,
                               GL_SRC_ALPHA, glBlendFunc, glDisable, glEnable)
        from pyglet.math import Mat4
        w, h = self.window.width, self.window.height
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDisable(GL_DEPTH_TEST)
        self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -255, 255)
        self.window.view = Mat4()
        self.hud.draw()
        self.panel.draw()
        self.timeline_ui.draw()
        self.window.projection = self.perspective
        glEnable(GL_DEPTH_TEST)

    def _on_resize(self, width, height):
        result = super()._on_resize(width, height)
        if hasattr(self, 'renderer'):
            self.renderer.resize(width, height)
        if hasattr(self, 'timeline_ui'):
            self.timeline_ui.layout(width, height)
        if hasattr(self, 'panel'):
            self.panel.layout(width, height)
        self._place_help()                     # super 里 hud.layout 会把它挪回底部
        return result

    def status_text(self):
        base = super().status_text()
        chans = self.shot.channels
        ks = sum(len(c['times']) for c in chans)
        sel = (f'{chans[self.selected]["node"]}.{chans[self.selected]["prop"]}'
               if chans else '-')
        return (f'{base}  {self.shot.name}  tgt={self.target}  '
                f'{len(chans)}通道/{ks}键  选中[{self.selected + 1}]{sel}  « {self.msg} »')


# ------------------------------------------------------------------ 入口 --
def _extract_shot_json(argv):
    """从 argv 里摘出 ``--shot-json``（world3d.platform.app.parse_args 不认识它）。

    必须在 ``parse_args`` 之前摘掉 —— argparse 会把它当成 ``--shot`` 的
    前缀歧义（实测：``unrecognized arguments: --shot-json x``）。
    """
    args = list(argv)
    path = None
    for i, a in enumerate(args):
        if a == '--shot-json' and i + 1 < len(args):
            path = args[i + 1]
            del args[i:i + 2]
            break
        if a.startswith('--shot-json='):
            path = a.split('=', 1)[1]
            del args[i]
            break
    return path, args


def main():
    path, argv = _extract_shot_json(sys.argv[1:])
    if path:
        DirectorApp.SHOT_PATH = path
    os.makedirs(SHOT_DIR, exist_ok=True)
    run(DirectorApp, argv)


if __name__ == '__main__':
    main()
