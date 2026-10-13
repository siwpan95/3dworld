# -*- coding: utf-8 -*-
"""神之手 / GodHand —— P1-① 神之手内核（Cordis 委托阶段 C · 交互 demo）。

机制致敬 Populous / From Dust 的「神力实时改写地形」：玩家以「抬山 / 挖海」之力
实时修改高度场，淹没或掩埋敌方聚落即胜。原创美术 / 命名 / 世界观，不碰任何原 IP。

阶段 A（demos/godhand.py 初版）：直连 engine，自管地形与聚落状态。
阶段 B（experiments/cordis/godhand_kernel.py）：把玩法拆成「共同核（Render/Economy/
         Entity/Rules/Terrain）+ 内核（GodHandKernel）」，用 cordis v4.0.0 fiber 属性式
         API 接线，并验证 GL 资源随内核 fiber dispose 可逆回滚（headless 模拟淹没通关 + 回滚断言）。
阶段 C（本文件）：demo 不再自管玩法，而是**委托 Cordis 内核**——挂载则由
    **cordis.godhand.yml 配置树**声明（试点三：setup() 经 asyncio 一次性引导配置树，
    host.tree.mount_tree 负责挂载与就绪等待），共同核 + GodHandKernel 内核全由 yaml
    声明；内核在 __cordis_init__ 注入神力、放置聚落、取好 GL 句柄；
    update()  把 dt 交给内核驱动；demo 侧自动驾驶（依次挖海淹没每个聚落）使录制自演示；
              左键涂抹 = 抬山 / 挖海（神手之力），N 键无（内核无此动作）；
    render()  只读内核 / 实体注册表 / 地形共同核的状态来画（地形高度场、水面、聚落、神手光标）；
    on_key    R 抬山 / F 挖海 切换神手，A 切换自动驾驶。

渲染层（GL 程序、立方体网格、地形/水面、相机、HUD、音效、拾取）由本 demo 负责——
这是「共同核 + 内核」范式在切片里的落地：逻辑在 Cordis，表现由宿主持有。真实
全 Cordis 化时，RenderService 会接管 GL 上下文；此处为保持离线可录制，宿主保留
pyglet 直接绘制（内核的 RenderService 仅做 headless 记账与回滚语义）。

复用：demos/_harness.DemoApp/run、logic.camera.OrbitCamera、engine.picking、
engine.audio（SfxPlayer/Track），以及 experiments/cordis/godhand_kernel
（共同核 + 神之手内核，12 核契约见 core/contracts.py）。

操作：右键拖拽 转视角   滚轮 缩放   左键涂抹 抬山/挖海（R/F 切换神手）   A 切换自动驾驶
      SPACE 暂停   ESC 退出。
"""
import array
import asyncio
import math
import os
import sys
import tempfile

import pyglet
from pyglet.gl import GL_DEPTH_TEST, GL_TRIANGLES, glDisable, glEnable
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4, Vec3
from pyglet.window import mouse

from _harness import DemoApp, run
from logic.camera import OrbitCamera
from engine.picking import ray_from_screen
from engine.audio import Track, render_wav, SfxPlayer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 试点三：挂载由 cordis.godhand.yml 配置树声明（host.tree 共享引导）；
# 绘制所需几何常量从内核「包路径」取（单一模块身份，不再走顶层名双副本）
from host.tree import WANT_GODHAND, config_path, mount_tree  # noqa: E402
from experiments.cordis.godhand_kernel import HALF_T, SEA  # noqa: E402

SKY = (0.60, 0.72, 0.86)


# ============================================================= 纯函数几何 ==
def _mat_translate(tx, ty, tz):
    return Mat4(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, tx, ty, tz, 1)


def _mat_scale(sx, sy, sz):
    return Mat4(sx, 0, 0, 0, 0, sy, 0, 0, 0, 0, sz, 0, 0, 0, 0, 1)


# ============================================================= 网格几何 ==
def _unit_cube():
    """单位立方体（中心原点），每面烘焙一个明暗 shade（顶亮/侧中/底暗），免法线。"""
    faces = [
        ([(-.5, -.5, +.5), (+.5, -.5, +.5), (+.5, +.5, +.5), (-.5, +.5, +.5)], 1.00),
        ([(-.5, -.5, -.5), (+.5, -.5, -.5), (+.5, +.5, -.5), (-.5, +.5, -.5)], 0.50),
        ([(-.5, -.5, +.5), (+.5, -.5, +.5), (+.5, -.5, -.5), (-.5, -.5, -.5)], 0.82),
        ([(-.5, +.5, +.5), (+.5, +.5, +.5), (+.5, +.5, -.5), (-.5, +.5, -.5)], 0.82),
        ([(+.5, -.5, +.5), (+.5, -.5, -.5), (+.5, +.5, -.5), (+.5, +.5, +.5)], 0.75),
        ([(-.5, -.5, +.5), (-.5, -.5, -.5), (-.5, +.5, -.5), (-.5, +.5, +.5)], 0.75),
    ]
    pos, shade = [], []
    for quad, s in faces:
        a, b, c, d = quad
        for p in (a, b, c, a, c, d):
            pos.extend(p)
            shade.extend((s, s, s))
    return pos, shade


