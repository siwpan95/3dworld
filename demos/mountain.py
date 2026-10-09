# -*- coding: utf-8 -*-
"""《山》(Mountain) —— 用 terrain_flyover.py 的程序化地形管线复刻 David O'Reilly 的存在主义自然模拟。

本 demo 直接复用 terrain_flyover.py 的噪声原语（_hash01 / _value_noise / _fbm）与
「渐进式换种子不冻画面」的网格构建思路，但把"低空飞越的无限地形"改造成 Mountain 的
**漂浮在虚空中的一座山**：

- 程序化山体：值噪声 fBm 叠出单峰 + 底面下垂的"悬浮岛"造型（顶/底两面在边缘闭合）。
- 动态光照：顶点带法线，片元着色器按太阳方向实时算 lambert——于是昼夜、阴晴都在
  着色器里完成，不需要像 terrain_flyover 那样把光照烘焙进顶点色。
- 人格测验：开局用键 1~4 回答 4 道题，答案会**嵌进**这座山的生成参数（峰高/崎岖/
  植被/暖度），生成只属于你的山。
- 时间流逝：太阳绕山转 → 昼夜交替（暖→橙→月蓝），云、雨、雪随季节轮替。
- 生命感：随机物体从天而降落在山上；植物长出又凋零；山偶尔冒出一句"想法"。
- 键盘钢琴：A S D F G H J K 弹出五声音阶（即时合成 WAV，零外部素材）。
- 终局：流星坠落撞击 → 山崩解（缩放/下坠/淡出）→ 重生一座新山。

用法::

    python demos/mountain.py               正常运行（鼠标拖动转视角，滚轮缩放）
    python demos/mountain.py --smoke 3     冒烟：3 秒后自动关闭
    python demos/mountain.py --shot        抓两张验证截图（昼/夜）后关闭

按键：拖动/滚轮 视角   SPACE 暂停   N 重新做人格测验   R 随机新山
     C 召唤流星（强制崩解）   M 静音   1~4 答题（测验中）   A S D F G H J K 钢琴
"""
import array
import math
import os
import random
import sys
import time

# 复用 terrain_flyover 的纯噪声原语（同一套可复现哈希，跨平台逐位一致）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from terrain_flyover import _hash01, _value_noise, _fbm  # noqa: E402

import pyglet
from pyglet.gl import (GL_BLEND, GL_DEPTH_TEST, GL_ONE_MINUS_SRC_ALPHA,
                       GL_POINTS, GL_SRC_ALPHA, GL_TRIANGLES,
                       glBlendFunc, glClearColor, glDisable, glEnable)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4, Vec3
from pyglet.window import key as K

from _harness import DemoApp, orbit_camera_input, run
from logic.camera import OrbitCamera

try:
    from engine.audio import Track, render_wav
except Exception:  # 音频引擎缺失时钢琴退化为静默 no-op
    Track = None
    render_wav = None

TAU = 6.28318531
SPAN = 120.0          # 山体底面世界尺寸
HALF = SPAN * 0.5


# ============================================================ 小工具函数 ==
def _smooth(e0, e1, x):
    if e1 == e0:
        return 0.0
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def _mix3(a, b, t):
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


# ====================================================== 山体高度场（复用噪声）==
def mountain_field(x, z, P):
    """世界坐标 -> (顶面高, 底面高)。P 为人格参数 dict。

    形状逻辑：归一化半径 r∈[0,1]，dome=(1-r²)² 把质量压在中心形成单峰；
    叠加 fBm 的"脊线"做出山脊棱角；再乘暖度/峰高/崎岖等人格参数。
    边缘用 smoothstep 蒙版收口到 y=0，使顶/底两面在边界闭合，看上去像一整块
    悬浮岩。
    """
    nx, nz = x / HALF, z / HALF
    r2 = nx * nx + nz * nz
    dome = max(0.0, 1.0 - r2)
    d2 = dome * dome
    lump = _fbm(x * 0.010, z * 0.010, P['seed'] + 131, 4)        # 大尺度起伏
    ridg = _fbm(x * 0.022 + 7.0, z * 0.022 - 4.0, P['seed'] + 977, 4)
    ridge = 1.0 - abs(2.0 * ridg - 1.0)                          # 折叠成尖脊
    h = (P['peak'] * 46.0) * d2 * (0.55 + 0.45 * ridge)
    h += 9.0 * (lump - 0.5) * d2
    top = max(0.0, h)
    bot = -P['keel'] * (dome ** 1.7)
    # 边界蒙版：border 28% 内平滑收到 0 —— 收得越紧，悬浮岛的"平裙边"越小
    e = 1.0 - max(abs(nx), abs(nz))
    m = _smooth(0.0, 0.28, e)
    return (top * m, bot * m)


def top_albedo(h, slope, P):
    """顶面反照率（仅颜色，光照在片元着色器里算）。"""
    snow_t = _smooth(P['snow'] - 6.0, P['snow'] + 4.0, h) * (1.0 - 0.5 * slope)
    # 岩石：陡坡露出 + 中海拔过渡带（草->岩->雪 的层次感）
    rock_t = max(_smooth(0.22, 0.52, slope), _smooth(14.0, 24.0, h) * 0.55)
    w = P['warm']
    grass = (0.20 + 0.10 * w, 0.42 + 0.06 * P['veg'], 0.16)
    rock = (0.42 + 0.05 * w, 0.37, 0.32 - 0.03 * w)
    c = _mix3(grass, rock, rock_t)
    c = _mix3(c, (0.93, 0.95, 0.98), snow_t)
    c = _mix3(c, (0.30, 0.28, 0.26), _smooth(3.0, 0.0, h) * 0.6)  # 底部裙边略暗
    return c


