# -*- coding: utf-8 -*-
"""诺德之境 · 龙之黎明 —— 上古卷轴风格 60s 程序化动画（pyglet 引擎）。

雪原山谷里的诺德遗迹：黎明星空下的松林与长屋、石柱阵与坍塌拱门、
一堵刻满龙语的古墙，和一条从天而降的巨龙。运镜走"硬件演示片"的路子：
高空俯冲扎入 → 贴地掠行 → 穿拱门 → 转身锁定飞龙 → 环绕追击 →
低角度仰拍龙焰喷墙 → 推轨贴近符文 → 冲天拉升追龙远去 → 回落开场。

一切几何与材质都是程序生成的（engine.proctex 的北欧图案集 +
logic.nordic_scene 的程序化场景）；配乐由 logic.nordic_audio 离线合成，
与动画共用事件时刻，音画逐拍对齐。

用法::

    python demos/nordic_saga.py                正常跑（60s 循环 + 配乐）
    python demos/nordic_saga.py --smoke 2      冒烟
    python demos/nordic_saga.py --shot         抓帧到 docs/ 后关闭

按键：ESC 退出  SPACE 暂停  B Bloom  O SSAO  F2 性能面板
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pyglet.math import Vec3                                      # noqa: E402

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError: No module named 'world3d'（P2-1 全量验收实测）。

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logic.nordic_scene import (CAMPFIRE, build_nordic_scene,      # noqa: E402
                               snow_height)
from engine.app import EngineApp, run                             # noqa: E402
from engine.camera import Camera                                  # noqa: E402
from engine.env import LightEnvironment                           # noqa: E402
from engine.renderer import Renderer                              # noqa: E402
from engine.scene import Scene                                    # noqa: E402
from engine.shaders import get                                    # noqa: E402
from logic.nordic_audio import ensure_ost                         # noqa: E402
from logic import nordic_saga as NS                               # noqa: E402

TITLE = '3dworld · 诺德之境 — 上古卷轴风格雪原（程序化几何/纹理/配乐）'
HELP = ('ESC quit  SPACE pause  F2 perf  B bloom  O ssao  '
        '60s loop with procedural OST')
SHOTS = ((4.0, 'docs/nordic_01_dive.png'),
         (12.0, 'docs/nordic_02_campfire.png'),
         (18.4, 'docs/nordic_03_arch.png'),
         (29.0, 'docs/nordic_04_dragon.png'),
         (45.6, 'docs/nordic_05_runes.png'),
         (52.2, 'docs/nordic_06_takeoff.png'))
DURATION = NS.DURATION


class NordicSagaApp(EngineApp):
    TITLE = TITLE
    HELP = HELP
    SHOTS = SHOTS
    DURATION = DURATION
    Z_FAR = 500.0

    def setup(self):
        w, h = self.window.width, self.window.height
        # 黎明前的北境：低角度暖光 + 深蓝夜空。
        # sun_dir 是光线的传播方向（PBR 里 L = -sunDir）——太阳位于场景
        # 西南上空（+x +z 侧），光线朝北照：镜头主要朝 -z 看，所以建筑
        # 朝镜头的一面全部受光；y 分量为负 ⇒ 天空不着太阳盘（黎明未升）。
        self.env = LightEnvironment(
            sun_dir=(-0.30, -0.28, -0.91), sun_color=(1.0, 0.74, 0.54),
            sun_intensity=1.28, ibl_intensity=0.50,
            sky_zenith=(0.035, 0.050, 0.110),
            sky_horizon=(0.160, 0.150, 0.200),
            sky_ground=(0.075, 0.090, 0.120)).bake()
        self.renderer = Renderer(w, h, environment=self.env)
        self.scene = Scene(name='nordic_saga', environment=self.env)
        # 夜晚场景的自发光件多（极光/符文/火焰/月光）：Bloom 阈值上抬、
        # 强度收敛——否则大发光体糊成一团全白（喷火段实测过曝）
        ps = self.renderer.post.settings
        ps.bloom_threshold = 1.35
        ps.bloom_intensity = 0.42
        # SSAO 关掉：雪原是大尺度低起伏表面，屏幕空间遮蔽在近景会算出
        # 大片伪影黑斑（贴地掠行镜头尤其明显），夜景本身也不需要它
        ps.ssao_enabled = False
        self.cam = Camera(position=(44.0, 30.0, 26.0),
                          target=(0.0, 12.0, -60.0),
                          fov=62.0, near=0.1, far=self.Z_FAR)
        pbr = get('pbr')
        self.parts = build_nordic_scene(self.scene, pbr)
        self.mats = self.parts['mats']

        # 龙的初始缩放（1.8 倍 = 巨龙的体量）；透明淡入淡出用 BLEND
        self.parts['dragon'].set_scale((1.8, 1.8, 1.8))
        for key in ('dragon', 'eyes'):
            self.mats[key].alpha_mode = 'OPAQUE'

        self.scene.update()

        # ---- 程序化配乐：缺失则现场渲染（一次性，之后走缓存）----
        self.audio = None
        try:
            wav = ensure_ost(duration=DURATION)
            from engine.audio import TimelineAudio
            self.audio = TimelineAudio(wav, loop=True, volume=0.8)
            if self.audio.ok:
                self.audio.play()
            else:
                print('[audio] unavailable (silent driver / no device)')
        except Exception as e:                    # 音频失败不阻断视觉演出
            print(f'[audio] init failed: {e}')
            self.audio = None

    # ------------------------------------------------------------ 每帧 --
    def update(self, dt):
        if self.paused:
            return
        t = self.time
        parts, mats = self.parts, self.mats

        # 相机：电影化关键帧（追龙窗口内自动锁定）
        eye, tgt = NS.camera_pose(t)
        self.cam.set_position((eye.x, eye.y, eye.z))
        self.cam.set_target((tgt.x, tgt.y, tgt.z))
        cam_fwd = (tgt.x - eye.x, tgt.y - eye.y, tgt.z - eye.z)

        # 龙：位置 + 朝向（含转弯侧倾）+ 透明淡入
        p = NS.dragon_position(t)
        parts['dragon'].set_translation((p.x, p.y, p.z))
        parts['dragon'].set_rotation(NS.dragon_rotation(t))
        a = NS.dragon_alpha(t)
        mats['dragon'].alpha = a
        mats['eyes'].alpha = a
        mode = 'BLEND' if a < 0.999 else 'OPAQUE'
        mats['dragon'].alpha_mode = mode
        mats['eyes'].alpha_mode = mode

        # 双翼扑动（镜像）+ 龙焰伸缩
        ang = NS.wing_angle(t)
        parts['wing_r'].set_rotation(
            _axis_quat((0.0, 0.0, 1.0), ang))
        parts['wing_l'].set_rotation(
            _axis_quat((0.0, 0.0, 1.0), -ang))
        fs = NS.fire_scale(t)
        parts['dragon_fire'].set_scale((fs, fs, fs * 1.22))

        # 篝火：跳动 + 慢旋；符文光柱升起
        cf = NS.campfire_scale(t)
        parts['campfire'].set_scale((cf, cf * 0.94, cf))
        parts['campfire'].set_rotation(NS.campfire_rotation(t))
        b = NS.rune_beam(t)
        parts['beam'].set_scale((1.0, 0.02 + 1.30 * b, 1.0))

        # 极光：整圈慢旋 + 漂移；亮度走材质 emissive
        for k, node in enumerate(parts['aurora']):
            pose = NS.aurora_pose(k, t)
            bx, by, bz = parts['aurora_base'][k]
            off = pose['translation']
            node.set_translation((bx + off.x, by + off.y, bz + off.z))
            node.set_rotation(pose['rotation'])
            tint = ((1.15, 1.25, 1.15), (0.92, 1.08, 1.42),
                    (1.24, 1.02, 1.22), (0.85, 1.26, 1.12))[k]
            b = pose['bright']
            mats[f'aurora_{k}'].emissive = (tint[0] * b, tint[1] * b,
                                            tint[2] * b)

        # 符文墙 / 光柱 / 窗光 / 星空 / 月轮 的亮度
        g = NS.rune_glow(t)
        mats['rune'].emissive = (g * 0.98, g * 1.02, g * 1.10)
        bg = 0.35 * b * min(1.0, g)
        mats['beam'].emissive = (0.55 * bg, 1.60 * bg, 2.30 * bg)
        wb = NS.window_bright(t)
        mats['win'].emissive = (2.30 * wb, 1.50 * wb, 0.66 * wb)
        sb = NS.stars_bright(t)
        mats['stars'].emissive = (1.45 * sb, 1.55 * sb, 1.80 * sb)
        mb = NS.moon_bright(t)
        mats['moon'].emissive = (2.05 * mb, 2.15 * mb, 2.40 * mb)

        # ---- 点光：篝火 / 符文墙辉 / 龙焰 / 月华补光 ----
        cfx, cfz = CAMPFIRE
        cfy = snow_height(cfx, cfz) + 1.15
        fl = NS.fire_light(t)
        lights = [((cfx, cfy, cfz), (2.10 * fl, 1.05 * fl, 0.34 * fl)),
                  ((0.0, 3.4, -41.8),                 # 贴墙冷光照亮符文石
                   (0.50 * min(1.3, g), 1.05 * min(1.3, g),
                    1.45 * min(1.3, g))),
                  ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
                  ((0.0, 2.6, -38.0), (0.40, 0.75, 1.10))]  # 墙前补光
        fw = NS.fire_window(t)
        if fw > 0.01:                             # 龙焰灯：龙腹正下方
            lights[2] = ((p.x, p.y - 1.8, p.z),
                         (2.40 * fw, 1.10 * fw, 0.32 * fw))
        self.renderer.set_point_lights(lights)

        # 飘雪（billboard 由相机方向定；每帧重写顶点缓冲）
        for field in parts['snowfall']:
            field.update(dt, self.cam.position, cam_fwd)

        self.scene.update(dt)
        if self.audio is not None:
            self.audio.sync(t, True)

    # ------------------------------------------------------------ 绘制 --
    def render(self):
        w, h = self.window.width, self.window.height
        self.renderer.resize(w, h)
        stats = self.renderer.render(self.cam, self.scene, w / float(h),
                                     self.window)
        self.perf.reset_counters()
        self.perf.draw_calls = stats['draw_calls']
        self.perf.triangles = stats['triangles']
        self.perf.visible = stats['visible']
        self.perf.culled = stats['culled']

    def on_resize_hook(self, width, height):
        self.renderer.resize(width, height)

    def status_text(self):
        act = NS.ACTS[0][1]
        for t0, name in NS.ACTS:
            if self.time >= t0:
                act = name
        audio = '-'
        if self.audio is not None and self.audio.ok:
            audio = 'MUTED' if getattr(self.audio, 'muted', False) else 'ON'
        return (f'{act}   {self.time:6.2f} / {self.DURATION:.1f}s   '
                f'{self.fps:5.1f} fps   audio {audio}')

    def on_key(self, symbol, modifiers):
        import pyglet
        s = chr(symbol) if 32 <= symbol < 127 else ''
        post = self.renderer.post.settings
        if s.upper() == 'B':
            post.bloom_enabled = not post.bloom_enabled
        elif s.upper() == 'O':
            post.ssao_enabled = not post.ssao_enabled
        elif s.upper() == 'M' and self.audio is not None:
            self.audio.toggle_mute()

    def close(self):
        if getattr(self, 'audio', None) is not None:
            self.audio.close()
        super().close()


def _axis_quat(axis, deg):
    from engine.mathkit import quat_from_axis_angle
    return quat_from_axis_angle(axis, deg)


if __name__ == '__main__':
    sys.exit(run(NordicSagaApp))
