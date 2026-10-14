# -*- coding: utf-8 -*-
"""归仓 / Boxward —— P1-④ 方块解谜（Cordis 委托 · 光栅贪心合批渲染）。

机制致敬 `Sokoban`(1982) 的「推箱入仓」——**原创命名 / 美术 / 关卡**，
不碰任何原 IP 名称与素材（合规见 `docs/remake_ideas.md` §7）。
设计 note 见 `docs/remake_ideas.md` §11；先问结论：带关卡编辑器 / 光栅贪心合批 /
最小切片 + 程序化关卡。

分层（与既有内核 demo 一致「逻辑在 Cordis、表现由宿主」）：

- setup()   经 asyncio 引导 `cordis.puzzle.yml` 配置树（`world3d.host.tree.mount_tree`），
            内核加载手工关、取得 GL 句柄记账；
- update()  键盘走子 / `--flag auto` 的求解器回放交给内核，事件转音效；
- render()  只读内核 / 网格状态：**静态墙 = 贪心合并网格**（`greedy_mesh`，一次建网），
            箱 / 玩家 / 目标 = 动态小 cube 逐个 batch（u_model 平移缩放），
            **正交等距视角**（偏航 = 45° + 90°·档，Q/E 切档；俯角 P 键循环，默认 45°），
            WASD 方向随相机映射（映射按 90° 档取整，故屏幕上呈四个斜向）。

**单一事实来源 = 关卡 JSON**：编辑器（M 键）导出的 = 生成器产出的 = 内核吃的。
**auto-pilot = 求解器回放**：`--flag auto` 与 `config_boot puzzle` 的 headless 验收同源。

操作：WASD/方向键 走/推   Q/E 旋转视角   Z 撤销   N 下一关   R 换新种子关
      M 编辑模式（左键循环摆格：墙→箱→目标→玩家）  C 清内部墙  X 试解  E 导出 JSON
      SPACE 暂停   ESC 退出
"""
import asyncio
import math
import os
import sys
import tempfile
import time

import pyglet
from pyglet.gl import GL_DEPTH_TEST, GL_TRIANGLES, glDisable, glEnable
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4, Vec3
from pyglet.window import key, mouse

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError: No module named 'world3d'（P2-1 全量验收实测）。

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.platform.app import DemoApp, run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 试点三：挂载由 cordis.puzzle.yml 声明（与 config_boot 的 headless 验证同一入口）
from world3d.host.tree import WANT_PUZZLE, config_path, mount_tree  # noqa: E402
from world3d.games.puzzle.kernel import (  # noqa: E402
    _goal_plate, cam_move_dirs, level_from_json, level_to_json,
)
from world3d.logic.camera import OrbitCamera  # noqa: E402
from world3d.engine.picking import ray_from_screen  # noqa: E402

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


def _floor_quad(half=20.0, y=0.0):
    a, b, c, d = (-half, y, half), (half, y, half), (half, y, -half), (-half, y, -half)
    pos, shade = [], []
    for p in (a, b, c, a, c, d):
        pos.extend(p)
        shade.extend((1.0, 1.0, 1.0))
    return pos, shade


def _cube_geom():
    """单位立方体（与内核 _unit_cube 同款：顶亮/侧中/底暗的烘焙 shade）。"""
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


TINT_WALL = (0.52, 0.50, 0.47)
TINT_FLOOR = (0.14, 0.13, 0.125)
TINT_BOX = (0.82, 0.60, 0.18)
TINT_BOX_ON_GOAL = (0.35, 0.85, 0.45)
TINT_PLAYER = (0.38, 0.58, 0.92)
TINT_GOAL = (0.95, 0.72, 0.22)
TINT_CURSOR = (0.30, 0.90, 0.95)


