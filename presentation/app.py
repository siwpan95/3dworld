# -*- coding: utf-8 -*-
"""应用装配：窗口事件 → 逻辑层状态变更 → 事件总线 → 渲染 + UI。

数据流（单向，M3）：
  clock → PlaybackController.advance（logic）→ Timeline 采样 → 模型矩阵 → 渲染
  窗口输入 → OrbitCamera / PlaybackController（logic）→ EventBus → 视图/渲染
  UI 面板（presentation）读播放状态直绘，回调注入 PlaybackController
"""
import pyglet
from pyglet.math import Mat4, Vec3

from core.renderer import WorldRenderer
from logic.animation import build_demo_timeline, quat_to_mat4
from logic.camera import OrbitCamera
from logic.events import EventBus
from logic.playback import PlaybackController
from presentation.timeline_ui import TimelineUI


class WorldApp:
    def __init__(self):
        self.events = EventBus()
        self.camera = OrbitCamera()          # 逻辑层：相机纯状态
        self.renderer = WorldRenderer()      # 核心层：窗口与 GPU 资源
        self.time = 0.0
        self._ui_captured = False            # 本帧鼠标事件是否被 UI 消费

        # 逻辑层：时间轴 + 播放控制
        self.timeline = build_demo_timeline(self.renderer.cube_meta, duration=4.0)
        self.playback = PlaybackController(self.timeline, looping=True)

        # 表现层：时间轴 UI 面板
        self.ui = TimelineUI(
            duration=self.timeline.duration,
            on_toggle_play=self.playback.toggle,
            on_toggle_loop=self.playback.set_looping,
            on_seek=self.playback.seek)

        # ---- 事件订阅 ----
        self.events.push_handlers(
            on_frame=self._on_frame,
            on_view_changed=self._on_view_changed,
            on_timeline_end=self._on_timeline_end)

        # ---- 窗口事件 ----
        w = self.renderer.window
        w.push_handlers(
            on_resize=self._on_resize,
            on_mouse_press=self._on_mouse_press,
            on_mouse_drag=self._on_mouse_drag,
            on_mouse_release=self._on_mouse_release,
            on_mouse_scroll=self._on_mouse_scroll,
            on_key_press=self._on_key_press,
            on_draw=self._on_draw)

        self.renderer.apply_projection()
        self._on_view_changed()              # 初始相机位
        self.ui.layout(w.width, w.height)
        pyglet.clock.schedule_interval(self.update, 1 / 60.0)

    # ---- 主循环（clock 驱动） ----
    def update(self, dt):
        self.time += dt
        result = self.playback.advance(dt)
        if result == PlaybackController.WRAPPED:
            self.events.dispatch_event('on_timeline_end')
        elif result == PlaybackController.ENDED:
            pass                              # UI 状态直读，无需事件
        self.events.dispatch_event('on_frame', dt)

    # ---- 逻辑层事件 ----
    def _on_frame(self, dt):
        # 每帧采样（暂停/seek 后姿态同样刷新）→ 合成 T·R·S → 写渲染器
        sampled = self.timeline.sample()
        for idx, (name, _base) in enumerate(self.renderer.cube_meta):
            node = sampled.get(name, {})
            t = node.get('translation', Vec3(0, 0, 0))
            q = node.get('rotation')
            model = Mat4.from_translation(t)
            if q is not None:
                model = model @ quat_to_mat4(q)
            self.renderer.set_cube_model(idx, model)
        self._on_view_changed()

    def _on_timeline_end(self):
        pass                                  # 预留：循环计数/闪烁等反馈

    def _on_view_changed(self):
        self.renderer.window.view = self.camera.view_matrix()

    # ---- 窗口事件 ----
    def _on_resize(self, width, height):
        self.renderer.apply_projection()
        self.ui.layout(width, height)
        return pyglet.event.EVENT_HANDLED   # 覆盖默认的正交投影

    def _on_mouse_press(self, x, y, button, modifiers):
        if button == pyglet.window.mouse.LEFT:
            self._ui_captured = self.ui.on_press(x, y)

    def _on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        if self._ui_captured:
            self.ui.on_drag(x, y)
        else:
            self.camera.orbit(dx, dy)
            self.events.dispatch_event('on_view_changed')

    def _on_mouse_release(self, x, y, button, modifiers):
        self.ui.on_release()
        self._ui_captured = False

    def _on_mouse_scroll(self, x, y, sx, sy):
        self.camera.zoom(sy)
        self.events.dispatch_event('on_view_changed')

    def _on_key_press(self, symbol, modifiers):
        if symbol == pyglet.window.key.ESCAPE:
            self.close()
        elif symbol == pyglet.window.key.SPACE:
            self.playback.toggle()
        elif symbol == pyglet.window.key.L:
            self.playback.set_looping(not self.playback.looping)

    def _on_draw(self):
        self.renderer.draw()                  # 3D 视口（透视 + 相机）
        self.renderer.apply_screen_projection()
        self.ui.update(self.timeline.playhead,
                       self.playback.playing, self.playback.looping)
        self.ui.draw()                        # 时间轴面板（屏幕空间）
        self.renderer.restore_3d_projection()
        self._on_view_changed()               # 还原相机视图矩阵

    # ---- 供外部（main）使用 ----
    def close(self):
        self.renderer.window.close()
