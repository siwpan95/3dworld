# -*- coding: utf-8 -*-
"""kid_a_walker_3d —— 《KID-A WALKER 3D》暗色赛博无限走廊（真 3D 重制）。

把 arcade 里的 raycasting 伪 3D 步行模拟器升级为**真 3D**，并吸收 kkrieger
的程序化走廊美学（深蓝荧光 / 发光柱 / 暗室 / 数据神殿）：

    玩法内核（移植自 arcade/kid-a-walker，MIT · siwpan）
    ------------------------------------------------------
    - 21×21 房间模版 → 区块化无限地图（出口互连推导 + 3×3 预加载 + 远区块卸载）
    - 能量机制：移动 1/s、红区 +3/s、酸液 15/s，发光柱附近 +0.5/s，出生保护 3s
    - 碎片收集：10 种可收集装饰 + 图鉴（跨局保留）
    - 暗室 / 传送门 / 区域（zone）驱动氛围

    3D 化新增（本 demo）
    ----------------------
    - 每区块一个 3D 网格：墙柱挤出（邻墙剔除）+ 地面/天花板 + 装饰几何
    - 顶点色直接编码亮度与自发光（无贴图）——发光柱不受环境光影响
    - 区域调色板：区块主导区域驱动墙面色偏与 fog 色（"走进去，颜色就变了"）
    - 暗室：环境光骤降 + 视野收缩，只剩发光柱
    - 区域程序化配乐（ZoneSynth 风格：波形 + ADSR + 混响，随区域交叉渐变）
    - 区块化网格缓存：进 3×3 建、离开释放（复用 blockworld 的工程经验）

键位：WASD 走  SHIFT 冲刺  鼠标拖动转视角  E 收集碎片  F 手电光
      M 配乐开关  T 区域名播报  ESC 退出

用法::

    python demos/kid_a_walker_3d.py
    python demos/kid_a_walker_3d.py --smoke 4 --flag go     # 自动探索（自检/录制）
    python demos/kid_a_walker_3d.py --shot
"""
import array
import math
import random
import threading

import pyglet
from pyglet.gl import (GL_BLEND, GL_DEPTH_TEST, GL_LINES, GL_ONE_MINUS_SRC_ALPHA,
                       GL_SRC_ALPHA, GL_TRIANGLES, glBlendFunc, glClearColor,
                       glDisable, glEnable)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4, Vec3

from _harness import DemoApp, run
from engine.fixedstep import FixedStepper
from logic import kid_a as K

