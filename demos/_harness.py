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
    """统一的命令行开关。返回 Namespace（额外带 .width/.height/.flags 字段）。

    所有**控制台输出一律 ASCII**：中文注释可以，但 print 出去的中文在
    Windows GBK 代码页的 cmd/PowerShell 里会显示成乱码（实测
    ``[shot] OK 2 张`` → 一串问号），而乱码会污染自动化脚本的解析。
    """
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument('--smoke', nargs='?', type=float, const=2.0, default=None,
                   metavar='SEC', help='smoke test: auto-close after N sec (default 2.0)')
    p.add_argument('--shot', action='store_true',
                   help='capture frames to docs/ then auto-close (times in SHOTS)')
    p.add_argument('--size', default='1280x720', metavar='WxH',
                   help='window size, default 1280x720')
    p.add_argument('--fps', type=float, default=60.0, help='logic tick rate, default 60')
    p.add_argument('--duration', type=float, default=None,
                   help='override demo loop duration (seconds)')
    p.add_argument('--no-vsync', action='store_true',
                   help='disable vsync (use with --bench for true frame rate)')
    p.add_argument('--bench', type=float, default=None, metavar='SEC',
                   help='benchmark SEC seconds without vsync, print frame stats, exit')
    p.add_argument('--flag', action='append', default=[], metavar='NAME',
                   help='demo-specific switch, readable as args.flags (repeatable)')
    args = p.parse_args(argv)
    w, _, h = args.size.lower().partition('x')
    try:
        args.width, args.height = int(w), int(h)
    except ValueError:
        p.error(f'--size needs WxH like 1280x720, got {args.size!r}')
    if args.bench is not None:
        args.no_vsync = True                 # 基准就是要真实吞吐，vsync 会把它压成刷新率
        args.smoke = None
    # --flag 允许一次给多个：--flag a,b 与 --flag a --flag b 等价
    args.flags = {f.strip() for item in args.flag
                  for f in str(item).split(',') if f.strip()}
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
        from engine.fonts import resolve_cjk_font
        font_kw = {}
        _cjk = resolve_cjk_font()             # 显式 CJK 字体（跨平台兜底；见 engine/fonts.py）
        if _cjk:
            font_kw['font_name'] = _cjk
        self.batch = pyglet.graphics.Batch()
        self.bar_top = shapes.Rectangle(0, 0, 10, self.BAR_H, color=self.BAR_COLOR,
                                        batch=self.batch)
        self.bar_bottom = shapes.Rectangle(0, 0, 10, self.BAR_H, color=self.BAR_COLOR,
                                           batch=self.batch)
        self.title = text.Label(title, font_size=15, weight='bold', **font_kw,
                                x=14, y=0, anchor_x='left', anchor_y='center',
                                color=COL_TITLE, batch=self.batch)
        self.status = text.Label('', font_size=12, **font_kw,
                                 x=0, y=0, anchor_x='right', anchor_y='center',
                                 color=COL_STATUS, batch=self.batch)
        self.help = text.Label(help_text, font_size=12, **font_kw,
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
            width, height, caption=self.TITLE, resizable=True,
            vsync=not getattr(self.args, 'no_vsync', False))
        self.time = 0.0
        self.paused = False
        self.fps = 0.0
        self.frame_ms = []                   # --bench：逐帧渲染耗时（毫秒）
        self._last_draw = None
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
        if self.args.bench is not None:
            # 基准模式：逻辑每帧都跑（schedule = 每个循环一次），否则 1/60 的
            # 定时器会把事件循环压到 60Hz —— 那样量到的是"定时器多久响一次"，
            # 不是"渲染跑多快"（第一版就是这么白测的）。
            pyglet.clock.schedule(self._tick)
        else:
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
        self._sample_frame_time()

    def _sample_frame_time(self):
        """--bench 用：量**渲染帧**耗时（不是逻辑 tick）。

        逻辑 tick 是 60Hz 定时器，用它的 dt 算帧率会把"渲染跑多快"这个问题
        测成"定时器多久响一次"。这里直接量 on_draw 之间的间隔，vsync 关掉后
        就是真实吞吐。前 1.0s 丢弃（着色器编译 / 首帧上传的预热——实测
        raymarch 的首帧编译能到 260ms，算进最慢帧会把"稳态卡不卡"带偏）。
        """
        import time as _t
        now = _t.perf_counter()
        if self._last_draw is not None:
            dt = now - self._last_draw
            if getattr(self, '_bench_warm', False):
                self.frame_ms.append(dt * 1000.0)
        self._last_draw = now
        if not getattr(self, '_bench_warm', False) and self.time > 1.0:
            self._bench_warm = True

    def bench_report(self):
        """返回 (frames, span_s, mean, median, p1, worst_ms, jank_ratio)。

        p1 = 第 1 百分位帧率（1% 最慢帧的下界），jank = 超过 20ms 的帧占比
        —— 均值会被偶发快帧拉高，p1 与 jank 才反映"卡不卡"。
        """
        ms = sorted(self.frame_ms)
        if not ms:
            return (0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        span = sum(ms) / 1000.0
        mean = len(ms) / span
        median = 1000.0 / ms[len(ms) // 2]
        p1 = 1000.0 / ms[min(len(ms) - 1, int(len(ms) * 0.99))]
        jank = sum(1 for m in ms if m > 20.0) / len(ms)
        return (len(ms), span, mean, median, p1, ms[-1], jank)

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
        """播头越过截图时刻就抓帧；全部抓完后自动关闭窗口（--shot）。

        抓帧失败（目标目录只读/不可写——打包 exe 在受限目录运行时）只告警
        并跳过该帧，不打断演示。
        """
        while (self._shot_i < len(self._shots)
               and self.time >= self._shots[self._shot_i][0]):
            _, path = self._shots[self._shot_i]
            if not os.path.isabs(path):
                path = os.path.join(PROJECT_ROOT, path)
            try:
                print('[shot]', capture(self.window, path))
            except Exception as e:                     # 不打断演示
                print(f'[shot] failed ({path}): {e}')
            self._shot_i += 1
        # 仅 --shot 模式在抓完全部帧后自动关闭；其余模式（smoke/正常/bench）
        # 只抓帧不停演示——历史 bug：这里漏了模式判断，会让声明了 SHOTS 的
        # 长跑演示在抓完最后一帧时被提前关掉。
        if (self.args.shot and self._shots
                and self._shot_i >= len(self._shots)
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
        raise SystemExit(f'{app_cls.__name__}: --shot requires SHOTS')
    app = app_cls(args.width, args.height, args=args)

    if args.bench is not None:
        pyglet.clock.schedule_once(lambda dt: app.close(), args.bench + 0.5)
        print(f'[bench] vsync=off, running {args.bench}s ...')
    elif args.smoke is not None:
        pyglet.clock.schedule_once(lambda dt: app.close(), args.smoke)
        print(f'[smoke] auto-close in {args.smoke}s')
    elif args.shot:
        print(f'[shot] planned {len(app_cls.SHOTS)} frames, close when done')
    # interval=0 → 窗口重绘"能多快就多快"。默认值是 1/60，那才是 60fps 的真正
    # 来源（不是 vsync）：基准模式必须关掉它，否则量的还是 60。正常跑用 1/60，
    # 与 pyglet 默认行为一致。
    pyglet.app.run(interval=0 if args.bench is not None else 1 / 60)

    if args.bench is not None:
        n, span, mean, median, p1, worst, jank = app.bench_report()
        print(f'[bench] frames={n} span={span:.2f}s '
              f'fps mean={mean:.1f} median={median:.1f} p1={p1:.1f} '
              f'worst_frame_ms={worst:.2f} jank_gt20ms={jank * 100:.1f}%')
    if args.shot:
        missing = [p for _, p in app_cls.SHOTS
                   if not os.path.exists(os.path.join(PROJECT_ROOT, p))]
        if missing:
            raise SystemExit(f'[shot] missing: {missing}')
        print(f'[shot] OK {len(app_cls.SHOTS)} files')
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
