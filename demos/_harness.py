# -*- coding: utf-8 -*-
"""demos 公共骨架（DemoApp）：窗口、DPI、命令行、HUD、截图与主循环。

设计目标：每个 ``demos/*.py`` 只关心"这一帧画什么"，其余全部由本模块兜底。

命令行（所有 demo 一致）::

    python demos/<name>.py                 正常运行
    python demos/<name>.py --smoke [秒]    冒烟：N 秒后自动关闭（默认 2.0）
    python demos/<name>.py --shot          按时抓帧存 docs/ 后自动关闭
    python demos/<name>.py --size 960x540  指定窗口尺寸
    python demos/<name>.py --fps 30        指定逻辑刷新率

实测坑位（pyglet 2.1.16 / Win10 150% DPI，全部在本模块内一次性兜住）：

- ``dpi_scaling`` 默认 ``'platform'`` 时 framebuffer = 请求尺寸，但 viewport
  setter 会乘 scale → 渲染放大裁切；统一设 ``'real'``（逻辑 = 物理）。
- 截图不用 ``get_color_buffer()``：它按 GL_VIEWPORT（被 DPI 放大）读取会越界
  读出黑边；这里用 ``glReadPixels`` 按实际 buffer 尺寸直读。
- ``Batch.add`` / ``c3f`` 在 2.1.16 已移除，几何一律走
  ``ShaderProgram.vertex_list(...)``；着色器里用到哪些属性就要声明哪些
  （``colors`` 不声明而传入会抛 ShaderException）。
- ``Quaternion.to_mat4()`` 输出是标准旋转矩阵的转置，旋转请用
  ``logic.animation.quat_to_mat4``。

子类契约（只覆盖这几个钩子）::

    TITLE, HELP, SHOTS, DURATION   类属性（SHOTS = ((时间秒, 输出路径), ...)）
    setup()                        创建 GL 资源（窗口上下文已就绪）
    update(dt)                     推进动画（self.time 已由基类累加）
    render()                       绘制 3D（清屏由基类负责）
    on_key(symbol, modifiers)      额外按键（ESC 由基类处理）

投影约定：基类在 ``on_resize`` 里把 ``window.projection`` 设为透视矩阵，
``render()`` 内自行设置 ``window.view``（相机视图矩阵）。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys

import pyglet

# 必须在创建 Window 之前设置（导入本模块即生效）
pyglet.options['dpi_scaling'] = 'real'

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DOCS_DIR = os.path.join(PROJECT_ROOT, 'docs')

from pyglet import text                                        # noqa: E402
from pyglet.gl import (GL_BLEND, GL_DEPTH_TEST,                 # noqa: E402
                       GL_ONE_MINUS_SRC_ALPHA, GL_RGBA, GL_SRC_ALPHA,
                       GL_TRIANGLES, GL_UNSIGNED_BYTE, glBlendFunc,
                       glDisable, glEnable, glReadPixels)
from pyglet.math import Mat4                                   # noqa: E402

COL_TITLE = (245, 247, 250, 255)
COL_STATUS = (200, 210, 225, 255)
COL_HELP = (168, 176, 190, 255)


# --------------------------------------------------------------- 命令行 --
def parse_args(argv=None):
    """统一的命令行开关。返回 Namespace（额外带 .width/.height 数值字段）。"""
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument('--smoke', nargs='?', type=float, const=2.0, default=None,
                   metavar='SEC', help='冒烟测试：N 秒后自动关闭（默认 2.0）')
    p.add_argument('--shot', action='store_true',
                   help='按时抓帧写 docs/ 后自动关闭（时间点见各 demo 的 SHOTS）')
    p.add_argument('--size', default='1280x720', metavar='WxH',
                   help='窗口尺寸，默认 1280x720')
    p.add_argument('--fps', type=float, default=60.0, help='逻辑刷新率，默认 60')
    p.add_argument('--duration', type=float, default=None,
                   help='覆盖演示循环时长（秒）')
    args = p.parse_args(argv)
    w, _, h = args.size.lower().partition('x')
    try:
        args.width, args.height = int(w), int(h)
    except ValueError:
        p.error(f'--size 需要形如 1280x720，收到 {args.size!r}')
    return args


# ----------------------------------------------------------------- 截图 --
def capture(window, path):
    """按实际 buffer 尺寸抓帧并存 PNG（DPI 无关，正立）。

    pyglet 图像约定原点在左下（与 glReadPixels 一致），PNG 编码器保存时
    会自动翻到顶部起始——原样保存即为正立图像。
    """
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    w, h = window.width, window.height
    row = w * 4
    buf = (ctypes.c_ubyte * (row * h))()
    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, buf)
    pyglet.image.ImageData(w, h, 'RGBA', bytes(buf), row).save(path)
    return path


# ------------------------------------------------------------------ HUD --
class Hud:
    """屏幕空间信息层：标题/帮助（顶部）与状态（右上）。

    顶部与底部各铺一条**半透明暗底**：亮背景 demo（水面、雪地地形）里
    纯文字会被背景吃掉——实测 water_waves 的帮助行就糊在水面上看不清。
    底条先加入 Batch，因此总在文字之下。
    """

    BAR_H = 30
    BAR_COLOR = (8, 10, 14, 96)

    def __init__(self, title='', help_text=''):
        from pyglet import shapes
        self.batch = pyglet.graphics.Batch()
        self.bar_top = shapes.Rectangle(0, 0, 10, self.BAR_H, color=self.BAR_COLOR,
                                        batch=self.batch)
        self.bar_bottom = shapes.Rectangle(0, 0, 10, self.BAR_H, color=self.BAR_COLOR,
                                           batch=self.batch)
        self.title = text.Label(title, font_size=15, weight='bold',
                                x=14, y=0, anchor_x='left', anchor_y='center',
                                color=COL_TITLE, batch=self.batch)
        self.status = text.Label('', font_size=12,
                                 x=0, y=0, anchor_x='right', anchor_y='center',
                                 color=COL_STATUS, batch=self.batch)
        self.help = text.Label(help_text, font_size=12,
                               x=14, y=0, anchor_x='left', anchor_y='center',
                               color=COL_HELP, batch=self.batch)
        self.visible = True

    def layout(self, width, height):
        # shapes.Rectangle.position 只接受 (x, y)（与 text.Label 的三元组不同）
        self._width = width
        self.bar_top.position = (0, height - self.BAR_H)
        self.bar_top.width, self.bar_top.height = width, self.BAR_H
        self.bar_bottom.position = (0, 0)
        self.bar_bottom.width, self.bar_bottom.height = width, self.BAR_H
        self.title.position = (14, height - 22, 0)
        self.status.position = (width - 14, height - 22, 0)
        self.help.position = (14, 16, 0)

    def set_status(self, text):
        self.status.text = text
        # 窄窗口下标题会和状态行撞在一起（实测 --size 1000x700 就糊成一团）。
        # 撞车时让标题让位——窗口标题栏里还有一份，信息不丢。
        self.title.visible = (self.title.content_width + self.status.content_width
                              <= getattr(self, '_width', 9999) - 40)

    def draw(self):
        if self.visible:
            self.batch.draw()


# ---------------------------------------------------------- 全屏四边形 --
def make_fullscreen_quad(program):
    """NDC 全屏四边形（两个三角形），供纯片元着色器 demo 使用。

    顶点直接落在裁剪空间 [-1,1]，不依赖 WindowBlock；着色器里必须同时声明
    ``in vec3 position;`` 与 ``in vec4 colors;``（pyglet 按名字校验属性）。
    """
    verts = (-1.0, -1.0, 0.0, 1.0, -1.0, 0.0, 1.0, 1.0, 0.0,
             -1.0, -1.0, 0.0, 1.0, 1.0, 0.0, -1.0, 1.0, 0.0)
    colors = (1.0, 1.0, 1.0, 1.0) * 6
    return program.vertex_list(6, GL_TRIANGLES,
                               position=('f', verts), colors=('f', colors))


# -------------------------------------------------------------- DemoApp --
class DemoApp:
    """所有 demo 的基类：窗口 + 主循环 + 截图 + HUD。"""

    TITLE = 'pyglet demo'
    HELP = 'ESC 退出   SPACE 暂停/继续'
    SHOTS = ()                    # ((t, 'docs/xxx.png'), ...)
    DURATION = None               # 循环时长（秒），None = 循环 / 不适用
    HUD = True                    # 是否绘制 HUD（截图同样包含）
    FOV = 60.0
    Z_NEAR, Z_FAR = 0.1, 500.0

    def __init__(self, width=1280, height=720, args=None):
        self.args = args if args is not None else parse_args([])
        if self.args.duration is not None:
            self.DURATION = self.args.duration
        self.window = pyglet.window.Window(
            width, height, caption=self.TITLE, resizable=True, vsync=True)
        self.time = 0.0
        self.paused = False
        self.fps = 0.0
        self.perspective = Mat4()
        self.hud = Hud(self.TITLE, self.HELP) if self.HUD else None
        self._shots = sorted(self.SHOTS)
        self._shot_i = 0
        self._closing = False

        self.window.push_handlers(
            on_resize=self._on_resize,
            on_key_press=self._on_key_press,
            on_draw=self._on_draw,
            on_mouse_drag=self._on_mouse_drag,
            on_mouse_scroll=self._on_mouse_scroll)

        self.setup()                       # 子类：创建 GL 资源
        self._apply_projection(width, height)
        if self.hud is not None:
            self.hud.layout(width, height)
        pyglet.clock.schedule_interval(self._tick, 1.0 / max(1e-3, self.args.fps))

    # -------------------------------------------------- 子类覆盖点 --
    def setup(self):
        """创建 GL 资源（窗口上下文已就绪）。"""

    def update(self, dt):
        """推进动画（self.time 已累加）。"""

    def render(self):
        """绘制 3D 场景（清屏已由基类完成）。"""

    def on_key(self, symbol, modifiers):
        """额外按键处理（ESC 由基类处理）。"""

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        """鼠标拖动（默认忽略）。"""

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        """滚轮（默认忽略）。"""

    def status_text(self):
        """HUD 右上角状态文本。"""
        dur = '' if self.DURATION is None else f' / {self.DURATION:.1f}s'
        flag = '  [PAUSED]' if self.paused else ''
        return f't={self.time:7.2f}s{dur}   {self.fps:5.1f} fps{flag}'

    # ---------------------------------------------------------- 主循环 --
    def _tick(self, dt):
        if dt > 1e-6:                                # 帧率 EMA（pyglet 2.1.16 无 clock.get_fps）
            inst = 1.0 / dt
            self.fps = inst if self.fps <= 0.0 else self.fps * 0.9 + inst * 0.1
        if not self.paused:
            self.time += dt
            if self.DURATION:
                self.time %= self.DURATION
        self.update(dt)

    def _apply_projection(self, width, height):
        height = max(1, height)
        self.window.viewport = (0, 0, width, height)
        self.perspective = Mat4.perspective_projection(
            width / float(height), z_near=self.Z_NEAR, z_far=self.Z_FAR,
            fov=self.FOV)
        self.window.projection = self.perspective

    def _on_resize(self, width, height):
        self._apply_projection(width, height)
        if self.hud is not None:
            self.hud.layout(width, height)
        return pyglet.event.EVENT_HANDLED

    def _on_key_press(self, symbol, modifiers):
        if symbol == pyglet.window.key.ESCAPE:
            self.window.close()
        elif symbol == pyglet.window.key.SPACE:
            self.paused = not self.paused        # 统一：SPACE = 暂停/继续
        else:
            self.on_key(symbol, modifiers)

    def _on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.on_mouse_drag(x, y, dx, dy, buttons, modifiers)

    def _on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        self.on_mouse_scroll(x, y, scroll_x, scroll_y)

    def _on_draw(self):
        self.window.clear()
        self.render()
        if self.hud is not None:
            self.hud.set_status(self.status_text())
            self._draw_hud()
        self._capture_due()

    def _draw_hud(self):
        w, h = self.window.width, self.window.height
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDisable(GL_DEPTH_TEST)
        self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -255, 255)
        self.window.view = Mat4()
        self.hud.draw()
        self.window.projection = self.perspective
        glEnable(GL_DEPTH_TEST)

    def _capture_due(self):
        """播头越过截图时刻就抓帧；全部抓完后自动关闭窗口（--shot）。"""
        while (self._shot_i < len(self._shots)
               and self.time >= self._shots[self._shot_i][0]):
            _, path = self._shots[self._shot_i]
            if not os.path.isabs(path):
                path = os.path.join(PROJECT_ROOT, path)
            print('[shot]', capture(self.window, path))
            self._shot_i += 1
        if (self._shots and self._shot_i >= len(self._shots)
                and not self._closing):
            self._closing = True
            pyglet.clock.schedule_once(lambda dt: self.close(), 0.2)

    # ------------------------------------------------------------- 收尾 --
    def close(self):
        self.window.close()


# ------------------------------------------------------------------ 入口 --
def run(app_cls, argv=None):
    """统一入口：解析命令行 → 建 app → 按模式排定关闭 → 跑主循环。"""
    args = parse_args(argv)
    if args.shot and not app_cls.SHOTS:
        raise SystemExit(f'{app_cls.__name__}: --shot 需要定义 SHOTS')
    app = app_cls(args.width, args.height, args=args)

    if args.smoke is not None:
        pyglet.clock.schedule_once(lambda dt: app.close(), args.smoke)
        print(f'[smoke] {args.smoke}s 后自动关闭')
    elif args.shot:
        print(f'[shot] 计划 {len(app_cls.SHOTS)} 张，抓完自动关闭')
    pyglet.app.run()

    if args.shot:
        missing = [p for _, p in app_cls.SHOTS
                   if not os.path.exists(os.path.join(PROJECT_ROOT, p))]
        if missing:
            raise SystemExit(f'[shot] 缺失: {missing}')
        print(f'[shot] OK {len(app_cls.SHOTS)} 张')
    print('EXIT_OK')
    return 0


def orbit_camera_input(app, camera, invert_y=False):
    """把鼠标拖动/滚轮接到 ``logic.camera.OrbitCamera``。

    返回 ``(on_drag, on_scroll)``，赋给子类同名方法即可::

        self.on_mouse_drag, self.on_mouse_scroll = orbit_camera_input(self, cam)
    """
    sign = -1.0 if invert_y else 1.0

    def on_drag(x, y, dx, dy, buttons, modifiers):
        camera.orbit(dx, sign * dy)

    def on_scroll(x, y, scroll_x, scroll_y):
        camera.zoom(scroll_y)

    return on_drag, on_scroll
