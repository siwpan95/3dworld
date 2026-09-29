# -*- coding: utf-8 -*-
"""应用装配：窗口事件 → 逻辑层状态变更 → 事件总线 → 渲染。

数据流（单向，M2）：
  clock → Timeline.advance/采样（logic）→ 模型矩阵 → WorldRenderer（core）
  窗口输入 → OrbitCamera（logic）→ EventBus(on_view_changed) → window.view
"""
import pyglet
from pyglet.math import Mat4, Vec3

from core.renderer import WorldRenderer
from logic.animation import build_demo_timeline, quat_to_mat4
from logic.camera import OrbitCamera
from logic.events import EventBus


class WorldApp:
    def __init__(self):
        self.events = EventBus()
        self.camera = OrbitCamera()          # 逻辑层：相机纯状态
        self.renderer = WorldRenderer()      # 核心层：窗口与 GPU 资源
        self.time = 0.0

        # 逻辑层：演示时间轴（升降 + 旋转编排，4 秒循环）
        self.timeline = build_demo_timeline(self.renderer.cube_meta, duration=4.0)

        # ---- 事件订阅（表现层响应逻辑层） ----
        self.events.push_handlers(
            on_frame=self._on_frame,
            on_view_changed=self._on_view_changed,
            on_timeline_end=self._on_timeline_end)

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
        if self.timeline.advance(dt):
            self.events.dispatch_event('on_timeline_end')
        self.events.dispatch_event('on_frame', dt)

    # ---- 逻辑层事件 ----
    def _on_frame(self, dt):
        # 采样时间轴 → 合成模型矩阵（T·R·S）→ 写入渲染器
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
        pass                                  # 预留：M3 播放控制时接 UI 反馈

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
