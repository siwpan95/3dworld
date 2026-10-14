# -*- coding: utf-8 -*-
"""阈廊 / Liminall 场景渲染器（KKRenderer 同款范式：程序化几何 + 材质 uniform + 雾）。

复用 ``presentation.kkrieger_scene`` 的 shader/工具（box_data/_hash01），场景换成：

- **L 形循环走廊**（后室装修：单黄壁纸 / 潮湿地毯 / 荧光灯管 / 浴室门 / 镜柜 /
  电话 / 尽头门）——几何一次建网；
- **后室迷宫**（7×7 房间格，内墙按 ``logic.liminall.maze_layout`` 的墙段建）；
- **装修变体层**（8 圈差异全部走可显隐对象 + tint + 雾色 uniform：
  椅子 / 垂灯 / 涂鸦 / 红壁纸门 / 实体黑影 / 手电）。

显隐实现：每个变体对象保存**原始顶点**，隐藏 = 整体挪到地下（y=-50），
显示 = 写回原始顶点（pyglet VertexList 无 enabled 开关的兼容写法）。
恐怖感三件套：雾色随圈变化（红潮圈全场景变红）、变体显隐（"上一圈没有的东西"）、
手电提亮 uniform。
"""
from __future__ import annotations

import pyglet
from pyglet.gl import GL_DEPTH_TEST, GL_TRIANGLES, glEnable, glClearColor
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4

from world3d.core.geometry import box_data
from world3d.logic.liminall import HALL_A_LEN, HALL_B_LEN, HALL_H, HALL_W, \
    MAZE_CELL, MAZE_GRID, maze_layout
from world3d.presentation.kkrieger_scene import KK_VERTEX_SRC, _hash01

FOG_BASE = (0.020, 0.020, 0.018)
FOG_RED = (0.085, 0.008, 0.006)
FOG_NEAR, FOG_FAR = 3.0, 30.0

# 阈廊专用 fragment：kkrieger 同款 + u_light（手电提亮——照进黑暗的那一束）
LIM_FRAGMENT_SRC = '''#version 330 core
in vec4 v_color;
in float v_dist;
out vec4 final_colors;
uniform vec3 fog_color;
uniform float fog_near;
uniform float fog_far;
uniform float u_light;
void main() {
    float f = smoothstep(fog_near, fog_far, v_dist);
    vec3 lit = v_color.rgb * (1.0 + u_light * 0.85);
    final_colors = vec4(mix(lit, fog_color, f), v_color.a);
}'''

# 后室配色（单黄潮闷）
WALL_Y = (0.42, 0.37, 0.16)
CARPET = (0.30, 0.27, 0.14)
CEIL = (0.34, 0.33, 0.28)
DOOR_DARK = (0.16, 0.13, 0.07)
TINT_DEEPWALL = (0.10, 0.09, 0.08)
TINT_GRAFFITI = (0.85, 0.80, 0.55)
TINT_ENTITY = (0.045, 0.045, 0.05)
TINT_PHONE = (0.12, 0.12, 0.13)

_IDENT = Mat4()


def _ywall(i, j):
    """黄壁纸：基准色 ± 哈希抖动（潮渍暗斑）。"""
    k = _hash01(i, j)
    damp = 1.0 if _hash01(j, i * 3) > 0.82 else 0.0
    out = tuple(c * (0.85 + 0.3 * k) for c in WALL_Y)
    if damp:
        out = tuple(c * 0.55 for c in out)
    return out