# ============================================================= 着色器 ==
VERTEX_SRC = '''#version 330 core
in vec3 position;
in vec3 shade;
uniform WindowBlock {
    mat4 projection;
    mat4 view;
} window;
uniform mat4 u_model;
uniform vec3 u_tint;
uniform vec3 u_eye;
uniform vec3 u_fog;
out vec3 v_col;
out vec3 v_world;
void main() {
    vec4 world = u_model * vec4(position, 1.0);
    v_world = world.xyz;
    v_col = shade * u_tint;
    gl_Position = window.projection * window.view * world;
}'''

FRAGMENT_SRC = '''#version 330 core
in vec3 v_col;
in vec3 v_world;
uniform vec3 u_eye;
uniform vec3 u_fog;
out vec4 final_colors;
void main() {
    float d = distance(v_world, u_eye);
    float fog = 1.0 - exp(-d * 0.012);
    final_colors = vec4(mix(v_col, u_fog, clamp(fog, 0.0, 1.0)), 1.0);
}'''


# ============================================================= DemoApp ==
class GodHandDemo(DemoApp):
    TITLE = '神之手 / GodHand（P1-① 神之手内核 · Cordis 委托）'
    HELP = '右键拖拽 转视角   滚轮 缩放   左键涂抹 抬山/挖海   R抬山 F挖海 A自动   SPACE 暂停'
    DURATION = None                 # 不循环：时间累计，便于录制完整一局
    HUD = True
    FOV = 60.0

    def setup(self):
        glEnable(GL_DEPTH_TEST)
        pyglet.gl.glClearColor(SKY[0], SKY[1], SKY[2], 1.0)
        self.program = ShaderProgram(Shader(VERTEX_SRC, 'vertex'),
                                     Shader(FRAGMENT_SRC, 'fragment'))

        # 相机：近俯视等距（世界 48×48，dist 64 装得下）
        self.camera = OrbitCamera(target=Vec3(0, 0, 0), yaw=45.0, pitch=68.0, dist=64.0)

        # 几何（一次性建好，运行时只改 u_model/u_tint；地形逐帧重算顶点）
        cpos, cshade = _unit_cube()
        self.cube = self.program.vertex_list(len(cpos) // 3, GL_TRIANGLES,
                                             position=('f', cpos), shade=('f', cshade))
        # ---------- 挂载 Cordis 内核（阶段 C：玩法委托给内核）----------
        # ⚠️ **必须在 _build_surface/_build_water 之前**：它们要读 self.kernel.ctx.terrain
        #    （原顺序反了 -> demo 一进 setup 就 AttributeError；verify_demos 抓到）
        self.ctx, self.kernel = self._boot_kernel()

        self._build_surface()
        self._build_water()

        # 交互 / 展示状态
        self.power = 'raise'        # 神手当前之力：raise=抬山 / lower=挖海
        self.auto = True            # 自动驾驶：依次挖海淹没每个聚落，使录制自演示
        self._auto_t = 0.0
        self._last_apply = -1.0
        self._mx = self.window.width // 2
        self._my = self.window.height // 2
        self._press = None
        self.window.push_handlers(
            on_mouse_drag=self._on_drag,
            on_mouse_scroll=self._on_scroll,
            on_mouse_press=self._on_press,
            on_mouse_release=self._on_release,
            on_mouse_motion=self._on_motion,
        )

        # 音效（自包含、best-effort；沙箱无驱动则静默）——由内核事件驱动
        self._init_audio()

        # 胜负叠层
        from engine.fonts import resolve_cjk_font
        fk = resolve_cjk_font()
        kw = {'font_name': fk} if fk else {}
        self.overlay = pyglet.text.Label('', font_size=54, color=(255, 230, 120, 255),
                                         anchor_x='center', anchor_y='center',
                                         x=self.window.width // 2, y=self.window.height // 2, **kw)

    # -------------------------------------------------- Cordis 挂载 --
    def _boot_kernel(self):
        """同步引导 cordis 配置树（共同核 + 神之手内核）。"""
        return asyncio.run(self._mount_kernel())

    async def _mount_kernel(self):
        # 试点三落点：本次挂哪些插件、各带什么配置，全由 cordis.godhand.yml 声明；
        # host.tree 按 Cordis 原生 Loader 装载并等待就绪（服务可达 + GL 句柄已取得）。
        ctx = await mount_tree(config_path('cordis.godhand.yml'), want=WANT_GODHAND)
        return ctx, ctx.godhand

    # -------------------------------------------------- 网格 / 水面 --
    def _build_surface(self):
        pos, shade = self.kernel.ctx.terrain.rebuild_mesh()
        self.surface = self.program.vertex_list(len(pos) // 3, GL_TRIANGLES,
                                                position=('f', pos), shade=('f', shade))

    def _update_surface(self):
        """逐帧把可编辑高度场重算进顶点缓冲（x,z 不动，只改 y 与按高度上色）。"""
        pos, shade = self.kernel.ctx.terrain.rebuild_mesh()
        self.surface.position[:] = pos
        self.surface.shade[:] = shade

    def _build_water(self):
        """海平面整片水面（y = SEA 的平铺四边形），覆盖被挖海淹没的区域。"""
        pos = [-HALF_T, SEA, -HALF_T, HALF_T, SEA, -HALF_T, HALF_T, SEA, -HALF_T,
               -HALF_T, SEA, -HALF_T, HALF_T, SEA, -HALF_T, -HALF_T, SEA, HALF_T]
        shade = [0.22, 0.50, 0.78] * 6
        self.water = self.program.vertex_list(6, GL_TRIANGLES,
                                              position=('f', pos), shade=('f', shade))

    # -------------------------------------------------- 音效 --
    def _init_audio(self):
        self.sfx = None
        try:
            d = tempfile.mkdtemp(prefix='gh_sfx_')
            self._sfx_dir = d
            self._make_sfx('raise', 300, 520, 'sine', 0.12)
            self._make_sfx('lower', 520, 220, 'sine', 0.14)
            self._make_sfx('destroy', 180, 60, 'saw', 0.22)
            self._make_sfx('win', 523, 784, 'triangle', 0.5)
            self._make_sfx('lose', 320, 90, 'saw', 0.6)
            self.sfx = SfxPlayer(d, names=('raise', 'lower', 'destroy', 'win', 'lose'), gain=0.5)
        except Exception as e:
            print('[audio] disabled:', e)
            self.sfx = None

    def _make_sfx(self, name, f0, f1, wave, dur):
        tr = Track(dur, rate=32000)
        tr.tone(0.0, dur, f0, f1, wave=wave, amp=0.3, attack=0.005, release=dur * 0.4)
        render_wav(tr, os.path.join(self._sfx_dir, name + '.wav'))

    def _sfx(self, name):
        if self.sfx:
            try:
                self.sfx.play(name)
            except Exception:
                pass

    # -------------------------------------------------- 输入 --
    def _on_drag(self, x, y, dx, dy, buttons, modifiers):
        if buttons & mouse.RIGHT:
            self.camera.orbit(dx, dy)
        elif buttons & mouse.LEFT:
            g = self._screen_to_ground(x, y)
            if g:
                self._apply_at(g[0], g[1])     # 左键涂抹 = 神手之力

    def _on_scroll(self, x, y, scroll_x, scroll_y):
        self.camera.zoom(scroll_y)

    def _on_press(self, x, y, button, modifiers):
        self._press = (x, y, button)
        if button == mouse.LEFT:
            g = self._screen_to_ground(x, y)
            if g:
                self._apply_at(g[0], g[1])

    def _on_release(self, x, y, button, modifiers):
        self._press = None

    def _on_motion(self, x, y, dx, dy):
        self._mx, self._my = x, y

    def _apply_at(self, gx, gz):
        """在地面点 (gx,gz) 施加当前神手之力（抬山 + / 挖海 −）。"""
        if self.time - self._last_apply < 0.05:     # 涂抹节流，避免一次拖动耗尽神力
            return
        self._last_apply = self.time
        if self.power == 'raise':
            self.kernel.raise_at(gx, gz, 1.0)
        else:
            self.kernel.lower_at(gx, gz, 1.0)

    def _screen_to_ground(self, x, y):
        aspect = self.window.width / max(1, self.window.height)
        try:
            inv = self.camera.inverse_view_projection(aspect)
        except Exception:
            inv = self.camera.view_projection(aspect).inverse()
        ray = ray_from_screen(x, y, self.window.width, self.window.height, inv)
        o, d = ray.origin, ray.direction
        if d[1] >= -1e-4:
            return None
        t = -o[1] / d[1]
        if t < 0:
            return None
        return (o[0] + d[0] * t, o[2] + d[2] * t)

    def on_key(self, symbol, modifiers):
        from pyglet.window import key
        if symbol == key.R:
            self.power = 'raise'
        elif symbol == key.F:
            self.power = 'lower'
        elif symbol == key.A:
            self.auto = not self.auto          # 切换自动驾驶

    # -------------------------------------------------- 逐帧（委托内核）--
    def update(self, dt):
        if self.paused:
            return
        # 驱动模拟：内核自管地形结算 / 聚落淹没 / 胜负
        self.kernel.update(dt)
        # 把内核发出的语义事件转成音效
        for name in self.kernel.drain_events():
            self._sfx(name)
        # 自动驾驶：依次把每个聚落挖海淹没，使 demo 无操作也走向胜利（录制自演示）
        if self.auto and self.kernel.ctx.rules.state() not in ('won', 'lost'):
            self._auto_t += dt
            if self._auto_t >= 0.4 and self.kernel.settlements:
                self._auto_t = 0
                eid = self.kernel.settlements[0]
                e = self.kernel.ctx.entity.get(eid)
                if e is not None:
                    self.kernel.lower_at(e['x'], e['z'], 0.4)

    # -------------------------------------------------- 渲染（读内核状态）--
    def render(self):
        cam = self.camera
        self.window.view = cam.view_matrix()
        eye = cam.eye
        eye_t = (eye.x, eye.y, eye.z) if hasattr(eye, 'x') else tuple(eye)
        prog = self.program
        prog.use()
        prog['u_eye'] = eye_t
        prog['u_fog'] = SKY

        k = self.kernel
        terrain = k.ctx.terrain

        # 地形高度场（逐帧重算顶点，体现神手改写）
        self._update_surface()
        prog['u_model'] = _mat_translate(0, 0, 0)
        prog['u_tint'] = (1.0, 1.0, 1.0)
        self.surface.draw(GL_TRIANGLES)

        # 水面（覆盖被挖海淹没的区域）
        prog['u_tint'] = (1.0, 1.0, 1.0)
        self.water.draw(GL_TRIANGLES)

        # 敌方聚落（实体来自内核实体注册表，立于当前地形高度，附 HP 条）
        for eid in k.settlements:
            e = k.ctx.entity.get(eid)
            if e is None:
                continue
            x, z = e['x'], e['z']
            y = terrain.height_at(x, z)
            frac = max(0.0, e['hp'] / e['max_hp'])
            col = (0.35 + 0.65 * frac, 0.15 + 0.55 * frac, 0.20)   # 绿→红
            prog['u_model'] = _mat_translate(x, y + 1.0, z) @ _mat_scale(2.4, 2.0, 2.4)
            prog['u_tint'] = col
            self.cube.draw(GL_TRIANGLES)
            # HP 条
            prog['u_model'] = _mat_translate(x, y + 2.7, z) @ _mat_scale(2.2 * frac + 0.01, 0.22, 0.22)
            prog['u_tint'] = (0.2 + 0.8 * frac, 0.9 * frac, 0.15)
            self.cube.draw(GL_TRIANGLES)

        # 神手光标（地面命中点，按当前神手之力着色）
        g = self._screen_to_ground(self._mx, self._my)
        if g is not None:
            gx, gz = g
            gy = terrain.height_at(gx, gz)
            col = (1.0, 0.85, 0.2) if self.power == 'raise' else (0.30, 0.70, 1.0)
            prog['u_model'] = _mat_translate(gx, gy + 0.06, gz) @ _mat_scale(2.2, 0.18, 2.2)
            prog['u_tint'] = col
            self.cube.draw(GL_TRIANGLES)

        # 胜负 / 暂停叠层
        state = k.ctx.rules.state()
        if state in ('won', 'lost') or self.paused:
            w, h = self.window.width, self.window.height
            glDisable(GL_DEPTH_TEST)
            self.window.view = Mat4()
            self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -1, 1)
            txt = {'won': '神迹降临 · 胜利', 'lost': '神力枯竭 · 失败'}.get(state, '')
            if self.paused and not txt:
                txt = '暂 停'
            self.overlay.text = txt
            self.overlay.x, self.overlay.y = w // 2, h // 2
            self.overlay.draw()
            self.window.projection = self.perspective
            glEnable(GL_DEPTH_TEST)

    # -------------------------------------------------- HUD --
    def status_text(self):
        k = self.kernel
        r = k.ctx.rules
        st = {'prep': '宁静', 'won': '胜利', 'lost': '失败'}.get(r.state(), r.state())
        mana = k.ctx.economy.balance('mana')
        powname = '抬山' if self.power == 'raise' else '挖海'
        auto = 'AUTO' if self.auto else 'MANUAL'
        return (f'神力{mana:.0f}  聚落{len(k.settlements)} {st}  神手:{powname} {auto}  '
                f'| R抬山 F挖海 A自动  SPACE暂停  t={k.time:.0f}s {self.fps:.0f}fps'
                + ('  [PAUSED]' if self.paused else ''))


if __name__ == '__main__':
    run(GodHandDemo)
