# -*- coding: utf-8 -*-
"""应用装配：窗口事件 → 逻辑层状态变更 → 事件总线 → 渲染 + UI。

数据流（单向，M1~M6 一以贯之）：
  clock → PlaybackController.advance（logic）→ Timeline 采样 → 模型矩阵 / 材质
        → 渲染；窗口输入 → OrbitCamera / PlaybackController（logic）→ EventBus
        → 视图与 UI

M6 的三处升级：

1. **编排**：4s 的"环形浮动"换成 ``logic/choreography.py`` 的 48s 五幕演出
   （升起 / 环形波 / 机械臂取放 / 螺旋 / 归位），通道类型从 TRS 扩到
   tint+glow（材质），采样结果直接喂给渲染器的 per-object uniform。
2. **相机双模式**：默认走时间轴的电影机位（eye/target 通道 + look-at），
   按 C 或直接拖动鼠标切换到自由轨道相机；切换时按当前机位反解
   yaw/pitch/dist，画面不跳。
3. **夹持混合**：第三幕宝石的位置不再来自自己的平移通道，而是由
   ``carry_weight(t)`` 把"静置姿态"混合到"夹爪世界坐标"——夹爪矩阵来自
   骨骼节点树，所以宝石是**真的被臂带着走**。
"""
import math

import pyglet
from pyglet.math import Mat4, Vec3

from core.renderer import WorldRenderer
from logic.animation import Timeline, quat_to_mat4
from logic.camera import (DIST_MAX, DIST_MIN, PITCH_MAX, PITCH_MIN, OrbitCamera,
                          look_at_matrix)
from logic.choreography import (ACTS, ARM_BASE, CAM, CAM_TGT, DURATION,
                                build_world_timeline, carry_weight)
from logic.events import EventBus
from logic.playback import PlaybackController
from logic.skeleton import Skeleton, build_arm
from presentation.hud import Hud
from presentation.timeline_ui import TimelineUI

HELP_TEXT = ('SPACE 播放/暂停   L 循环   C 相机模式   N/P 下一幕/上一幕   '
             'R 重播   -/= 速度   拖动/滚轮 自由视角   ESC 退出')


