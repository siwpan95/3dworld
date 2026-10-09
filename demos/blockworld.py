# -*- coding: utf-8 -*-
"""blockworld —— 方块世界 · 可交互版（voxel_terrain × minecraft_style 结合）。

把两条既有路线焊在一起，并补齐"可玩性"：

    minecraft_style：程序化世界 / 16×16 像素图集 / 昼夜天空 / 面亮度分级
    voxel_terrain  ：面剔除的实证精神——HUD 实时喂给你面数与剔除率
    scene_walker   ：第一人称物理漫步的经验（固定步长驱动，卡帧不穿透）

本次新增（联网调研结论落地：区块化重建 / DDA 体素射线 / 放置校验）：

    - 第一人称漫步 + 跳跃 + 冲刺（voxel 专用碰撞：AABB 对逐轴解析，
      不走通用物理引擎——玩家只与"附近的方块格子"打交道）

    - 准星 DDA 射线拾取（Amanatides & Woo 步进，reach 6 格）——
      选中方块实时线框描边（放置前的确认反馈）

    - 左键挖掘 / 右键放置：hotbar `1-6` 选方块；放置前校验
      "目标为空 + 不与玩家身体相交"（调研：碰撞校验是放置的必备闸门）

    - **区块化网格重建**：世界按 16 列切成 chunk；改一块只重扫所在区块
      （若在边界，相邻区块一并重建——面剔除跨边界必须保持正确）。
      实测单次编辑重建耗时打印在日志（`[edit] ...`）与 HUD。

键位：WASD 走  SPACE 跳  SHIFT 冲刺  鼠标拖动转视角
      左键挖  右键放  1-6 选方块  C 走/飞切换  P 暂停昼夜  ESC 退出

用法::

    python demos/blockworld.py
    python demos/blockworld.py --smoke 3        # 冒烟
    python demos/blockworld.py --smoke 3 --flag digdemo   # 自检：自动挖/放/重建
"""
import array
import math

import pyglet
from pyglet.gl import (GL_DEPTH_TEST, GL_LINES, GL_NEAREST, GL_TRIANGLES,
                       glClearColor, glEnable)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4, Vec3

from _harness import DemoApp, run
from engine.fixedstep import FixedStepper
from logic import blocks as B

# ---------------------------------------------------------------- 着色器 --
WORLD_VS = '''#version 330 core
in vec3 position;
in vec4 colors;
in vec2 tex_coords;
out vec4 v_col;
out vec2 v_uv;
out float v_dist;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 u_model;
void main() {
    vec4 wp = u_model * vec4(position, 1.0);
    vec4 vp = window.view * wp;
    v_dist = length(vp.xyz);
    v_col = colors;
    v_uv = tex_coords;
    gl_Position = window.projection * vp;
}'''

# 顶点色 rgba：rgb = 面亮度×色调（顶 1.0 / 侧 0.8 / 底 0.5），a = 自发光强度
WORLD_FS = '''#version 330 core
in vec4 v_col;
in vec2 v_uv;
in float v_dist;
out vec4 final_colors;
uniform sampler2D u_atlas;
uniform float u_sky;
uniform vec3  u_fog_color;
uniform float u_fog_near;
uniform float u_fog_far;
uniform float u_scroll;
uniform int   u_cutout;
void main() {
    vec2 uv = v_uv;
    if (u_scroll > 0.0) { uv.y += u_scroll; uv.x += u_scroll * 0.35; }
    vec4 tex = texture(u_atlas, uv);
    if (u_cutout == 1 && tex.a < 0.5) { discard; }
    vec3 lit = tex.rgb * (v_col.rgb * u_sky + v_col.a);
    lit = mix(lit, u_fog_color, smoothstep(u_fog_near, u_fog_far, v_dist));
    final_colors = vec4(lit, tex.a);
}'''

