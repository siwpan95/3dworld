# -*- coding: utf-8 -*-
"""级联阴影贴图（CSM）—— ④-1：把一张阴影图按视距切成 N 级。

**为什么需要它**：单张阴影图的精度是固定的。它拿一个正交光盒罩住整段可见距离，
纹素在世界里的尺寸 = 2·半径/分辨率；半径 200、分辨率 2048 ⇒ 每纹素约 0.2 世界单位，
近处物体的影子边缘会糊成锯齿。把半径压到 20 保近处，远处就整片没阴影。
CSM 按视距分区，**近处用小盒（纹素细）+ 远处用大盒（纹素粗）**，两头都保住。

本 demo 让"纹素粗细"肉眼可见：

* 地面是**程序化棋盘**（格宽 4 世界单位）—— 影子的锯齿/爬行在棋盘上最刺眼；
* 一排柱子从近到远排开 200 单位，相机沿排看过去；
* 按 **V** 给"影子按级染色"（第 0 级青、第 1 级紫、第 2 级橙…），一眼看出分界线在哪，
  以及各级的纹素粗细差多少；
* 右上角画中画显示**第 0 级的深度图**（render-to-texture 是否真的产出了内容）。

数学全在 `engine/csm.py`（纯函数、可 headless 断言：分割距离 / 视锥角点 / 光盒拟合 /
纹素吸附 / 选级）。本文件只负责"把它画出来"。

用法::

    python examples/csm_shadow.py
    python examples/csm_shadow.py --flag cascades=4        # 4 级
    python examples/csm_shadow.py --shot                   # 两张不同光照角度

按键：C 切级数(1~4)　S 阴影开关　V 按级染色　P PCF 半径　B bias 档　
A 自动环绕相机　SPACE 暂停　鼠标拖动/滚轮　ESC 退出
"""
import math
import os
import sys

from pyglet.gl import (GL_COLOR_ATTACHMENT0, GL_COLOR_BUFFER_BIT, GL_DEPTH_ATTACHMENT,
                       GL_DEPTH_BUFFER_BIT, GL_DEPTH_COMPONENT24, GL_DEPTH_TEST,
                       GL_LINEAR, GL_R32F, GL_RED, GL_TEXTURE_2D, GL_TRIANGLES,
                       glClear, glClearColor, glDisable, glEnable, glViewport)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.image import Framebuffer, Texture
from pyglet.image.buffer import Renderbuffer
from pyglet.math import Mat4, Vec3
from pyglet.window import key

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError（P2-1 全量验收实测，30 个 demo 一起炸）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.platform.app import DemoApp, orbit_camera_input, run  # noqa: E402
from core.geometry import box_data  # noqa: E402
from engine.csm import (MAX_CASCADES, csm_glsl, csm_matrices,  # noqa: E402
                        csm_uniforms, mat4_as_Mat4)
from engine.mathkit import mat4_from_Mat4  # noqa: E402
from logic.camera import OrbitCamera  # noqa: E402

# ---------------------------------------------------------------- 着色器 --
DEPTH_VS = '''#version 330 core
in vec3 position;
uniform mat4 u_light_vp;
uniform mat4 u_model;
out float v_depth;
void main() {
    vec4 lp = u_light_vp * u_model * vec4(position, 1.0);
    v_depth = lp.z * 0.5 + 0.5;          // 正交投影：z 已是线性深度
    gl_Position = lp;
}'''

DEPTH_FS = '''#version 330 core
in float v_depth;
out vec4 final_colors;
void main() { final_colors = vec4(v_depth, 0.0, 0.0, 1.0); }'''

MAIN_VS = '''#version 330 core
in vec3 position;
in vec4 colors;        // `box_data` 给的是 RGBA
uniform WindowBlock {                 // 平台层提供的窗口块（不声明就用不了）
    mat4 projection;
    mat4 view;
} window;
uniform mat4 u_model;
out vec3 v_world;
out vec3 v_col;
void main() {
    vec4 wp = u_model * vec4(position, 1.0);
    v_world = wp.xyz;
    v_col = colors.rgb;
    gl_Position = window.projection * window.view * wp;
}'''