def bot_albedo(depth_frac, P):
    """底面反照率：越深越暗的冷灰岩。"""
    return _mix3((0.30, 0.27, 0.25), (0.05, 0.05, 0.08), min(1.0, depth_frac))


# ============================================================== 着色器 ==
LIT_VERT = '''#version 330 core
in vec3 position;
in vec3 normal;
in vec4 color;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 u_model;
out vec4 v_col;
out vec3 v_normal;
out vec3 v_world;
void main() {
    vec4 wp = u_model * vec4(position, 1.0);
    v_world = wp.xyz;
    v_normal = mat3(u_model) * normal;
    v_col = color;
    gl_Position = window.projection * window.view * wp;
}'''

LIT_FRAG = '''#version 330 core
in vec4 v_col;
in vec3 v_normal;
in vec3 v_world;
out vec4 final_colors;
uniform vec3 u_sunDir;
uniform vec3 u_sunColor;
uniform vec3 u_ambient;
uniform vec3 u_eye;
uniform vec3 u_fog;
uniform float u_fogDensity;
uniform float u_alpha;
void main() {
    vec3 N = normalize(v_normal);
    float ndl = abs(dot(N, normalize(u_sunDir)));   // 双面受光，底面不发黑
    vec3 lit = u_ambient + u_sunColor * ndl;
    vec3 col = v_col.rgb * lit;
    float d = distance(v_world, u_eye);
    float f = 1.0 - exp(-d * u_fogDensity);
    col = mix(col, u_fog, clamp(f, 0.0, 1.0));
    final_colors = vec4(col, v_col.a * u_alpha);
}'''

PT_VERT = '''#version 330 core
in vec3 position;
in vec4 color;
in float psize;
uniform WindowBlock { mat4 projection; mat4 view; } window;
out vec4 v_col;
void main() {
    v_col = color;
    gl_PointSize = psize;
    gl_Position = window.projection * window.view * vec4(position, 1.0);
}'''

PT_FRAG = '''#version 330 core
in vec4 v_col;
out vec4 final_colors;
void main() {
    vec2 c = gl_PointCoord - vec2(0.5);
    float r2 = dot(c, c);
    if (r2 > 0.25) discard;
    float a = smoothstep(0.25, 0.0, r2) * v_col.a;
    final_colors = vec4(v_col.rgb, a);
}'''

MAX_POINTS = 3200


