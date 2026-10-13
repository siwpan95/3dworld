# -*- coding: utf-8 -*-
"""砖境 / Brickward —— BQM 机制致敬（B1/B2a/B2b）的**可玩 + 可编辑** demo。

> 机制致敬 **BlockQuest Maker（《砖块迷宫建造者》）**，不是移植、不是复刻：
> 名称 / 美术 / 音效 / 关卡数据 / 联网经济一律自创或不做，只吸收玩法机制。
> 合规边界见 `docs/BQM机制致敬_复刻可行性.md` §2。

分层（与既有内核 demo 一致「逻辑在 Cordis、表现由宿主」）：

- setup()   经 asyncio 引导 `cordis.brickward.yml` 配置树（`host.tree.mount_tree`），
            内核装载关卡；`render` 服务随同一棵配置树挂载（与 headless 验证同一个入口）；
- update()  键盘走子 / `--flag auto` 的**求解器回放**交给内核，事件转音效；
- render()  只读内核 `snapshot()`：**墙 = 贪心合并网格**（`engine.greedy_mesh`，一次建网），
            其余元素 = 共享小网格逐个 batch（`u_model` 平移/缩放/旋转），
            **正交等距视角**（偏航 +45°，Q/E 切 90° 档；俯角 P 键循环）。

**单一事实来源 = 关卡 JSON**：编辑器导出的 = 内核吃下的 = `--flag level=` 读回的
（schema 与往返规则见 `games/brickward/kernel.py` 的 `level_to_json` / `level_from_json`）。
**auto-pilot = 求解器回放**：与 `config_boot brickward` 的 headless 验收同源。

操作（游玩）：WASD/方向键 走·推·点火   Z 重开本关   N 下一关   R 换种子关
             Q/E 旋转视角   P 俯角   SPACE 暂停   ESC 退出
操作（编辑，M 进入）：左键 = 摆当前道具（`[`/`]` 或 TAB 换道具）
             右键 = 擦成地板   C 连接模式（先点开关、再点门）   T 换连接模式
             SHIFT+C 清空连接   V 校验+试解   E 导出 JSON   I 载入 levels/*.json
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
# 少了它就会 ModuleNotFoundError（P2-1 全量验收实测，30 个 demo 一起炸）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.platform.app import DemoApp, run  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from host.tree import WANT_BRICKWARD, config_path, mount_tree  # noqa: E402
from games.brickward.kernel import (  # noqa: E402
    BOMB, BUTTON, CRATE, DOOR, EXIT, FIREBALL, FLOOR, GOLD_DOOR, KEY, LEVER,
    LEVELS, PLAYER, ROCK, SLIME, SPIKE, WALL,
    generate, level_add_link, level_blank, level_clear_links, level_from_json,
    level_set_cell, level_to_json, solve_level, validate_level,
)
from engine.baked_mesh import unit_cube, unit_sphere  # noqa: E402
from engine.greedy_mesh import layer_mesh  # noqa: E402
from engine.picking import ray_from_screen  # noqa: E402
from logic.camera import OrbitCamera  # noqa: E402

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


# ---------------------------------------------------------------- 几何（共享小网格）
def _quad_geom():
    """单位地砖（XZ 平面，y = 0，中心在原点）。"""
    a, b, c, d = (-.5, 0, -.5), (.5, 0, -.5), (.5, 0, .5), (-.5, 0, .5)
    pos, shade = [], []
    for p in (a, b, c, a, c, d):
        pos.extend(p)
        shade.extend((1.0, 1.0, 1.0))
    return pos, shade


def _pyramid_geom():
    """单位尖刺（底 1×1 @ y = 0，顶点 y = 1）。四面各自烘焙明暗。"""
    apex = (0.0, 1.0, 0.0)
    base = [(-.5, 0, .5), (.5, 0, .5), (.5, 0, -.5), (-.5, 0, -.5)]
    shades = (0.95, 0.80, 0.62, 0.86)
    pos, shade = [], []
    for k in range(4):
        p0, p1 = base[k], base[(k + 1) % 4]
        for p in (p0, p1, apex):
            pos.extend(p)
            shade.extend((shades[k], shades[k], shades[k]))
    return pos, shade


def _mat_trs(tx, ty, tz, sx=1.0, sy=1.0, sz=1.0, spin=0.0):
    """平移 × 绕 y 旋转 × 缩放（绕 y 的旋转对上下对称的体块没有视觉差，
    但对"键/炸弹/史莱姆"这类有朝向感的件有用，也让静止画面有细微活气）。"""
    m = Mat4.from_translation(Vec3(tx, ty, tz))
    if spin:
        m = m @ Mat4.from_rotation(math.radians(spin), Vec3(0, 1, 0))
    return m @ Mat4.from_scale(Vec3(sx, sy, sz))


# ---------------------------------------------------------------- 配色
TINT_FLOOR = (0.15, 0.145, 0.155)
TINT_WALL = (0.52, 0.50, 0.47)
TINT_WALL_ALT = (0.60, 0.56, 0.50)
TINT_CRATE = (0.78, 0.55, 0.20)
TINT_BUTTON_OFF = (0.42, 0.36, 0.30)
TINT_BUTTON_ON = (0.30, 0.92, 0.45)
TINT_LEVER_OFF = (0.55, 0.52, 0.30)
TINT_LEVER_ON = (0.95, 0.85, 0.25)
TINT_DOOR_OPEN = (0.28, 0.34, 0.30)
TINT_DOOR_SHUT = (0.55, 0.42, 0.28)
TINT_GOLD_SHUT = (0.58, 0.47, 0.16)
TINT_GOLD_OPEN = (0.30, 0.28, 0.22)
TINT_KEY = (0.95, 0.82, 0.30)
TINT_SPIKE = (0.86, 0.30, 0.30)
TINT_SPIKE_DEAD = (0.36, 0.34, 0.34)
TINT_EXIT = (0.25, 0.80, 0.95)
TINT_BOMB = (0.30, 0.30, 0.36)
TINT_BOMB_LIT = (0.95, 0.35, 0.25)
TINT_FIREBALL = (0.98, 0.55, 0.15)
TINT_FIREBALL_IDLE = (0.55, 0.36, 0.20)
TINT_ROCK = (0.56, 0.53, 0.50)
TINT_SLIME = (0.35, 0.85, 0.42)
TINT_PLAYER = (0.38, 0.58, 0.92)
TINT_BLAST = (1.00, 0.78, 0.35)
TINT_CURSOR = (0.30, 0.90, 0.95)
TINT_LINK = (0.95, 0.30, 0.85)

#: 编辑器道具栏（顺序 = 数字键/`[` `]` 的循环顺序）
PALETTE = (CRATE, BUTTON, LEVER, DOOR, GOLD_DOOR, KEY, SPIKE, BOMB, FIREBALL,
           ROCK, SLIME, WALL, FLOOR, PLAYER, EXIT)
#: 每种元素在编辑器里的一个字母代号（HUD 里显示道具栏用）
GLYPH = {CRATE: '箱', BUTTON: '钮', LEVER: '杆', DOOR: '门', GOLD_DOOR: '金',
         KEY: '钥', SPIKE: '刺', BOMB: '弹', FIREBALL: '火', ROCK: '石',
         SLIME: '怪', WALL: '墙', FLOOR: '板', PLAYER: '人', EXIT: '出'}

ORDER = ('U', 'R', 'D', 'L')          # 顺时针；用于把屏幕方向映射成格方向
LINK_MODES = ('open', 'toggle', 'close')


def _screen_dirs(rot):
    """屏幕方向 → 格方向（相机绕 y 转 90°·rot，输入映射跟着按 90° 取整）。"""
    r = rot % 4
    return {'W': ORDER[r], 'D': ORDER[(1 + r) % 4],
            'S': ORDER[(2 + r) % 4], 'A': ORDER[(3 + r) % 4]}


class BrickwardDemo(DemoApp):
    TITLE = '砖境 / Brickward —— 机关解谜 + 谜题编辑器'
    # 单行：Hud 的底条只有 30px，两行会溢出；宽度也量过（帮 550px，窗口 1080）
    HELP = ('WASD 走/推/点火   Z 重开   N 下一关   R 种子关   Q/E 视角   P 俯角   '
            'M 谜题编辑器（B 新空白关）')
    DURATION = None          # 交互型：time 不取模，持续累加
    HUD = True
    # 剧照：auto 回放推进，两张落在不同关卡的中段（静态棋盘截图看不出机制）
    SHOTS = ((4.0, 'docs/brickward_a.png'),
             (16.0, 'docs/brickward_b.png'))

    ZOOM = 6.6               # 正交半高（世界单位）：11×7 的关刚好铺满画面

    # 等距视角：偏航固定比"轴对齐"多 45°，这样每个方块同时露出**两个侧面 + 顶面**，
    # 立体感才出来（轴对齐时一个方块只能看到一个面，整幅画面读起来像 2D 平面）。
    # 这与 boxward 的约定一致（`docs/boxward_a.png` 是同一套看的）：
    # ⚠️ 「相机偏航 +45°」与「输入按 90° 分档」是两件独立的事，别合并 ——
    # 输入不跟着转，四个方向键在屏幕上就呈现为四个"斜向"（经典等距操作感）。
    YAW_OFFSET = 45.0
    PITCH_PRESETS = (30.0, 38.0, 45.0, 52.0)

    AUTO_DT = 0.30           # auto 回放每步间隔（秒）
    AUTO_WAIT = 1.0          # 通关后停留多久再下一关

    # ------------------------------------------------------------------ setup
    def setup(self):
        glEnable(GL_DEPTH_TEST)
        self.program = ShaderProgram(Shader(VERTEX_SRC, 'vertex'),
                                     Shader(FRAGMENT_SRC, 'fragment'))
        # ---- Cordis 挂载（配置树驱动；render 与 headless 同一个入口）----
        self.ctx, self.kernel = self._boot_kernel()
        self.cam = OrbitCamera(target=Vec3(0, 0.3, 0), yaw=self.YAW_OFFSET, pitch=45.0,
                               dist=18.0, pitch_range=(25.0, 60.0),
                               dist_range=(8.0, 34.0))
        self._rot = 0

        # ---- flags ----
        # --shot 也走 auto：静帧棋盘看不出"机关在动"，而剧照要能自证机制
        self.auto = ('auto' in self.args.flags) or bool(self.args.shot)
        self._auto_acc = 0.0
        self._auto_wait = 0.0
        self._plan, self._plan_i = [], 0
        self._walls = None            # 必须在下面的 flags 循环之前：_load_level 要重建静态网格
        self._editing = False
        self._palette_i = 0
        self._linking = False
        self._link_src = None
        self._link_mode = 'open'
        self._cursor = None
        self._msg = ''
        self._exported = []
        self._import_i = 0
        for f in self.args.flags:
            if f.startswith('seed') and f[4:].lstrip('-').isdigit():
                self._load_level(generate(int(f[4:])))
            elif f.startswith('level='):
                try:                          # 命令行读坏文件不该 traceback 出来
                    self._load_level(self._read_level_file(self._resolve(f[6:])))
                except (OSError, ValueError) as e:
                    self._msg = f'--flag level= 读入失败：{e}'
                    print(f'[brickward] level import failed: {e}', flush=True)
            elif f.startswith('play='):
                self._play_by_name(f[5:])
            elif f.startswith('pace=') and f[5:].replace('.', '', 1).isdigit():
                # auto 回放每步间隔（秒）。录制成片要放慢，让观众看清机关；
                # 剧照与验收用默认值（快，能在 16s 里走到第三关）。
                self.AUTO_DT = max(0.05, float(f[5:]))

        # ---- 共享网格 ----
        cp, cs = unit_cube()
        self.cube = self.program.vertex_list(len(cp) // 3, GL_TRIANGLES,
                                            position=('f', cp), shade=('f', cs))
        sp, ss = unit_sphere(seg=12, rings=7)
        self.sphere = self.program.vertex_list(len(sp) // 3, GL_TRIANGLES,
                                              position=('f', sp), shade=('f', ss))
        qp, qs = _quad_geom()
        self.quad = self.program.vertex_list(len(qp) // 3, GL_TRIANGLES,
                                            position=('f', qp), shade=('f', qs))
        pp, ps = _pyramid_geom()
        self.pyramid = self.program.vertex_list(len(pp) // 3, GL_TRIANGLES,
                                               position=('f', pp), shade=('f', ps))
        self._rebuild_static()        # flags 里可能已经换过关，这里统一按最终关卡建一次
        # ⚠ 必须在这里补一次：`_plan` 只在 `_load_level` 里算，而**没有任何 flag** 时
        # 一次 `_load_level` 都不会发生 —— 于是 auto 回放"计划为空、一步不动"
        # （实踩：两张剧照逐像素一样，看着像剧照机制坏了，其实是计划没算）。
        self._replan()

        self.window.push_handlers(on_mouse_press=self._on_press,
                                  on_mouse_motion=self.on_mouse_motion,
                                  on_mouse_scroll=self._on_scroll)
        self._init_audio()

        from engine.fonts import resolve_cjk_font
        fk = resolve_cjk_font()
        kw = {'font_name': fk} if fk else {}
        self.overlay = pyglet.text.Label('', font_size=44, color=(255, 235, 130, 255),
                                         anchor_x='center', anchor_y='center',
                                         x=self.window.width // 2,
                                         y=self.window.height // 2, **kw)

    def _resolve(self, p):
        return p if os.path.isabs(p) else os.path.join(ROOT, p)

    def _read_level_file(self, path):
        return level_from_json(path)          # 不合法直接抛，别静默跑一个假关卡

    def _play_by_name(self, name):
        for lv in LEVELS:
            if lv['name'] == name:
                self._load_level(lv)
                return
        print(f'[brickward] 没有名为 {name!r} 的随包关卡；现有：'
              f'{[l["name"] for l in LEVELS]}', flush=True)
        self._msg = f'没有名为 {name!r} 的随包关卡'

    def _load_level(self, level):
        """换关的统一入口：装载 + 重建静态网格 + 重算 auto 计划 + 清提示。"""
        if not self.kernel.load_level(level):
            self._msg = f'装载失败：{self.kernel.load_error}'
            print(f'[brickward] load failed: {self.kernel.load_error}', flush=True)
            return False
        self._rebuild_static()
        self._replan()
        return True

    # -------------------------------------------------- Cordis 挂载 --
    def _boot_kernel(self):
        return asyncio.run(self._mount_kernel())

    async def _mount_kernel(self):
        # require_gl=False：本内核 `inject = []`（纯逻辑），**从不取 GL 句柄** ——
        # `await_ready` 默认那套"等 live_count() > 0"会一直等不到东西而超时。
        # render 服务仍在树里（本 demo 用它的记账接口），只是不用它当就绪信号。
        ctx = await mount_tree(config_path('cordis.brickward.yml'), want=WANT_BRICKWARD,
                               require_gl=False)
        return ctx, ctx.brickward

    # -------------------------------------------------- 世界坐标 --
    def world_of(self, i, j):
        """格 (i, j) → 世界坐标 (x, z)。与 `layer_mesh` 的 x0/z0 同约定。"""
        k = self.kernel
        return (j + 0.5 - k.w / 2.0, i + 0.5 - k.h / 2.0)

    def cell_of(self, x, z):
        k = self.kernel
        return (int(math.floor(z + k.h / 2.0)), int(math.floor(x + k.w / 2.0)))

    def _rebuild_static(self):
        """换关后重建墙网格（贪心合并 —— 「一次建网」的地基）。"""
        k = self.kernel
        pos, shade = layer_mesh(k.walls, k.h, k.w, cs=1.0,
                                x0=-k.w / 2.0, z0=-k.h / 2.0, ytop=1.0)
        if self._walls is not None:
            self._walls.delete()
        self._walls = self.program.vertex_list(
            len(pos) // 3, GL_TRIANGLES,
            position=('f', tuple(pos)), shade=('f', tuple(shade)))

    # -------------------------------------------------- 输入 --
    def _on_scroll(self, x, y, sx, sy):
        if self._editing:
            self._palette_i = (self._palette_i + (1 if sy > 0 else -1)) % len(PALETTE)
            self._msg = f'道具 → {GLYPH[PALETTE[self._palette_i]]}'
            return pyglet.event.EVENT_HANDLED
        return None

    def _on_press(self, x, y, button, modifiers):
        if not self._editing:
            return
        cell = self._pick_cell(x, y)
        if cell is None:
            return
        if button == mouse.RIGHT:
            self._set_cell(cell, FLOOR)
            return
        if button != mouse.LEFT:
            return
        if self._linking:
            self._link_click(cell)
        else:
            self._set_cell(cell, PALETTE[self._palette_i])

    def _pick_cell(self, x, y):
        """屏幕 → 世界射线 → 与 y=0.5 平面求交 → 格坐标。"""
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
        i, j = self.cell_of(o[0] + d[0] * t, o[2] + d[2] * t)
        return (i, j) if (0 <= i < self.kernel.h and 0 <= j < self.kernel.w) else None

    def on_mouse_motion(self, x, y, dx, dy):
        if self._editing:
            self._cursor = self._pick_cell(x, y)

    # -------------------------------------------------- 编辑器 --
    def _locked(self, cell):
        """外圈锁死（墙围）—— 放它出去，玩家就能走出地图；校验也会立刻报错。"""
        i, j = cell
        k = self.kernel
        return i in (0, k.h - 1) or j in (0, k.w - 1)

    def _set_cell(self, cell, ch):
        """摆一格。**规则全在内核**（`level_set_cell`）：这里只负责把错误变成提示。

        早先把"边框锁死 / 玩家唯一 / 连接失效 / 摆完校验"这四条写在本 demo 里，
        结果它们既没法 headless 测，也会和内核的校验规则慢慢漂开。
        """
        try:
            level = level_set_cell(self.kernel.level, cell, ch)
        except ValueError as e:
            self._msg = f'不能放：{e}'
            return
        self._load_level(level)
        self._msg = f'{cell} ← {GLYPH.get(ch, ch)}'

    def _link_click(self, cell):
        if self._link_src is None:
            ch = self.kernel.level['rows'][cell[0]][cell[1]]
            if ch not in (BUTTON, LEVER):
                self._msg = '连接第一步：先点一个开关（按钮 o / 拉杆 L）'
                return
            self._link_src = cell
            self._msg = f'源 = {cell}（{GLYPH[ch]}）；再点一扇普通门 D'
            return
        try:
            level = level_add_link(self.kernel.level, self._link_src, cell,
                                   self._link_mode)
        except ValueError as e:
            self._msg = f'连接被拒：{e}'
            self._link_src = None
            return
        self._msg = f'连接 {self._link_src} → {cell}（{self._link_mode}）'
        self._link_src = None
        self._load_level(level)

    def _clear_links(self):
        self._load_level(level_clear_links(self.kernel.level))
        self._msg = '已清空全部连接'

    def _validate(self):
        k = self.kernel
        ok, why = validate_level(k.level)
        if not ok:
            self._msg = f'校验失败：{why}'
            return None
        sol, path = solve_level(k.level)
        self._msg = (f'可解：{len(path)} 步（路径 {path}）'
                     if sol else '校验通过，但**不可解**（求解器搜不到通路）')
        return path if sol else None

    def _export_level(self):
        k = self.kernel
        path_sol = self._validate()
        name = f'editor-{time.strftime("%H%M%S")}'
        rows = list(k.level['rows'])
        if len({len(r) for r in rows}) > 1:
            self._msg = '导出失败：各行不等长'
            return
        level = dict(k.level)
        level['name'] = name
        level['par'] = len(path_sol) if path_sol else None
        out = os.path.join(ROOT, 'levels', name + '.json')
        level_to_json(level, out)
        self._exported.append(out)
        self._msg = f'已导出 levels/{name}.json（{self._msg}）'
        print(f'[brickward] level exported: {out}', flush=True)

    def _import_next(self):
        d = os.path.join(ROOT, 'levels')
        files = sorted(f for f in os.listdir(d) if f.endswith('.json')) \
            if os.path.isdir(d) else []
        if not files:
            self._msg = 'levels/ 里没有 .json 可载入'
            return
        self._import_i %= len(files)
        p = os.path.join(d, files[self._import_i])
        self._import_i += 1
        try:
            self._load_level(level_from_json(p))
            self._msg = f'已载入 levels/{os.path.basename(p)}'
        except ValueError as e:
            self._msg = f'载入失败：{e}'

    # -------------------------------------------------- 键盘 --
    def on_key(self, symbol, modifiers):
        if self._editing:
            self._edit_key(symbol, modifiers)
            return
        dirs = _screen_dirs(self._rot)
        if symbol in (key.W, key.UP):
            self._do(dirs['W'])
        elif symbol in (key.S, key.DOWN):
            self._do(dirs['S'])
        elif symbol in (key.A, key.LEFT):
            self._do(dirs['A'])
        elif symbol in (key.D, key.RIGHT):
            self._do(dirs['D'])
        elif symbol == key.Q:
            self._rot = (self._rot - 1) % 4
            self.cam.yaw = self.YAW_OFFSET + 90.0 * self._rot
        elif symbol == key.E:
            self._rot = (self._rot + 1) % 4
            self.cam.yaw = self.YAW_OFFSET + 90.0 * self._rot
        elif symbol == key.P:
            i = (self.PITCH_PRESETS.index(self.cam.pitch)
                 if self.cam.pitch in self.PITCH_PRESETS else -1)
            self.cam.pitch = self.PITCH_PRESETS[(i + 1) % len(self.PITCH_PRESETS)]
            self._msg = f'俯角 {self.cam.pitch:.0f}°'
        elif symbol == key.Z:
            self._load_level(self.kernel.level)
            self._msg = '重开本关'
        elif symbol == key.N:
            self._next_level()
        elif symbol == key.R:
            self._load_level(generate((self.kernel.seed * 1103515245 + 12345) % (2 ** 31)))
            print(f'[brickward] new seed={self.kernel.seed}', flush=True)
        elif symbol == key.M:
            self._editing = True
            self._auto = False
            self._msg = (f'编辑：左键摆[{GLYPH[PALETTE[self._palette_i]]}] 右键擦 '
                         f'[ ]换 [C]连接⇄[T]模式 [V]试解 [E]导出 [I]载入 [B]新建 [M]退出')

    def _edit_key(self, symbol, modifiers):
        if symbol == key.M:
            self._editing = False
            self._cursor = None
            self._linking = False
            self._link_src = None
            self._msg = '退出编辑'
        elif symbol == key.C:
            if modifiers & key.MOD_SHIFT:
                self._clear_links()
            else:
                self._linking = not self._linking
                self._link_src = None
                self._msg = ('连接模式：先点开关、再点门' if self._linking else '退出连接模式')
        elif symbol == key.T:
            i = LINK_MODES.index(self._link_mode)
            self._link_mode = LINK_MODES[(i + 1) % len(LINK_MODES)]
            self._msg = f'新连接的模式 = {self._link_mode}'
        elif symbol == key.BRACKETRIGHT or symbol == key.TAB:
            self._palette_i = (self._palette_i + 1) % len(PALETTE)
            self._msg = f'道具 → {GLYPH[PALETTE[self._palette_i]]}'
        elif symbol == key.BRACKETLEFT:
            self._palette_i = (self._palette_i - 1) % len(PALETTE)
            self._msg = f'道具 → {GLYPH[PALETTE[self._palette_i]]}'
        elif symbol == key.V:
            self._validate()
        elif symbol == key.E:
            self._export_level()
        elif symbol == key.I:
            self._import_next()
        elif symbol == key.B:
            self._load_level(level_blank(11, 9))
            self._msg = '新空白关：先摆 `人`(P) 和 `出`(E) 才能试解'
        elif symbol == key.N:
            self._next_level()

    def _next_level(self):
        names = [lv['name'] for lv in LEVELS]
        try:
            i = names.index(self.kernel.level['name'])
        except ValueError:
            i = -1
        self._load_level(LEVELS[(i + 1) % len(LEVELS)])
        self._plan_i = 0

    def _do(self, d):
        ev = self.kernel.step(d)
        self._play_sfx(ev)
        if self.kernel.blasts:
            self._sfx('boom')

    # -------------------------------------------------- 每帧 --
    def update(self, dt):
        if self.paused or self._editing:
            return
        if self.auto:
            if self.kernel.status() == 'won':
                self._auto_wait -= dt
                if self._auto_wait <= 0.0:
                    self._auto_wait = self.AUTO_WAIT
                    self._next_level()
            else:
                self._auto_step(dt)

    def _replan(self):
        ok, path = solve_level(self.kernel.level)
        self._plan, self._plan_i = (list(path), 0) if ok else ([], 0)
        if not ok:
            self._msg = '本关不可解（auto 停摆）'

    def _auto_step(self, dt):
        self._auto_acc += dt
        while self._auto_acc >= self.AUTO_DT and self._plan_i < len(self._plan):
            self._auto_acc -= self.AUTO_DT
            d = self._plan[self._plan_i]
            self._plan_i += 1
            self._do(d)
            if self.kernel.status() != 'playing':
                break

    # -------------------------------------------------- 渲染 --
    def render(self):
        w, h = self.window.width, self.window.height
        aspect = w / max(1, h)
        k = self.kernel
        self.cam.target = Vec3(0, 0.3, 0)
        self.window.view = self.cam.view_matrix()
        half = self.ZOOM
        self.window.projection = Mat4.orthogonal_projection(
            -half * aspect, half * aspect, -half, half, 0.1, 100.0)

        prog = self.program
        prog.use()
        snap = k.snapshot()
        # 帧级动画参数存成员：`_draw_cell` / `_put_*` 都要用（早先在 render 里当局部量，
        # 结果被调用的方法看不见 —— 冒烟直接 NameError）
        self._pulse = pulse = 0.72 + 0.28 * math.sin(self.time * 3.0)
        self._wob = wob = 60.0 * self.time   # 缓慢自转（静止画面不呆板）

        # 地板（整块）
        prog['u_model'] = _mat_trs(0, -0.02, 0, k.w, 1.0, k.h)
        prog['u_tint'] = TINT_FLOOR
        self.quad.draw(GL_TRIANGLES)
        # 墙（贪心合并网格：已在世界坐标，u_model = I）
        prog['u_model'] = _mat_trs(0, 0, 0)
        prog['u_tint'] = TINT_WALL
        self._walls.draw(GL_TRIANGLES)

        # 地板格 / 尖刺 / 按钮 / 拉杆 / 钥匙 / 门 / 金门 / 出口
        for p in [(i, j) for i in range(k.h) for j in range(k.w)]:
            if p in k.walls:
                continue
            self._draw_cell(snap, p, pulse)

        # 箱子 / 炸弹 / 火球 / 滚石 / 史莱姆（实体层）
        for p in snap['crates']:
            self._put(self.cube, p, 0.42, 0.86, 0.84, 0.86, TINT_CRATE)
        for p in snap['bombs']:
            lit = p in k.fuses
            tint = TINT_BOMB_LIT if lit else TINT_BOMB
            if lit:                            # 引信越短闪得越快（提示"要炸了"）
                tint = tuple(c * (0.55 + 0.45 * math.sin(self.time * 18.0))
                             for c in tint)
            self._put_sphere(p, 0.34, 0.72 - 0.05 * (p in k.fuses), spin=self._wob * 2.0,
                             tint=tint)
        for p, d in snap['fireballs']:
            tint = TINT_FIREBALL if d else TINT_FIREBALL_IDLE
            self._put_sphere(p, 0.40, 0.80, spin=self._wob * (4.0 if d else 0.4), tint=tint)
        for p in snap['rocks']:
            self._put_sphere(p, 0.40, 0.94, spin=self._wob, tint=TINT_ROCK)
        for p in snap['slimes']:
            self._put_sphere(p, 0.30, 0.78, spin=self._wob * 1.5, tint=TINT_SLIME,
                             squash=0.72)
        # 玩家（身体 + 头）
        px, pz = self.world_of(*snap['player'])
        prog['u_model'] = _mat_trs(px, 0.26, pz, 0.56, 0.52, 0.56)
        prog['u_tint'] = TINT_PLAYER
        self.cube.draw(GL_TRIANGLES)
        prog['u_model'] = _mat_trs(px, 0.66, pz, 0.30, 0.28, 0.30)
        prog['u_tint'] = tuple(c * 1.25 for c in TINT_PLAYER)
        self.cube.draw(GL_TRIANGLES)

        # 本步的爆炸火光（瞬态，只在这一帧可见）
        for p in snap['blasts']:
            x, z = self.world_of(*p)
            prog['u_model'] = _mat_trs(x, 0.02, z, 1.0, 1.0, 1.0)
            prog['u_tint'] = tuple(c * pulse for c in TINT_BLAST)
            self.quad.draw(GL_TRIANGLES)

        # 编辑层：连接线（开关 → 门）+ 光标 + 道具预览
        if self._editing:
            for lk in k.level.get('links') or []:
                sx, sz = self.world_of(*lk['from'])
                dx, dz = self.world_of(*lk['to'])
                for t in (0.25, 0.5, 0.75):    # 三点虚线示意（没有画线器，用方块点）
                    x = sx + (dx - sx) * t
                    z = sz + (dz - sz) * t
                    prog['u_model'] = _mat_trs(x, 0.55, z, 0.22, 0.22, 0.22)
                    prog['u_tint'] = TINT_LINK
                    self.cube.draw(GL_TRIANGLES)
            if self._link_src is not None:
                x, z = self.world_of(*self._link_src)
                prog['u_model'] = _mat_trs(x, 0.9 + 0.1 * pulse, z, 0.4, 0.4, 0.4,
                                           spin=self._wob * 3)
                prog['u_tint'] = TINT_LINK
                self.cube.draw(GL_TRIANGLES)
            if self._cursor is not None and not self._locked(self._cursor):
                x, z = self.world_of(*self._cursor)
                prog['u_model'] = _mat_trs(x, 0.03, z, 0.94, 1.0, 0.94)
                prog['u_tint'] = TINT_CURSOR
                self.quad.draw(GL_TRIANGLES)
                self._preview(PALETTE[self._palette_i], x, 0.06, z, pulse)

        self._draw_overlay(w, h)

    def _put(self, mesh, p, y, sx, sy, sz, tint, spin=0.0):  # 保留：单点/单影调用
        x, z = self.world_of(*p)
        self.program['u_model'] = _mat_trs(x, y, z, sx, sy, sz, spin)
        self.program['u_tint'] = tint
        mesh.draw(GL_TRIANGLES)

    def _put_sphere(self, p, y, size, spin=0.0, tint=(1, 1, 1), squash=1.0):
        x, z = self.world_of(*p)
        self.program['u_model'] = _mat_trs(x, y, z, size, size * squash, size, spin)
        self.program['u_tint'] = tint
        self.sphere.draw(GL_TRIANGLES)

    def _draw_cell(self, snap, p, pulse):
        """画一个非墙格上的静态/半静态元素。"""
        k = self.kernel
        ch = k.level['rows'][p[0]][p[1]]
        x, z = self.world_of(*p)
        prog = self.program
        prog['u_model'] = _mat_trs(x, 0.01, z, 0.94, 1.0, 0.94)
        prog['u_tint'] = (0.19, 0.185, 0.20)
        self.quad.draw(GL_TRIANGLES)
        if p in snap['spikes_dead']:
            prog['u_model'] = _mat_trs(x, 0.01, z, 0.8, 1.0, 0.8)
            prog['u_tint'] = TINT_SPIKE_DEAD
            self.pyramid.draw(GL_TRIANGLES)
        elif ch == SPIKE:
            prog['u_model'] = _mat_trs(x, 0.0, z, 0.78, 0.62, 0.78)
            prog['u_tint'] = TINT_SPIKE
            self.pyramid.draw(GL_TRIANGLES)
        elif ch == BUTTON:
            on = p in snap['buttons_pressed']
            prog['u_model'] = _mat_trs(x, 0.07 if on else 0.12, z, 0.66, 0.25, 0.66)
            prog['u_tint'] = TINT_BUTTON_ON if on else TINT_BUTTON_OFF
            self.cube.draw(GL_TRIANGLES)
        elif ch == LEVER:
            on = p in snap['levers_on']
            prog['u_model'] = _mat_trs(x, 0.14 if on else 0.32, z, 0.22, 0.6, 0.22)
            prog['u_tint'] = TINT_LEVER_ON if on else TINT_LEVER_OFF
            self.cube.draw(GL_TRIANGLES)
        elif ch == KEY:
            prog['u_model'] = _mat_trs(x, 0.30 + 0.06 * pulse, z, 0.34, 0.34, 0.34,
                                       spin=90.0 * self.time)
            prog['u_tint'] = TINT_KEY
            self.cube.draw(GL_TRIANGLES)
        elif ch in (DOOR, GOLD_DOOR):
            opened = p in (snap['doors_open'] if ch == DOOR else snap['gold_open'])
            if opened:
                prog['u_model'] = _mat_trs(x, 0.05, z, 0.96, 0.10, 0.96)
                prog['u_tint'] = TINT_DOOR_OPEN if ch == DOOR else TINT_GOLD_OPEN
                self.cube.draw(GL_TRIANGLES)
            else:
                prog['u_model'] = _mat_trs(x, 0.5, z, 1.0, 1.0, 0.30)
                prog['u_tint'] = TINT_DOOR_SHUT if ch == DOOR else TINT_GOLD_SHUT
                self.cube.draw(GL_TRIANGLES)
        elif ch == EXIT:
            prog['u_model'] = _mat_trs(x, 0.02, z, 0.96, 1.0, 0.96)
            prog['u_tint'] = tuple(c * pulse for c in TINT_EXIT)
            self.quad.draw(GL_TRIANGLES)
            prog['u_model'] = _mat_trs(x, 0.45 + 0.16 * pulse, z, 0.34, 0.34, 0.34,
                                       spin=self._wob * 2)
            prog['u_tint'] = TINT_EXIT
            self.cube.draw(GL_TRIANGLES)

    def _preview(self, ch, x, y, z, pulse):
        """编辑光标处的道具预览（每个元素一眼可辨的剪影）。"""
        prog = self.program
        prog['u_tint'] = TINT_CURSOR
        if ch == WALL:
            prog['u_model'] = _mat_trs(x, 0.5, z, 1.0, 1.0, 1.0)
            self.cube.draw(GL_TRIANGLES)
        elif ch == SPIKE:
            prog['u_model'] = _mat_trs(x, 0.0, z, 0.8, 0.9, 0.8)
            self.pyramid.draw(GL_TRIANGLES)
        elif ch in (BOMB, ROCK, SLIME, FIREBALL):
            prog['u_model'] = _mat_trs(x, 0.42, z, 0.8, 0.8, 0.8)
            self.sphere.draw(GL_TRIANGLES)
        elif ch == EXIT:
            prog['u_model'] = _mat_trs(x, 0.02, z, 0.96, 1.0, 0.96)
            self.quad.draw(GL_TRIANGLES)
        else:
            prog['u_model'] = _mat_trs(x, 0.42, z, 0.8, 0.8, 0.8)
            self.cube.draw(GL_TRIANGLES)

    def _draw_overlay(self, w, h):
        txt = ''
        if self.kernel.status() == 'won':
            txt = '通 关 ！'
        elif self.kernel.status() == 'lost':
            txt = '失 败（Z 重开）'
        elif self.paused:
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
            d = tempfile.mkdtemp(prefix='brickward_sfx_')
            self._sfx_dir = d
            self._make_sfx('move', 700, 560, 'sine', 0.045)
            self._make_sfx('push', 240, 130, 'square', 0.09)
            self._make_sfx('ignite', 520, 900, 'triangle', 0.10)
            self._make_sfx('roll', 150, 90, 'sawtooth', 0.16)
            self._make_sfx('unlock', 880, 1320, 'sine', 0.14)
            self._make_sfx('boom', 180, 45, 'square', 0.34)
            self._make_sfx('blocked', 130, 95, 'square', 0.07)
            self._make_sfx('die', 320, 90, 'triangle', 0.36)
            self._make_sfx('win', 523, 784, 'triangle', 0.50)
            from engine.audio import SfxPlayer
            self.sfx = SfxPlayer(d, names=('move', 'push', 'ignite', 'roll', 'unlock',
                                           'boom', 'blocked', 'die', 'win'), gain=0.45)
        except Exception as e:
            print('[audio] disabled:', e)
            self.sfx = None

    def _make_sfx(self, name, f0, f1, wave, dur):
        from engine.audio import Track, render_wav
        tr = Track(dur, rate=32000)
        tr.tone(0.0, dur, f0, f1, wave=wave, amp=0.3,
                attack=0.005, release=dur * 0.45)
        render_wav(tr, os.path.join(self._sfx_dir, name + '.wav'))

    def _sfx(self, name):
        if self.sfx:
            try:
                self.sfx.play(name)
            except Exception:
                pass

    def _play_sfx(self, ev):
        self._sfx({'moved': 'move', 'pushed': 'push', 'ignited': 'ignite',
                   'rolled': 'roll', 'unlock': 'unlock', 'blocked': 'blocked',
                   'died': 'die', 'won': 'win'}.get(ev, 'move'))

    # -------------------------------------------------- HUD --
    def status_text(self):
        """HUD 右上状态行。**必须短**：Hud 的标题在左、状态在右，状态一长就压到标题上
        （实测第一版把道具栏也塞进来，两行字直接叠在一起糊掉）。"""
        k = self.kernel
        snap = k.snapshot()
        ent = (f"箱{len(snap['crates'])}弹{len(snap['bombs'])}火{len(snap['fireballs'])}"
               f"石{len(snap['rocks'])}怪{len(snap['slimes'])}钥{snap['keys_held']}")
        st = {'won': '通关', 'lost': '失败'}.get(snap['status'], '')
        flags = (f"{'[编辑]' if self._editing else ''}"
                 f"{'[连接' + self._link_mode + ']' if self._linking else ''}"
                 f"{'[AUTO]' if self.auto else ''}"
                 f"{'[PAUSED]' if self.paused else ''}")
        pal = ''
        if self._editing:                    # 道具栏只在编辑模式显示
            pal = '  ' + ' '.join(('[' + GLYPH[c] + ']') if i == self._palette_i
                                  else GLYPH[c] for i, c in enumerate(PALETTE))
        return (f"{snap['level']} 步{snap['steps']} 推{snap['pushes']} "
                f"死{snap['deaths']} {ent} {st}{flags}{pal} {self._msg} "
                f"t={self.time:4.1f}s")


if __name__ == '__main__':
    run(BrickwardDemo)