# `csm_glsl()` 生成的片段会在下面拼进来（uniform 声明 + 逐级 PCF + 选级函数）
MAIN_FS_HEAD = '''#version 330 core
in vec3 v_world;
in vec3 v_col;
out vec4 final_colors;
uniform WindowBlock {              // ⚠ 片段着色器里**也要声明一遍**：
    mat4 projection;               // GLSL 的 uniform block 不是"跨阶段共享声明"，
    mat4 view;                     // 只在顶点着色器声明、片段里用 ⇒
} window;                          // "ERROR: 0:60: 'window' : undeclared identifier"
uniform vec3  u_light_dir;        // 指向光源的单位向量
uniform float u_shadow_on;
uniform float u_tint_cascades;    // >0：影子按级染色（看分界线用）
uniform float u_is_ground;        // 1：这是地面（画程序化棋盘）
uniform vec3  u_cam_pos;
uniform float u_csm_count;        // **实际启用**的级数（着色器按 MAX 展开编译）
'''

MAIN_FS_BODY = '''
void main() {
    // 几何法线：屏幕导数现算（面片着色，省掉一整套法线数据）。
    // ⚠ **不要**按相机位置翻面 —— 实测那样写会把受光面翻成背光面，整幅画面变暗；
    // `shadow_map` 也是直接用 cross(dFdx, dFdy) 的。
    vec3 n = normalize(cross(dFdx(v_world), dFdy(v_world)));
    float ndl = max(dot(n, u_light_dir), 0.0);
    float z_view = (window.view * vec4(v_world, 1.0)).z;

    vec3 base = v_col;
    if (u_is_ground > 0.5) {
        // 程序化棋盘：影子的锯齿/爬行在棋盘上最刺眼（也是"纹素粗细"的尺子）
        float c = mod(floor(v_world.x / 4.0) + floor(v_world.z / 4.0), 2.0);
        base *= (0.66 + 0.34 * c);
    }

    // csm_shadow 返回 **1 = 在阴影里**（与 `engine.csm` 的约定一致）
    float sh = (u_shadow_on > 0.5) ? csm_shadow(v_world, z_view, ndl) : 0.0;

    float amb = 0.34 + 0.12 * max(n.y, 0.0);          // 天光：朝上的面更亮
    vec3 col = base * (amb + 0.92 * ndl * (1.0 - sh));
    // 影子不是全黑：留一点环境色，否则"远处还有没有影子"根本看不出来
    float d = length(v_world - u_cam_pos);
    col = mix(col, vec3(0.12, 0.14, 0.18), smoothstep(220.0, 560.0, d));

    if (u_tint_cascades > 0.5 && sh > 0.5) {
        // 只在阴影里染色，这样分界线恰好落在"影子换级"的地方
        // 按级染色：青 → 紫 → 橙 → 绿（分界线一眼可见）
        // 染色按**实际启用**的级数 gate（着色器是按 MAX 展开编译的，
        // 所以编译期常量在这里不管用 —— 得看运行期的 u_csm_count）
        float dv = -z_view;
        vec3 tint = vec3(0.20, 0.85, 0.95);
        if (u_csm_count > 1.5 && dv > u_csm_split[0]) tint = vec3(0.65, 0.35, 0.95);
        if (u_csm_count > 2.5 && dv > u_csm_split[1]) tint = vec3(0.98, 0.55, 0.15);
        if (u_csm_count > 3.5 && dv > u_csm_split[2]) tint = vec3(0.35, 0.95, 0.45);
        col = mix(col, tint, 0.65);
    }
    final_colors = vec4(col, 1.0);
}'''

PANEL_VS = '''#version 330 core
in vec2 position;
in vec2 uv;
out vec2 v_uv;
void main() {
    v_uv = uv;
    gl_Position = vec4(position, 0.0, 1.0);
}'''

PANEL_FS = '''#version 330 core
in vec2 v_uv;
out vec4 final_colors;
uniform sampler2D u_tex;
void main() {
    float d = texture(u_tex, v_uv).r;
    // ×3 提对比：光盒比几何大得多，深度值集中在 0.85~0.95 这一小段，
    // 不拉一下整张小窗几乎是纯黑（看得出"有内容"才算验证通过）
    final_colors = vec4(vec3(clamp((1.0 - d) * 3.0, 0.0, 1.0)) * 0.95, 1.0);
}'''