class LimRenderer:
    """阈廊场景渲染器（走廊 + 迷宫一次建网，变体层可显隐/换 tint）。"""

    def __init__(self, kernel, window, caption='阈廊 / Liminall — 后室循环走廊'):
        self.kernel = kernel
        self.window = window                     # 窗口由宿主 App（EngineApp）提供
        self.program = ShaderProgram(
            Shader(KK_VERTEX_SRC, 'vertex'), Shader(LIM_FRAGMENT_SRC, 'fragment'))
        glEnable(GL_DEPTH_TEST)

        self._sv, self._sc = [], []
        self.variants = {}              # name -> dict(vl, tint, orig, visible)

        self._build_hall()
        self._build_maze()
        self._build_variant_props()
        self.static = self.program.vertex_list(
            len(self._sv) // 3, GL_TRIANGLES,
            position=('f', self._sv), colors=('f', self._sc))
        self._sv = self._sc = None

    # ---------------------------------------------------- 几何积攒 --
    def _add_box(self, sx, sy, sz, ox, oy, oz, rgb):
        v, c = box_data(sx, sy, sz, ox, oy, oz, rgb)
        self._sv += v
        self._sc += c

    def _add_variant(self, name, boxes, tint=(1, 1, 1)):
        """变体对象：一组 [(sx,sy,sz,ox,oy,oz)]，初始隐藏。"""
        v, c = [], []
        for (sx, sy, sz, ox, oy, oz) in boxes:
            vv, cc = box_data(sx, sy, sz, ox, oy, oz, (1, 1, 1))
            v += vv
            c += cc
        vl = self.program.vertex_list(len(v) // 3, GL_TRIANGLES,
                                      position=('f', v), colors=('f', c))
        self.variants[name] = dict(vl=vl, tint=tint, orig=list(v), visible=False)
        self._hide(name)

    def _hide(self, name):
        vl = self.variants[name]['vl']
        n = len(vl.position)
        vl.position[:] = [0.0, -60.0, 0.0] * (n // 3)

    def _show(self, name):
        vl = self.variants[name]['vl']
        vl.position[:] = self.variants[name]['orig']

    def set_visible(self, name, visible):
        d = self.variants.get(name)
        if d is None or d['visible'] == visible:
            return
        d['visible'] = visible
        (self._show if visible else self._hide)(name)

    def move_variant(self, name, dx, dz):
        """整体平移（实体 stalk 位姿用；增量式）。"""
        d = self.variants.get(name)
        if d is None:
            return
        pos = d['vl'].position
        out = []
        for i in range(0, len(pos), 3):
            out += [pos[i] + dx, pos[i + 1], pos[i + 2] + dz]
        d['vl'].position[:] = out

    def reset_variant_pos(self, name):
        d = self.variants.get(name)
        if d is not None:
            d['vl'].position[:] = d['orig']

    # ---------------------------------------------------- 场景构建 --
    def _build_hall(self):
        """L 形走廊：A 段沿 -z（x∈±1.2），B 段沿 -x（z∈±1.2），高 3。"""
        hw, hh = HALL_W / 2, HALL_H
        n = int(HALL_A_LEN)
        for iz in range(n):
            zc = HALL_A_LEN - iz - 0.5
            self._add_box(HALL_W, 0.2, 1.0, 0.0, -0.1, zc, _ywall(0, iz))
            self._add_box(HALL_W, 0.2, 1.0, 0.0, hh - 0.1, zc, CEIL)
            self._add_box(0.2, hh, 1.0, -hw - 0.1, hh / 2, zc, _ywall(1, iz))
            self._add_box(0.2, hh, 1.0, hw + 0.1, hh / 2, zc, _ywall(2, iz))
        m = int(HALL_B_LEN)
        for ix in range(m):
            xc = -ix - 0.5
            self._add_box(1.0, 0.2, HALL_W, xc, -0.1, 0.0, _ywall(3, ix))
            self._add_box(1.0, 0.2, HALL_W, xc, hh - 0.1, 0.0, CEIL)
            self._add_box(1.0, hh, 0.2, xc, hh / 2, hw + 0.1, _ywall(4, ix))
            self._add_box(1.0, hh, 0.2, xc, hh / 2, -hw - 0.1, _ywall(5, ix))
        # 起点小间 + 尽头门框 + 壁挂电话（A 段中段）
        self._add_box(HALL_W, 0.2, 1.0, 0.0, -0.1, HALL_A_LEN + 0.5, CARPET)
        self._add_box(HALL_W, 0.2, 1.0, 0.0, hh - 0.1, HALL_A_LEN + 0.5, CEIL)
        self._add_box(0.15, 2.2, 1.1, -HALL_B_LEN + 0.08, 1.1, 0.0, DOOR_DARK)
        self._add_box(0.2, 0.35, 0.22, 1.1, 1.35, 4.0, TINT_PHONE)

    def _build_maze(self):
        """后室迷宫：GRID×GRID 房间格（每格 4m），内墙段 + 边界墙。"""
        k = self.kernel
        walls, _open, _start, _far = maze_layout(k.seed)
        cell = MAZE_CELL
        half = MAZE_GRID * cell / 2
        self._add_box(MAZE_GRID * cell, 0.2, MAZE_GRID * cell, 0.0, -0.1, 0.0, CARPET)
        self._add_box(MAZE_GRID * cell, 0.2, MAZE_GRID * cell,
                      0.0, HALL_H - 0.1, 0.0, CEIL)
        for s in range(MAZE_GRID):
            c = -half + s * cell + cell / 2
            self._add_box(cell, HALL_H, 0.25, c, HALL_H / 2, -half - 0.12, _ywall(6, s))
            self._add_box(cell, HALL_H, 0.25, c, HALL_H / 2, half + 0.12, _ywall(7, s))
            self._add_box(0.25, HALL_H, cell, -half - 0.12, HALL_H / 2, c, _ywall(8, s))
            self._add_box(0.25, HALL_H, cell, half + 0.12, HALL_H / 2, c, _ywall(9, s))
        for ((i, j), side) in sorted(walls):
            cx = -half + j * cell + cell / 2
            cz = -half + i * cell + cell / 2
            if side == 'W':
                self._add_box(0.22, HALL_H, cell, cx - cell / 2, HALL_H / 2, cz,
                              _ywall(10 + i, j))
            else:
                self._add_box(cell, HALL_H, 0.22, cx, HALL_H / 2, cz - cell / 2,
                              _ywall(11 + i, j))

    def _build_variant_props(self):
        """装修变体层（显隐/tint 由 apply_variant 驱动）。"""
        self._add_variant('chair', [
            (0.5, 0.08, 0.5, 0.0, 0.45, 1.2), (0.5, 0.6, 0.08, 0.0, 0.78, 0.9),
            (0.06, 0.45, 0.06, -0.2, 0.22, 1.35), (0.06, 0.45, 0.06, 0.2, 0.22, 1.35)],
            tint=(0.35, 0.22, 0.12))
        self._add_variant('droplamp', [
            (0.08, 0.9, 0.08, 0.5, 2.6, 6.0), (0.9, 0.07, 0.14, 0.7, 2.1, 6.05)],
            tint=(0.85, 0.85, 0.7))
        # 走廊涂鸦（谜题 1 暗示）
        self._add_variant('graffiti', [(0.06, 0.5, 0.9, 1.13, 1.5, 8.0)],
                          tint=TINT_GRAFFITI)
        # 迷宫 3 块涂鸦（谜题 2 素材格——内墙面上）
        half = MAZE_GRID * MAZE_CELL / 2
        k = self.kernel
        boxes = []
        for idx, (i, j) in enumerate(k.maze_far):
            cx = (j + 0.5) * MAZE_CELL - half
            cz = (i + 0.5) * MAZE_CELL - half
            boxes.append((0.06, 0.5, 0.9, cx - MAZE_CELL / 2 + 0.13, 1.5, cz))
        self._add_variant('marks', boxes, tint=TINT_GRAFFITI)
        # 红壁纸禁入门（迷宫北缘闪现）+ 深墙（谜题 2 noclip 出口，迷宫东缘）
        self._add_variant('reddoor', [(0.1, 2.1, 1.0, half - 0.15, 1.05, 0.0)],
                          tint=(0.55, 0.08, 0.06))
        self._add_variant('deepwall', [(0.12, 2.4, 1.4, half - 0.18, 1.2,
                                        -MAZE_CELL / 2)], tint=TINT_DEEPWALL)
        # 实体（黑影人形：躯干 + 头；stalk 位姿由 apply_variant 平移）
        self._add_variant('entity', [
            (0.42, 1.15, 0.30, 0.0, 0.95, 0.0), (0.26, 0.30, 0.24, 0.0, 1.65, 0.0)],
            tint=TINT_ENTITY)
        # 手电（玩家手持小块，L3 获得）
        self._add_variant('handlight', [(0.09, 0.09, 0.28, 0.0, 0.0, 0.0)],
                          tint=(0.75, 0.75, 0.65))

    # ---------------------------------------------------- 变体应用 --
    def apply_variant(self, variant, light=False):
        """按当前圈变体换装（显隐 / 雾色 / 手电 / 实体显隐）。"""
        props = variant['props']
        base = variant['fog']
        red = float(props.get('red', 0.0))
        fog = tuple(base[i] + (FOG_RED[i] - base[i]) * red for i in range(3))
        self.program.use()
        self.program['fog_color'] = fog
        self.program['fog_near'] = FOG_NEAR
        self.program['fog_far'] = FOG_FAR
        self.program['u_light'] = 1.0 if (props.get('handlight') or light) else 0.0
        glClearColor(fog[0], fog[1], fog[2], 1.0)

        maze = bool(props.get('maze'))
        vis = dict(chair=props.get('chair', False),
                   droplamp=props.get('droplamp', False),
                   graffiti=props.get('graffiti', False),
                   marks=maze, handlight=bool(props.get('handlight')),
                   reddoor=maze and self.kernel.loop >= 5,
                   deepwall=maze and self.kernel.p2_done,
                   entity=self.kernel.entity.get('active', False))
        for name, on in vis.items():
            self.set_visible(name, on)
        if vis['entity']:
            self.reset_variant_pos('entity')
            self.move_variant('entity', self.kernel.entity['x'],
                              self.kernel.entity['z'])

    # ---------------------------------------------------- 渲染 --
    def apply_projection(self, width=None, height=None):
        w = width or self.window.width
        h = height or self.window.height
        self.window.projection = Mat4.perspective_projection(
            w / float(max(1, h)), z_near=0.05, z_far=120.0, fov=75.0)

    def begin_ui(self):
        self._saved_proj = self.window.projection
        self._saved_view = self.window.view
        glDisable(GL_DEPTH_TEST)
        self.window.view = Mat4()
        self.window.projection = Mat4.orthogonal_projection(
            0, self.window.width, 0, self.window.height, -1, 1)

    def end_ui(self):
        glEnable(GL_DEPTH_TEST)
        self.window.projection = self._saved_proj
        self.window.view = self._saved_view

    def draw(self):
        self.program.use()
        self.program['model'] = _IDENT
        self.static.draw(GL_TRIANGLES)
        for (name, d) in self.variants.items():
            if not d['visible']:
                continue
            self.program['tint'] = d['tint']
            d['vl'].draw(GL_TRIANGLES)