SKY_VS = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec2 v_uv;
void main() {
    v_uv = position.xy * 0.5 + 0.5;
    gl_Position = vec4(position.xy, 0.0, 1.0);
}'''

SKY_FS = '''#version 330 core
in vec2 v_uv;
out vec4 final_colors;
uniform vec3  u_sky;
uniform vec3  u_horizon;
uniform float u_stars;
float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
void main() {
    float t = pow(clamp(v_uv.y, 0.0, 1.0), 0.65);
    vec3 col = mix(u_horizon, u_sky, t);
    if (u_stars > 0.01) {
        vec2 g = floor(v_uv * vec2(220.0, 130.0));
        float h = hash(g);
        float star = step(0.9955, h) * (0.55 + 0.45 * hash(g + 7.7));
        col += vec3(star) * u_stars * (1.0 - t);
    }
    final_colors = vec4(col, 1.0);
}'''

# 选中框：单位盒线框（相对坐标 + u_origin，每帧只换一个 uniform）
LINE_VS = '''#version 330 core
in vec3 position;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform vec3 u_origin;
void main() {
    gl_Position = window.projection * window.view * vec4(position + u_origin, 1.0);
}'''

LINE_FS = '''#version 330 core
out vec4 final_colors;
uniform vec4 u_color;
void main() { final_colors = u_color; }'''

_E = 1.004                      # 微扩，避免与方块面 z-fighting
_BOX_LINES = []
for _i in range(8):
    _x, _y, _z = _i & 1, (_i >> 1) & 1, (_i >> 2) & 1
    _BOX_LINES.append((_x, _y, _z))
_LINKS = ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3),
          (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7))
_BOX_VERTS = []
for _a, _b in _LINKS:
    _BOX_VERTS.append(_BOX_LINES[_a])
    _BOX_VERTS.append(_BOX_LINES[_b])


class BlockWorldApp(DemoApp):
    TITLE = 'blockworld · 方块世界（可交互：漫步 / 挖掘 / 放置）'
    HELP = ('WASD 走  SPACE 跳  SHIFT 冲刺  左键挖  右键放  1-6 方块  '
            'C 走/飞  P 暂停昼夜  鼠标拖动转视角')
    FOV = 60.0
    Z_NEAR, Z_FAR = 0.15, 420.0
    SHOTS = ((2.0, 'docs/blockworld_01.png'),)

    CHUNK = 16                 # 区块边长（列）：调研推荐值（Minecraft 标准）
    REACH = 6.0                # 方块拾取距离（MC 手感值）
    DAY_SECONDS = 180.0        # 一昼夜时长
    WALK_SPEED = 5.2
    SPRINT = 1.8
    JUMP_V = 7.8
    EYE = 1.62                 # 眼高（MC 同款 1.62）
    HALF = (0.3, 0.9, 0.3)     # 玩家半宽/半高（MC 0.6 宽 × 1.8 高）
    HOTBAR = (B.GRASS, B.DIRT, B.STONE, B.SAND, B.LOG, B.GLASS)
    HOTBAR_NAME = ('草方块', '泥土', '石头', '沙子', '原木', '玻璃')

    def __init__(self, width, height, args=None):
        self.walk = True                   # 默认第一人称；C 切自由飞
        self.yaw, self.pitch = 135.0, -10.0
        self._keys = set()
        self.px = self.py = self.pz = 36.5
        self.vy = 0.0
        self.on_ground = False
        self.slot = 0
        self.sel = None                    # (ix,iy,iz, normal) 选中方块
        self.day_phase = 0.34              # 白天开始（0.25=正午）
        self.paused_day = False
        self._dig_t = None                 # --flag digdemo 自检
        self._edit_ms = 0.0
        super().__init__(width, height, args=args)

    # ------------------------------------------------------------ setup --
    def setup(self):
        glEnable(GL_DEPTH_TEST)
        glClearColor(0.0, 0.0, 0.0, 1.0)

        self.world_prog = ShaderProgram(Shader(WORLD_VS, 'vertex'),
                                        Shader(WORLD_FS, 'fragment'))
        self.sky_prog = ShaderProgram(Shader(SKY_VS, 'vertex'),
                                      Shader(SKY_FS, 'fragment'))
        self.line_prog = ShaderProgram(Shader(LINE_VS, 'vertex'),
                                       Shader(LINE_FS, 'fragment'))

        # --- 图集（像素在 logic 层现算，这里只上传）---
        w, h, px, self.tile_index = B.atlas_pixels()
        img = pyglet.image.ImageData(w, h, 'RGBA', px)
        self.atlas = img.get_texture()
        self.atlas.min_filter = GL_NEAREST
        self.atlas.mag_filter = GL_NEAREST
        self.tile_count = float(len(self.tile_index))

        # --- 世界与区块网格（16 列一块；初始逐块建）---
        # logic 层返回不可变 bytes（供演示动画的确定性）；可编辑版在此转 bytearray
        raw_blocks, self.heights, self.trees, self.houses = B.generate_world()
        self.blocks = bytearray(raw_blocks)
        self.n = B.WORLD_W
        self.chunks = {}                   # (cx,cz) -> {'terrain': vl, 'water': vl, 'faces': n}
        import time as _t
        t0 = _t.perf_counter()
        for cz in range((self.n + self.CHUNK - 1) // self.CHUNK):
            for cx in range((self.n + self.CHUNK - 1) // self.CHUNK):
                self._rebuild_chunk(cx, cz)
        build_ms = (_t.perf_counter() - t0) * 1000.0
        nfaces = sum(c['faces'] for c in self.chunks.values())
        print(f'[blockworld] chunks={len(self.chunks)} faces={nfaces} '
              f'build={build_ms:.0f}ms', flush=True)

        # --- 天空 / 太阳 / 月亮 ---
        quad = (-1.0, -1.0, 0.0, 1.0, -1.0, 0.0, 1.0, 1.0, 0.0,
                -1.0, -1.0, 0.0, 1.0, 1.0, 0.0, -1.0, 1.0, 0.0)
        self.sky_quad = self.sky_prog.vertex_list(
            6, GL_TRIANGLES, position=('f', quad), colors=('f', (1.0,) * 24))
        self.sun = self._mesh_list('cloud', (9.0, 9.0, 0.6), (1.0, 0.93, 0.60))
        self.moon = self._mesh_list('cloud', (5.5, 5.5, 0.6), (0.88, 0.91, 1.0))

        # --- 选中框线（12 边，a 面微扩）---
        flat = []
        for (x, y, z) in _BOX_VERTS:
            flat.extend((x * _E - (_E - 1) * 0.5, y * _E - (_E - 1) * 0.5,
                         z * _E - (_E - 1) * 0.5))
        self.sel_lines = self.line_prog.vertex_list(
            len(_BOX_VERTS), GL_LINES, position=('f', flat))

        # --- 出生点：村庄中心附近找一块平地 ---
        self._find_spawn()

        # --- 相机（自由飞用的朝向容器；第一人称直接用它）---
        from engine.camera import FlyCamera
        self.eye = FlyCamera(position=(self.px, self.py + self.EYE, self.pz),
                             fov=self.FOV, far=self.Z_FAR)

        # --- 物理：固定步长驱动（voxel 专用碰撞）---
        self.stepper = FixedStepper(lambda dt: self._step(dt),
                                    fixed_dt=1.0 / 60.0)
        self.on_mouse_scroll = self._on_scroll
        self.on_mouse_drag = self._on_mouse_drag_impl
        self.window.push_handlers(on_key_release=self._on_key_release,
                                  on_mouse_press=self.on_mouse_press)
        self.sky_state = B.sky_state(self.day_phase)

        if 'digdemo' in self.args.flags:   # 自检：3.2s 后自动挖脚下的方块
            self._dig_t = 3.2
        if 'topdown' in self.args.flags:   # 诊断：高空俯瞰（验证世界/玩家相对位置）
            self.walk = False
            self.px, self.py, self.pz = 36.5, 40.0, 36.5
            self.yaw, self.pitch = 90.0, -62.0
            self.eye.position = (self.px, self.py, self.pz)

    def _mesh_list(self, tile, size, tint, offset=(0.0, 0.0, 0.0)):
        p, c, u = B.box_mesh(self.tile_index[tile], len(self.tile_index),
                             size, offset, tint)
        return self.world_prog.vertex_list(len(p) // 3, GL_TRIANGLES,
                                           position=('f', p), colors=('f', c),
                                           tex_coords=('f', u))

    def _find_spawn(self):
        """村庄附近找一块开阔平地出生：5×5 同高、头顶 4 格净空、顶面是草；
        并选一个视线最开阔的朝向（避免睁眼就是墙面）。"""
        cx, cz = self.n // 2, self.n // 2
        best = None
        for r in range(0, 18):
            for dz in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    x, z = cx + dx, cz + dz
                    if not (3 <= x < self.n - 3 and 3 <= z < self.n - 3):
                        continue
                    h0 = self.heights[z * self.n + x]
                    if h0 <= B.WATER_LEVEL + 1:
                        continue                     # 别生在水线附近
                    top = self.blocks[(h0 * self.n + z) * self.n + x]
                    if top != B.GRASS:
                        continue                     # 顶面须是草（排除屋顶/沙地）
                    ok = True
                    for j in range(-2, 3):
                        for k in range(-2, 3):
                            xx, zz = x + j, z + k
                            if self.heights[zz * self.n + xx] != h0:
                                ok = False
                                break
                            for y in range(h0 + 1, h0 + 5):
                                if self.blocks[(y * self.n + zz) * self.n + xx] != B.AIR:
                                    ok = False
                                    break
                            if not ok:
                                break
                        if not ok:
                            break
                    if ok:
                        best = (x, h0, z)
                        break
                if best:
                    break
            if best:
                break
        if best is None:
            best = (cx, self.heights[cz * self.n + cx], cz)
        self.px, self.pz = best[0] + 0.5, best[2] + 0.5
        self.py = best[1] + 1.95        # 脚 = 顶面 + 0.05（自由落下贴地）
        # 视线最开阔的朝向（0.5 格内不被挡）
        for cand in (135.0, 225.0, 45.0, 315.0, 180.0, 0.0, 90.0, 270.0):
            self.yaw = cand
            self.pitch = -8.0
            if self._raycast(reach=14.0) is None:
                break
        print(f'[blockworld] spawn=({self.px:.1f},{self.py:.1f},{self.pz:.1f}) '
              f'yaw={self.yaw:.0f} h0={best[1]}', flush=True)

    # ----------------------------------------------------- 网格（区块化）--
    def _chunk_of(self, x, z):
        return x // self.CHUNK, z // self.CHUNK

    def _rebuild_chunk(self, cx, cz):
        """重扫一个区块的面（含水面）；数组追加到独立缓冲再上传。"""
        c = self.chunks.get((cx, cz))
        if c is not None:
            if c['terrain'] is not None:
                c['terrain'].delete()
            if c['water'] is not None:
                c['water'].delete()
        x0, x1 = cx * self.CHUNK, min(self.n, (cx + 1) * self.CHUNK)
        z0, z1 = cz * self.CHUNK, min(self.n, (cz + 1) * self.CHUNK)
        pos, col, uv = array.array('f'), array.array('f'), array.array('f')
        faces, _ = B.mesh_world(self.blocks, self.tile_index, pos=pos,
                                col=col, uv=uv, x0=x0, x1=x1, z0=z0, z1=z1)
        terrain = self.world_prog.vertex_list(
            len(pos) // 3, GL_TRIANGLES, position=('f', pos),
            colors=('f', col), tex_coords=('f', uv))
        wpos, wcol, wuv = array.array('f'), array.array('f'), array.array('f')
        wfaces, _ = B.mesh_world(self.blocks, self.tile_index, want_water=True,
                                 pos=wpos, col=wcol, uv=wuv,
                                 x0=x0, x1=x1, z0=z0, z1=z1)
        water = self.world_prog.vertex_list(
            len(wpos) // 3, GL_TRIANGLES, position=('f', wpos),
            colors=('f', wcol), tex_coords=('f', wuv))
        self.chunks[(cx, cz)] = {'terrain': terrain, 'water': water,
                                 'faces': faces, 'wfaces': wfaces}

    def _rebuild_around(self, x, y, z):
        """改一块后重建所在区块；贴边时相邻区块一并重建（面剔除跨界正确）。"""
        import time as _t
        t0 = _t.perf_counter()
        cx, cz = self._chunk_of(x, z)
        todo = {(cx, cz)}
        if x % self.CHUNK == 0:
            todo.add((cx - 1, cz))
        if x % self.CHUNK == self.CHUNK - 1:
            todo.add((cx + 1, cz))
        if z % self.CHUNK == 0:
            todo.add((cx, cz - 1))
        if z % self.CHUNK == self.CHUNK - 1:
            todo.add((cx, cz + 1))
        for (a, b) in todo:
            if (a, b) in self.chunks:
                self._rebuild_chunk(a, b)
        self._edit_ms = (_t.perf_counter() - t0) * 1000.0
        return self._edit_ms

    # ---------------------------------------------------------- voxel 物理 --
    def _solid(self, x, y, z):
        if not (0 <= x < self.n and 0 <= y < B.MAX_H and 0 <= z < self.n):
            return False
        b = self.blocks[(y * self.n + z) * self.n + x]
        return b != B.AIR and b != B.WATER

    def _aabb_hit(self):
        """玩家 AABB 是否与实心方块重叠（按覆盖格扫描）。"""
        hx, hy, hz = self.HALF
        x0, x1 = int(math.floor(self.px - hx)), int(math.floor(self.px + hx))
        y0, y1 = int(math.floor(self.py - hy)), int(math.floor(self.py + hy))
        z0, z1 = int(math.floor(self.pz - hz)), int(math.floor(self.pz + hz))
        for y in range(y0, y1 + 1):
            for z in range(z0, z1 + 1):
                for x in range(x0, x1 + 1):
                    if self._solid(x, y, z):
                        return True
        return False

    def _step(self, dt):
        """固定步长物理：重力 + 分轴移动（撞了就回退该轴）。"""
        if not self.walk:
            return
        g = -24.0
        self.vy = max(-40.0, self.vy + g * dt)
        # 水平速度（朝向 yaw）
        ks = self._keys
        f = (1.0 if 'W' in ks else 0.0) - (1.0 if 'S' in ks else 0.0)
        r = (1.0 if 'D' in ks else 0.0) - (1.0 if 'A' in ks else 0.0)
        yr = math.radians(self.yaw)
        fx, fz = math.cos(yr), math.sin(yr)
        rx, rz = -fz, fx
        sp = self.WALK_SPEED * (self.SPRINT if 'SHIFT' in ks else 1.0)
        vx, vz = f * fx + r * rx, f * fz + r * rz
        ln = math.hypot(vx, vz)
        if ln > 1e-6:
            vx, vz = vx / ln * sp, vz / ln * sp
        else:
            vx = vz = 0.0
        # 分轴推进 + 碰撞回退
        for axis, d in ((0, vx * dt), (2, vz * dt), (1, self.vy * dt)):
            old = (self.px, self.py, self.pz)
            if axis == 0:
                self.px += d
                if self._aabb_hit():
                    self.px = old[0]
            elif axis == 2:
                self.pz += d
                if self._aabb_hit():
                    self.pz = old[2]
            else:
                self.py += d
                if self._aabb_hit():
                    if self.vy <= 0.0:
                        # 落到方块顶面：脚 = floor(脚) + 1
                        self.py = math.floor(self.py - self.HALF[1]) + 1 + self.HALF[1]
                        self.on_ground = True
                    else:
                        # 上撞天花板：头顶贴到方块底面
                        self.py = math.floor(self.py + self.HALF[1]) - self.HALF[1]
                    self.vy = 0.0
                else:
                    self.on_ground = False
        # 贴地检测（向下 2cm 探针）
        if self.vy <= 0.0:
            y0 = int(math.floor(self.py - self.HALF[1] - 0.02))
            hx, hz = self.HALF[0], self.HALF[2]
            for z in range(int(math.floor(self.pz - hz)), int(math.floor(self.pz + hz)) + 1):
                for x in range(int(math.floor(self.px - hx)), int(math.floor(self.px + hx)) + 1):
                    if self._solid(x, y0, z):
                        self.on_ground = True
                        break
                if self.on_ground:
                    break

    # ------------------------------------------------------------ 射线 --
    def _forward(self):
        y = math.radians(self.yaw)
        p = math.radians(self.pitch)
        return (math.cos(p) * math.cos(y), math.sin(p), math.cos(p) * math.sin(y))

    def _raycast(self, reach=None):
        """DDA 体素步进（Amanatides & Woo）：返回 (x,y,z, nx,ny,nz) 或 None。"""
        reach = self.REACH if reach is None else reach
        ox, oy, oz = self.px, self.py + self.EYE, self.pz
        dx, dy, dz = self._forward()
        ix, iy, iz = int(math.floor(ox)), int(math.floor(oy)), int(math.floor(oz))
        sx = 1 if dx > 0 else (-1 if dx < 0 else 0)
        sy = 1 if dy > 0 else (-1 if dy < 0 else 0)
        sz = 1 if dz > 0 else (-1 if dz < 0 else 0)
        inf = float('inf')
        tdx = abs(1.0 / dx) if dx else inf
        tdy = abs(1.0 / dy) if dy else inf
        tdz = abs(1.0 / dz) if dz else inf
        tmx = ((ix + 1 - ox) / dx if dx > 0 else
               ((ix - ox) / dx if dx < 0 else inf))
        tmy = ((iy + 1 - oy) / dy if dy > 0 else
               ((iy - oy) / dy if dy < 0 else inf))
        tmz = ((iz + 1 - oz) / dz if dz > 0 else
               ((iz - oz) / dz if dz < 0 else inf))
        n = (0, 0, 0)
        t = 0.0
        while t <= reach:
            if self._solid(ix, iy, iz):
                return (ix, iy, iz, n[0], n[1], n[2])
            if tmx <= tmy and tmx <= tmz:
                ix += sx
                t = tmx
                tmx += tdx
                n = (-sx, 0, 0)
            elif tmy <= tmz:
                iy += sy
                t = tmy
                tmy += tdy
                n = (0, -sy, 0)
            else:
                iz += sz
                t = tmz
                tmz += tdz
                n = (0, 0, -sz)
        return None

    # ------------------------------------------------------------ 交互 --
    def _dig(self):
        """挖掉选中方块（校验：在边界内）。"""
        if self.sel is None:
            return False
        x, y, z = self.sel[0], self.sel[1], self.sel[2]
        if not (0 <= x < self.n and 0 <= z < self.n and 0 <= y < B.MAX_H):
            return False
        old = self.blocks[(y * self.n + z) * self.n + x]
        if old == B.AIR:
            return False
        self.blocks[(y * self.n + z) * self.n + x] = B.AIR
        ms = self._rebuild_around(x, y, z)
        print(f'[edit] dig ({x},{y},{z}) block={old} rebuild={ms:.1f}ms',
              flush=True)
        return True

    def _place(self):
        """放置 hotbar 当前方块：目标空 + 不与玩家 AABB 相交。"""
        if self.sel is None:
            return False
        x = self.sel[0] + self.sel[3]
        y = self.sel[1] + self.sel[4]
        z = self.sel[2] + self.sel[5]
        if not (0 <= x < self.n and 0 <= z < self.n and 0 <= y < B.MAX_H):
            return False
        idx = (y * self.n + z) * self.n + x
        if self.blocks[idx] not in (B.AIR, B.WATER):
            return False
        # 放置校验：新方块 AABB 不得与玩家相交（调研要点）
        hx, hy, hz = self.HALF
        if (self.px - hx < x + 1 and self.px + hx > x
                and self.py - hy < y + 1 and self.py + hy > y
                and self.pz - hz < z + 1 and self.pz + hz > z):
            self._msg = '不能放在自己身上'
            return False
        self.blocks[idx] = self.HOTBAR[self.slot]
        ms = self._rebuild_around(x, y, z)
        print(f'[edit] place ({x},{y},{z}) block={self.HOTBAR[self.slot]} '
              f'rebuild={ms:.1f}ms', flush=True)
        return True

    def on_mouse_press(self, x, y, button, modifiers):
        if button == pyglet.window.mouse.LEFT:
            self._dig()
        elif button == pyglet.window.mouse.RIGHT:
            self._place()

    def _on_scroll(self, x, y, sx, sy):
        self.slot = (self.slot + (1 if sy < 0 else -1)) % len(self.HOTBAR)

    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        name = getattr(key, 'symbol_string', lambda s: '')(symbol)
        if name in ('W', 'A', 'S', 'D', 'SHIFT', 'LSHIFT', 'RSHIFT'):
            self._keys.add('SHIFT' if 'SHIFT' in name else name)
            return
        if name in ('_1', '_2', '_3', '_4', '_5', '_6', '1', '2', '3',
                    '4', '5', '6'):
            digits = '123456'
            for i, d in enumerate(digits):
                if name.endswith(d):
                    self.slot = i
                    break
        elif name == 'C':
            self.walk = not self.walk
        elif name == 'P':
            self.paused_day = not self.paused_day

    def _on_key_release(self, symbol, modifiers):
        key = pyglet.window.key
        name = getattr(key, 'symbol_string', lambda s: '')(symbol)
        self._keys.discard(name)
        if 'SHIFT' in name:
            self._keys.discard('SHIFT')

    def _on_key_press(self, symbol, modifiers):
        if symbol == pyglet.window.key.SPACE and self.walk and self.on_ground:
            self.vy = self.JUMP_V
            self.on_ground = False
            return
        super()._on_key_press(symbol, modifiers)

    # ------------------------------------------------------------ 主循环 --
    def _update_impl(self, dt):
        if self._dig_t is not None and self.time >= self._dig_t:
            self._dig_t = None
            # 1) 低头看向脚下地形，验证 DDA 命中
            self.pitch = -70.0
            hit = self._raycast()
            print(f'[digdemo] ray hit={hit}', flush=True)
            # 2) 挖→放闭环：脚下找第一块实心地形，挖掉再放回
            bx = int(math.floor(self.px))
            bz = int(math.floor(self.pz))
            target = None
            for dy in range(1, 10):
                yy = int(math.floor(self.py)) - dy
                if self._solid(bx, yy, bz):
                    target = (bx, yy, bz)
                    break
            if target is not None:
                self.sel = (target[0], target[1], target[2], 0, 0, 0)
                ok_d = self._dig()
                self.sel = (target[0], target[1], target[2], 0, 0, 0)
                ok_p = self._place()
                print(f'[digdemo] dig={ok_d} place={ok_p}', flush=True)
            else:
                print('[digdemo] no solid under feet', flush=True)
            self.pitch = -10.0
        if self.walk:
            self.stepper.advance(dt)
            self.eye.position = (self.px, self.py + self.EYE, self.pz)
        else:
            # 自由飞：直接平移 eye
            sp = self.WALK_SPEED * 4 * dt
            ks = self._keys
            fwd = self._forward()
            mv = [0.0, 0.0, 0.0]
            if 'W' in ks:
                mv = [mv[i] + fwd[i] * sp for i in range(3)]
            if 'S' in ks:
                mv = [mv[i] - fwd[i] * sp for i in range(3)]
            self.px += mv[0]
            self.py += mv[1]
            self.pz += mv[2]
            self.eye.position = (self.px, self.py, self.pz)
        # 渲染相机朝向同步（关键：否则渲染与射线/移动朝向不一致）
        self.eye.yaw = self.yaw
        self.eye.pitch = self.pitch
        if not self.paused_day:
            self.day_phase = (self.day_phase + dt / self.DAY_SECONDS) % 1.0
        self.sky_state = B.sky_state(self.day_phase)
        self.sel = self._raycast()

    def update(self, dt):
        try:
            self._update_impl(dt)
        except Exception:
            import traceback
            print('[update] EXC:\n' + traceback.format_exc(), flush=True)

    def _on_mouse_drag_impl(self, x, y, dx, dy, buttons, modifiers):
        self.yaw += dx * 0.28
        self.pitch = max(-89.0, min(89.0, self.pitch - dy * 0.28))

    def render(self):
        w, h = self.window.width, self.window.height
        self.eye.update(0.0)
        self.window.view = self.eye.view_matrix()
        # 天空（先画，深度关）
        from pyglet.gl import glDepthMask, GL_FALSE, GL_TRUE, GL_BLEND, \
            glBlendFunc, GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, glDisable
        glDisable(GL_DEPTH_TEST)
        st = self.sky_state
        self.sky_prog.use()
        self.sky_prog['u_sky'] = st['sky']
        self.sky_prog['u_horizon'] = st['horizon']
        self.sky_prog['u_stars'] = st['stars']
        self.sky_quad.draw(GL_TRIANGLES)
        glEnable(GL_DEPTH_TEST)

        # 世界
        self.world_prog.use()
        self.world_prog['u_atlas'] = 0
        self.atlas.bind()
        self.world_prog['u_model'] = Mat4()
        self.world_prog['u_sky'] = st['ambient']
        self.world_prog['u_fog_color'] = tuple(st['horizon'])
        self.world_prog['u_fog_near'] = 90.0
        self.world_prog['u_fog_far'] = 300.0
        self.world_prog['u_scroll'] = (self.time * 0.03) % 1.0
        self.world_prog['u_cutout'] = 0
        for c in self.chunks.values():
            c['terrain'].draw(GL_TRIANGLES)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDepthMask(GL_FALSE)
        self.world_prog['u_cutout'] = 1
        for c in self.chunks.values():
            c['water'].draw(GL_TRIANGLES)
        glDepthMask(GL_TRUE)

        # 太阳 / 月亮
        sun = st['sun']
        for vl, vis, dist, dirv in ((self.sun, st['sun_visible'], 160.0, sun),
                                    (self.moon, st['moon_visible'], 150.0,
                                     tuple(-c for c in sun))):
            if vis:
                self.world_prog['u_model'] = Mat4.from_translation(
                    Vec3(self.px + dirv[0] * dist, self.py + dirv[1] * dist,
                         self.pz + dirv[2] * dist))
                vl.draw(GL_TRIANGLES)

        # 选中框
        if self.sel is not None:
            self.line_prog.use()
            self.line_prog['u_origin'] = (self.sel[0], self.sel[1], self.sel[2])
            self.line_prog['u_color'] = (1.0, 0.95, 0.5, 1.0)
            self.sel_lines.draw(GL_LINES)

    def status_text(self):
        base = super().status_text()
        b = self.blocks[(int(math.floor(self.py)) * self.n
                         + int(math.floor(self.pz))) * self.n
                        + int(math.floor(self.px))] \
            if 0 <= int(self.py) < B.MAX_H else 0
        sel = ('-' if self.sel is None else
               f'({self.sel[0]},{self.sel[1]},{self.sel[2]})')
        mode = '走' if self.walk else '飞'
        return (f'{base}   [{mode}] pos=({self.px:5.1f},{self.py:4.1f},{self.pz:5.1f})'
                f'  sel={sel}  方块[{self.slot + 1}]={self.HOTBAR_NAME[self.slot]}'
                f'  edit={self._edit_ms:.0f}ms  {self.fps:4.1f} fps')


def main():
    run(BlockWorldApp)


if __name__ == '__main__':
    main()
