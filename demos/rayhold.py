# -*- coding: utf-8 -*-
"""迷堡枪影 / Rayhold —— P1-③ 射线 FPS（Cordis 委托 · SDF 光线步进渲染）。

机制致敬 `Wolfenstein 3D`(1992) / `Doom`(1993) 的「走廊迷宫 + 第一人称射击」循环；
**原创命名 / 美术 / 世界观**，不碰任何原 IP 名称与素材（合规见 `docs/remake_ideas.md` §7）。
设计 note 见 `docs/remake_ideas.md` §10；先问结论：SDF 光线步进 / 程序化随机迷宫 /
hitscan 单武器 / 完整状态机 AI。

分层（与 Phase C/D 的「逻辑在 Cordis、表现由宿主」一致）：

- setup()   经 asyncio 引导 `cordis.fps.yml` 配置树（`tree_boot.mount_tree`），
            内核在 `__cordis_init__` 生成迷宫、布敌与拾取、注入弹药、取得 GL 句柄；
- update()  把输入（或 `--flag auto` 的自动驾驶）交给内核，并把内核事件转成音效；
- render()  只读内核 / 迷宫状态：全屏片元着色器做 SDF 光线步进（墙 + 地板 + 敌人 +
            光弹），准星 / 枪口闪光 / 受击红晕在同一 pass 里叠加。

**CPU/GPU 同源**：命中判定（内核 DDA）与画面（着色器）用的是同一份迷宫数据——
距离场由 `MazeService.distance_field()` 现算后以 uniform 数组传入，不另写一份。

操作：WASD 移动   SHIFT 疾跑   鼠标拖动 转视角   左键（按住）射击
      R 换一局（新种子）   M 切鼠标锁定   SPACE 暂停   ESC 退出
"""
import asyncio
import math
import os
import sys
import tempfile

import pyglet
from pyglet.gl import GL_DEPTH_TEST, GL_TRIANGLES, glDisable
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4
from pyglet.window import key, mouse

from _harness import DemoApp, make_fullscreen_quad, run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 试点三：挂载由 cordis.fps.yml 声明（与 config_boot 的 headless 验证同一入口）
from experiments.cordis.tree_boot import WANT_FPS, config_path, mount_tree  # noqa: E402
from experiments.cordis.fps_kernel import CELL, DEATH_T, EYE, WALL_H  # noqa: E402

GRID_N = 15          # 必须与 cordis.fps.yml 的 cells 一致（着色器数组长度）

VERTEX_SRC = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec2 v_uv;
void main() {
    v_uv = position.xy * 0.5 + 0.5;     // NDC → [0,1]²
    gl_Position = vec4(position.xy, 0.0, 1.0);
}'''

FRAGMENT_SRC = '''#version 330 core
in vec2 v_uv;
out vec4 final_colors;

uniform vec2  u_res;
uniform vec3  u_eye;
uniform float u_yaw;
uniform float u_time;
uniform float u_cell;
uniform float u_half;
uniform float u_wall_h;
uniform float u_grid[225];    // 迷宫距离场（世界单位；索引 i*GRID_N+j）
uniform vec4  u_enemy[16];    // xy = 位置, z = 类型+淡出编码, w = 受击白闪
uniform vec4  u_bolt[8];      // xy = 位置, z = 1 有效
uniform float u_muzzle;       // 枪口闪光 0..1
uniform float u_hurt;         // 受击红晕 0..1

const int   GRID_N = 15;      // 必须与 cordis.fps.yml 的 cells 一致
const int   MAX_STEPS = 96;
const float T_MAX = 30.0;
const float ENEMY_R = 0.62;   // 敌人造型半径（与内核命中判定同源）

float grid_at(int i, int j) {
    i = clamp(i, 0, GRID_N - 1);
    j = clamp(j, 0, GRID_N - 1);
    return u_grid[i * GRID_N + j];
}

