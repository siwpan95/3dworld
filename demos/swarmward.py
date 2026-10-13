# -*- coding: utf-8 -*-
"""弹潮 / Swarmward —— P1-⑤ 弹幕固定屏射击（Cordis 委托 · 粒子/编队双核首秀）。

机制致敬 `Robotron 2084`(1982) 的「固定屏全向生存 + 双摇杆」——**原创命名 / 美术 /
敌型**，不碰任何原 IP 名称与素材（合规见 `docs/remake_ideas.md` §7）。
设计 note 见 `docs/remake_ideas.md` §12；先问结论：全向生存 / 程序化编波 / 双摇杆。

分层（与既有内核 demo 一致「逻辑在 Cordis、表现由宿主」）：

- setup()   经 asyncio 引导 `cordis.storm.yml` 配置树（`host.tree.mount_tree`），
            内核取得 GL 句柄记账；
- update()  双摇杆输入（或 `--flag auto` 闪避 AI）交给内核，事件转音效；
- render()  只读内核状态：正交固定屏（竞技场完整可见），实体小方块 + 弹点 +
            **particles.snapshot() 粒子层**（粒子核首个内核消费者的渲染端）。

**Aimlabs 手感件**：受击白闪（hitmarker 的 3D 等价物）+ 连击数字 + 量化结算
（波数/击杀/命中率/最长连击/得分）。

操作：WASD 移动   方向键 射击（或鼠标左键指向射击）   R 重开（新种子）
      SPACE 暂停   ESC 退出
"""
import asyncio
import math
import os
import sys
import tempfile

import pyglet
from pyglet.gl import GL_DEPTH_TEST, GL_TRIANGLES, glDisable, glEnable
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4, Vec3, Vec4
from pyglet.window import key, mouse

from _harness import DemoApp, run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from host.tree import WANT_STORM, config_path, mount_tree  # noqa: E402
from engine.camera import look_at_matrix  # noqa: E402
from games.storm.kernel import (  # noqa: E402
    ARENA_H, ARENA_W, BOSS_R, PLAYER_R,
)

VERTEX_SRC = '''#version 330 core
in vec3 position;
in vec3 shade;
uniform WindowBlock {
    mat4 projection;
    mat4 view;
} window;
uniform mat4 u_model;
uniform vec3 u_tint;
out vec3 v_col;
void main() {
    vec4 world = u_model * vec4(position, 1.0);
    v_col = shade * u_tint;
    gl_Position = window.projection * window.view * world;
}'''

FRAGMENT_SRC = '''#version 330 core
in vec3 v_col;
out vec4 final_colors;
void main() {
    final_colors = vec4(v_col, 1.0);
}'''


def _mat_translate(tx, ty, tz):
    return Mat4(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, tx, ty, tz, 1)


def _mat_scale(sx, sy, sz):
    return Mat4(sx, 0, 0, 0, 0, sy, 0, 0, 0, 0, sz, 0, 0, 0, 0, 1)


def _flat_quad(hx, hy, y=0.0):
    """XZ 平面上的方形（半宽 hx / 半深 hy）。"""
    a, b, c, d = (-hx, y, hy), (hx, y, hy), (hx, y, -hy), (-hx, y, -hy)
    pos, shade = [], []
    for p in (a, b, c, a, c, d):
        pos.extend(p)
        shade.extend((1.0, 1.0, 1.0))
    return pos, shade


def _unit_cube():
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


TINT_FLOOR = (0.10, 0.105, 0.125)
TINT_BORDER = (0.05, 0.05, 0.06)
TINT_PLAYER = (0.40, 0.62, 0.95)
TINT_CHASER = (0.90, 0.30, 0.24)
TINT_WEAVER = (0.72, 0.40, 0.92)
TINT_DRONE = (0.92, 0.78, 0.30)
TINT_BOSS = (0.98, 0.34, 0.44)
TINT_BULLET = (0.95, 0.97, 1.00)
TINT_EBULLET = (1.00, 0.45, 0.25)
TINT_PARTICLE = (1.00, 0.82, 0.45)
TINT_FLASH = (1.00, 1.00, 1.00)

PARTICLE_SLOTS = 512            # 粒子渲染预分配槽位（snapshot 超出部分截断）