#: 级 → 染色（与着色器里的分支一致，仅用于 HUD 文案）
CASCADE_TINTS = ('青', '紫', '橙', '绿')
PILLAR_COLORS = [(0.82, 0.76, 0.62), (0.70, 0.78, 0.86), (0.86, 0.70, 0.66),
                 (0.74, 0.82, 0.70), (0.84, 0.74, 0.86)]


class CsmShadowApp(DemoApp):
    TITLE = '级联阴影（CSM）—— 按视距分层，近处锐、远处也还在'
    HELP = ('C 切级数 1~4   S 阴影开关   V 按级染色   P PCF 半径   B bias   '
            '拖动/滚轮 看视角   A 自动环绕   SPACE 暂停   ESC 退出')
    DURATION = 40.0                       # 光照绕场一周，循环无缝
    HUD = True
    SHOTS = ((6.0, 'docs/csm_01.png'),    # 默认 3 级，光斜射
             (22.0, 'docs/csm_02.png'))   # 光转到另一侧

    SUN_SIZE = 2048                       # 每级阴影图分辨率

    def setup(self):
        glEnable(GL_DEPTH_TEST)
        glClearColor(0.05, 0.06, 0.08, 1.0)
        src = csm_glsl(MAX_CASCADES, fn='csm_shadow', samples=3)
        # ⚠ 着色器必须**按最大级数**编译：`csm_glsl` 的选级是**展开**写死的
        # （GL 3.3 不允许动态下标索引 sampler2D 数组），所以运行期只能在
        # `1..MAX_CASCADES` 之间切"用几级"（多余的级用同一套矩阵占位），
        # 不能改数组长度。这也正是 `MAX_CASCADES` 存在的理由。
        self.main_prog = ShaderProgram(
            Shader(MAIN_VS, 'vertex'),
            Shader(MAIN_FS_HEAD + src + MAIN_FS_BODY, 'fragment'))
        self.depth_prog = ShaderProgram(Shader(DEPTH_VS, 'vertex'),
                                       Shader(DEPTH_FS, 'fragment'))
        self.panel_prog = ShaderProgram(Shader(PANEL_VS, 'vertex'),
                                       Shader(PANEL_FS, 'fragment'))

        # 构图：从高处斜看"柱林"——近处（画面下方）的柱基影子要细，远处（上方）
        # 也必须有影子，这正是 CSM 与单张阴影图的差别所在。
        self.cam = OrbitCamera(target=Vec3(0, 0, -80), yaw=228.0, pitch=42.0,
                               dist=195.0, pitch_range=(10.0, 62.0),
                               dist_range=(60.0, 300.0))
        # `logic.camera.OrbitCamera` 是兼容层，只透传 orbit 的参数；fov/near/far 直接改属性
        # （`engine.camera.Camera` 就是普通属性）。far 要给足：场景纵深 200 + 相机高度 130。
        self.cam.fov = 50.0
        self.cam.near = 0.5
        self.cam.far = 900.0

        # ---- flags ----
        n = int(next((f.split('=', 1)[1] for f in self.args.flags
                      if f.startswith('cascades=')), 3))
        self.cascades = max(1, min(MAX_CASCADES, n))
        self.shadow_on = 'noshadow' not in self.args.flags
        # 剧照默认开"按级染色"：级联边界是 CSM 的**核心证据**，静帧不染色看不出来
        # （交互时按 V 随时切）。这和 brickward 在 --shot 下默认 auto 回放是同一个理由。
        self.tint = 1.0 if ('tint' in self.args.flags or self.args.shot) else 0.0
        self.pcf = 1.5
        self.bias_slope = 0.006

        # ---- 场景：一排柱子从近到远 200 单位 + 几块浮空盒 ----
        self.ground_v = (-300.0, 0.0, -300.0, 300.0, 0.0, -300.0, 300.0, 0.0, 300.0,
                         -300.0, 0.0, -300.0, 300.0, 0.0, 300.0, -300.0, 0.0, 300.0)
        # 地面颜色必须也是 4 分量：`colors` 这个属性名在 pyglet 里就是 vec4
        self.ground_c = tuple(c for _ in range(6) for c in (0.60, 0.62, 0.66, 1.0))
        self.ground_list = self.main_prog.vertex_list(
            6, GL_TRIANGLES, position=('f', self.ground_v), colors=('f', self.ground_c))
        self.ground_depth = self.depth_prog.vertex_list(
            6, GL_TRIANGLES, position=('f', self.ground_v))

        self.main_lists, self.depth_lists = [], []
        self.caster_count = 0
        raw = []
        # 「柱林」：一片铺到 200 单位深的柱子。**深度覆盖**正是要展示的东西 ——
        # 近处（画面下方）的柱基影子要细，最远那排也必须还有影子。
        # 高度用确定性"哈希"（sin 混整数式）而不是 random：剧照必须可复现。
        for iz in range(9):
            z = -6.0 - iz * 22.0
            for ix in range(6):
                x = -55.0 + ix * 22.0 + 5.0 * math.sin(iz * 2.3 + ix * 1.7)
                hush = abs(math.sin(iz * 12.9898 + ix * 78.233)) * 43758.5453
                hush -= math.floor(hush)
                h = 7.0 + 9.0 * hush
                raw.append((4.4, h, 4.4, x, h / 2.0, z,
                            PILLAR_COLORS[(iz + ix) % len(PILLAR_COLORS)]))
        for i, (tx, ty, tz) in enumerate(((0.0, 6.5, -30.0), (-32.0, 5.0, -96.0),
                                          (34.0, 8.0, -140.0), (0.0, 11.0, -186.0))):
            raw.append((9.0, 2.6, 9.0, tx, ty, tz, PILLAR_COLORS[(i + 2) % 5]))
        for (sx, sy, sz, ox, oy, oz, rgb) in raw:
            v, c = box_data(sx, sy, sz, ox, oy, oz, rgb)
            self.main_lists.append(self.main_prog.vertex_list(
                len(v) // 3, GL_TRIANGLES, position=('f', v), colors=('f', c)))
            self.depth_lists.append(self.depth_prog.vertex_list(
                len(v) // 3, GL_TRIANGLES, position=('f', v)))
            self.caster_count += 1

        # ---- N 张阴影图（各自一个 FBO）----
        # 深度用 R32F：8 位只有 255 级，60 单位的深度范围里每级 0.24 世界单位，
        # 比 bias 还大 ⇒ 满屏条纹状自阴影（acne）。浮点纹理从根上消掉。
        self.shadows, self.fbos = [], []
        for _ in range(MAX_CASCADES):
            # 参数顺序照 shadow_map 的实测写法（Texture.create 的位置参数不是"名字顺序"）
            tex = Texture.create(self.SUN_SIZE, self.SUN_SIZE, GL_TEXTURE_2D, GL_R32F,
                                 GL_LINEAR, GL_LINEAR, GL_RED)
            fbo = Framebuffer()
            fbo.attach_texture(tex, attachment=GL_COLOR_ATTACHMENT0)
            fbo.attach_renderbuffer(
                Renderbuffer(self.SUN_SIZE, self.SUN_SIZE, GL_DEPTH_COMPONENT24),
                attachment=GL_DEPTH_ATTACHMENT)
            self.shadows.append(tex)
            self.fbos.append(fbo)
        # FBO 完整性必须**当场查**：不完整的 FBO 会让深度趟"画到空气里"，
        # 纹理永远是清屏值（= 全 1 = 全远）—— 表现为"完全没有阴影"，
        # 而画面本身看起来一切正常，极难定位。shadow_map 也是这么做的。
        bad = [i for i, f in enumerate(self.fbos) if not bool(f.is_complete)]
        if bad:
            print(f'[csm] WARNING: framebuffer {bad} 不完整 -> {self.fbos[bad[0]].get_status()}',
                  '(阴影将全部失效，只剩场景)', flush=True)
        else:
            print(f'[csm] {len(self.fbos)} 张阴影 FBO 全部完整', flush=True)

        # ---- 画中画：某一级的深度图（render-to-texture 有没有内容，肉眼即可确认）----
        # 显示**中间那一级**而不是第 0 级：本 demo 的相机离场景 195m，第 0 级（0~60m）
        # 罩的是相机前方的空空间，那张图**本来就该是全远（= 全黑）** ——
        # 拿它当"有没有渲染成功"的凭据会误判（实测：我盯着黑窗查了半天，
        # 最后发现图是对的、只是那一级确实没东西可画）。
        self.panel = self.panel_prog.vertex_list(
            6, GL_TRIANGLES,
            position=('f', (-0.97, 0.55, 0.55, 0.55, 0.55, 0.97,
                            -0.97, 0.55, 0.55, 0.97, -0.97, 0.97)),
            uv=('f', (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 1.0)))

        orbit_camera_input(self, self.cam)
        self.cascades_info = []
        # 画中画显示哪一级（构造期还不知道级数，先占位；render 里会夹到合法范围）
        self.panel_cascade = 0

    # ------------------------------------------------------------ 光源 --
    def _light_dir(self):
        """太阳方向（指向光源）。绕场一周，40s 循环。"""
        az = math.radians(215.0 + 360.0 * self.time / self.DURATION)
        el = math.radians(30.0)          # 偏低：影子拉长才看得出边缘质量
        return (math.cos(el) * math.cos(az), math.sin(el),
                math.cos(el) * math.sin(az))

    def _update_cascades(self):
        """按当前相机 + 光源重算各级矩阵（**每帧**跑，因为光在转）。"""
        aspect = self.window.width / max(1, self.window.height)
        # ⚠ 这里的 fov / near / far **必须**和 render() 里那套投影逐字对齐，
        # 否则"分割点算的是一段视锥、真正渲染的是另一段" —— 表现为级联边界错位。
        tf = math.tan(math.radians(self.cam.fov) / 2.0)
        # `OrbitCamera.view_matrix()` 给的是 pyglet `Mat4`；csm 那一套是 mathkit 的
        # **列主序元组**，中间必须显式转一次（少这一步会 AttributeError: 'Mat4' object
        # has no attribute ... 之类，且只在运行期炸）。
        view = mat4_from_Mat4(self.cam.view_matrix())
        self.cascades_info = csm_matrices(
            view, self.cam.near, self.cam.far * 0.75, tf, aspect,
            self._light_dir(), n=self.cascades, lambda_=0.75,
            size=self.SUN_SIZE, pad=1.5, snap=True)

    # ------------------------------------------------------------ 键盘 --
    def on_key(self, symbol, modifiers):
        if symbol == key.C:
            self.cascades = self.cascades % MAX_CASCADES + 1     # 1→2→3→4→1
            print(f'[csm] cascades={self.cascades}', flush=True)
        elif symbol == key.S:
            self.shadow_on = not self.shadow_on
        elif symbol == key.V:
            self.tint = 0.0 if self.tint > 0.5 else 1.0
        elif symbol == key.P:
            self.pcf = 1.0 if self.pcf > 2.0 else self.pcf + 0.5
        elif symbol == key.B:
            self.bias_slope = 0.012 if self.bias_slope <= 0.006 else 0.006
        else:
            super().on_key(symbol, modifiers)

    # ------------------------------------------------------------ 渲染 --
    def render(self):
        w, h = self.window.width, self.window.height
        self._update_cascades()
        light = self._light_dir()

        # ---- 第一趟：逐级渲染深度图 ----
        for i, tex in enumerate(self.shadows):
            self.fbos[i].bind()
            glViewport(0, 0, self.SUN_SIZE, self.SUN_SIZE)
            glClearColor(1.0, 1.0, 1.0, 1.0)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            prog = self.depth_prog
            prog.use()
            # 级数 < MAX 时，多余的级用最后一级的矩阵占位（省得留一张黑图在闪烁）
            vp = self.cascades_info[min(i, len(self.cascades_info) - 1)]['vp']
            prog['u_light_vp'] = mat4_as_Mat4(vp)
            prog['u_model'] = Mat4()
            self.ground_depth.draw(GL_TRIANGLES)
            for vl in self.depth_lists:
                vl.draw(GL_TRIANGLES)
        self.fbos[0].unbind()

        # ---- 第二趟：主渲染 ----
        # ⚠ **必须**把相机的 view/projection 写进 `window` 块：平台层只在 resize 时设
        # `projection`，**`view` 一直是单位矩阵** ⇒ 相机形同不存在。症状很误导：
        # 改机位"没反应"，而且地面正好落在相机高度上、被看成零厚度的片 ⇒ **整片地面消失**
        # （实测：画面 43% 是清屏色、20% 是黑的开着空深度图的小窗）。boxward 也是显式设的。
        self.window.view = self.cam.view_matrix()
        self.window.projection = self.cam.projection(w / max(1, h))
        glViewport(0, 0, w, h)
        glClearColor(0.05, 0.06, 0.08, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        prog = self.main_prog
        prog.use()
        mvp, splits = csm_uniforms(self.cascades_info)
        for name, mat in mvp.items():
            prog[name] = mat
        # 级数 < MAX 时把多余的分割点重复最后一级（着色器按 MAX 展开编译，
        # 数组长度必须给满；重复值让多出来的分支永远不会被选中）
        prog['u_csm_split'] = splits + [splits[-1]] * (MAX_CASCADES - len(splits))
        prog['u_csm_pcf'] = self.pcf
        # ⚠ `Texture.bind()` 的 `texture_unit` 默认是 **0** —— 写成
        # `glActiveTexture(GL_TEXTURE0+i); tex.bind()` 的话，`bind()` 会**又把活动单元切回 0**，
        # 于是 N 张阴影图全绑在 0 号单元上（最后一张覆盖前面所有），
        # 而 `u_csm_tex_i = i` 让 1~3 号采到**未绑定**的单元 ⇒ 表现为"完全没有阴影"、
        # 且画面其它一切正常。pyglet 的 `bind(i)` 就是替你切单元 + 绑纹理。
        for i, tex in enumerate(self.shadows):
            tex.bind(i)
            prog[f'u_csm_tex{i}'] = i
        prog['u_light_dir'] = light
        prog['u_shadow_on'] = 1.0 if self.shadow_on else 0.0
        prog['u_tint_cascades'] = self.tint
        prog['u_cam_pos'] = (self.cam.eye.x, self.cam.eye.y, self.cam.eye.z)
        prog['u_csm_count'] = float(len(self.cascades_info))
        prog['u_model'] = Mat4()
        prog['u_is_ground'] = 1.0
        self.ground_list.draw(GL_TRIANGLES)
        prog['u_is_ground'] = 0.0
        for vl in self.main_lists:
            vl.draw(GL_TRIANGLES)

        # ---- 画中画：第 0 级深度图 ----
        glDisable(GL_DEPTH_TEST)
        self.panel_prog.use()
        self.panel_cascade = min(1, len(self.cascades_info) - 1)
        self.shadows[self.panel_cascade].bind(0)      # bind(0) = 切到 0 号单元并绑上
        self.panel_prog['u_tex'] = 0
        self.panel.draw(GL_TRIANGLES)
        glEnable(GL_DEPTH_TEST)

    # ------------------------------------------------------------ HUD --
    def status_text(self):
        info = self.cascades_info
        if not info:
            return '初始化中…'
        parts = []
        for c in info:
            parts.append(f'{CASCADE_TINTS[c["index"]]}'
                         f'{c["z_near"]:.0f}-{c["z_far"]:.0f}m/'
                         f'{c["texel"]:.3f}m·px')
        return (f'级数 {len(info)}  '
                f'{" | ".join(parts)}  PCF {self.pcf:.1f}tex  '
                f'{self.SUN_SIZE}²/级  '
                f'{"[染色]" if self.tint > 0.5 else ""}'
                f'{"[无阴影]" if not self.shadow_on else ""}'
                f'  阴影图 {len(self.shadows)}/{MAX_CASCADES} 张  投影片 {self.caster_count}  '
                f't={self.time:4.1f}s {self.fps:4.1f}fps')


if __name__ == '__main__':
    run(CsmShadowApp)
