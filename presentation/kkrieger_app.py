# -*- coding: utf-8 -*-
"""kkrieger 致敬 Demo 装配：96s 四幕程序化走廊漫游。

数据流（单向，沿用 M1~M6 分层约定）：
  clock → PlaybackController.advance（logic）→ Timeline 采样
        → look-at 视图矩阵 + 场景对象 model 矩阵 + 材质（tint/glow）→ 渲染

交互：SPACE 暂停/继续，R 重播，N/P 下一幕/上一幕，←/→ 快退快进，ESC 退出。
相机由时间轴脚本驱动（电影感漫游），不做鼠标轨道——轨道模式见 main.py。

DPI 实测记录（2026-09-29，pyglet 2.1.16 / Win10 150%）：默认 "platform" 模式下
实际 framebuffer = 请求尺寸（1280x720 物理），但 viewport setter 会乘
scale(1.5) → 渲染 1.5 倍放大裁切、get_framebuffer_size() 返回值也不可靠。
"real" 模式 scale 恒为 1.0，逻辑=物理，全部 1:1，绕开整套 DPI 混乱。
"""
import pyglet

pyglet.options['dpi_scaling'] = 'real'

from pyglet.math import Mat4, Vec3

from core.kkrieger_scene import KKRenderer
from logic.animation import quat_to_mat4
from logic.camera import look_at_matrix
from logic.kkrieger import (ACTS, CAM, CAM_TGT, DURATION, build_kkrieger_timeline,
                            next_act_time, prev_act_time)
from logic.playback import PlaybackController
from presentation.hud import Hud
from presentation.shot import capture_window
from presentation.timeline_ui import TimelineUI

HELP_TEXT = ('SPACE 暂停/继续   R 重播   N/P 下一幕/上一幕   '
             '←/→ 快退/快进   L 循环   ESC 退出')


class KKApp:
    def __init__(self, shots=None, duration=DURATION):
        """shots: [(t, path)] 播头越过 t 时抓帧存盘（--shot 验证用）。"""
        self.renderer = KKRenderer()
        self.timeline = build_kkrieger_timeline(self.renderer.animated_meta,
                                               duration=duration)
        self.playback = PlaybackController(self.timeline, looping=True)
        self.playback.play()
        self.shots = sorted(shots or [])
        self._fps = 0.0

        self.hud = Hud(acts=ACTS, help_text=HELP_TEXT)
        self.ui = TimelineUI(
            duration=self.timeline.duration,
            on_toggle_play=self.playback.toggle,
            on_toggle_loop=self.playback.set_looping,
            on_seek=self.playback.seek,
            acts=ACTS)

        w = self.renderer.window
        w.push_handlers(
            on_resize=self._on_resize,
            on_key_press=self._on_key_press,
            on_draw=self._on_draw)
        self.renderer.apply_projection()
        self.hud.layout(w.width, w.height)
        self.ui.layout(w.width, w.height)
        self._apply_frame()                  # t=0 初始位姿（避免首帧黑屏）
        pyglet.clock.schedule_interval(self.update, 1 / 60.0)

    # ---- 主循环 ----
    def update(self, dt):
        if dt > 1e-6:
            inst = 1.0 / dt
            self._fps = inst if self._fps <= 0.0 else self._fps * 0.9 + inst * 0.1
        self.playback.advance(dt)
        self._apply_frame()

    def _apply_frame(self):
        sampled = self.timeline.sample()
        # 相机：采样 eye/target → look-at 视图矩阵
        eye = sampled[CAM]['translation']
        tgt = sampled[CAM_TGT]['translation']
        self.renderer.window.view = look_at_matrix(eye, tgt, Vec3(0, 1, 0))
        # 场景对象：T·R·S 合成（无对应通道的属性用 base 兜底）+ 材质
        for idx, (name, base) in enumerate(self.renderer.animated_meta):
            node = sampled.get(name, {})
            t = node.get('translation', base)
            model = Mat4.from_translation(t)
            q = node.get('rotation')
            if q is not None:
                model = model @ quat_to_mat4(q)
            s = node.get('scale')
            if s is not None:
                if not isinstance(s, Vec3):
                    s = Vec3(s, s, s)
                model = model @ Mat4.from_scale(s)
            self.renderer.set_animated_model(idx, model)
            self.renderer.set_animated_material(
                idx, node.get('tint', Vec3(1, 1, 1)), node.get('glow', 0.0))

    # ---- 窗口事件 ----
    def _on_resize(self, width, height):
        self.renderer.apply_projection()
        self.hud.layout(width, height)
        self.ui.layout(width, height)
        return pyglet.event.EVENT_HANDLED

    def _on_key_press(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol == key.ESCAPE:
            self.close()
        elif symbol == key.SPACE:
            self.playback.toggle()
        elif symbol == key.R:
            self.playback.seek(0.0)
            self.playback.play()
        elif symbol == key.N:
            self.playback.seek(next_act_time(self.timeline.playhead))
        elif symbol == key.P:
            self.playback.seek(prev_act_time(self.timeline.playhead))
        elif symbol == key.L:
            self.playback.set_looping(not self.playback.looping)
        elif symbol == key.LEFT:
            self.playback.seek(max(0.0, self.timeline.playhead - 5.0))
        elif symbol == key.RIGHT:
            self.playback.seek(self.timeline.playhead + 5.0)

    def _on_draw(self):
        self.renderer.draw()
        self.renderer.begin_ui()             # 正交投影 + 关深度（视图存档）
        self.ui.update(self.timeline.playhead, self.playback.playing,
                       self.playback.looping)
        self.ui.draw()                       # 时间轴面板 + 幕刻度
        self.hud.update(self.timeline.playhead, stats=self._stats())
        self.hud.draw()                      # 幕字幕 + 状态行
        self.renderer.end_ui()               # 还原深度/投影/相机视图

    # ---- --shot 截图验证：直接把播头跳到各时刻抓帧，不等实时播放 ----
    def capture_shots(self):
        """按 shots 列表逐帧抓图（每帧：seek → 采样 → 渲染 → 抓图）。

        早期版本靠"播头越过时刻就抓帧"，96s 的片子要真等 90 秒才跑完；
        现在一次性跳帧采样，整个 --shot 流程 1 秒内结束。
        """
        was_playing = self.playback.playing
        self.playback.pause()
        for t, path in self.shots:
            self.playback.seek(t)
            self._apply_frame()
            self._on_draw()
            print('[shot]', capture_window(self.renderer.window, path))
        if was_playing:
            self.playback.play()

    def _stats(self):
        act = ACTS[0]
        for a in ACTS:
            if self.timeline.playhead >= a[0]:
                act = a
        return (f'{act[1]}   {self.timeline.playhead:6.2f} / '
                f'{self.timeline.duration:.2f}s   {self._fps:5.1f} fps')

    def close(self):
        self.renderer.window.close()