class WorldApp:
    def __init__(self):
        self.events = EventBus()
        self.camera = OrbitCamera()          # 逻辑层：自由轨道相机状态
        self.camera_mode = 'cine'            # 'cine' = 时间轴机位 / 'free' = 轨道
        self.renderer = WorldRenderer()      # 核心层：窗口与 GPU 资源
        self.time = 0.0
        self._fps = 0.0
        self._ui_captured = False            # 本帧鼠标事件是否被 UI 消费

        # 逻辑层：48s 五幕编排（立方体 + 宝石 + 机械臂 + 相机）
        self.skeleton = Skeleton()
        build_arm(self.skeleton)
        self.renderer.build_arm()
        self.timeline = build_world_timeline(self.renderer.cube_meta,
                                             self.renderer.model_meta,
                                             duration=DURATION)
        self.playback = PlaybackController(self.timeline, looping=True)
        self.playback.play()

        # 表现层：字幕 HUD + 带幕标记的时间轴面板
        self.hud = Hud(acts=ACTS, help_text=HELP_TEXT)
        self.ui = TimelineUI(
            duration=self.timeline.duration,
            on_toggle_play=self.playback.toggle,
            on_toggle_loop=self.playback.set_looping,
            on_seek=self.playback.seek,
            acts=ACTS)

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
        self.hud.layout(w.width, w.height)
        self._on_frame(0.0)                  # 初始帧：t=0 姿态（不黑屏）
        self.ui.layout(w.width, w.height)
        pyglet.clock.schedule_interval(self.update, 1 / 60.0)

    # ---- 主循环（clock 驱动） ----
    def update(self, dt):
        self.time += dt
        if dt > 1e-6:                        # 帧率 EMA（pyglet 2.1.16 无 clock.get_fps）
            inst = 1.0 / dt
            self._fps = inst if self._fps <= 0.0 else self._fps * 0.9 + inst * 0.1
        result = self.playback.advance(dt)
        if result == PlaybackController.WRAPPED:
            self.events.dispatch_event('on_timeline_end')
        self.events.dispatch_event('on_frame', dt)

    # ---- 逻辑层事件 ----
    def _on_frame(self, dt):
        sampled = self.timeline.sample()

        # 1) 立方体：T·R·S 合成 + 材质（tint/glow）
        for idx, (name, _base) in enumerate(self.renderer.cube_meta):
            node = sampled.get(name, {})
            t = node.get('translation', Vec3(0, 0, 0))
            model = Mat4.from_translation(t)
            q = node.get('rotation')
            if q is not None:
                model = model @ quat_to_mat4(q)
            s = node.get('scale')
            if s is not None:
                model = model @ Mat4.from_scale(s)
            self.renderer.set_cube_model(idx, model)
            self.renderer.set_cube_material(idx,
                                            node.get('tint', Vec3(1, 1, 1)),
                                            float(node.get('glow', 0.0)))

        # 2) 骨骼节点树：先算世界矩阵，再写机械臂各段（父子继承）
        self.skeleton.apply_sample(sampled)
        self.skeleton.compute()
        self.renderer.set_arm_model(0, Mat4.from_translation(ARM_BASE))
        for idx, node in enumerate(('turret', 'seg1', 'seg2', 'grip'), start=1):
            self.renderer.set_arm_model(idx, self.skeleton.nodes[node].world_matrix)

        # 3) 外部模型（宝石）：静置姿态 ↔ 夹爪世界坐标按 carry_weight 混合
        carry = carry_weight(self.timeline.playhead)
        grip = self.skeleton.nodes['grip'].world_matrix
        grip_pos = Vec3(grip[12], grip[13], grip[14])
        for m_idx, (name, base) in enumerate(self.renderer.model_meta):
            node = sampled.get(name, {})
            t = node.get('translation', base)
            if carry > 0.0:
                t = Vec3(t.x + (grip_pos.x - t.x) * carry,
                         t.y + (grip_pos.y - t.y) * carry,
                         t.z + (grip_pos.z - t.z) * carry)
            q = node.get('rotation')
            model = Mat4.from_translation(t)
            if q is not None:
                model = model @ quat_to_mat4(q)
            self.renderer.models[m_idx].matrix = model

        # 4) 相机
        if self.camera_mode == 'cine':
            eye = sampled[CAM]['translation']
            tgt = sampled[CAM_TGT]['translation']
            self.renderer.window.view = look_at_matrix(eye, tgt, Vec3(0, 1, 0))
        else:
            self._on_view_changed()

    def _on_timeline_end(self):
        pass                                  # 预留：循环计数/闪烁等反馈

    def _on_view_changed(self):
        self.renderer.window.view = self.camera.view_matrix()

    # ---- 相机模式 ----
    def set_camera_mode(self, mode):
        """切换电影机位 / 自由轨道。切到 free 时按当前机位反解轨道参数。"""
        if mode == self.camera_mode:
            return
        if mode == 'free':
            eye, tgt = self._cine_pose()
            d = eye - tgt
            dist = max(DIST_MIN, min(DIST_MAX, d.length()))
            self.camera.target = tgt
            self.camera.dist = dist
            self.camera.pitch = max(PITCH_MIN, min(PITCH_MAX, math.degrees(
                math.asin(max(-1.0, min(1.0, d.y / max(1e-6, d.length())))))))
            self.camera.yaw = math.degrees(math.atan2(d.z, d.x))
        self.camera_mode = mode
        self._on_frame(0.0)

    def _cine_pose(self):
        """当前播头下时间轴给出的 (eye, target)。"""
        sampled = self.timeline.sample()
        return sampled[CAM]['translation'], sampled[CAM_TGT]['translation']

    # ---- 窗口事件 ----
    def _on_resize(self, width, height):
        self.renderer.apply_projection()
        self.ui.layout(width, height)
        self.hud.layout(width, height)
        return pyglet.event.EVENT_HANDLED   # 覆盖默认的正交投影

    def _on_mouse_press(self, x, y, button, modifiers):
        if button == pyglet.window.mouse.LEFT:
            self._ui_captured = self.ui.on_press(x, y)

    def _on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        if self._ui_captured:
            self.ui.on_drag(x, y)
        else:
            self.set_camera_mode('free')     # 拖动即接管相机（电影模式让位）
            self.camera.orbit(dx, dy)
            self.events.dispatch_event('on_view_changed')

    def _on_mouse_release(self, x, y, button, modifiers):
        self.ui.on_release()
        self._ui_captured = False

    def _on_mouse_scroll(self, x, y, sx, sy):
        self.set_camera_mode('free')
        self.camera.zoom(sy)
        self.events.dispatch_event('on_view_changed')

    def _on_key_press(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol == key.ESCAPE:
            self.close()
        elif symbol == key.SPACE:
            self.playback.toggle()
        elif symbol == key.L:
            self.playback.set_looping(not self.playback.looping)
        elif symbol == key.C:
            self.set_camera_mode('free' if self.camera_mode == 'cine' else 'cine')
        elif symbol == key.R:
            self.playback.seek(0.0)
            self.playback.play()
        elif symbol == key.N:
            self._jump_act(+1)
        elif symbol == key.P:
            self._jump_act(-1)
        elif symbol in (key.MINUS, key.NUM_MINUS):
            self.playback.set_speed(self.playback.speed - 0.25)
        elif symbol in (key.EQUAL, key.NUM_ADD):
            self.playback.set_speed(self.playback.speed + 0.25)

    def _jump_act(self, step):
        """跳到下一幕 / 上一幕（沿用 playhead，保持播放状态）。"""
        from logic.choreography import next_act_time, prev_act_time
        t = self.timeline.playhead
        self.playback.seek(next_act_time(t) if step > 0 else prev_act_time(t))

    def _on_draw(self):
        self.renderer.draw()                  # 3D 视口（透视 + 相机）
        self.renderer.begin_ui()              # 正交投影 + 关深度测试（视图存档）
        self.ui.update(self.timeline.playhead,
                       self.playback.playing, self.playback.looping)
        self.ui.draw()                        # 时间轴面板（屏幕空间）
        self.hud.update(self.timeline.playhead, stats=self._stats())
        self.hud.draw()                       # 幕字幕 + 状态行
        self.renderer.end_ui()                # 还原深度测试 + 投影 + 相机视图

    def _stats(self):
        cam = '电影机位' if self.camera_mode == 'cine' else '自由轨道'
        return (f'{cam}   速度 x{self.playback.speed:.2f}   '
                f'{self.timeline.playhead:6.2f} / {self.timeline.duration:.2f}s   '
                f'{self._fps:5.1f} fps')

    # ---- 供外部（main）使用 ----
    def close(self):
        self.renderer.window.close()

    # ---- 供 --shot 截图验证使用 ----
    def capture_at(self, t, path):
        """把播头移到 t、渲染一帧并抓图（渲染验证用，见 main.py --shot）。"""
        from presentation.shot import capture_window
        self.playback.pause()
        self.playback.seek(t)
        self._on_frame(0.0)
        self.renderer.draw()
        self.renderer.begin_ui()
        self.ui.update(self.timeline.playhead, self.playback.playing,
                       self.playback.looping)
        self.ui.draw()
        self.hud.update(self.timeline.playhead, stats=self._stats())
        self.hud.draw()
        self.renderer.end_ui()
        capture_window(self.renderer.window, path)
        print('[shot]', path)