// 迷宫的 2D SDF：对距离场做双线性采样。
// 格坐标约定与 CPU 的 cell_of/world_of **完全一致**：j ↔ x，i ↔ z。
float maze2d(vec2 p) {
    vec2 g = (p + u_half) / u_cell - 0.5;     // 连续格坐标（格中心为整数）
    vec2 fr = floor(g);
    vec2 t = g - fr;
    int i0 = int(fr.y);                       // 行 ↔ z
    int j0 = int(fr.x);                       // 列 ↔ x
    return mix(mix(grid_at(i0, j0),     grid_at(i0, j0 + 1), t.x),
               mix(grid_at(i0 + 1, j0), grid_at(i0 + 1, j0 + 1), t.x), t.y);
}

// 墙 = 2D 迷宫沿竖直方向拉伸到「地板→天花板」
float scene(vec3 p) {
    return max(maze2d(p.xz), max(p.y - u_wall_h, -p.y));
}

vec3 calc_normal(vec3 p, float t) {
    float e = 0.0015 + 0.0020 * t;
    vec2 h = vec2(e, 0.0);
    return normalize(vec3(scene(p + h.xyy) - scene(p - h.xyy),
                          scene(p + h.yxy) - scene(p - h.yxy),
                          scene(p + h.yyx) - scene(p - h.yyx)));
}

float sphere_t(vec3 ro, vec3 rd, vec3 c, float r) {
    vec3 oc = ro - c;
    float b = dot(oc, rd);
    float cc = dot(oc, oc) - r * r;
    float h = b * b - cc;
    if (h < 0.0) return 1e9;
    float t = -b - sqrt(h);
    return (t < 0.0) ? 1e9 : t;
}

vec3 wall_color(vec3 p, vec3 n) {
    float brick = step(0.06, fract(p.y * 1.6)) * step(0.05, fract((p.x + p.z) * 0.55));
    vec3 base = mix(vec3(0.085, 0.095, 0.115), vec3(0.15, 0.16, 0.19), brick);
    return mix(base, vec3(0.20, 0.22, 0.27), smoothstep(0.4, 1.0, n.y));
}

vec3 floor_color(vec3 p) {
    vec2 c = floor(p.xz * 0.7);
    return mix(vec3(0.055, 0.058, 0.070), vec3(0.10, 0.105, 0.125), mod(c.x + c.y, 2.0));
}