class BoxwardDemo(DemoApp):
    TITLE = '归仓 / Boxward —— 方块解谜（推箱入仓 · 贪心合批 · 程序化关卡）'
    HELP = ('WASD 走/推   Q/E 旋转视角   P 俯角   Z 撤销   N 下一关   '
            'R 新种子关   M 编辑   SPACE 暂停   ESC 退出')
    DURATION = None          # 交互型：time 不取模，持续累加
    HUD = True
    SHOTS = ((6.0, 'docs/boxward_a.png'),       # 手工关中段：推箱进行时
             (18.0, 'docs/boxward_b.png'))      # auto 连续闯关中的生成关

    ZOOM = 8.2               # 正交半高（世界单位）

    # 等距视角：偏航固定比"轴对齐"多 45°，这样每个方块同时露出**两个侧面 + 顶面**，
    # 立体感才出来（轴对齐时一个方块只能看到一个面，整幅画面读起来像 2D 平面）。
    # ⚠️ 输入映射**不跟着转**：`cam_move_dirs(rot)` 仍按 `90°·rot` 取整 —— 于是四个
    # 方向键在屏幕上呈现为四个"斜向"（经典等距游戏的操作感）。「相机偏航 +45°」与
    # 「输入按 90° 分档」是两件独立的事，别把它们合并成一件。
    YAW_OFFSET = 45.0
    PITCH_PRESETS = (30.0, 38.0, 45.0, 52.0)   # P 键循环（俯角越大越俯视）

    def setup(self):
        glEnable(GL_DEPTH_TEST)
        self.program = ShaderProgram(Shader(VERTEX_SRC, 'vertex'),
                                     Shader(FRAGMENT_SRC, 'fragment'))
        self._cube_pos, self._cube_shade = _cube_geom()

        # ---- Cordis 挂载（配置树驱动）----
        self.ctx, self.kernel = self._boot_kernel()
        self.grid = self.kernel.ctx.grid
        self.cam = OrbitCamera(target=Vec3(0, 0.4, 0), yaw=self.YAW_OFFSET, pitch=45.0,
                               dist=17.0, pitch_range=(30.0, 60.0),
                               dist_range=(10.0, 30.0))
        self._rot = 0

        # ---- flags ----
        self.auto = 'auto' in self.args.flags
        self._auto_wait = 0.0
        self._editing = False
        self._cursor = None
        self._edit_msg = ''
        for f in self.args.flags:
            if f.startswith('seed') and f[4:].isdigit():
                self.kernel.load_generated(int(f[4:]))
            elif f.startswith('level='):
                data = level_from_json(self._resolve(f[6:]))
                self.kernel.load_level(data)

        # ---- 静态网格（floor 固定；walls 随关重建；cube/goal 为共享动态网格）----
        fp, fs = _floor_quad()
        self.floor = self.program.vertex_list(len(fp) // 3, GL_TRIANGLES,
                                              position=('f', fp), shade=('f', fs))
        cp, cs = self._cube_pos, self._cube_shade
        self.cube_list = self.program.vertex_list(len(cp) // 3, GL_TRIANGLES,
                                                  position=('f', cp), shade=('f', cs))
        gp, gs = _goal_plate()
        self.goal_list = self.program.vertex_list(len(gp) // 3, GL_TRIANGLES,
                                                  position=('f', gp), shade=('f', gs))
        self._walls = None
        self._rebuild_static()

        # ---- 鼠标（编辑器摆格 / 非编辑时点击相邻格走子）----
        self.window.push_handlers(
            on_mouse_press=self._on_press,
            on_mouse_motion=self.on_mouse_motion,
        )

        # ---- 音效（自包含、best-effort）----
        self._init_audio()

        # ---- 胜负叠层 ----
        from world3d.engine.fonts import resolve_cjk_font
        fk = resolve_cjk_font()
        kw = {'font_name': fk} if fk else {}
        self.overlay = pyglet.text.Label('', font_size=44, color=(255, 230, 120, 255),
                                         anchor_x='center', anchor_y='center',
                                         x=self.window.width // 2,
                                         y=self.window.height // 2, **kw)

    def _resolve(self, p):
        return p if os.path.isabs(p) else os.path.join(ROOT, p)

    # -------------------------------------------------- Cordis 挂载 --
    def _boot_kernel(self):
        return asyncio.run(self._mount_kernel())

    async def _mount_kernel(self):
        ctx = await mount_tree(config_path('cordis.puzzle.yml'), want=WANT_PUZZLE)
        return ctx, ctx.puzzle

    # -------------------------------------------------- 静态网格 --
    def _rebuild_static(self):
        """换关后重建墙网格（贪心合并——「一次建网」的地基）。"""
        pos, shade = self.grid.world_mesh()
        if self._walls is not None:
            self._walls.delete()
        self._walls = self.program.vertex_list(
            len(pos) // 3, GL_TRIANGLES,
            position=('f', tuple(pos)), shade=('f', tuple(shade)))

    # -------------------------------------------------- 输入 --
    def _on_press(self, x, y, button, modifiers):
        if button != mouse.LEFT:
            return
        if self._editing:
            cell = self._pick_cell(x, y)
            if cell is not None:
                what = self.kernel.editor_cycle(*cell)
                self._edit_msg = f'{cell}: {what}'
            return
        # 非编辑模式：点相邻格 = 朝那个方向走一步（可选的鼠标走子）
        cell = self._pick_cell(x, y)
        if cell is None or self.grid.player is None:
            return
        di, dj = cell[0] - self.grid.player[0], cell[1] - self.grid.player[1]
        if abs(di) + abs(dj) == 1:
            self.kernel.step((di, dj))

    def _pick_cell(self, x, y):
        """屏幕 → 世界射线 → 与 y=0.5 平面求交 → 格坐标（编辑器拾取）。"""
        aspect = self.window.width / max(1, self.window.height)
        try:
            inv = self.cam.inverse_view_projection(aspect)
        except Exception:
            inv = self.cam.view_projection(aspect).inverse()
        ray = ray_from_screen(x, y, self.window.width, self.window.height, inv)
        o, d = ray.origin, ray.direction
        if abs(d[1]) < 1e-6:
            return None
        t = (0.5 - o[1]) / d[1]
        if t < 0:
            return None
        wx, wz = o[0] + d[0] * t, o[2] + d[2] * t
        i, j = self.grid.cell_of(wx, wz)
        return (i, j) if self.grid.in_bounds(i, j) else None

    def on_mouse_motion(self, x, y, dx, dy):
        if self._editing:
            self._cursor = self._pick_cell(x, y)

    def on_key(self, symbol, modifiers):
        dirs = cam_move_dirs(self._rot)
        if self._editing:
            if symbol == key.M:
                self._exit_editor()
            elif symbol == key.X:
                sol = self.kernel.editor_validate()
                self._edit_msg = ('可解：{} 步 / {} 推'.format(len(sol.moves), sol.pushes)
                                  if sol is not None else '不可解（或格数不齐/缺玩家）')
            elif symbol == key.E:
                self._export_level()
            elif symbol == key.C:
                self._clear_interior_walls()
            return
        if symbol in (key.W, key.UP):
            self.kernel.step(dirs['W'])
        elif symbol in (key.S, key.DOWN):
            self.kernel.step(dirs['S'])
        elif symbol in (key.A, key.LEFT):
            self.kernel.step(dirs['A'])
        elif symbol in (key.D, key.RIGHT):
            self.kernel.step(dirs['D'])
        elif symbol == key.Q:
            self._rot = (self._rot - 1) % 4
            self.cam.yaw = self.YAW_OFFSET + 90.0 * self._rot
        elif symbol == key.E:
            self._rot = (self._rot + 1) % 4
            self.cam.yaw = self.YAW_OFFSET + 90.0 * self._rot
        elif symbol == key.P:
            # 俯角循环：30°（最平、侧面最露）→ 52°（最俯视）。默认 45° 是"等距感"最均衡的一档。
            i = self.PITCH_PRESETS.index(self.cam.pitch) if self.cam.pitch in self.PITCH_PRESETS else -1
            self.cam.pitch = self.PITCH_PRESETS[(i + 1) % len(self.PITCH_PRESETS)]
            self._edit_msg = f'俯角 {self.cam.pitch:.0f}°'
        elif symbol == key.Z:
            self.kernel.undo()
        elif symbol == key.N:
            self.kernel.next_level()
            self._rebuild_static()
        elif symbol == key.R:
            seed = (self.kernel.seed * 1103515245 + 12345) % (2 ** 31)
            self.kernel.restart(seed)
            self._rebuild_static()
            print(f'[boxward] new seed={self.kernel.seed}', flush=True)
        elif symbol == key.M:
            self._editing = True
            self._edit_msg = '编辑模式：左键摆格 / X 试解 / E 导出 / M 退出'

    # -------------------------------------------------- 编辑器 --
    def _clear_interior_walls(self):
        g = self.grid
        for i in range(1, g.h - 1):
            for j in range(1, g.w - 1):
                g.walls.discard((i, j))
        self.kernel._rebuild_entities()
        self._edit_msg = '已清空内部墙'

    def _exit_editor(self):
        self._editing = False
        self._cursor = None
        self.kernel.refresh_par()
        self._edit_msg = ''

    def _export_level(self):
        sol = self.kernel.editor_validate()
        if sol is None:
            self._edit_msg = '导出失败：缺玩家 / 箱目标数不齐 / 不可解'
            return
        name = f'editor-{time.strftime("%H%M%S")}'
        data = self.grid.to_dict(name=name)
        data['meta']['par'] = sol.pushes
        out_dir = os.path.join(ROOT, 'levels')
        os.makedirs(out_dir, exist_ok=True)
        path = level_to_json(data, os.path.join(out_dir, name + '.json'))
        self._edit_msg = (f'已导出 {os.path.relpath(path, ROOT)} '
                          f'（可解：{len(sol.moves)} 步 / {sol.pushes} 推）')
        print(f'[boxward] level exported: {path}', flush=True)

    # -------------------------------------------------- 每帧 --
    def update(self, dt):
        if self.paused:
            return
        if self.auto:
            if self.kernel.is_solved():
                self._auto_wait -= dt
                if self._auto_wait <= 0.0:
                    self._auto_wait = 1.0
                    self.kernel.next_level()
                    self._rebuild_static()
            else:
                self.kernel.auto_step(dt)
        for ev in self.kernel.drain_events():
            self._sfx(ev)

    # -------------------------------------------------- 渲染 --
    def render(self):
        w, h = self.window.width, self.window.height
        aspect = w / max(1, h)
        self.cam.target = Vec3(0, 0.4, 0)
        self.window.view = self.cam.view_matrix()
        half = self.ZOOM
        self.window.projection = Mat4.orthogonal_projection(
            -half * aspect, half * aspect, -half, half, 0.1, 100.0)

        prog = self.program
        prog.use()
        k = self.kernel
        g = self.grid
        pulse = 0.75 + 0.25 * math.sin(self.time * 2.5)

        # 地板
        prog['u_model'] = _mat_translate(0, 0, 0)
        prog['u_tint'] = TINT_FLOOR
        self.floor.draw(GL_TRIANGLES)
        # 墙（贪心合并网格，u_model = I：world_mesh 已在世界坐标）
        prog['u_model'] = _mat_translate(0, 0, 0)
        prog['u_tint'] = TINT_WALL
        self._walls.draw(GL_TRIANGLES)
        # 目标（空目标才可见：被箱子盖住时箱子会变色）
        prog['u_tint'] = (TINT_GOAL[0] * pulse, TINT_GOAL[1] * pulse, TINT_GOAL[2] * pulse)
        for (i, j) in sorted(g.goals):
            if (i, j) in g.boxes:
                continue
            x, z = g.world_of(i, j)
            prog['u_model'] = _mat_translate(x, 0, z)
            self._draw_shared('goal')
        # 箱子（入仓变色）
        for (i, j) in sorted(g.boxes):
            x, z = g.world_of(i, j)
            prog['u_model'] = _mat_translate(x, 0.43, z) @ _mat_scale(0.86, 0.86, 0.86)
            prog['u_tint'] = TINT_BOX_ON_GOAL if g.on_goal(i, j) else TINT_BOX
            self._draw_shared('cube')
        # 玩家
        if g.player is not None:
            x, z = g.world_of(*g.player)
            prog['u_model'] = _mat_translate(x, 0.28, z) @ _mat_scale(0.55, 0.56, 0.55)
            prog['u_tint'] = TINT_PLAYER
            self._draw_shared('cube')
        # 编辑光标
        if self._editing and self._cursor is not None:
            x, z = g.world_of(*self._cursor)
            prog['u_model'] = _mat_translate(x, 0.03, z) @ _mat_scale(0.9, 1.0, 0.9)
            prog['u_tint'] = TINT_CURSOR
            self._draw_shared('goal')

        self._draw_overlay(w, h)

    def _draw_shared(self, name):
        vlist = self.goal_list if name == 'goal' else self.cube_list
        vlist.draw(GL_TRIANGLES)

    def _draw_overlay(self, w, h):
        r = self.kernel.ctx.rules
        txt = {'won': '归 仓 ！'}.get(r.state(), '')
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
        glEnable(GL_DEPTH_TEST)
        self.window.projection = self.perspective

    # -------------------------------------------------- 音效 --
    def _init_audio(self):
        self.sfx = None
        try:
            d = tempfile.mkdtemp(prefix='boxward_sfx_')
            self._sfx_dir = d
            self._make_sfx('move', 660, 520, 'sine', 0.05)
            self._make_sfx('push', 210, 120, 'square', 0.09)
            self._make_sfx('goal', 880, 1320, 'sine', 0.15)
            self._make_sfx('undo', 520, 660, 'sine', 0.07)
            self._make_sfx('blocked', 140, 100, 'square', 0.08)
            self._make_sfx('win', 523, 784, 'triangle', 0.50)
            from world3d.engine.audio import SfxPlayer
            self.sfx = SfxPlayer(d, names=('move', 'push', 'goal', 'undo',
                                           'blocked', 'win'), gain=0.5)
        except Exception as e:
            print('[audio] disabled:', e)
            self.sfx = None

    def _make_sfx(self, name, f0, f1, wave, dur):
        from world3d.engine.audio import Track, render_wav
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
        g = self.grid
        par = f'{k.par_pushes}' if k.par_pushes is not None else '—'
        st = {'won': '入仓', 'prep': ''}.get(k.ctx.rules.state(), '')
        return (f'关 {k.level_name}   步 {k.steps}   推 {k.pushes}/par{par}   '
                f'箱 {sum(1 for c in g.boxes if g.on_goal(*c))}/{len(g.goals)}   '
                f'视角 {int(round(self.cam.yaw)) % 360}°/俯 {int(round(self.cam.pitch))}°   '
                f'{self._edit_msg}'
                f'{"  [编辑]" if self._editing else ""}'
                f'{"  [AUTO]" if self.auto else ""}'
                f'{"  [PAUSED]" if self.paused else ""}'
                f'{"  " + st if st else ""}'
                f'   t={self.time:5.1f}s {self.fps:4.1f}fps')


if __name__ == '__main__':
    run(BoxwardDemo)
