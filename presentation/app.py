# -*- coding: utf-8 -*-
"""应用装配：窗口事件 → 逻辑层状态变更 → 事件总线 → 渲染。

数据流（单向）：
  输入/clock → OrbitCamera（logic）→ EventBus → WorldRenderer（core）
"""
import pyglet

from core.renderer import WorldRenderer
from logic.camera import OrbitCamera
from logic.events import EventBus


class WorldApp:
    def __init__(self):
        self.events = EventBus()
        self.camera = OrbitCamera()          # 逻辑层：纯状态
        self.renderer = WorldRenderer()      # 核心层：窗口与 GPU 资源
        self.time = 0.0

        # ---- 事件订阅（表现层响应逻辑层） ----
        self.events.push_handlers(
            on_frame=self._on_frame,
            on_view_changed=self._on_view_changed)

        # ---- 窗口事件（核心层窗口 → 逻辑层状态） ----
        w = self.renderer.window
        w.push_handlers(
            on_resize=self._on_resize,
            on_mouse_drag=self._on_mouse_drag,
            on_mouse_scroll=self._on_mouse_scroll,
            on_key_press=self._on_key_press,
            on_draw=self._on_draw)

        self.renderer.apply_projection()
        self._on_view_changed()              # 初始相机位
        pyglet.clock.schedule_interval(self.update, 1 / 60.0)

    # ---- 主循环（clock 驱动） ----
    def update(self, dt):
        self.time += dt
        self.camera.advance(dt)              # M2 后由时间轴接管
        self.events.dispatch_event('on_frame', dt)

    # ---- 逻辑层事件 ----
    def _on_frame(self, dt):
        self._on_view_changed()

    def _on_view_changed(self):
        self.renderer.window.view = self.camera.view_matrix()

    # ---- 窗口事件 ----
    def _on_resize(self, width, height):
        self.renderer.apply_projection()
        return pyglet.event.EVENT_HANDLED   # 覆盖默认的正交投影

    def _on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.camera.orbit(dx, dy)
        self.events.dispatch_event('on_view_changed')

    def _on_mouse_scroll(self, x, y, sx, sy):
        self.camera.zoom(sy)
        self.events.dispatch_event('on_view_changed')

    def _on_key_press(self, symbol, modifiers):
        if symbol == pyglet.window.key.ESCAPE:
            self.close()

    def _on_draw(self):
        self.renderer.draw()

    # ---- 供外部（main）使用 ----
    def close(self):
        self.renderer.window.close()