void main() {
    vec2 uv = (gl_FragCoord.xy - 0.5 * u_res) / u_res.y;
    float cy = cos(u_yaw), sy = sin(u_yaw);
    vec3 fw = vec3(cy, 0.0, sy);
    vec3 rt = vec3(-sy, 0.0, cy);
    vec3 up = vec3(0.0, 1.0, 0.0);
    vec3 rd = normalize(uv.x * rt + uv.y * up + 1.55 * fw);   // 1.55 ≈ 焦距

    // ---- 墙步进 ----
    float t = 0.0;
    bool hit = false;
    for (int i = 0; i < MAX_STEPS; i++) {
        vec3 p = u_eye + rd * t;
        float d = scene(p);
        if (d < 0.0018 * t + 0.0008) { hit = true; break; }
        t += d * 0.85;
        if (t > T_MAX) break;
    }
    float t_wall = hit ? t : 1e9;

    // ---- 敌人 / 光弹（解析球求交，只取比墙更近的）----
    float t_obj = 1e9;
    vec3  obj_col = vec3(0.0);
    vec3  obj_n = vec3(0.0, 1.0, 0.0);
    float obj_glow = 0.0;
    vec3  light_dir = normalize(vec3(-0.35, 0.80, 0.45));

    for (int k = 0; k < 16; k++) {
        vec4 e = u_enemy[k];
        float kid = floor(e.z);
        float fade = clamp((e.z - kid) / 0.9, 0.0, 1.0);
        float r = ENEMY_R * fade;
        float tt = (kid < 0.5 || r < 0.02)
                 ? 1e9 : sphere_t(u_eye, rd, vec3(e.x, 1.05, e.y), r);
        if (tt < t_obj && tt < t_wall) {
            t_obj = tt;
            vec3 p = u_eye + rd * tt;
            obj_n = normalize(p - vec3(e.x, 1.05, e.y));
            obj_col = (kid < 1.5) ? vec3(0.85, 0.28, 0.22)    // 游荡者：暖红
                                  : vec3(0.30, 0.72, 0.95);   // 哨卫：冷青
            obj_col = mix(obj_col, vec3(1.0), e.w);            // 受击白闪
            obj_glow = 0.35 + 0.5 * e.w;
        }
    }
    for (int k = 0; k < 8; k++) {
        vec4 b = u_bolt[k];
        float tt = (b.z < 0.5) ? 1e9 : sphere_t(u_eye, rd, vec3(b.x, 1.15, b.y), 0.20);
        if (tt < t_obj && tt < t_wall) {
            t_obj = tt;
            vec3 p = u_eye + rd * tt;
            obj_n = normalize(p - vec3(b.x, 1.15, b.y));
            obj_col = vec3(1.0, 0.85, 0.35);
            obj_glow = 1.0;
        }
    }

    // ---- 着色 ----
    vec3 col;
    float tmin = min(t_wall, t_obj);
    if (t_obj < t_wall) {
        float diff = max(dot(obj_n, light_dir), 0.0);
        col = obj_col * (0.45 + 0.75 * diff) + obj_col * obj_glow * 0.6;
    } else if (t_wall < 1e8) {
        vec3 p = u_eye + rd * t_wall;
        vec3 n = calc_normal(p, t_wall);
        vec3 base = (n.y > 0.7) ? floor_color(p) : wall_color(p, n);
        col = base * (0.28 + 0.9 * max(dot(n, light_dir), 0.0));
    } else {
        col = vec3(0.02, 0.022, 0.03);          // 兜底（射线打空）
    }

    // 指数雾：远处融进暗色，顺带藏掉 T_MAX 的硬边
    float fog = 1.0 - exp(-0.0026 * tmin * tmin - 0.030 * tmin);
    col = mix(col, vec3(0.015, 0.018, 0.026), fog);

    // 枪口闪光：屏幕下方一团暖光（只在开火后的几帧亮）
    float mz = u_muzzle * smoothstep(0.60, 0.28, v_uv.y)
             * smoothstep(0.30, 0.45, v_uv.x) * smoothstep(0.70, 0.55, v_uv.x);
    col += vec3(1.0, 0.82, 0.45) * mz * 0.55;

    // 准星：屏幕中心小十字
    vec2 dc = abs(v_uv - vec2(0.5));
    float cross = ((dc.x < 0.0016 && dc.y < 0.020)
                || (dc.y < 0.0026 && dc.x < 0.020)) ? 1.0 : 0.0;
    col = mix(col, vec3(0.95, 0.97, 1.0), cross * 0.75);

    // 受击红晕 + 暗角
    col = mix(col, vec3(0.65, 0.06, 0.06), u_hurt * 0.45);
    col *= mix(0.55, 1.0, smoothstep(1.15, 0.35, length(v_uv - 0.5) * 1.6));

    final_colors = vec4(col, 1.0);
}'''


class RayholdDemo(DemoApp):
    TITLE = '迷堡枪影 / Rayhold —— 射线 FPS（SDF 光线步进 · 程序化迷宫）'
    HELP = ('WASD 走   SHIFT 疾跑   鼠标拖动 转视角   左键（按住）射击   '
            'R 换一局   M 鼠标锁定   SPACE 暂停   ESC 退出')
    DURATION = None          # 交互型：time 不取模，持续累加
    HUD = True
    SHOTS = ((7.0, 'docs/rayhold_a.png'),      # 迷宫中段：转角与敌人
             (17.0, 'docs/rayhold_b.png'))     # 逼近出口（auto 模式约 19s 通关）

    LOOK_SPEED = 0.005       # 鼠标拖动 → yaw（弧度/px）

    def setup(self):
        glDisable(GL_DEPTH_TEST)                 # 全屏片元 pass，不需要深度
        self.program = ShaderProgram(Shader(VERTEX_SRC, 'vertex'),
                                     Shader(FRAGMENT_SRC, 'fragment'))
        self.quad = make_fullscreen_quad(self.program)

        # ---- Cordis 挂载（配置树驱动）----
        self.ctx, self.kernel = self._boot_kernel()
        maze = self.kernel.ctx.maze
        assert maze.n == GRID_N, \
            f'cordis.fps.yml 的 cells（{maze.n}）必须与着色器 GRID_N（{GRID_N}）一致'
        self._grid = self._grid_uniform()

        # ---- 输入 ----
        self.keys = set()
        self._firing = False
        self._locked = False
        self.window.push_handlers(
            on_mouse_press=self._on_press,
            on_mouse_release=self._on_release,
            on_mouse_motion=self._on_motion,
            on_key_press=self._on_key_press,
            on_key_release=self._on_key_release,
        )

        # ---- flags ----
        self.auto = 'auto' in self.args.flags
        for f in self.args.flags:
            if f.startswith('seed') and f[4:].isdigit():
                self.kernel.restart(int(f[4:]))
                self._grid = self._grid_uniform()

        # ---- 音效（自包含、best-effort；沙箱无驱动则静默）----
        self._init_audio()

        # ---- 胜负叠层 ----
        from engine.fonts import resolve_cjk_font
        fk = resolve_cjk_font()
        kw = {'font_name': fk} if fk else {}
        self.overlay = pyglet.text.Label('', font_size=54, color=(255, 230, 120, 255),
                                         anchor_x='center', anchor_y='center',
                                         x=self.window.width // 2,
                                         y=self.window.height // 2, **kw)

    # -------------------------------------------------- Cordis 挂载 --
    def _boot_kernel(self):
        """同步引导 cordis 配置树（tree_boot 是唯一挂载入口）。"""
        return asyncio.run(self._mount_kernel())

    async def _mount_kernel(self):
        ctx = await mount_tree(config_path('cordis.fps.yml'), want=WANT_FPS)
        return ctx, ctx.fps

    # -------------------------------------------------- uniform 打包 --
    def _grid_uniform(self):
        """迷宫距离场 → 长度 225 的 float 元组（与 maze.grid 的 [i][j] 同序）。"""
        df = self.kernel.ctx.maze.distance_field()
        flat = []
        for i in range(GRID_N):
            for j in range(GRID_N):
                flat.append(float(df[i][j]))
        return tuple(flat)

    def _enemy_uniform(self):
        """敌人 → 16×vec4：xy=位置，z=类型(整数位)+淡出(小数位/0.9)，w=受击白闪。"""
        out = [0.0] * (16 * 4)
        for k, e in enumerate(self.kernel.enemies[:16]):
            fade = 1.0 if e['state'] != 'DEAD' else max(0.0, 1.0 - e['dying'] / DEATH_T)
            kind = 1.0 if e['kind'] == 'stalker' else 2.0
            out[k * 4 + 0] = float(e['x'])
            out[k * 4 + 1] = float(e['z'])
            out[k * 4 + 2] = kind + 0.9 * fade
            out[k * 4 + 3] = float(e['flash'])
        return tuple(out)

    def _bolt_uniform(self):
        out = [0.0] * (8 * 4)
        for k, b in enumerate(self.kernel.bolts[:8]):
            out[k * 4 + 0] = float(b['x'])
            out[k * 4 + 1] = float(b['z'])
            out[k * 4 + 2] = 1.0
        return tuple(out)

    # -------------------------------------------------- 输入 --
    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.kernel.look(-dx * self.LOOK_SPEED)

    def _on_press(self, x, y, button, modifiers):
        if button == mouse.LEFT:
            self._firing = True

    def _on_release(self, x, y, button, modifiers):
        if button == mouse.LEFT:
            self._firing = False

    def _on_motion(self, x, y, dx, dy):
        # 指针锁定模式下，鼠标位移增量直接转视角（真正的 FPS 手感）；
        # 未锁定时忽略——避免与拖拽转视角叠加成"双倍灵敏度"。
        if self._locked:
            self.kernel.look(-dx * self.LOOK_SPEED)

    def _on_key_press(self, symbol, modifiers):
        self.keys.add(symbol)

    def _on_key_release(self, symbol, modifiers):
        self.keys.discard(symbol)

    def on_key(self, symbol, modifiers):
        if symbol == key.R:
            seed = (self.kernel.seed * 1103515245 + 12345) % (2 ** 31)
            self.kernel.restart(seed)
            self._grid = self._grid_uniform()
            print(f'[rayhold] new seed={self.kernel.seed}', flush=True)
        elif symbol == key.M:
            self._locked = not self._locked
            try:
                self.window.set_exclusive_mouse(self._locked)
                self.window.set_mouse_visible(not self._locked)
            except Exception as e:                  # 平台不支持就退回拖拽
                print('[rayhold] exclusive mouse unavailable:', e, flush=True)
                self._locked = False

    def _input_move(self, dt):
        fwd = (1.0 if key.W in self.keys else 0.0) - (1.0 if key.S in self.keys else 0.0)
        rgt = (1.0 if key.D in self.keys else 0.0) - (1.0 if key.A in self.keys else 0.0)
        if fwd or rgt:
            self.kernel.move(fwd, rgt, dt, sprint=(key.LSHIFT in self.keys))

    # -------------------------------------------------- 每帧 --
    def update(self, dt):
        if self.paused:
            return
        if self.auto:
            self.kernel.auto_step(dt)              # 与 headless 验收同一套逻辑
        else:
            self._input_move(dt)
            if self._firing:
                self.kernel.shoot()
        self.kernel.update(dt)
        for ev in self.kernel.drain_events():
            self._sfx(ev)

    # -------------------------------------------------- 渲染 --
    def render(self):
        w, h = self.window.width, self.window.height
        k = self.kernel
        p = k.player
        maze = k.ctx.maze
        prog = self.program
        prog.use()
        prog['u_res'] = (float(w), float(h))
        prog['u_eye'] = (float(p['x']), float(EYE), float(p['z']))
        prog['u_yaw'] = float(p['yaw'])
        prog['u_time'] = float(self.time)
        prog['u_cell'] = float(maze.cell)
        prog['u_half'] = float(maze.half)
        prog['u_wall_h'] = float(WALL_H)
        prog['u_grid'] = self._grid                # 数组 uniform：不带下标、一次赋整份
        prog['u_enemy'] = self._enemy_uniform()
        prog['u_bolt'] = self._bolt_uniform()
        prog['u_muzzle'] = float(k.muzzle)
        prog['u_hurt'] = float(k.hurt_flash)
        self.quad.draw(GL_TRIANGLES)
        self._draw_overlay(w, h)

    def _draw_overlay(self, w, h):
        st = self.kernel.ctx.rules.state()
        txt = {'won': '突 围 成 功', 'lost': '倒 在 迷 堡'}.get(st, '')
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
        self._apply_projection(w, h)

    # -------------------------------------------------- 音效 --
    def _init_audio(self):
        self.sfx = None
        try:
            d = tempfile.mkdtemp(prefix='rayhold_sfx_')
            self._sfx_dir = d
            self._make_sfx('shoot', 760, 170, 'saw', 0.12)
            self._make_sfx('hit', 330, 140, 'square', 0.08)
            self._make_sfx('kill', 190, 60, 'saw', 0.26)
            self._make_sfx('hurt', 230, 90, 'triangle', 0.30)
            self._make_sfx('pickup', 520, 880, 'sine', 0.18)
            self._make_sfx('bolt', 900, 480, 'sine', 0.15)
            self._make_sfx('dry', 150, 120, 'square', 0.06)
            self._make_sfx('win', 523, 784, 'triangle', 0.50)
            self._make_sfx('lose', 320, 90, 'saw', 0.60)
            from engine.audio import SfxPlayer
            self.sfx = SfxPlayer(d, names=('shoot', 'hit', 'kill', 'hurt', 'pickup',
                                           'bolt', 'dry', 'win', 'lose'), gain=0.5)
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
        r = k.ctx.rules
        st = {'prep': '探索', 'won': '突围成功', 'lost': '倒下'}.get(r.state(), r.state())
        ex_x, ex_z = k.ctx.maze.world_of(*k.ctx.maze.exit)
        dist = math.hypot(ex_x - k.player['x'], ex_z - k.player['z'])
        return (f'HP {r.base_hp:3.0f}/{r.base_hp0}   弹药 {k.ammo():3.0f}   '
                f'击杀 {k.kills}   敌 {len(k.enemies)}   '
                f'出口 {dist:5.1f}m   种子 {k.seed}   {st}   '
                f't={self.time:5.1f}s {self.fps:4.1f}fps'
                f'{"  [AUTO]" if self.auto else ""}'
                f'{"  [PAUSED]" if self.paused else ""}')


if __name__ == '__main__':
    run(RayholdDemo)