# ---------------------------------------------------------------- 着色器 --
VS = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec4 v_col;
out float v_dist;
uniform WindowBlock { mat4 projection; mat4 view; } window;
void main() {
    vec4 vp = window.view * vec4(position, 1.0);
    v_dist = length(vp.xyz);
    v_col = colors;
    gl_Position = window.projection * vp;
}'''

# v_col.a = 自发光强度：普通表面吃环境光，发光柱/传送门不吃
FS = '''#version 330 core
in vec4 v_col;
in float v_dist;
out vec4 final_colors;
uniform vec3  u_ambient;      // 环境光（暗室会骤降）
uniform vec3  u_glow;         // 全局辉光色偏（区域色）
uniform vec3  u_fog_color;
uniform float u_fog_near;
uniform float u_fog_far;
uniform float u_lamp;         // 手电光强度（0 = 关）
void main() {
    float lamp = u_lamp * exp(-v_dist * 0.06);
    vec3 lit = v_col.rgb * (u_ambient + v_col.a * 1.35 + lamp * 0.5)
             + u_glow * v_col.a * 0.35;
    lit = mix(lit, u_fog_color, smoothstep(u_fog_near, u_fog_far, v_dist));
    final_colors = vec4(lit, 1.0);
}'''

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

_BOXV = []
for _i in range(8):
    _BOXV.append((_i & 1, (_i >> 1) & 1, (_i >> 2) & 1))
_LINKS = ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7),
          (0, 4), (1, 5), (2, 6), (3, 7))
_BOX_LINES = []
for _a, _b in _LINKS:
    _BOX_LINES.append(_BOXV[_a])
    _BOX_LINES.append(_BOXV[_b])


# ------------------------------------------------------------ 视觉调色板 --
WALL_RGB = (0.24, 0.27, 0.38)        # 深蓝灰墙（kkrieger 走廊色系）
FLOOR_RGB = (0.13, 0.14, 0.20)
CEIL_RGB = (0.07, 0.08, 0.12)

# 装饰格 -> (rgb, emissive, 形态)：形态 'pillar'|'box'|'pad'
DECOS = {
    K.GLOW:      ((0.12, 1.00, 0.85), 1.00, 'pillar'),
    K.RED:       ((0.90, 0.16, 0.12), 0.35, 'pad'),
    K.GOLD:      ((1.00, 0.80, 0.28), 0.55, 'box'),
    K.ACID:      ((0.20, 0.90, 0.38), 0.50, 'pad'),
    K.PURPLE:    ((0.66, 0.32, 1.00), 0.95, 'pillar'),
    K.ORANGE:    ((1.00, 0.58, 0.16), 0.80, 'pillar'),
    K.DEBRIS:    ((0.42, 0.42, 0.46), 0.00, 'box'),
    K.TECH:      ((0.28, 0.82, 1.00), 0.65, 'pillar'),
    K.CRYSTAL_A: ((0.38, 0.66, 1.00), 0.60, 'pillar'),
    K.CRYSTAL_B: ((0.72, 0.40, 1.00), 0.60, 'pillar'),
    K.PORTAL:    ((1.00, 0.95, 0.40), 1.00, 'pillar'),
}

# 区域 -> 环境光/辉光/fog（暗色赛博，"走进去颜色就变了"）
ZONE_LOOK = {
    2:  ((0.55, 0.60, 0.72), (0.00, 0.10, 0.09), (0.020, 0.030, 0.050)),
    3:  ((0.62, 0.46, 0.46), (0.12, 0.01, 0.01), (0.040, 0.020, 0.022)),
    4:  ((0.72, 0.66, 0.48), (0.12, 0.10, 0.02), (0.045, 0.040, 0.022)),
    5:  ((0.48, 0.62, 0.50), (0.02, 0.12, 0.04), (0.018, 0.040, 0.026)),
    6:  ((0.58, 0.50, 0.78), (0.08, 0.03, 0.14), (0.035, 0.022, 0.055)),
    7:  ((0.66, 0.58, 0.46), (0.12, 0.07, 0.01), (0.042, 0.032, 0.018)),
    8:  ((0.42, 0.42, 0.46), (0.00, 0.00, 0.00), (0.020, 0.020, 0.024)),
    9:  ((0.48, 0.62, 0.78), (0.01, 0.09, 0.14), (0.018, 0.030, 0.052)),
    10: ((0.50, 0.62, 0.80), (0.02, 0.08, 0.16), (0.020, 0.032, 0.058)),
    11: ((0.60, 0.50, 0.80), (0.09, 0.03, 0.14), (0.032, 0.022, 0.055)),
    12: ((0.78, 0.74, 0.48), (0.16, 0.14, 0.02), (0.050, 0.048, 0.020)),
}
DARK_AMBIENT = (0.13, 0.14, 0.20)     # 暗室环境光
DARK_FOG = (0.008, 0.010, 0.020)

WALL_H = 3.0                          # 走廊净高（也是天花板高度）
MOVE_SPEED = 4.6
SPRINT = 1.7
PLAYER_R = 0.32


# ------------------------------------------------------------ 区块网格 --
def _quad(pos, col, p0, p1, p2, p3, rgb, emissive, shade):
    """两三角 + 顶点色（shade 为面亮度）。"""
    r, g, b = rgb[0] * shade, rgb[1] * shade, rgb[2] * shade
    c = (r, g, b, emissive)
    for p in (p0, p1, p2, p0, p2, p3):
        pos.extend(p)
        col.extend(c)


STRIP_RGB = (0.10, 0.62, 0.58)       # 发光踢脚线（kkrieger 走廊标志色）
STRIP_H = 0.15                        # 踢脚线高度


def build_chunk_mesh(world, cx, cy):
    """把区块 (cx,cy) 网格化成 (pos, col) 数组。

    - 墙格：四侧面（邻墙剔除）+ 顶面 + **发光踢脚线**（走廊底部的青色光带）
    - 地面/天花板：**逐格**平板（装饰格的天花板开洞——光柱穿出去）
    - 装饰：柱体高过天花板（穿出 0.5），远处即可看见辉光
    """
    pos = array.array('f')
    col = array.array('f')
    x0, z0 = cx * K.CHUNK, cy * K.CHUNK

    def solid(wx, wy):
        return world.cell(wx, wy) == K.WALL

    for ly in range(K.CHUNK):
        for lx in range(K.CHUNK):
            gx, gz = x0 + lx, z0 + ly
            v = world.cell(gx + 0.5, gz + 0.5)
            fx, fz = float(gx), float(gz)
            fx1, fz1 = fx + 1.0, fz + 1.0
            # 地面（逐格）
            _quad(pos, col, (fx, 0, fz), (fx1, 0, fz), (fx1, 0, fz1),
                  (fx, 0, fz1), FLOOR_RGB, 0.0, 1.0)
            # 天花板：装饰格开洞（光柱穿出）
            if v < 2:
                _quad(pos, col, (fx, WALL_H, fz1), (fx1, WALL_H, fz1),
                      (fx1, WALL_H, fz), (fx, WALL_H, fz), CEIL_RGB, 0.0, 1.0)
            if v == K.WALL:
                h = WALL_H
                # 四侧：底部发光踢脚线 + 上部暗墙（亮度分级保留）
                if not solid(gx, gz + 1):
                    _quad(pos, col, (fx, 0, fz1), (fx1, 0, fz1),
                          (fx1, STRIP_H, fz1), (fx, STRIP_H, fz1),
                          STRIP_RGB, 0.85, 1.0)
                    _quad(pos, col, (fx, STRIP_H, fz1), (fx1, STRIP_H, fz1),
                          (fx1, h, fz1), (fx, h, fz1), WALL_RGB, 0.0, 0.82)
                if not solid(gx, gz - 1):
                    _quad(pos, col, (fx1, 0, fz), (fx, 0, fz),
                          (fx, STRIP_H, fz), (fx1, STRIP_H, fz),
                          STRIP_RGB, 0.85, 1.0)
                    _quad(pos, col, (fx1, STRIP_H, fz), (fx, STRIP_H, fz),
                          (fx, h, fz), (fx1, h, fz), WALL_RGB, 0.0, 0.60)
                if not solid(gx + 1, gz):
                    _quad(pos, col, (fx1, 0, fz1), (fx1, 0, fz),
                          (fx1, STRIP_H, fz), (fx1, STRIP_H, fz1),
                          STRIP_RGB, 0.85, 1.0)
                    _quad(pos, col, (fx1, STRIP_H, fz1), (fx1, STRIP_H, fz),
                          (fx1, h, fz), (fx1, h, fz1), WALL_RGB, 0.0, 0.72)
                if not solid(gx - 1, gz):
                    _quad(pos, col, (fx, 0, fz), (fx, 0, fz1),
                          (fx, STRIP_H, fz1), (fx, STRIP_H, fz),
                          STRIP_RGB, 0.85, 1.0)
                    _quad(pos, col, (fx, STRIP_H, fz), (fx, STRIP_H, fz1),
                          (fx, h, fz1), (fx, h, fz), WALL_RGB, 0.0, 0.94)
                _quad(pos, col, (fx, h, fz), (fx, h, fz1), (fx1, h, fz1),
                      (fx1, h, fz), WALL_RGB, 0.0, 1.0)      # 顶
            elif v >= 2:
                rgb, em, form = DECOS.get(v, (WALL_RGB, 0.0, 'box'))
                ccx, ccz = gx + 0.5, gz + 0.5
                if form == 'pillar':                      # 光柱：穿出天花板
                    r = 0.12
                    h = WALL_H + 0.55
                    _box(pos, col, ccx - r, 0, ccz - r, ccx + r, h, ccz + r,
                         rgb, em)
                    _box(pos, col, ccx - 0.17, 0.02, ccz - 0.17, ccx + 0.17,
                         0.07, ccz + 0.17, tuple(min(1.0, c * 1.5) for c in rgb),
                         1.0)                              # 地面光圈
                elif form == 'pad':                       # 地面发光板
                    _box(pos, col, ccx - 0.44, 0.001, ccz - 0.44,
                         ccx + 0.44, 0.08, ccz + 0.44, rgb, em)
                else:                                     # 小盒
                    _box(pos, col, ccx - 0.28, 0, ccz - 0.28,
                         ccx + 0.28, 0.62, ccz + 0.28, rgb, em)
    return pos, col


def _box(pos, col, x0, y0, z0, x1, y1, z1, rgb, em):
    """通用小盒（六面，亮度分级）。"""
    _quad(pos, col, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
          rgb, em, 0.84)
    _quad(pos, col, (x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0),
          rgb, em, 0.62)
    _quad(pos, col, (x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1),
          rgb, em, 0.74)
    _quad(pos, col, (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0),
          rgb, em, 0.92)
    _quad(pos, col, (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0),
          rgb, em, 1.0)
    _quad(pos, col, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1),
          rgb, em, 0.5)


# --------------------------------------------------------------- 配乐 --
class ZoneSynthLite:
    """区域程序化配乐（移植自原版 ZoneSynth，sounddevice 流式播放）。

    无 numpy/sounddevice 时静默降级——不影响画面。区域切换做 ~1.8s 交叉渐变。
    """

    SR = 22050

    def __init__(self, enabled=True):
        self.enabled = False
        self.zone = 2
        self.prev_zone = 2
        self.fade = 1.0
        self.beat = 0.0
        self.prev_beat = 0.0
        self.muted = not enabled
        if not enabled:
            return
        try:
            import numpy as np
            import sounddevice as sd
            self.np = np
            self.stream = sd.OutputStream(samplerate=self.SR, channels=1,
                                          dtype='float32')
            self.stream.start()
            self.enabled = True
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()
            print('[zone] synth started', flush=True)
        except Exception as e:
            print(f'[zone] synth disabled ({type(e).__name__})', flush=True)

    # 波形
    def _wave(self, freq, waveform, dur, np):
        n = int(self.SR * dur)
        t = np.arange(n, dtype=np.float32) / self.SR
        if waveform == 'sine':
            v = np.sin(2 * np.pi * freq * t)
        elif waveform == 'sawtooth':
            v = 2.0 * ((freq * t) % 1.0) - 1.0
        elif waveform == 'square':
            v = np.where((freq * t) % 1.0 < 0.5, 1.0, -1.0)
        elif waveform == 'triangle':
            p = (freq * t) % 1.0
            v = np.where(p < 0.5, 4.0 * p - 1.0, 3.0 - 4.0 * p)
        else:                                   # organ
            v = (np.sin(2 * np.pi * freq * t) * 0.5
                 + np.sin(4 * np.pi * freq * t) * 0.3
                 + np.sin(6 * np.pi * freq * t) * 0.15
                 + np.sin(8 * np.pi * freq * t) * 0.05)
        # ADSR（简化）
        a = int(0.02 * n) or 1
        r = int(0.18 * n) or 1
        env = np.ones(n, dtype=np.float32)
        env[:a] = np.linspace(0, 1, a)
        env[-r:] *= np.linspace(1, 0, r)
        return (v * env).astype(np.float32)

    _NOTE = {'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'Eb': 3, 'E': 4, 'F': 5,
             'F#': 6, 'G': 7, 'G#': 8, 'A': 9, 'Bb': 10, 'B': 11}

    def _freq(self, note):
        name = note[:-1]
        octv = int(note[-1])
        semi = self._NOTE[name] + (octv - 4) * 12
        return 261.626 * (2.0 ** (semi / 12.0))

    def _gen_beat(self, zone, beat):
        np = self.np
        notes, bpm, wave, reverb, shift = K.ZONE_INFO[zone][3]
        dur = (60.0 / bpm) * 0.5
        nm = notes[int(beat) % len(notes)]
        f = self._freq(nm) * (2.0 ** (shift / 12.0))
        v = self._wave(f, wave, dur, np)
        # 简易混响（反馈延迟）
        d = int(self.SR * (0.05 + reverb * 0.08))
        if d and len(v) > d:
            v[d:] += v[:-d] * (0.2 + reverb * 0.3)
        n = int(self.SR * dur)
        if len(v) < n:
            v = np.pad(v, (0, n - len(v)))
        return (np.tanh(v[:n] * 1.1) * 0.085).astype(np.float32)

    def _worker(self):
        np = self.np
        while True:
            if self.muted:
                import time
                time.sleep(0.2)
                continue
            cur = self._gen_beat(self.zone, self.beat)
            self.beat += 0.5
            if self.fade < 1.0:
                prev = self._gen_beat(self.prev_zone, self.prev_beat)
                self.prev_beat += 0.5
                self.fade = min(1.0, self.fade + 0.045)
                mix = cur * self.fade + prev * (1.0 - self.fade)
            else:
                mix = cur
            try:
                self.stream.write(np.ascontiguousarray(mix))
            except Exception:
                return

    def set_zone(self, z):
        if self.enabled and z != self.zone:
            self.prev_zone = self.zone
            self.prev_beat = self.beat
            self.zone = z
            self.beat = 0.0
            self.fade = 0.0

    def toggle(self):
        self.muted = not self.muted
        return self.muted


# ---------------------------------------------------------------- Demo --
class KidAApp(DemoApp):
    TITLE = 'KID-A WALKER 3D · 暗色赛博无限走廊（能量 / 碎片 / 传送门）'
    HELP = ('WASD 走  SHIFT 冲刺  鼠标拖动转视角  E 收集  F 手电  '
            'M 配乐  T 播报区域  ESC 退出')
    FOV = 68.0
    Z_NEAR, Z_FAR = 0.05, 160.0
    SHOTS = ((2.0, 'docs/kid_a_3d_01.png'), (6.5, 'docs/kid_a_3d_02.png'),
             (11.0, 'docs/kid_a_3d_03.png'))

    def __init__(self, width, height, args=None):
        self.px = self.pz = 10.5
        self.yaw, self.pitch = 0.0, 0.0
        self._keys = set()
        self.world = K.WorldMap(seed=20261009)
        self.energy = K.EnergySystem()
        self.frag = K.FragmentSystem()
        self.chunks = {}                        # (cx,cy) -> vertex_list
        self.visited = set()
        self.zone = 2
        self.chunk_cell = {}
        self._go = False                        # --flag go 自动探索
        self._go_turn = 0.0
        self._go_path = None                    # BFS 路径（格子序列）
        self._go_path_age = 0.0
        self._go_last = None
        self._prev_chunk = None                 # 来路区块（回避回头路）
        self._visited_prev = None
        self._go_stuck = 0.0
        self.lamp = True
        self.msg = ''
        self.msg_until = 0.0
        self.collapsed = False
        self._edits = 0
        self._autocollect = 0
        super().__init__(width, height, args=args)

    # ------------------------------------------------------------ setup --
    def setup(self):
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glClearColor(0.01, 0.012, 0.02, 1.0)

        self.prog = ShaderProgram(Shader(VS, 'vertex'), Shader(FS, 'fragment'))
        self.line_prog = ShaderProgram(Shader(LINE_VS, 'vertex'),
                                       Shader(LINE_FS, 'fragment'))

        spawn = self.world.find_safe_spawn(0, 0)
        self.px, self.pz = spawn
        # 朝向走廊开口（找一条可行方向）
        for cand in (0.0, 90.0, 180.0, 270.0):
            self.yaw = cand
            fx, fz = self._forward()
            if not self._blocked(self.px + fx * 1.5, self.pz + fz * 1.5):
                break
        self.world.ensure_neighbors(*K.WorldMap.world_to_chunk(self.px, self.pz))
        self._sync_chunks()

        from engine.camera import FlyCamera
        self.eye = FlyCamera(position=(self.px, 1.55, self.pz), fov=self.FOV,
                             far=self.Z_FAR)
        self.stepper = FixedStepper(lambda dt: self._step(dt),
                                    fixed_dt=1.0 / 60.0)
        self.synth = ZoneSynthLite(enabled=True)
        self.zone = self.world.chunk_zone(*self._chunk())
        self.synth.set_zone(self.zone)
        self.on_mouse_drag = self._on_drag
        self.window.push_handlers(on_key_release=self._on_key_release)
        try:
            from engine.audio import SfxPlayer
            self.sfx = SfxPlayer()
        except Exception:
            self.sfx = None
        print(f'[kid_a] spawn=({self.px:.1f},{self.pz:.1f}) '
              f'zone={self.zone}({K.ZONE_INFO[self.zone][0]})', flush=True)
        if 'go' in self.args.flags:
            self._go = True
            print('[kid_a] auto-explore (go)', flush=True)

    # ------------------------------------------------------------ 区块网格 --
    def _chunk(self):
        return K.WorldMap.world_to_chunk(self.px, self.pz)

    def _sync_chunks(self):
        ccx, ccy = self._chunk()
        self.world.ensure_neighbors(ccx, ccy)
        want = set()
        for dcy in range(-1, 2):
            for dcx in range(-1, 2):
                want.add((ccx + dcx, ccy + dcy))
        for coord in list(self.chunks):
            if coord not in want:
                self.chunks.pop(coord).delete()
        for coord in want:
            if coord in self.chunks or coord not in self.world.chunks:
                continue
            pos, col = build_chunk_mesh(self.world, *coord)
            self.chunks[coord] = self.prog.vertex_list(
                len(pos) // 3, GL_TRIANGLES, position=('f', pos),
                colors=('f', col))
        # 区域切换
        z = self.world.chunk_zone(ccx, ccy)
        if z != self.zone:
            self.zone = z
            self.synth.set_zone(z)
            self.msg = f'区域：{K.ZONE_INFO[z][0]}'
            self.msg_until = self.time + 4.0
            print(f'[zone] -> {z} {K.ZONE_INFO[z][0]}', flush=True)

    def is_dark(self):
        ccx, ccy = self._chunk()
        return self.world.is_dark_room(ccx, ccy)

    # ------------------------------------------------------------ 物理 --
    def _blocked(self, x, z):
        r = PLAYER_R
        for dx, dz in ((-r, -r), (-r, r), (r, -r), (r, r), (0, 0)):
            if self.world.cell_blocking(x + dx, z + dz):
                return True
        return False

    def _forward(self):
        y = math.radians(self.yaw)
        return math.cos(y), math.sin(y)

    def _step(self, dt):
        ks = self._keys
        f = (1.0 if 'W' in ks else 0.0) - (1.0 if 'S' in ks else 0.0)
        r = (1.0 if 'D' in ks else 0.0) - (1.0 if 'A' in ks else 0.0)
        fx, fz = self._forward()
        rx, rz = -fz, fx
        sp = MOVE_SPEED * (SPRINT if 'SHIFT' in ks else 1.0)
        vx, vz = f * fx + r * rx, f * fz + r * rz
        ln = math.hypot(vx, vz)
        moving = ln > 1e-6
        if moving:
            vx, vz = vx / ln * sp, vz / ln * sp
            nx = self.px + vx * dt
            if not self._blocked(nx, self.pz):
                self.px = nx
            nz = self.pz + vz * dt
            if not self._blocked(self.px, nz):
                self.pz = nz
        self._moving = moving

    # ------------------------------------------------------------ 玩法 --
    def _near_glow(self):
        ccx, ccy = self._chunk()
        for wx, wz, v in self.world.decorations(ccx, ccy):
            if v == K.GLOW and math.hypot(wx - self.px, wz - self.pz) < 2.4:
                return True
        return False

    def _deco_near(self):
        """最近的可收集装饰（2.2 范围内）。"""
        ccx, ccy = self._chunk()
        best = None
        for wx, wz, v in self.world.decorations(ccx, ccy):
            if v not in K.FRAGMENT_TYPES:
                continue
            d = math.hypot(wx - self.px, wz - self.pz)
            if d < K.INTERACT_RANGE and (best is None or d < best[0]):
                best = (d, wx, wz, v)
        return best

    def _try_collect(self):
        hit = self._deco_near()
        if hit is None:
            return False
        _d, wx, wz, v = hit
        ccx, ccy = self._chunk()
        lx = int(wx - ccx * K.CHUNK)
        ly = int(wz - ccy * K.CHUNK)
        if self.frag.collect(v, (ccx, ccy, lx, ly)):
            name = K.CELL_NAMES.get(v, '碎片')
            self.msg = (f'收集 {name}（{self.frag.unique_types()}/'
                        f'{len(K.FRAGMENT_TYPES)} 种）')
            self.msg_until = self.time + 2.5
            if self.sfx:
                self.sfx.play('pickup')
            print(f'[frag] {name} total={self.frag.total()} '
                  f'unique={self.frag.unique_types()}', flush=True)
            return True
        return False

    def _portal_check(self):
        """踩上传送门：随机传送到远处（保留能量惩罚感——传送耗 5 点）。"""
        ccx, ccy = self._chunk()
        for wx, wz, v in self.world.decorations(ccx, ccy):
            if v == K.PORTAL and math.hypot(wx - self.px, wz - self.pz) < 0.8:
                rng = random.Random(int(self.time * 1000) ^ 0x5F3A)
                dx = rng.choice((-9, -8, 8, 9))
                dy = rng.choice((-9, -8, 8, 9))
                tx, ty = ccx + dx, ccy + dy
                self.world.ensure_neighbors(tx, ty)
                nx, nz = self.world.find_safe_spawn(tx, ty)
                self.px, self.pz = nx, nz
                self.energy.energy = max(0.0, self.energy.energy - 5.0)
                self.msg = '传送门：你被抛向了远方……'
                self.msg_until = self.time + 3.0
                self._sync_chunks()
                print(f'[portal] -> chunk({tx},{ty})', flush=True)
                return True
        return False

    # ------------------------------------------------------------ 输入 --
    def _on_drag(self, x, y, dx, dy, buttons, mods):
        self.yaw += dx * 0.24
        self.pitch = max(-80.0, min(80.0, self.pitch - dy * 0.24))

    def on_key(self, symbol, mods):
        key = pyglet.window.key
        name = getattr(key, 'symbol_string', lambda s: '')(symbol)
        if name in ('W', 'A', 'S', 'D'):
            self._keys.add(name)
        elif 'SHIFT' in name:
            self._keys.add('SHIFT')
        elif name == 'E':
            self._try_collect()
        elif name == 'F':
            self.lamp = not self.lamp
        elif name == 'M':
            if self.synth.enabled:
                m = self.synth.toggle()
                self.msg = '配乐：静音' if m else '配乐：开'
                self.msg_until = self.time + 2.0
        elif name == 'T':
            self.msg = (f'当前区域：{K.ZONE_INFO[self.zone][0]} —— '
                        f'{K.ZONE_INFO[self.zone][2]}')
            self.msg_until = self.time + 3.5

    def _on_key_release(self, symbol, mods):
        key = pyglet.window.key
        name = getattr(key, 'symbol_string', lambda s: '')(symbol)
        self._keys.discard(name)
        if 'SHIFT' in name:
            self._keys.discard('SHIFT')

    # ------------------------------------------------------------ 主循环 --
    def _update_impl(self, dt):
        if self.collapsed:
            return
        if self._go:
            self._go_step(dt)
            self.pitch = -7.0              # 自动探索：轻微俯视，构图更稳
        else:
            self.stepper.advance(dt)
        ccx, ccy = self._chunk()
        if (ccx, ccy) not in self.visited:
            self.visited.add((ccx, ccy))
            self._prev_chunk = getattr(self, '_visited_prev', None)
            self._sync_chunks()
            if self._go:
                print(f'[go] 区块({ccx},{ccy}) 已探索 {len(self.visited)} 区 '
                      f'zone={self.zone}', flush=True)
        self._visited_prev = (ccx, ccy)
        cell = self.world.cell(self.px, self.pz)
        self.energy.update(dt, cell, getattr(self, '_moving', False),
                           self._near_glow())
        if self.energy.is_dead and not self.collapsed:
            self.collapsed = True
            self.msg = '能量耗尽…… 按 R 重新开始'
            self.msg_until = self.time + 999.0
            print('[kid_a] energy depleted -> collapse', flush=True)
        self.eye.position = (self.px, 1.55, self.pz)
        self.eye.yaw = self.yaw
        self.eye.pitch = self.pitch

    # ------------------------------------------------------- 自动探索 --
    @staticmethod
    def _exit_pos(cx, cy, d):
        if d == 'N':
            return cx * K.CHUNK + 10.5, cy * K.CHUNK + 0.5
        if d == 'S':
            return cx * K.CHUNK + 10.5, cy * K.CHUNK + 20.5
        if d == 'W':
            return cx * K.CHUNK + 0.5, cy * K.CHUNK + 10.5
        return cx * K.CHUNK + 20.5, cy * K.CHUNK + 10.5

    def _go_target(self):
        """朝"未访问区块"的出口走；都访问过就随机选一个出口。"""
        ccx, ccy = self._chunk()
        exits = self.world.exits.get((ccx, ccy), set())
        if not exits:
            return None
        fx, fz = self._forward()
        best = None
        for d in exits:
            nb = (ccx + (1 if d == 'E' else -1 if d == 'W' else 0),
                  ccy + (1 if d == 'S' else -1 if d == 'N' else 0))
            fresh = nb not in self.visited
            ex, ez = self._exit_pos(ccx, ccy, d)
            dx, dz = ex - self.px, ez - self.pz
            ln = math.hypot(dx, dz) or 1.0
            dot = (dx * fx + dz * fz) / ln          # 与当前朝向的夹角
            score = (1 if fresh else 0) * 2.0 + dot
            if best is None or score > best[0]:
                best = (score, ex, ez)
        return best[1], best[2]

    def _go_step(self, dt):
        """自动探索：**BFS 寻路**到目标出口（栅格已知，稳定不卡死）。"""
        import collections
        # 选定目标出口（优先未访问区块）
        ccx, ccy = self._chunk()
        exits = self.world.exits.get((ccx, ccy), set())
        if not exits:
            self._moving = False
            return
        if self._go_path is None or not self._go_path:
            # 选一个出口：优先未访问邻居 + **回避来路**（防止门间乒乓振荡）。
            # 位置型判定（"附近有门就跳过"）不可行——"接近门要走"与
            # "刚从门进来"位置重合；改用**来路记忆**做惩罚。
            best = None
            for d in exits:
                nb = (ccx + (1 if d == 'E' else -1 if d == 'W' else 0),
                      ccy + (1 if d == 'S' else -1 if d == 'N' else 0))
                score = (2.0 if nb not in self.visited else 0.0)
                if nb == self._prev_chunk:
                    score -= 4.0                    # 来路重罚（仅当无路可走才回头）
                if best is None or score > best[0]:
                    best = (score, self._exit_pos(ccx, ccy, d), d)
            target, tdir = best[1], best[2]
            tx, tz = target
            # 已在门口（2.2 内）：目标改为「门外 2.5」——继续穿过门进下一区块。
            # ⚠️ 门槛必须够大：门槛过小时目标就是脚下格子，会出现
            # "每帧 pop→重规划→再 pop"的死循环（曾把探索锁死在门口）。
            if math.hypot(tx - self.px, tz - self.pz) < 2.2:
                if tdir == 'W':
                    tx -= 2.5
                elif tdir == 'E':
                    tx += 2.5
                elif tdir == 'N':
                    tz -= 2.5
                else:
                    tz += 2.5
            self._go_path = self._bfs_path((int(self.px), int(self.pz)),
                                           (int(tx), int(tz)))
            if not self._go_path:
                # 兜底：门外目标也解不出（异常地形）→ 换一个出口
                for d in exits:
                    if d == tdir:
                        continue
                    ex, ez = self._exit_pos(ccx, ccy, d)
                    self._go_path = self._bfs_path((int(self.px), int(self.pz)),
                                                   (int(ex), int(ez)))
                    if self._go_path:
                        tdir = d
                        break
            self._go_path_age = 0.0
            self._go_last = (ccx + (1 if tdir == 'E' else -1 if tdir == 'W' else 0),
                             ccy + (1 if tdir == 'S' else -1 if tdir == 'N' else 0))
        self._go_path_age += dt

        # 沿路径走：**位移与朝向解耦**（朝目标格中心直接平移，贴墙滑动；
        # 朝向只做视觉平滑跟随——避免"绕着格心打转、永远对不齐"的振荡）
        moved = False
        if self._go_path:
            nxt = self._go_path[0]
            cx, cz = nxt[0] + 0.5, nxt[1] + 0.5
            if math.hypot(cx - self.px, cz - self.pz) < 0.4:
                self._go_path.pop(0)
                if self._go_path:
                    nxt = self._go_path[0]
                    cx, cz = nxt[0] + 0.5, nxt[1] + 0.5
                else:
                    nxt = None
            if nxt is not None:
                dx, dz = cx - self.px, cz - self.pz
                L = math.hypot(dx, dz) or 1.0
                step = MOVE_SPEED * 1.55 * dt
                mx, mz = dx / L * step, dz / L * step
                if not self._blocked(self.px + mx + (0.05 if mx > 0 else -0.05), self.pz):
                    self.px += mx
                    moved = True
                if not self._blocked(self.px, self.pz + mz + (0.05 if mz > 0 else -0.05)):
                    self.pz += mz
                    moved = True
                # 朝向：平滑跟随移动方向（视觉）
                want = math.degrees(math.atan2(dz, dx))
                diff = (want - self.yaw + 540.0) % 360.0 - 180.0
                self.yaw += max(-260.0 * dt, min(260.0 * dt, diff))
        self._moving = moved
        # 停滞兜底：1.5s 没动 -> 强行换向（清路径，随机转身再探索）
        if moved:
            self._go_stuck = 0.0
        else:
            self._go_stuck = getattr(self, '_go_stuck', 0.0) + dt
            if self._go_stuck > 1.5:
                self._go_stuck = 0.0
                self._go_path = None
                self.yaw += 137.0
                print('[go] 停滞兜底：换向重规划', flush=True)
        self._autocollect -= dt
        if self._autocollect <= 0:
            self._autocollect = 0.2
            self._try_collect()
        self._portal_check()

    def _bfs_path(self, start, goal):
        """栅格 BFS：返回从 start 到 goal 的格子序列（不含起点）；无解返回 []。

        ⚠️ 必须先 ensure_neighbors——"门外一格"属于邻居区块，未生成时
        ``cell()`` 一律返回 WALL，BFS 会直接失败（曾导致原地卡死）。
        """
        import collections
        self.world.ensure_neighbors(*self._chunk())
        if start == goal:
            return []
        seen = {start}
        q = collections.deque([(start, [])])
        n = K.CHUNK
        ccx, ccy = self._chunk()
        # 搜索范围：当前区块 ± 1 区块（3×3 已加载区域）
        lo_x, lo_z = (ccx - 1) * n, (ccy - 1) * n
        hi_x, hi_z = (ccx + 2) * n, (ccy + 2) * n
        while q:
            (x, z), path = q.popleft()
            if len(path) > 400:
                return []
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, nz = x + dx, z + dz
                if not (lo_x <= nx < hi_x and lo_z <= nz < hi_z):
                    continue
                if (nx, nz) in seen or self.world.cell(nx + 0.5, nz + 0.5) == K.WALL:
                    continue
                np_ = path + [(nx, nz)]
                if (nx, nz) == goal:
                    return np_
                seen.add((nx, nz))
                q.append(((nx, nz), np_))
        return []

    def update(self, dt):
        try:
            self._update_impl(dt)
        except Exception:
            import traceback
            print('[update] EXC:\n' + traceback.format_exc(), flush=True)

    def _restart(self):
        self.energy.reset()
        self.collapsed = False
        self.px, self.pz = 10.5, 10.5
        self.msg = '重新开始'
        self.msg_until = self.time + 2.0
        self._sync_chunks()

    def _on_key_press(self, symbol, mods):
        if symbol == pyglet.window.key.R:
            self._restart()
            return
        super()._on_key_press(symbol, mods)

    def render(self):
        self.eye.update(0.0)
        self.window.view = self.eye.view_matrix()
        amb, glow, fog = ZONE_LOOK.get(self.zone, ZONE_LOOK[2])
        if self.is_dark():
            amb, fog = DARK_AMBIENT, DARK_FOG
        fog_far = 60.0 if self.is_dark() else 95.0
        self.prog.use()
        self.prog['u_ambient'] = amb
        self.prog['u_glow'] = glow
        self.prog['u_fog_color'] = fog
        self.prog['u_fog_near'] = fog_far * 0.35
        self.prog['u_fog_far'] = fog_far
        self.prog['u_lamp'] = 1.0 if self.lamp else 0.0
        for vl in self.chunks.values():
            vl.draw(GL_TRIANGLES)

    def status_text(self):
        base = super().status_text()
        ccx, ccy = self._chunk()
        zname = K.ZONE_INFO[self.zone][0]
        e = self.energy.energy
        bar = '█' * int(e / 10) + '░' * (10 - int(e / 10))
        dark = ' [暗室]' if self.is_dark() else ''
        msg = f'  {self.msg}' if self.time < self.msg_until else ''
        return (f'{base}  能量 {bar} {e:5.1f}  区块({ccx},{ccy}) '
                f'{zname}{dark}  碎片 {self.frag.unique_types()}/10'
                f'（共{self.frag.total()}）  探索 {len(self.visited)} 区块'
                f'  {self.fps:4.1f} fps{msg}')


if __name__ == '__main__':
    run(KidAApp)