class SwarmwardDemo(DemoApp):
    TITLE = '弹潮 / Swarmward —— 弹幕固定屏射击（全向生存 · 双摇杆 · 程序化编波）'
    HELP = ('WASD 移动   方向键/鼠标左键 射击   R 重开   SPACE 暂停   ESC 退出')
    DURATION = None
    HUD = True
    SHOTS = ((8.0, 'docs/swarmward_a.png'),      # 波次进行中：编队蜂群 + 弹幕
             (22.0, 'docs/swarmward_b.png'))     # 后期高波：密集场面

    def setup(self):
        glEnable(GL_DEPTH_TEST)
        self.program = ShaderProgram(Shader(VERTEX_SRC, 'vertex'),
                                     Shader(FRAGMENT_SRC, 'fragment'))
        cp, cs = _unit_cube()
        self.cube = self.program.vertex_list(len(cp) // 3, GL_TRIANGLES,
                                             position=('f', cp), shade=('f', cs))

        # ---- Cordis 挂载（配置树驱动）----
        self.ctx, self.kernel = self._boot_kernel()
        # `--flag auto`：闪避 AI 驾驶（与 headless 验收同一套逻辑）
        # ⚠️ 原版漏了这一行 —— demo 一进 update() 就 AttributeError（headless
        #    验收走 config_boot 不经过 demo，所以这个缺陷一直没暴露）。
        self.auto = 'auto' in self.args.flags
        for f in self.args.flags:
            if f.startswith('seed') and f[4:].isdigit():
                self.kernel.restart(int(f[4:]))
            elif f == 'endless':
                self.kernel.endless = True

        # ---- 静态几何：边框 + 竞技场底板 ----
        bp, bs = _flat_quad(ARENA_W / 2 + 0.6, ARENA_H / 2 + 0.6, y=-0.02)
        self.border = self.program.vertex_list(len(bp) // 3, GL_TRIANGLES,
                                               position=('f', bp), shade=('f', bs))
        fp, fs = _flat_quad(ARENA_W / 2, ARENA_H / 2, y=0.0)
        self.floor = self.program.vertex_list(len(fp) // 3, GL_TRIANGLES,
                                              position=('f', fp), shade=('f', fs))

        # ---- 粒子渲染预分配（snapshot → 固定容量 vlist，逐帧整块写入）----
        pp, ps = [], []
        for _ in range(PARTICLE_SLOTS):
            a, b, c, d = (-0.09, 0.0, 0.09), (0.09, 0.0, 0.09), (0.09, 0.0, -0.09), \
                         (-0.09, 0.0, -0.09)
            for p in (a, b, c, a, c, d):
                pp.extend((p[0], 0.0, p[2]))
                ps.extend((1.0, 1.0, 1.0))
        self.particles_vl = self.program.vertex_list(
            PARTICLE_SLOTS * 6, GL_TRIANGLES, position=('f', pp), shade=('f', ps))

        # ---- 输入 ----
        self.keys = set()
        self._firing = False
        self._mouse_aim = None
        self.window.push_handlers(
            on_mouse_press=self._on_press,
            on_mouse_release=self._on_release,
            on_mouse_motion=self._on_motion,
            on_key_press=self._on_key_press,
            on_key_release=self._on_key_release,
            on_deactivate=self._on_deactivate,
        )

        # ---- 音效（自包含、best-effort）----
        self._init_audio()

        # ---- 结算叠层 ----
        from engine.fonts import resolve_cjk_font
        fk = resolve_cjk_font()
        kw = {'font_name': fk} if fk else {}
        self.overlay = pyglet.text.Label('', font_size=48, color=(255, 230, 120, 255),
                                         anchor_x='center', anchor_y='center',
                                         x=self.window.width // 2,
                                         y=self.window.height // 2, **kw)
        self.sub_overlay = pyglet.text.Label('', font_size=20, color=(215, 220, 235, 255),
                                             anchor_x='center', anchor_y='center',
                                             x=self.window.width // 2,
                                             y=self.window.height // 2, **kw)

    def _boot_kernel(self):
        return asyncio.run(self._mount_kernel())

    async def _mount_kernel(self):
        ctx = await mount_tree(config_path('cordis.storm.yml'), want=WANT_STORM)
        return ctx, ctx.storm

    # -------------------------------------------------- 输入 --
    def _on_press(self, x, y, button, modifiers):
        if button == mouse.LEFT:
            self._firing = True
            self._mouse_aim = self._screen_to_arena(x, y)

    def _on_release(self, x, y, button, modifiers):
        if button == mouse.LEFT:
            self._firing = False

    def _on_motion(self, x, y, dx, dy):
        if self._firing:
            self._mouse_aim = self._screen_to_arena(x, y)

    def _on_key_press(self, symbol, modifiers):
        self.keys.add(symbol)

    def _on_key_release(self, symbol, modifiers):
        self.keys.discard(symbol)

    def _on_deactivate(self):
        # 失焦清键（ALT-TAB 回来不再"自己走/自己射"）
        self.keys.clear()
        self._firing = False

    def _screen_to_arena(self, x, y):
        """屏幕像素 → 竞技场平面点（鼠标指向射击用）。"""
        aspect = self.window.width / max(1, self.window.height)
        inv = self.window.projection @ self.window.view
        try:
            inv = inv.inverse()
        except Exception:
            pass
        nx = 2.0 * x / max(1.0, float(self.window.width)) - 1.0
        ny = 2.0 * y / max(1.0, float(self.window.height)) - 1.0
        pts = []
        for z in (-1.0, 1.0):
            v = inv @ Vec4(nx, ny, z, 1.0)
            pts.append((v.x / v.w, v.y / v.w, v.z / v.w))
        (x0, y0, _), (x1, y1, _) = pts
        dx, dy, dz = x1 - x0, y1 - y0, 1.0
        t = (0.0 - y0) / dy if abs(dy) > 1e-9 else 0.0
        return (x0 + dx * t, y0 + dy * t)          # (x, z) @ y=0

    def on_key(self, symbol, modifiers):
        if symbol == key.R:
            seed = (self.kernel.seed * 1103515245 + 12345) % (2 ** 31)
            self.kernel.restart(seed)
            print(f'[swarmward] new seed={self.kernel.seed}', flush=True)

    # -------------------------------------------------- 每帧 --
    def _input_move(self, dt):
        k = self.keys
        dx = (1.0 if key.D in k else 0.0) - (1.0 if key.A in k else 0.0)
        dy = (1.0 if key.W in k else 0.0) - (1.0 if key.S in k else 0.0)
        if dx or dy:
            self.kernel.move(dx, dy, dt)

    def _input_shoot(self):
        k = self.keys
        sx = ((1.0 if key.RIGHT in k else 0.0)
              - (1.0 if key.LEFT in k else 0.0))
        sy = ((1.0 if key.UP in k else 0.0)
              - (1.0 if key.DOWN in k else 0.0))
        if sx or sy:
            self.kernel.shoot(sx, sy)
        elif self._firing and self._mouse_aim is not None:
            p = self.kernel.player
            self.kernel.shoot(self._mouse_aim[0] - p['x'],
                              self._mouse_aim[1] - p['z'])

    def update(self, dt):
        if self.paused:
            return
        if self.auto:
            # ⚠️ **固定 1/60 子步进**：headless 验收（config_boot）用固定 dt=1/60，
            #    而 demo 的真实帧时间随帧率浮动（且可能远大于 1/60）。auto-pilot 的
            #    预测/闪避对 dt 敏感 —— 直接用真实 dt 会让 demo 里的 AI 与验收表现
            #    不一致（实测：demo 里 22s 就死，headless 却 70s 通关）。
            left = min(dt, 0.25)
            while left > 1e-9:
                step = min(1.0 / 60.0, left)
                self.kernel.auto_step(step)
                self.kernel.update(step)
                left -= step
        else:
            self._input_move(dt)
            self._input_shoot()
            self.kernel.update(dt)
        for ev in self.kernel.drain_events():
            self._sfx(ev)

    # -------------------------------------------------- 渲染 --
    def render(self):
        w, h = self.window.width, self.window.height
        aspect = w / max(1, h)
        # 正交固定屏：竞技场完整可见（letterbox）
        scale = max(ARENA_W / (2.0 * aspect), ARENA_H) / 2.0
        hw, hh = scale * aspect, scale
        # ⚠️ 俯视（原本用 `Mat4()` 单位 view + near=0.1 —— 那是"相机在原点朝 -z 看"，
        #    地面（y=0 水平面）在正视图里退化成一条线、z>0 的实体被 near 裁掉：
        #    demo 从没正常出过图，因为 §12 只跑了 headless 验收）。俯视才符合
        #    ARENA_W×ARENA_H 固定屏的设计意图。
        self.window.view = look_at_matrix((0.0, 22.0, 0.0), (0.0, 0.0, 0.0),
                                          (0.0, 0.0, -1.0))
        self.window.projection = Mat4.orthogonal_projection(
            -hw, hw, -hh, hh, 1.0, 60.0)

        prog = self.program
        prog.use()
        k = self.kernel
        prog['u_model'] = _mat_translate(0, 0, 0)
        prog['u_tint'] = TINT_BORDER
        self.border.draw(GL_TRIANGLES)
        prog['u_tint'] = TINT_FLOOR
        self.floor.draw(GL_TRIANGLES)

        # 敌（受击白闪 = Aimlabs hitmarker 的 3D 等价物）
        for e in k.enemies:
            tint = {'chaser': TINT_CHASER, 'weaver': TINT_WEAVER,
                    'drone': TINT_DRONE, 'boss': TINT_BOSS}[e['kind']]
            f = e.get('flash', 0.0)
            if f > 0.0:
                tint = tuple(t + (TINT_FLASH[i] - t) * f for i, t in enumerate(tint))
            prog['u_tint'] = tint
            if e['kind'] == 'boss':
                # boss 明显更大（半球压在场地上的观感）
                prog['u_model'] = _mat_translate(e['x'], 0.75, e['z']) \
                    @ _mat_scale(BOSS_R * 1.55, BOSS_R * 1.15, BOSS_R * 1.55)
            else:
                prog['u_model'] = _mat_translate(e['x'], 0.35, e['z']) \
                    @ _mat_scale(0.62 if e['kind'] == 'drone' else 0.78, 0.7,
                                 0.62 if e['kind'] == 'drone' else 0.78)
            self.cube.draw(GL_TRIANGLES)

        # 玩家（无敌帧闪烁）
        p = k.player
        blink = 0.45 + 0.55 * abs(math.sin(p['inv'] * 20.0)) if p['inv'] > 0 else 1.0
        prog['u_tint'] = tuple(t * blink for t in TINT_PLAYER)
        prog['u_model'] = _mat_translate(p['x'], 0.32, p['z']) \
            @ _mat_scale(0.66, 0.64, 0.66)
        self.cube.draw(GL_TRIANGLES)

        # 玩家弹 / 敌弹（小点）
        prog['u_tint'] = TINT_BULLET
        for b in k.bullets:
            prog['u_model'] = _mat_translate(b['x'], 0.32, b['z']) \
                @ _mat_scale(0.2, 0.2, 0.2)
            self.cube.draw(GL_TRIANGLES)
        prog['u_tint'] = TINT_EBULLET
        for b in k.ebullets:
            prog['u_model'] = _mat_translate(b['x'], 0.32, b['z']) \
                @ _mat_scale(0.26, 0.26, 0.26)
            self.cube.draw(GL_TRIANGLES)

        # 粒子层（snapshot → 预分配 vlist 整块写入；淡出 = shade 衰减）
        snap = k.ctx.particles.snapshot()[:PARTICLE_SLOTS]
        pos_arr = [0.0] * (PARTICLE_SLOTS * 18)
        shade_arr = [0.0] * (PARTICLE_SLOTS * 18)
        for i, (px, _py, pz, lf) in enumerate(snap):
            s = 0.12 * (0.5 + 0.5 * lf)
            base = i * 18
            for vi, (ox, oz) in enumerate(((-s, s), (s, s), (s, -s),
                                           (-s, s), (s, -s), (-s, -s))):
                pos_arr[base + vi * 3] = px + ox
                pos_arr[base + vi * 3 + 1] = 0.06
                pos_arr[base + vi * 3 + 2] = pz + oz
                sh = 0.35 + 0.65 * lf
                shade_arr[base + vi * 3] = sh
                shade_arr[base + vi * 3 + 1] = sh
                shade_arr[base + vi * 3 + 2] = sh
        self.particles_vl.position[:] = pos_arr
        self.particles_vl.shade[:] = shade_arr
        prog['u_tint'] = TINT_PARTICLE
        prog['u_model'] = _mat_translate(0, 0, 0)
        self.particles_vl.draw(GL_TRIANGLES)

        self._draw_boss_hp(w, h)
        self._draw_overlay(w, h)

    def _draw_boss_hp(self, w, h):
        """顶部 boss 血条（2D 叠加；仅 boss 存在时画）。颜色随剩余血量分段。"""
        k = self.kernel
        if float(k.boss_max) <= 0.0:
            return
        b = next((e for e in k.enemies if e['kind'] == 'boss'), None)
        if b is None:
            return
        frac = max(0.0, min(1.0, float(b['hp']) / max(1.0, float(b['hp_max']))))
        glDisable(GL_DEPTH_TEST)
        keep = self.window.projection
        self.window.view = Mat4()
        self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -1, 1)
        prog = self.program
        prog.use()
        bar_w, bar_h = w * 0.52, 16.0
        cx, cy = w / 2.0, h - 52.0
        prog['u_tint'] = (0.10, 0.09, 0.11)
        prog['u_model'] = _mat_translate(cx, cy, 0.0) @ _mat_scale(bar_w, bar_h, 1.0)
        self.cube.draw(GL_TRIANGLES)
        if frac > 0.5:
            tint = (0.95, 0.30, 0.34)
        elif frac > 0.25:
            tint = (0.98, 0.58, 0.20)
        else:
            tint = (0.99, 0.20, 0.24)
        fw = bar_w * frac
        prog['u_tint'] = tint
        prog['u_model'] = _mat_translate(cx - (bar_w - fw) * 0.5, cy, 0.01) \
            @ _mat_scale(max(0.5, fw), bar_h * 0.66, 1.0)
        self.cube.draw(GL_TRIANGLES)
        glEnable(GL_DEPTH_TEST)
        self.window.projection = keep

    def _draw_overlay(self, w, h):
        rules = self.kernel.ctx.rules
        st = rules.state()
        txt = {'won': '守 潮 成 功', 'lost': '没 入 弹 潮'}.get(st, '')
        if self.paused and not txt:
            txt = '暂 停'
        if not txt:
            return
        glDisable(GL_DEPTH_TEST)
        self.window.view = Mat4()
        self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -1, 1)
        self.overlay.text = txt
        self.overlay.x, self.overlay.y = w // 2, h // 2
        self.overlay.draw()
        if st in ('won', 'lost'):
            # 量化结算（Aimlabs Results 延续）
            s = self.kernel.stats
            acc = 100.0 * s['hits'] / s['shots'] if s['shots'] else 0.0
            self.sub_overlay.text = (
                f'波数 {max(self.kernel.wave + 1, 0)}/10   击杀 {s["kills"]}   '
                f'命中率 {acc:.0f}%   最长连击 {s["max_combo"]}   '
                f'得分 {self.kernel.ctx.economy.balance("score"):.0f}')
            self.sub_overlay.x, self.sub_overlay.y = w // 2, h // 2 - 60
            self.sub_overlay.draw()
        glEnable(GL_DEPTH_TEST)
        self.window.projection = self.perspective

    # -------------------------------------------------- 音效 --
    def _init_audio(self):
        self.sfx = None
        try:
            d = tempfile.mkdtemp(prefix='swarmward_sfx_')
            self._sfx_dir = d
            self._make_sfx('shoot', 820, 640, 'saw', 0.05)
            self._make_sfx('hit', 340, 300, 'square', 0.05)
            self._make_sfx('kill', 220, 90, 'saw', 0.14)
            self._make_sfx('hurt', 200, 80, 'triangle', 0.28)
            self._make_sfx('wave', 440, 660, 'sine', 0.16)
            self._make_sfx('end', 320, 90, 'saw', 0.55)
            from engine.audio import SfxPlayer
            self.sfx = SfxPlayer(d, names=('shoot', 'hit', 'kill', 'hurt',
                                           'wave', 'end'), gain=0.45)
        except Exception as e:
            print('[audio] disabled:', e)
            self.sfx = None

    def _make_sfx(self, name, f0, f1, wave, dur):
        from engine.audio import Track, render_wav
        tr = Track(dur, rate=32000)
        tr.tone(0.0, dur, f0, f1, wave=wave, amp=0.3,
                attack=0.005, release=dur * 0.4)
        render_wav(tr, os.path.join(self._sfx_dir, name + '.wav'))

    def _sfx(self, name):
        if self.sfx:
            try:
                self.sfx.play(name)
            except Exception:
                pass

    # -------------------------------------------------- HUD --
    def status_text(self):
        k = self.kernel
        s = k.stats
        acc = 100.0 * s['hits'] / s['shots'] if s['shots'] else 0.0
        return (f'波 {max(k.wave + 1, 0)}/10   命 {k.ctx.rules.base_hp}/3   '
                f'分 {k.ctx.economy.balance("score"):.0f}   '
                f'连击 {k.combo}   敌 {len(k.enemies)}   '
                f'命中 {acc:.0f}%   种子 {k.seed}   '
                f't={self.time:5.1f}s {self.fps:4.1f}fps'
                f'{"  [AUTO]" if self.auto else ""}'
                f'{"  [ENDLESS]" if k.endless else ""}'
                f'{"  [PAUSED]" if self.paused else ""}')


if __name__ == '__main__':
    run(SwarmwardDemo)