# ============================================================== 应用 ==
class MountainApp(DemoApp):
    TITLE = 'Mountain — 一座山的存在主义自然模拟'
    HELP = ('拖动/滚轮 视角   SPACE 暂停   N 人格测验   R 随机新山   '
            'C 流星   M 静音   A S D F G H J K 钢琴')
    SHOTS = ((6.0, 'docs/mountain_day.png'), (45.0, 'docs/mountain_dusk.png'))
    DURATION = None           # 不循环，self.time 持续累加
    HUD = True
    FOV = 50.0

    GRID = 96                # 高程图边长（格点）
    STEP = SPAN / (GRID - 1)
    REBUILD_BUDGET_MS = 10.0
    DAY = 90.0              # 一个昼夜的秒数

    # ----- 人格测验（答案嵌进生成参数）-----
    QUESTIONS = [
        ('你最珍视的是？', ['自由', '宁静', '联结', '高度'], 'veg',
         [0.20, 0.62, 0.92, 0.45]),
        ('你如何面对变化？', ['顺应', '塑造', '回避', '对抗'], 'sharp',
         [1.6, 2.6, 1.0, 3.2]),
        ('你心中的高度？', ['低丘', '山峦', '高峰', '云端'], 'peak',
         [0.72, 1.0, 1.22, 1.5]),
        ('你偏爱的温度？', ['凛冽', '清冷', '温和', '炽热'], 'warm',
         [0.04, 0.30, 0.62, 0.96]),
    ]
    THOUGHTS = [
        'I am a mountain.', 'The wind remembers nothing.',
        'Things grow and things die.', 'You are very small.',
        'I have always been here.', 'A star will find me.',
        'Time moves forward.', 'Stillness is also a kind of motion.',
        'I am made of your answers.', 'The snow does not ask why.',
        '我是一座山。', '你来了，又走了。', '万物生长，亦寂灭。',
    ]

    # -------------------------------------------------- 生命周期 --
    def setup(self):
        glEnable(GL_DEPTH_TEST)
        glClearColor(0.02, 0.03, 0.06, 1.0)
        self.Z_NEAR, self.Z_FAR = 0.1, 4000.0

        self.lit = ShaderProgram(Shader(LIT_VERT, 'vertex'),
                                 Shader(LIT_FRAG, 'fragment'))
        self.pts = ShaderProgram(Shader(PT_VERT, 'vertex'),
                                Shader(PT_FRAG, 'fragment'))

        # 点云缓冲（星/日/月/云/雨雪 全在这一个 buffer 里每帧重建）
        self.pt_pos = [0.0] * (MAX_POINTS * 3)
        self.pt_col = [0.0] * (MAX_POINTS * 4)
        self.pt_sz = [0.0] * MAX_POINTS
        self.pt_vlist = self.pts.vertex_list(
            MAX_POINTS, GL_POINTS,
            position=('f', self.pt_pos),
            color=('f', self.pt_col), psize=('f', self.pt_sz))

        # 星：固定在大球壳上
        self.stars = []
        rng = random.Random(7)
        for _ in range(1300):
            u = rng.random() * TAU
            v = math.acos(2.0 * rng.random() - 1.0)
            R = 1600.0
            self.stars.append((R * math.sin(v) * math.cos(u),
                               R * math.cos(v),
                               R * math.sin(v) * math.sin(u),
                               rng.uniform(0.5, 1.0)))

        # 云：若干团在固定高度带缓慢绕 y 漂移
        self.clouds = []
        for _ in range(60):
            a = rng.random() * TAU
            self.clouds.append([a, rng.uniform(55.0, 80.0),
                                rng.uniform(0.4, 0.9)])

        self.mountain = None
        self.mountain_model = Mat4()
        self.params = self._default_params()
        self._rebuild_sync(self.params)

        # 相机：绕山体缓慢自转，鼠标可接管
        self.camera = OrbitCamera(target=Vec3(0.0, 6.0, 0.0), yaw=35.0,
                                  pitch=14.0, dist=120.0,
                                  pitch_range=(3.0, 88.0),
                                  dist_range=(30.0, 280.0))
        self.on_mouse_drag, self.on_mouse_scroll = orbit_camera_input(
            self, self.camera)

        # 生命体
        self.objects = []        # 落在山上的物体 {mesh,x,y,z,s,rot,rotv}
        self.plants = []         # 植物 {mesh,x,y,z,age,life,maxs}
        self.obj_timer = 4.0
        self.plant_timer = 6.0
        self.particles = []      # 雨/雪
        self.debris = []         # 崩解碎屑

        # 气象 / 昼夜
        self.weather = 'clear'
        self.weather_t = 0.0
        self.sun_dir = Vec3(0, 1, 0)
        self.sun_color = (1, 1, 1)
        self.ambient = (0.3, 0.3, 0.3)
        self.fog_color = (0.02, 0.03, 0.06)

        # 测验 / 想法
        self.quiz = {'active': False, 'idx': 0, 'P': self._default_params()}
        self.thought = random.choice(self.THOUGHTS)
        self.thought_t = 0.0
        self.meteor = None       # {pos,vel,t}
        self.collapse = None      # {t} 进行中
        self.meteor_schedule = 95.0

        # 钢琴（即时合成五声音阶 WAV）
        self._build_piano()

        # 居中想法 / 测验文字叠层
        from engine.fonts import resolve_cjk_font
        cjk = resolve_cjk_font()
        kw = {'font_name': cjk} if cjk else {}
        self._overlay = pyglet.text.Label(
            '', font_size=20, color=(235, 238, 245, 255),
            anchor_x='center', anchor_y='center', multiline=True,
            width=int(self.window.width * 0.82), align='center', **kw)

    def _default_params(self):
        rng = random.Random()
        return {'seed': rng.randrange(1, 10 ** 6),
                'peak': 1.0, 'sharp': 2.2, 'snow': 34.0,
                'veg': 0.6, 'warm': 0.5, 'keel': 26.0}

    # ------------------------------------------------- 渐进式重建（复用思路）--
    def _rebuild_begin(self, P):
        n = self.GRID
        self.params = dict(P)
        self._rb = {'n': n, 'row': 0, 'phase': 0,
                    'topH': [0.0] * (n * n), 'botH': [0.0] * (n * n),
                    'pos': array.array('f'), 'nrm': array.array('f'),
                    'col': array.array('f'),
                    'total': 3 * (n - 1), 'done': 0, 't': time.perf_counter()}

    def _rebuild_sync(self, P):
        self._rebuild_begin(P)
        while self._rebuild_step(1e9):
            pass

    def _rebuild_step(self, budget_ms=10.0):
        rb = self._rb
        if rb is None:
            return False
        n = rb['n']
        step = self.STEP
        ox, oz = -HALF, -HALF
        topH, botH = rb['topH'], rb['botH']
        P = self.params
        deadline = time.perf_counter() + budget_ms / 1000.0
        while True:
            if rb['phase'] == 0:
                j = rb['row']
                z = oz + j * step
                base = j * n
                for i in range(n):
                    t, b = mountain_field(ox + i * step, z, P)
                    topH[base + i] = t
                    botH[base + i] = b
                rb['row'] += 1
                rb['done'] += 1
                if rb['row'] >= n:
                    rb['phase'], rb['row'] = 1, 0
            elif rb['phase'] == 1:
                self._emit_top_row(rb, rb['row'], topH, botH, n, step, ox, oz)
                rb['row'] += 1
                rb['done'] += 1
                if rb['row'] >= n - 1:
                    rb['phase'], rb['row'] = 2, 0
            else:
                self._emit_bot_row(rb, rb['row'], topH, botH, n, step, ox, oz)
                rb['row'] += 1
                rb['done'] += 1
                if rb['row'] >= n - 1:
                    self._rebuild_finish()
                    return False
            if time.perf_counter() >= deadline:
                return True

    def _corner_top(self, i, j, topH, n, step):
        il, ir = max(0, i - 1), min(n - 1, i + 1)
        jd, ju = max(0, j - 1), min(n - 1, j + 1)
        hl = topH[j * n + il]
        hr = topH[j * n + ir]
        hd = topH[jd * n + i]
        hu = topH[ju * n + i]
        dx = (hr - hl) / max(1e-3, (ir - il) * step)
        dz = (hu - hd) / max(1e-3, (ju - jd) * step)
        nx, ny, nz = -dx, 1.0, -dz
        l = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nx, ny, nz = nx / l, ny / l, nz / l
        h = topH[j * n + i]
        r, g, b = top_albedo(h, 1.0 - ny, self.params)
        # 哈希抖动打散色块（terrain_flyover 同款手法）
        jv = 0.86 + 0.28 * _hash01(i, j, self.params['seed'] + 4241)
        return (nx, ny, nz, min(1.0, r * jv), min(1.0, g * jv), min(1.0, b * jv), 1.0)

    def _corner_bot(self, i, j, botH, n, step):
        il, ir = max(0, i - 1), min(n - 1, i + 1)
        jd, ju = max(0, j - 1), min(n - 1, j + 1)
        hl = botH[j * n + il]
        hr = botH[j * n + ir]
        hd = botH[jd * n + i]
        hu = botH[ju * n + i]
        dx = (hr - hl) / max(1e-3, (ir - il) * step)
        dz = (hu - hd) / max(1e-3, (ju - jd) * step)
        nx, ny, nz = dx, -1.0, dz          # 朝外的法线（向下）
        l = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nx, ny, nz = nx / l, ny / l, nz / l
        depth = -botH[j * n + i] / max(1e-3, self.params['keel'])
        r, g, b = bot_albedo(depth, self.params)
        jv = 0.92 + 0.16 * _hash01(i, j, self.params['seed'] + 4241)
        return (nx, ny, nz, min(1.0, r * jv), min(1.0, g * jv), min(1.0, b * jv), 1.0)

    @staticmethod
    def _v(pos, nrm, col, x, y, z, c):
        pos.extend((x, y, z))
        nrm.extend(c[:3])
        col.extend(c[3:])

    def _emit_top_row(self, rb, j, topH, botH, n, step, ox, oz):
        pos, nrm, col = rb['pos'], rb['nrm'], rb['col']
        for i in range(n - 1):
            ya = topH[j * n + i]
            yb = topH[j * n + i + 1]
            yc = topH[(j + 1) * n + i]
            yd = topH[(j + 1) * n + i + 1]
            # 顶/底同时归零的外圈四边形直接跳过 —— 去掉方形"平板裙边"，
            # 让悬浮岛轮廓自然收圆；跳过边界处两面都在 1e-3 内，无缝隙。
            if max(abs(ya), abs(yb), abs(yc), abs(yd)) < 1e-3:
                ba, bb = botH[j * n + i], botH[j * n + i + 1]
                bc, bd = botH[(j + 1) * n + i], botH[(j + 1) * n + i + 1]
                if max(abs(ba), abs(bb), abs(bc), abs(bd)) < 1e-3:
                    continue
            x0 = ox + i * step
            x1 = x0 + step
            z0 = oz + j * step
            z1 = z0 + step
            A = self._corner_top(i, j, topH, n, step)
            B = self._corner_top(i + 1, j, topH, n, step)
            C = self._corner_top(i, j + 1, topH, n, step)
            D = self._corner_top(i + 1, j + 1, topH, n, step)
            self._v(pos, nrm, col, x0, ya, z0, A)
            self._v(pos, nrm, col, x1, yb, z0, B)
            self._v(pos, nrm, col, x1, yd, z1, D)
            self._v(pos, nrm, col, x0, ya, z0, A)
            self._v(pos, nrm, col, x1, yd, z1, D)
            self._v(pos, nrm, col, x0, yc, z1, C)

    def _emit_bot_row(self, rb, j, topH, botH, n, step, ox, oz):
        pos, nrm, col = rb['pos'], rb['nrm'], rb['col']
        for i in range(n - 1):
            ya = botH[j * n + i]
            yb = botH[j * n + i + 1]
            yc = botH[(j + 1) * n + i]
            yd = botH[(j + 1) * n + i + 1]
            if max(abs(ya), abs(yb), abs(yc), abs(yd)) < 1e-3:
                ta, tb = topH[j * n + i], topH[j * n + i + 1]
                tc, td = topH[(j + 1) * n + i], topH[(j + 1) * n + i + 1]
                if max(abs(ta), abs(tb), abs(tc), abs(td)) < 1e-3:
                    continue
            x0 = ox + i * step
            x1 = x0 + step
            z0 = oz + j * step
            z1 = z0 + step
            A = self._corner_bot(i, j, botH, n, step)
            B = self._corner_bot(i + 1, j, botH, n, step)
            C = self._corner_bot(i, j + 1, botH, n, step)
            D = self._corner_bot(i + 1, j + 1, botH, n, step)
            self._v(pos, nrm, col, x0, ya, z0, A)
            self._v(pos, nrm, col, x1, yd, z1, D)
            self._v(pos, nrm, col, x1, yb, z0, B)
            self._v(pos, nrm, col, x0, ya, z0, A)
            self._v(pos, nrm, col, x0, yc, z1, C)
            self._v(pos, nrm, col, x1, yd, z1, D)

    def _rebuild_finish(self):
        rb = self._rb
        if self.mountain is not None:
            self.mountain.delete()
        self.mountain = self.lit.vertex_list(
            len(rb['pos']) // 3, GL_TRIANGLES,
            position=('f', rb['pos']), normal=('f', rb['nrm']),
            color=('f', rb['col']))
        self.tris = len(rb['pos']) // 9
        self._rb = None

    # ------------------------------------------------- 小网格（物体/植物）--
    def _make_octahedron(self, radius, rgb):
        verts = [(radius, 0, 0), (-radius, 0, 0), (0, radius, 0),
                 (0, -radius, 0), (0, 0, radius), (0, 0, -radius)]
        faces = [(0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4),
                 (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)]
        pos, nrm, col = array.array('f'), array.array('f'), array.array('f')
        for f in faces:
            p = [verts[k] for k in f]
            ax, ay, az = p[1][0] - p[0][0], p[1][1] - p[0][1], p[1][2] - p[0][2]
            bx, by, bz = p[2][0] - p[0][0], p[2][1] - p[0][1], p[2][2] - p[0][2]
            nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
            l = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            nx, ny, nz = nx / l, ny / l, nz / l
            for k in f:
                pos.extend(verts[k])
                nrm.extend((nx, ny, nz))
                col.extend((rgb[0], rgb[1], rgb[2], 1.0))
        return self.lit.vertex_list(len(pos) // 3, GL_TRIANGLES,
                                    position=('f', pos), normal=('f', nrm),
                                    color=('f', col))

    def _make_cone(self, radius, height, rgb):
        segs = 7
        pos, nrm, col = array.array('f'), array.array('f'), array.array('f')
        apex = (0.0, height, 0.0)
        for s in range(segs):
            a0 = s / segs * TAU
            a1 = (s + 1) / segs * TAU
            x0, z0 = radius * math.cos(a0), radius * math.sin(a0)
            x1, z1 = radius * math.cos(a1), radius * math.sin(a1)
            # 侧 face: apex, (x0,z0,0), (x1,z1,0)
            ax, ay, az = x0 - apex[0], -apex[1], z0 - apex[2]
            bx, by, bz = x1 - apex[0], -apex[1], z1 - apex[2]
            nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
            l = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            nx, ny, nz = nx / l, ny / l, nz / l
            for (px, py, pz) in (apex, (x0, 0, z0), (x1, 0, z1)):
                pos.extend((px, py, pz))
                nrm.extend((nx, ny, nz))
                col.extend((rgb[0], rgb[1], rgb[2], 1.0))
        # 底面
        for s in range(segs):
            a0 = s / segs * TAU
            a1 = (s + 1) / segs * TAU
            x0, z0 = radius * math.cos(a0), radius * math.sin(a0)
            x1, z1 = radius * math.cos(a1), radius * math.sin(a1)
            for (px, py, pz) in ((0, 0, 0), (x0, 0, z0), (x1, 0, z1)):
                pos.extend((px, py, pz))
                nrm.extend((0, -1, 0))
                col.extend((rgb[0] * 0.6, rgb[1] * 0.6, rgb[2] * 0.6, 1.0))
        return self.lit.vertex_list(len(pos) // 3, GL_TRIANGLES,
                                    position=('f', pos), normal=('f', nrm),
                                    color=('f', col))

    # ------------------------------------------------- 钢琴（即时合成）--
    def _build_piano(self):
        self.notes = []
        self.muted = False
        if Track is None or render_wav is None:
            return
        import tempfile
        base = 220.0
        semis = [0, 2, 4, 7, 9, 12, 14, 16]     # A 大调五声，越听越禅
        self.piano_keys = [K.A, K.S, K.D, K.F, K.G, K.H, K.J, K.K]
        tmp = tempfile.gettempdir()
        for i, s in enumerate(semis):
            freq = base * (2.0 ** (s / 12.0))
            tr = Track(0.8, rate=22050)
            tr.tone(0.0, 0.7, freq, wave='tri', amp=0.3,
                    attack=0.008, release=0.28, lp=0.5)
            path = os.path.join(tmp, f'mountain_note_{i}.wav')
            try:
                render_wav(tr, path)
                src = pyglet.media.load(path, streaming=False)
                self.notes.append(src)
            except Exception:
                self.notes.append(None)

    def _play_note(self, idx):
        if self.muted or idx >= len(self.notes) or self.notes[idx] is None:
            return
        try:
            p = self.notes[idx].play()
            if p is not None:
                p.volume = 0.8
        except Exception:
            pass

    # ------------------------------------------------- 气象 / 昼夜 --
    def _update_sky(self):
        a = self.time / self.DAY * TAU
        sx, sy, sz = math.cos(a), math.sin(a), 0.35
        l = math.sqrt(sx * sx + sy * sy + sz * sz) or 1.0
        self.sun_dir = Vec3(sx / l, sy / l, sz / l)
        elev = self.sun_dir.y
        day = _smooth(-0.15, 0.30, elev)
        # 太阳色：地平线橙 -> 高空暖白
        warm = _mix3((1.0, 0.55, 0.25), (1.0, 0.97, 0.85),
                     min(1.0, max(0.0, elev * 1.2)))
        sun_int = 0.12 + (1.15 - 0.12) * day
        self.sun_color = (warm[0] * sun_int, warm[1] * sun_int, warm[2] * sun_int)
        if elev <= 0.0:     # 夜间用对向月光补一点冷光
            m = _mix3((0.0, 0.0, 0.0), (0.45, 0.55, 0.8), _smooth(-0.3, 0.0, elev))
            self.sun_color = (self.sun_color[0] + m[0] * 0.4,
                              self.sun_color[1] + m[1] * 0.4,
                              self.sun_color[2] + m[2] * 0.4)
            self.sun_dir = Vec3(-self.sun_dir.x, -self.sun_dir.y, -self.sun_dir.z)
        self.ambient = _mix3((0.05, 0.07, 0.14), (0.40, 0.45, 0.52), day)
        self.fog_color = _mix3((0.015, 0.02, 0.05), (0.05, 0.07, 0.13), day)
        glClearColor(self.fog_color[0], self.fog_color[1], self.fog_color[2], 1.0)

    def _update_weather(self, dt):
        self.weather_t -= dt
        if self.weather_t <= 0.0:
            self.weather = random.Random().choice(
                ['clear', 'clear', 'cloud', 'rain', 'snow'])
            self.weather_t = random.uniform(18.0, 30.0)
        # 云绕 y 缓慢漂移
        for c in self.clouds:
            c[0] += dt * 0.05
        # 雨/雪粒子
        target = 0
        if self.weather == 'rain':
            target = 1100
        elif self.weather == 'snow':
            target = 600
        # 补充
        rng = random.Random(int(self.time * 7) + len(self.particles))
        while len(self.particles) < target:
            ang = rng.random() * TAU
            r = rng.random() ** 0.5 * 52.0
            self.particles.append(
                [r * math.cos(ang), rng.uniform(20.0, 85.0), r * math.sin(ang),
                 -1.0 if self.weather == 'rain' else -0.35])
        # 更新下落
        for p in self.particles:
            p[1] += p[3] * dt * (1.0 if self.weather == 'rain' else 0.6)
            top = mountain_field(p[0], p[2], self.params)[0]
            if p[1] < top:
                ang = rng.random() * TAU
                r = rng.random() ** 0.5 * 52.0
                p[0], p[2] = r * math.cos(ang), r * math.sin(ang)
                p[1] = rng.uniform(70.0, 90.0)
        if len(self.particles) > target:
            self.particles = self.particles[:target]

    # ------------------------------------------------- 生命体 --
    def _spawn_object(self):
        rng = random.Random()
        ang = rng.random() * TAU
        r = rng.random() ** 0.5 * 50.0
        x, z = r * math.cos(ang), r * math.sin(ang)
        y = mountain_field(x, z, self.params)[0]
        palette = [(0.85, 0.30, 0.30), (0.30, 0.55, 0.85), (0.95, 0.80, 0.25),
                   (0.55, 0.85, 0.45), (0.80, 0.50, 0.85)]
        s = rng.uniform(2.5, 5.5)
        mesh = self._make_octahedron(s, rng.choice(palette))
        self.objects.append({'mesh': mesh, 'x': x, 'y': y + s * 0.6, 'z': z,
                             's': s, 'rot': rng.random() * TAU,
                             'rotv': rng.uniform(-0.4, 0.4)})
        if len(self.objects) > 24:
            old = self.objects.pop(0)
            old['mesh'].delete()

    def _spawn_plant(self):
        rng = random.Random()
        ang = rng.random() * TAU
        r = rng.random() ** 0.5 * 48.0
        x, z = r * math.cos(ang), r * math.sin(ang)
        y = mountain_field(x, z, self.params)[0]
        if y < 1.0:
            return
        greens = [(0.30, 0.6, 0.25), (0.45, 0.7, 0.35), (0.25, 0.5, 0.3)]
        mesh = self._make_cone(rng.uniform(1.2, 2.2), rng.uniform(3.0, 6.0),
                               rng.choice(greens))
        self.plants.append({'mesh': mesh, 'x': x, 'y': y, 'z': z,
                            'age': 0.0, 'life': rng.uniform(16.0, 26.0),
                            'maxs': 1.0})

    # ------------------------------------------------- 流星 / 崩解 --
    def _launch_meteor(self):
        if self.meteor or self.collapse:
            return
        rng = random.Random()
        ang = rng.random() * TAU
        self.meteor = {'pos': Vec3(220 * math.cos(ang), 150.0, 220 * math.sin(ang)),
                       't': 0.0}
        # 朝山体中心上方飞
        self.meteor['vel'] = Vec3(-self.meteor['pos'].x / 2.4,
                                  -42.0, -self.meteor['pos'].z / 2.4)

    def _impact(self):
        # 崩解开始：缩放/下坠/淡出，并爆出碎屑
        self.collapse = {'t': 0.0}
        self.meteor = None
        rng = random.Random()
        for _ in range(120):
            ang = rng.random() * TAU
            r = rng.random() * 30.0
            self.debris.append([r * math.cos(ang), rng.uniform(0, 40),
                                r * math.sin(ang),
                                rng.uniform(-12, 12), rng.uniform(-18, -4),
                                rng.uniform(-12, 12), rng.uniform(0.6, 1.6)])

    def _rebirth(self):
        self.params = self._default_params()
        self._rebuild_sync(self.params)
        for o in self.objects:
            o['mesh'].delete()
        for p in self.plants:
            p['mesh'].delete()
        self.objects = []
        self.plants = []
        self.debris = []
        self.meteor = None
        self.collapse = None
        self.mountain_model = Mat4()
        self.meteor_schedule = self.time + 95.0

    # ----------------------------------------------------- 逐帧 --
    def update(self, dt):
        if self._rb is not None:
            self._rebuild_step(self.REBUILD_BUDGET_MS)
        if self.paused:
            return
        self._update_sky()
        self._update_weather(dt)

        # 节律：想法轮换
        self.thought_t -= dt
        if self.thought_t <= 0.0:
            self.thought = random.choice(self.THOUGHTS)
            self.thought_t = random.uniform(9.0, 14.0)

        # 物体 / 植物生成节律
        self.obj_timer -= dt
        if self.obj_timer <= 0.0 and not self.collapse:
            self._spawn_object()
            self.obj_timer = random.uniform(7.0, 13.0)
        self.plant_timer -= dt
        if self.plant_timer <= 0.0 and not self.collapse:
            self._spawn_plant()
            self.plant_timer = random.uniform(5.0, 10.0)

        # 物体自转
        for o in self.objects:
            o['rot'] += o['rotv'] * dt

        # 植物生长 / 凋零
        for p in self.plants:
            p['age'] += dt
        self.plants = [p for p in self.plants if p['age'] < p['life']]
        for p in self.plants:
            if p['mesh'] is not None and p['age'] > p['life'] - 1:
                p['mesh'].delete()
                p['mesh'] = None

        # 流星
        if self.meteor is not None:
            m = self.meteor
            m['t'] += dt
            m['pos'] = Vec3(m['pos'].x + m['vel'].x * dt,
                            m['pos'].y + m['vel'].y * dt,
                            m['pos'].z + m['vel'].z * dt)
            if m['pos'].y < 30.0 or m['t'] > 5.0:
                self._impact()
        # 定时召唤流星
        if (self.collapse is None and self.meteor is None
                and self.time >= self.meteor_schedule):
            self._launch_meteor()

        # 崩解动画
        if self.collapse is not None:
            self.collapse['t'] += dt
            k = min(1.0, self.collapse['t'] / 3.0)
            s = 1.0 - 0.8 * k
            self.mountain_model = (Mat4.from_translation(Vec3(0, -34.0 * k, 0))
                                   @ Mat4.from_scale(Vec3(s, s, s)))
            self._collapse_alpha = 1.0 - 0.92 * k
            # 碎屑飞散
            for d in self.debris:
                d[0] += d[3] * dt
                d[1] += d[4] * dt
                d[2] += d[5] * dt
                d[4] += 14.0 * dt   # 重力
            if self.collapse['t'] >= 3.2:
                self._rebirth()
        else:
            self._collapse_alpha = 1.0

        # 相机缓慢自转（用户拖动会叠在上面）
        self.camera.advance(dt * 1.2)

    # ----------------------------------------------------- 渲染 --
    def render(self):
        if self.mountain is None:
            return
        cam = self.camera
        self.window.view = cam.view_matrix()
        eye = cam.eye

        # ---- 山体 ----
        self.lit.use()
        self.lit['u_model'] = self.mountain_model
        self.lit['u_sunDir'] = (self.sun_dir.x, self.sun_dir.y, self.sun_dir.z)
        self.lit['u_sunColor'] = self.sun_color
        self.lit['u_ambient'] = self.ambient
        self.lit['u_eye'] = (eye.x, eye.y, eye.z)
        self.lit['u_fog'] = self.fog_color
        self.lit['u_fogDensity'] = 0.0016
        self.lit['u_alpha'] = getattr(self, '_collapse_alpha', 1.0)
        self.mountain.draw(GL_TRIANGLES)

        # ---- 落在山上的物体 ----
        for o in self.objects:
            M = (Mat4.from_translation(Vec3(o['x'], o['y'], o['z']))
                 @ Mat4.from_rotation(o['rot'], Vec3(0.3, 1.0, 0.2))
                 @ Mat4.from_scale(Vec3(1, 1, 1)))
            self.lit['u_model'] = M
            self.lit['u_alpha'] = 1.0
            o['mesh'].draw(GL_TRIANGLES)

        # ---- 植物 ----
        for p in self.plants:
            if p['mesh'] is None:
                continue
            grow = min(1.0, p['age'] / 2.0)
            # 凋零末段淡出
            fade = 1.0
            if p['age'] > p['life'] - 3.0:
                fade = max(0.0, (p['life'] - p['age']) / 3.0)
            M = Mat4.from_translation(Vec3(p['x'], p['y'], p['z'])) \
                @ Mat4.from_scale(Vec3(grow, grow, grow))
            self.lit['u_model'] = M
            self.lit['u_alpha'] = fade
            p['mesh'].draw(GL_TRIANGLES)

        # ---- 点云（星/日/月/云/雨/雪/碎屑）----
        self._fill_points()
        self.pts.use()
        self.pt_vlist.position[:] = self.pt_pos
        self.pt_vlist.color[:] = self.pt_col
        self.pt_vlist.psize[:] = self.pt_sz
        self.pt_vlist.draw(GL_POINTS)

    def _fill_points(self):
        pos, col, sz = self.pt_pos, self.pt_col, self.pt_sz
        n = 0

        def put(x, y, z, r, g, b, a, s):
            nonlocal n
            if n >= MAX_POINTS:
                return
            i3 = n * 3
            pos[i3], pos[i3 + 1], pos[i3 + 2] = x, y, z
            i4 = n * 4
            col[i4], col[i4 + 1], col[i4 + 2], col[i4 + 3] = r, g, b, a
            sz[n] = s
            n += 1

        # 星
        for (x, y, z, b) in self.stars:
            put(x, y, z, 1.0, 1.0, 0.95, b * 0.9, 2.0)
        # 太阳 / 月亮
        sd = self.sun_dir
        R = 1200.0
        put(sd.x * R, sd.y * R, sd.z * R, *self.sun_color, 1.0, 46.0)
        # 云
        for c in self.clouds:
            x = 58.0 * math.cos(c[0])
            z = 58.0 * math.sin(c[0])
            put(x, c[1], z, 0.9, 0.92, 0.96, c[2] * 0.45, 24.0)
        # 雨 / 雪
        if self.weather == 'rain':
            for p in self.particles:
                put(p[0], p[1], p[2], 0.6, 0.72, 0.95, 0.5, 2.2)
        elif self.weather == 'snow':
            for p in self.particles:
                put(p[0], p[1], p[2], 1.0, 1.0, 1.0, 0.85, 4.0)
        # 崩解碎屑
        for d in self.debris:
            put(d[0], d[1], d[2], 0.7, 0.6, 0.5, 0.9, 5.0)
        # 流星（拖尾亮点）
        if self.meteor is not None:
            m = self.meteor['pos']
            put(m.x, m.y, m.z, 1.0, 0.9, 0.7, 1.0, 22.0)
            put(m.x + 8, m.y + 3, m.z + 8, 1.0, 0.8, 0.5, 0.6, 10.0)

    # ----------------------------------------------------- 输入 --
    def on_key(self, symbol, modifiers):
        if self.quiz['active']:
            digit_keys = (K._1, K._2, K._3, K._4)
            if symbol in digit_keys:
                self._answer(digit_keys.index(symbol))
            return
        # 钢琴
        if symbol in self.piano_keys:
            self._play_note(self.piano_keys.index(symbol))
            return
        if symbol == K.R:
            self.params = self._default_params()
            self._rebuild_begin(self.params)
        elif symbol == K.N:
            self.quiz = {'active': True, 'idx': 0,
                         'P': self._default_params()}
        elif symbol == K.C:
            self._launch_meteor()
        elif symbol == K.M:
            self.muted = not self.muted

    def _answer(self, choice):
        q = self.QUESTIONS[self.quiz['idx']]
        self.quiz['P'][q[2]] = q[3][choice]
        self.quiz['idx'] += 1
        if self.quiz['idx'] >= len(self.QUESTIONS):
            # 收尾：根据暖度推雪线，随机底面，落种重生
            P = self.quiz['P']
            P['snow'] = 42.0 - P['warm'] * 22.0
            P['keel'] = random.uniform(20.0, 32.0)
            self.quiz['active'] = False
            self.thought = 'I am made of your answers.'
            self.thought_t = 8.0
            self._rebuild_begin(P)

    # ----------------------------------------------------- 叠层文字 --
    def _on_draw(self):
        # 与基类 _on_draw 相同的序列，唯一差别是把想法/测验叠层插在
        # _capture_due 之前 —— 否则 --shot 抓到的帧里没有这段文字，
        # 而山体签名式的"哲学独白"本就该出现在剧照里。
        self.window.clear()
        self.render()
        if self.hud is not None:
            self.hud.set_status(self.status_text())
            self._draw_hud()
        self._draw_overlay()
        self._capture_due()
        self._sample_frame_time()

    def _draw_overlay(self):
        w, h = self.window.width, self.window.height
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDisable(GL_DEPTH_TEST)
        self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -1, 1)
        self.window.view = Mat4()
        if self.quiz['active']:
            q = self.QUESTIONS[self.quiz['idx']]
            opts = '   '.join(f'{i+1}){o}' for i, o in enumerate(q[1]))
            self._overlay.text = (
                f'人格测验  {self.quiz["idx"]+1}/{len(self.QUESTIONS)}\n\n'
                f'{q[0]}\n\n{opts}')
        else:
            self._overlay.text = self.thought
        self._overlay.width = int(w * 0.82)
        self._overlay.x = w // 2
        self._overlay.y = int(h * 0.80)
        self._overlay.draw()
        glEnable(GL_DEPTH_TEST)
        self.window.projection = self.perspective

    def status_text(self):
        if self._rb is not None:
            return (f'seed {self.params["seed"]}   重建中 '
                    f'{self._rb["done"]/self._rb["total"]*100:4.1f}%   '
                    f'{self.fps:5.1f} fps')
        tod = 'day' if self.sun_dir.y > 0 else 'night'
        return (f'seed {self.params["seed"]}   天气 {self.weather}   {tod}   '
                f'物体 {len(self.objects)}  植物 {len(self.plants)}   '
                f'三角 {self.tris}   t={self.time:6.1f}s   '
                f'{self.fps:5.1f} fps{"  [PAUSED]" if self.paused else ""}')


if __name__ == '__main__':
    run(MountainApp)
