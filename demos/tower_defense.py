# -*- coding: utf-8 -*-
"""塔防切片「戍卫之路 / Roadwarden」—— P1-② 首个游戏内核（Cordis 委托阶段 C）。

机制致敬 Warcraft / Dune II 的「经济→兵力→战斗」循环；原创美术 / 命名 / 世界观，
不碰任何原 IP 名称与素材（合规见 docs/remake_ideas.md §7 / §9）。

阶段 A（demos/tower_defense.py 初版）：直连 engine，自管全部游戏状态与逻辑。
阶段 B（games/tower_defense/kernel.py）：把玩法拆成「共同核（Service）
+ 内核（TowerDefenseKernel）」两层，用 cordis v4.0.0 fiber 属性式 API 接线，
并验证 GL 资源随内核 fiber dispose 可逆回滚（headless 模拟通关 + 回滚断言）。

阶段 C（本文件）：demo 不再自管玩法，而是**委托 Cordis 内核**——挂载则由
    **cordis.td.yml 配置树**声明（试点三：setup() 经 asyncio 一次性引导配置树，
    host.tree.mount_tree 负责挂载与就绪等待），共同核 + TowerDefenseKernel 内核
    全由 yaml 声明；内核在 __cordis_init__ 自动布防并取好 GL 资源句柄；
    update()  只做两件事：把 dt 交给内核驱动模拟，并把内核发出的语义事件（build/
              shoot/hit/kill/won/lose）转成音效；
    render()  只读内核/实体注册表的状态来画（塔/敌/弹均为内核登记的实体）；
    on_key/N、左键建塔 直接委托内核的 early_wave() / build()。

渲染层（GL 程序、立方体/球网格、地面、相机、HUD、音效、拾取）仍由本 demo 负责——
这是「共同核 + 内核」范式在切片里的落地：逻辑在 Cordis，表现由宿主持有。真实
全 Cordis 化时，RenderService 会接管 GL 上下文；此处为保持离线可录制，宿主保留
pyglet 直接绘制（内核的 RenderService 仅做 headless 记账与回滚语义）。

复用：demos/_harness.DemoApp/run、logic.camera.OrbitCamera、engine.picking、
engine.audio（SfxPlayer/Track），以及 games/tower_defense/kernel
（共同核 + 塔防内核，12 核契约见 core/contracts.py）。

玩法：地图 20×20 格，敌人沿车道从入口走向基地；玩家在空地建箭/炮/冰三种塔拦截；
击杀得金、活过 5 波即胜、基地 HP 归零即败。开局自动布防 4 座塔，便于无操作录制自演示。

操作：右键拖拽转视角、滚轮缩放、左键点空地建塔、1/2/3 选塔、N 提前开波、SPACE 暂停、ESC 退出。
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

# 试点三：挂载由 cordis.td.yml 配置树声明（host.tree 共享引导——
# config_boot.py 的 headless 验证走同一函数与同一 WANT 列表）
from host.tree import WANT_TD, config_path, mount_tree  # noqa: E402

# ============================================================= 地图与平衡 ==
GRID = 20                       # 格数 / 边
CELL = 2.0                      # 格距 -> 世界 40×40（相机 DIST_MAX=60 装得下）
HALF = GRID * CELL / 2.0        # 20.0
SKY = (0.62, 0.73, 0.86)

# 车道 waypoint（世界 XZ，y=0）：从一侧蜿蜒到基地
LANE = [
    (-HALF + 1.0, -HALF + 3.0),
    ( HALF - 5.0, -HALF + 3.0),
    ( HALF - 5.0, -HALF + 7.0),
    (-HALF + 5.0, -HALF + 7.0),
    (-HALF + 5.0,  HALF - 5.0),
    ( HALF - 3.0,  HALF - 5.0),
]
BASE = LANE[-1]
# 平衡常量统一由内核 / 配置树定义，demo 不再复制（避免两处漂移）：
#   开局金 300、波数 5 -> experiments/cordis/cordis.td.yml
#   备战时长 / 基地 HP -> games/tower_defense/kernel.py

# 塔（展示用：渲染查 TOWER_TYPES[type] 取高度/颜色；逻辑全在内核）
TOWER_TYPES = {
    '1': dict(name='箭塔', cost=50,  range=9.0, damage=8.0,  rate=2.0, splash=0.0, slow=0.0,
              color=(0.30, 0.85, 0.95), height=1.4),
    '2': dict(name='炮塔', cost=120, range=7.5, damage=24.0, rate=0.6, splash=3.0, slow=0.0,
              color=(0.98, 0.62, 0.22), height=1.6),
    '3': dict(name='冰塔', cost=80,  range=8.0, damage=5.0,  rate=1.2, splash=0.0, slow=0.5,
              color=(0.55, 0.80, 1.00), height=1.5),
}

# 敌人（展示用：渲染查 ENEMY_TYPES[type] 取半径/颜色）
ENEMY_TYPES = {
    'swarm':   dict(hp=28,  speed=5.0, reward=7,  radius=0.5, leak=1, color=(0.95, 0.80, 0.30)),
    'grunt':   dict(hp=60,  speed=3.0, reward=12, radius=0.7, leak=2, color=(0.85, 0.35, 0.30)),
    'bruiser': dict(hp=180, speed=2.0, reward=30, radius=1.0, leak=3, color=(0.55, 0.30, 0.55)),
}

# 波次构成见 games/tower_defense/kernel.py（WAVES）；
# HUD 波数直接读 ctx.rules.total_waves（yaml: 5），demo 不再复制一份。

# 开局自动布防（键=(i,j) 格，值=塔类型键）——在内核 __cordis_init__ 里免费建造
AUTO_TOWERS = [((5, 2), '1'), ((15, 2), '1'), ((10, 2), '2'), ((16, 4), '3')]


# ============================================================= 纯函数几何 ==
def _seg_dist(px, pz, ax, az, bx, bz):
    """点到线段 (a,b) 的最短距离（XZ 平面）。"""
    dx, dz = bx - ax, bz - az
    l2 = dx * dx + dz * dz
    if l2 < 1e-9:
        return math.hypot(px - ax, pz - az)
    t = ((px - ax) * dx + (pz - az) * dz) / l2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + dx * t), pz - (az + dz * t))


def lane_length():
    L = 0.0
    for k in range(len(LANE) - 1):
        L += math.hypot(LANE[k + 1][0] - LANE[k][0], LANE[k + 1][1] - LANE[k][1])
    return L


def lane_point(dist):
    """沿车道折线取距离 dist 处的世界 (x,z)。"""
    d = dist
    for k in range(len(LANE) - 1):
        x0, z0 = LANE[k]
        x1, z1 = LANE[k + 1]
        seg = math.hypot(x1 - x0, z1 - z0)
        if d <= seg or k == len(LANE) - 2:
            t = d / seg if seg > 1e-6 else 0.0
            return (x0 + (x1 - x0) * t, z0 + (z1 - z0) * t)
        d -= seg
    return LANE[-1]


def cell_center(i, j):
    return (-HALF + (i + 0.5) * CELL, -HALF + (j + 0.5) * CELL)


def _mat_translate(tx, ty, tz):
    return Mat4(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, tx, ty, tz, 1)


def _mat_scale(sx, sy, sz):
    return Mat4(sx, 0, 0, 0, 0, sy, 0, 0, 0, 0, sz, 0, 0, 0, 0, 1)


def _hash01(i, j):
    h = (i * 0x27D4EB2D) & 0xFFFFFFFF
    h = (h ^ (j * 0x165667B1)) & 0xFFFFFFFF
    h = ((h ^ (h >> 15)) * 0x2C1B3C6D) & 0xFFFFFFFF
    h = ((h ^ (h >> 12)) * 0x297A2D39) & 0xFFFFFFFF
    h ^= h >> 15
    return (h & 0xFFFFFF) / 16777216.0


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


def _unit_sphere(seg=10, rings=8):
    """低面 UV 球，按纬度烘焙明暗。"""
    pos, shade = [], []
    for i in range(rings):
        lat0 = (i / rings - 0.5) * math.pi
        lat1 = ((i + 1) / rings - 0.5) * math.pi
        for j in range(seg):
            lon0 = (j / seg) * 2 * math.pi
            lon1 = ((j + 1) / seg) * 2 * math.pi
            a = (math.cos(lat0) * math.cos(lon0), math.sin(lat0), math.cos(lat0) * math.sin(lon0))
            b = (math.cos(lat0) * math.cos(lon1), math.sin(lat0), math.cos(lat0) * math.sin(lon1))
            c = (math.cos(lat1) * math.cos(lon1), math.sin(lat1), math.cos(lat1) * math.sin(lon1))
            d = (math.cos(lat1) * math.cos(lon0), math.sin(lat1), math.cos(lat1) * math.sin(lon0))
            for (v, lat) in ((a, lat0), (b, lat0), (c, lat1), (a, lat0), (c, lat1), (d, lat1)):
                pos.extend(v)
                sh = 0.6 + 0.4 * (math.sin(lat) * 0.5 + 0.5)
                shade.extend((sh, sh, sh))
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
class TowerDefenseDemo(DemoApp):
    TITLE = '戍卫之路 / Roadwarden（塔防切片 · P1-② · Cordis 委托）'
    HELP = '右键拖拽 转视角   滚轮 缩放   左键 建塔   1/2/3 选塔   N 提前开波   SPACE 暂停'
    DURATION = None                 # 不循环：时间累计，便于录制完整一波流程
    HUD = True
    FOV = 60.0

    def setup(self):
        glEnable(GL_DEPTH_TEST)
        pyglet.gl.glClearColor(SKY[0], SKY[1], SKY[2], 1.0)
        self.program = ShaderProgram(Shader(VERTEX_SRC, 'vertex'),
                                     Shader(FRAGMENT_SRC, 'fragment'))

        # 相机：近俯视等距（DIST_MAX=60，pitch 上限 89）
        self.camera = OrbitCamera(target=Vec3(0, 0, 0), yaw=45.0, pitch=72.0, dist=58.0)

        # 几何（一次性建好，运行时只改 u_model/u_tint）
        cpos, cshade = _unit_cube()
        spos, sshade = _unit_sphere()
        self.cube = self.program.vertex_list(len(cpos) // 3, GL_TRIANGLES,
                                              position=('f', cpos), shade=('f', cshade))
        self.sphere = self.program.vertex_list(len(spos) // 3, GL_TRIANGLES,
                                                position=('f', spos), shade=('f', sshade))

        # 网格 + 车道 + 基地分类（仅用于地面绘制与「可否建塔」判定）
        self._build_cells()
        self._build_ground()

        # ---------- 挂载 Cordis 内核（阶段 C：玩法委托给内核）----------
        self.ctx, self.kernel = self._boot_kernel()
        # 内核已在 __cordis_init__ 自动布防 AUTO_TOWERS（免费）并取好 GL 句柄，
        # 这里不再自管 gold/base_hp/enemies/towers/projectiles/wave 状态。

        # 交互
        self.selected = '1'
        self._press = None          # (x, y, button) 用于区分点击/拖拽
        self.window.push_handlers(
            on_mouse_drag=self._on_drag,
            on_mouse_scroll=self._on_scroll,
            on_mouse_press=self._on_press,
            on_mouse_release=self._on_release,
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
        """同步引导 cordis 配置树（共同核 + 塔防内核）。

        ``mount_tree`` 是异步的，但「挂载」是一次性的引导动作；用 ``asyncio.run``
        跑一个临时事件循环把整张依赖图解析完即可。解析完成后，内核实例与 Context
        都是普通 Python 对象，后续 ``update(dt)`` 同步驱动，无需事件循环常驻。
        """
        return asyncio.run(self._mount_kernel())

    async def _mount_kernel(self):
        # 试点三落点：本次挂哪些插件、各带什么配置，全由 cordis.td.yml 声明；
        # host.tree 按 Cordis 原生 Loader 装载并等待就绪（服务可达 + GL 句柄已取得）。
        ctx = await mount_tree(config_path('cordis.td.yml'), want=WANT_TD)
        return ctx, ctx.tower_defense

    # -------------------------------------------------- 网格 / 地面 --
    def _build_cells(self):
        self.cell_type = [['build' for _ in range(GRID)] for _ in range(GRID)]
        for i in range(GRID):
            for j in range(GRID):
                cx, cz = cell_center(i, j)
                if self._dist_to_lane(cx, cz) < CELL * 0.95:
                    self.cell_type[i][j] = 'lane'
        bi = int((BASE[0] + HALF) / CELL)
        bj = int((BASE[1] + HALF) / CELL)
        self.cell_type[bi][bj] = 'base'
        self.base_cell = (bi, bj)

    def _dist_to_lane(self, x, z):
        best = 1e9
        for k in range(len(LANE) - 1):
            best = min(best, _seg_dist(x, z, LANE[k][0], LANE[k][1], LANE[k + 1][0], LANE[k + 1][1]))
        return best

    def _cell_color(self, i, j):
        t = self.cell_type[i][j]
        if t == 'lane':
            return (0.55, 0.45, 0.30)
        if t == 'base':
            return (0.45, 0.45, 0.52)
        # build：草地 + 哈希抖动
        j = _hash01(i, j)
        return (0.18 + 0.06 * j, 0.40 + 0.08 * j, 0.16 + 0.05 * j)

    def _build_ground(self):
        pos = array.array('f')
        shade = array.array('f')
        for i in range(GRID):
            for j in range(GRID):
                x0 = -HALF + i * CELL
                z0 = -HALF + j * CELL
                x1, z1 = x0 + CELL, z0 + CELL
                c = self._cell_color(i, j)
                quad = [(x0, 0.0, z0), (x1, 0.0, z0), (x1, 0.0, z1), (x0, 0.0, z1)]
                for p in (quad[0], quad[1], quad[2], quad[0], quad[2], quad[3]):
                    pos.extend(p)
                    shade.extend(c)
        self.ground = self.program.vertex_list(len(pos) // 3, GL_TRIANGLES,
                                                 position=('f', pos), shade=('f', shade))

    # -------------------------------------------------- 音效 --
    def _init_audio(self):
        self.sfx = None
        try:
            d = tempfile.mkdtemp(prefix='td_sfx_')
            self._sfx_dir = d
            self._make_sfx('build', 440, 660, 'square', 0.12)
            self._make_sfx('shoot', 900, 1300, 'saw', 0.05)
            self._make_sfx('hit', 320, 140, 'sine', 0.08)
            self._make_sfx('kill', 220, 80, 'saw', 0.16)
            self._make_sfx('win', 523, 784, 'triangle', 0.5)
            self._make_sfx('lose', 320, 90, 'saw', 0.6)
            self.sfx = SfxPlayer(d, names=('build', 'shoot', 'hit', 'kill', 'win', 'lose'), gain=0.5)
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

    def _on_scroll(self, x, y, scroll_x, scroll_y):
        self.camera.zoom(scroll_y)

    def _on_press(self, x, y, button, modifiers):
        self._press = (x, y, button)

    def _on_release(self, x, y, button, modifiers):
        if self._press is None:
            return
        px0, py0, b0 = self._press
        self._press = None
        if button != mouse.LEFT or b0 != mouse.LEFT:
            return
        if math.hypot(x - px0, y - py0) > 6:      # 视为拖拽，不建塔
            return
        g = self._screen_to_ground(x, y)
        if g is None:
            return
        gx, gz = g
        i = int((gx + HALF) / CELL)
        j = int((gz + HALF) / CELL)
        # 车道/基地/越界不可建（其余合法性——已占、钱不够——由内核判定）
        if 0 <= i < GRID and 0 <= j < GRID and self.cell_type[i][j] == 'build':
            self.kernel.build(i, j, self.selected)   # 经济扣费在内核

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
        if symbol == key._1:
            self.selected = '1'
        elif symbol == key._2:
            self.selected = '2'
        elif symbol == key._3:
            self.selected = '3'
        elif symbol == key.N:
            self.kernel.early_wave()        # 立即开波 + 提前开波奖励（内核判定 state==prep）

    # -------------------------------------------------- 逐帧（委托内核）--
    def update(self, dt):
        if self.paused:
            return
        # 驱动模拟：内核自管所有游戏状态与逻辑
        self.kernel.update(dt)
        # 把内核发出的语义事件转成音效
        for name in self.kernel.drain_events():
            self._sfx(name)

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
        r = k.ctx.rules

        # 地面
        prog['u_model'] = _mat_translate(0, 0, 0)
        prog['u_tint'] = (1.0, 1.0, 1.0)
        self.ground.draw(GL_TRIANGLES)

        # 塔（实体来自内核实体注册表）
        for tw in k.towers.values():
            spec = TOWER_TYPES[tw['type']]
            h = spec['height']
            prog['u_model'] = _mat_translate(tw['wx'], h / 2, tw['wz']) @ _mat_scale(1.1, h, 1.1)
            prog['u_tint'] = spec['color']
            self.cube.draw(GL_TRIANGLES)

        # 敌人（内核逐帧按 lane_point(dist) 更新「语义位置」，渲染再算世界坐标）
        for eid in k.enemies:
            e = k.ctx.entity.get(eid)
            if e is None:
                continue
            spec = ENEMY_TYPES[e['type']]
            x, z = lane_point(e['dist'])
            rad = spec['radius']
            prog['u_model'] = _mat_translate(x, rad, z) @ _mat_scale(rad * 1.6, rad * 1.6, rad * 1.6)
            prog['u_tint'] = spec['color']
            self.sphere.draw(GL_TRIANGLES)
            # 血条（细长立方体，按血量缩放）
            frac = max(0.0, e['hp'] / e['max_hp'])
            prog['u_model'] = _mat_translate(x, rad * 2.2, z) @ _mat_scale(1.4 * frac + 0.01, 0.12, 0.12)
            prog['u_tint'] = (0.2 + 0.8 * frac, 0.9 * frac, 0.15)
            self.cube.draw(GL_TRIANGLES)

        # 子弹（内核实体，pos 为 (x,y,z) 元组）
        for peid in k.projectiles:
            p = k.ctx.entity.get(peid)
            if p is None:
                continue
            px, py, pz = p['pos']
            prog['u_model'] = _mat_translate(px, py, pz) @ _mat_scale(0.25, 0.25, 0.25)
            prog['u_tint'] = p['color']
            self.cube.draw(GL_TRIANGLES)

        # 基地标记
        bx, bz = BASE
        prog['u_model'] = _mat_translate(bx, 1.0, bz) @ _mat_scale(1.6, 2.0, 1.6)
        prog['u_tint'] = (0.85, 0.85, 0.55)
        self.cube.draw(GL_TRIANGLES)

        # 胜负 / 暂停叠层
        state = r.state()
        if state in ('won', 'lost') or self.paused:
            w, h = self.window.width, self.window.height
            glDisable(GL_DEPTH_TEST)
            self.window.view = Mat4()
            self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -1, 1)
            txt = {'won': '胜 利 !', 'lost': '失 败', 'prep': ''}.get(state, '')
            if self.paused and not txt:
                txt = '暂 停'
            self.overlay.text = txt
            self.overlay.x, self.overlay.y = w // 2, h // 2
            self.overlay.draw()
            self.window.projection = self.perspective
            glEnable(GL_DEPTH_TEST)

    # -------------------------------------------------- HUD --
    def status_text(self):
        sel = TOWER_TYPES[self.selected]['name']
        k = self.kernel
        r = k.ctx.rules
        st = {'prep': '备战', 'wave': '交战', 'won': '胜利', 'lost': '失败'}.get(r.state(), r.state())
        gold = k.ctx.economy.balance('gold')
        extra = ''
        if r.state() == 'prep':
            extra = f'  下一波 {max(0.0, k.prep_until - k.time):.0f}s'
        return (f'金{gold:.0f}  波{r.wave_index + 1}/{r.total_waves} {st}{extra}  '
                f'基地HP{r.base_hp}  选中:{sel}  | 1箭塔 2炮塔 3冰塔 N开波  SPACE暂停  '
                f't={k.time:.0f}s {self.fps:.0f}fps' + ('  [PAUSED]' if self.paused else ''))


if __name__ == '__main__':
    run(TowerDefenseDemo)
