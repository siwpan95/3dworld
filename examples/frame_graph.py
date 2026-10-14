# -*- coding: utf-8 -*-
"""帧图驱动渲染（P3-b）—— 渲染循环**按编译出来的计划**逐 pass 派发。

上一批（P3-a）做出了帧图内核：声明"每个 pass 读什么写什么"，编译期校验 + 剔除 + 排序 + 别名。
本 demo 是它的 **consumer** —— 证明那套东西不是纸面工作：

* **离屏目标由资源声明创建**（`'screen'` / `(1024, 1024)` / `r32f` 都真的被用到）；
* **渲染循环遍历 `plan.order`**，每个 pass 通过名字派发；
* **每个 pass 读哪张纹理来自 `plan.reads_of(名字)`** —— 所以关掉一个效果时，
  下游会**自动**改读到直通后的那张（关景深 ⇒ 运动模糊直接读雾的输出）；
* **按 `1`~`4` 关效果会现场重编译**：被关的 pass 从计划里消失、独占的中间纹理被别名回收，
  HUD 上能直接看到 pass 数与别名组的变化。

四个效果都来自 ④ 轨道（`engine/csm.py` / `volumetrics.py` / `dof.py` / `motionblur.py`），
它们的 GLSL **拼在同一个片段里**（这一点已由 `tests/` 验证过：21 个 uniform 全在位）。

用法::

    python examples/frame_graph.py
    python examples/frame_graph.py --flag off=dof,motionblur   # 起手就关掉两个

按键：1 阴影  2 雾  3 景深  4 运动模糊   P 打印当前计划   SPACE 暂停   ESC 退出
"""
import math
import os
import sys

from pyglet.gl import (GL_COLOR_ATTACHMENT0, GL_COLOR_BUFFER_BIT, GL_DEPTH_ATTACHMENT,
                       GL_DEPTH_BUFFER_BIT, GL_DEPTH_COMPONENT24, GL_DEPTH_TEST,
                       GL_FRAMEBUFFER, GL_LINEAR, GL_NEAREST, GL_R32F, GL_RED,
                       GL_RGBA, GL_RGBA16F,
                       GL_RGBA8, GL_TEXTURE_2D, GL_TRIANGLES, GL_UNSIGNED_BYTE,
                       glBindFramebuffer, glClear, glClearColor, glEnable, glViewport)
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
from world3d.core.geometry import box_data  # noqa: E402
from world3d.engine.csm import csm_glsl, csm_matrices, mat4_as_Mat4  # noqa: E402
from world3d.engine.dof import dof_glsl  # noqa: E402
from world3d.engine.framegraph import standard_3d_plan  # noqa: E402
from world3d.engine.mathkit import mat4_from_Mat4, mat4_inverse, mat4_mul  # noqa: E402
from world3d.engine.motionblur import mb_glsl  # noqa: E402
from world3d.engine.targets import bind_texture, create_texture  # noqa: E402
from world3d.engine.volumetrics import volumetric_glsl  # noqa: E402
from world3d.logic.camera import OrbitCamera  # noqa: E402

WINDOW_BLOCK = '''uniform WindowBlock {
    mat4 projection;
    mat4 view;
} window;
'''

# ---------------------------------------------------------------- 几何 pass --
SCENE_VS = '#version 330 core\n' + WINDOW_BLOCK + '''
in vec3 position;
in vec4 colors;
uniform mat4 u_model;
out vec3 v_world;
out vec3 v_col;
void main() {
    vec4 wp = u_model * vec4(position, 1.0);
    v_world = wp.xyz;
    v_col = colors.rgb;
    gl_Position = window.projection * window.view * wp;
}
'''

SCENE_FS = '#version 330 core\n' + WINDOW_BLOCK + '''
in vec3 v_world;
in vec3 v_col;
out vec4 final_colors;
uniform vec3 u_light_dir;
void main() {
    vec3 n = normalize(cross(dFdx(v_world), dFdy(v_world)));
    float ndl = max(dot(n, u_light_dir), 0.0);
    float amb = 0.34 + 0.13 * max(n.y, 0.0);
    vec3 col = v_col * (amb + 0.95 * ndl);
    if (v_world.y < 0.35) {                 // 地面棋盘：拖影/虚化在细纹理上最显眼
        float c = mod(floor(v_world.x) + floor(v_world.z), 2.0);
        col *= (0.72 + 0.28 * c);
    }
    final_colors = vec4(col, 1.0);
}
'''

DEPTH_VS = '#version 330 core\n' + WINDOW_BLOCK + '''
in vec3 position;
uniform mat4 u_model;
uniform vec3 u_cam;
out float v_dist;
void main() {
    vec4 wp = u_model * vec4(position, 1.0);
    v_dist = length(wp.xyz - u_cam);
    gl_Position = window.projection * window.view * wp;
}
'''

DIST_FS = '''#version 330 core
in float v_dist;
out vec4 final_colors;
void main() { final_colors = vec4(v_dist, 0.0, 0.0, 1.0); }
'''

SHADOW_VS = '''#version 330 core
in vec3 position;
uniform mat4 u_light_vp;
uniform mat4 u_model;
out float v_depth;
void main() {
    vec4 lp = u_light_vp * u_model * vec4(position, 1.0);
    v_depth = lp.z * 0.5 + 0.5;
    gl_Position = lp;
}
'''

SHADOW_FS = '''#version 330 core
in float v_depth;
out vec4 final_colors;
void main() { final_colors = vec4(v_depth, 0.0, 0.0, 1.0); }
'''

# ---------------------------------------------------------------- 后处理 pass --
FULL_VS = '''#version 330 core
in vec2 position;
in vec2 uv;
out vec2 v_uv;
void main() {
    v_uv = uv;
    gl_Position = vec4(position, 0.0, 1.0);
}
'''

# 每个后处理 pass 的片段 = `#version` + 引擎模块的 GLSL + 一段 main。
# ⚠ `window` 块在**每个用到它的阶段**都要声明一遍（本 demo 的后处理只用 u_* 标量，不需要它）
FOG_BODY = '''
in vec2 v_uv;
out vec4 final_colors;
uniform sampler2D u_fog_src;
uniform float u_fog_on;
void main() {
    vec3 src = texture(u_fog_src, v_uv).rgb;
    float z = vol_fog_scene_dist(v_uv);
    vec4 f = vol_fog(v_uv, z, vol_fog_dither(gl_FragCoord.xy));
    final_colors = vec4(src * f.a + f.rgb, 1.0);
}
'''

DOF_BODY = '''
in vec2 v_uv;
out vec4 final_colors;
uniform sampler2D u_dof_src;
void main() { final_colors = vec4(dof_apply(v_uv), 1.0); }
'''

MB_BODY = '''
in vec2 v_uv;
out vec4 final_colors;
void main() { final_colors = vec4(mb_apply(v_uv), 1.0); }
'''

TONEMAP_BODY = '''
in vec2 v_uv;
out vec4 final_colors;
uniform sampler2D u_ldr_src;
uniform float u_exposure;
void main() {
    vec3 c = texture(u_ldr_src, v_uv).rgb * u_exposure;
    final_colors = vec4(pow(c / (1.0 + c), vec3(1.0 / 2.2)), 1.0);
}
'''

PRESENT_BODY = '''
in vec2 v_uv;
out vec4 final_colors;
uniform sampler2D u_screen;
void main() { final_colors = vec4(texture(u_screen, v_uv).rgb, 1.0); }
'''

GROUND = (0.24, 0.30, 0.26, 1.0)
PILLAR = (0.72, 0.62, 0.52, 1.0)
PILLAR_FAR = (0.42, 0.46, 0.56, 1.0)


def _scene():
    out = [((400.0, 0.4, 400.0), (0.0, -0.2, -40.0), GROUND)]
    for i in range(14):
        z = -6.0 - i * 8.0
        t = i / 13.0
        col = tuple(PILLAR[k] * (1 - t) + PILLAR_FAR[k] * t for k in range(4))
        out.append(((2.4, 11.0, 2.4), (7.0 if i % 2 == 0 else -7.0, 5.5, z), col))
    for i in range(4):
        out.append(((9.0, 1.2, 9.0), (-19.0 + i * 13.0, 13.0, -20.0 - i * 10.0),
                    (0.5, 0.48, 0.44, 1.0)))
    return out


class FrameGraphApp(DemoApp):
    TITLE = '帧图驱动渲染 —— 按编译出来的计划逐 pass 派发'
    HELP = ('1 阴影  2 雾  3 景深  4 运动模糊   P 打印计划   '
            '拖动/滚轮 看视角   SPACE 暂停   ESC 退出')
    DURATION = 36.0
    HUD = True
    # 两个时刻都取「推拉速度接近最大」的相位（0.6·t ≈ kπ），画面差别明显
    SHOTS = ((4.0, 'docs/framegraph_01.png'),
             (10.5, 'docs/framegraph_02.png'))

    SHADOW_SIZE = 1024

    def setup(self):
        glEnable(GL_DEPTH_TEST)
        # ---- 帧图：**先声明，再编译** ----
        self.graph = standard_3d_plan()
        # ⚠ 平台层的 `--flag` 会**按逗号切分**（`--flag a,b` 等价于 `--flag a --flag b`），
        # 所以 `--flag off=fog,dof` 到手是 `{'off=fog', 'dof'}` 两个 token ——
        # 只取最后一个 `off=` 的话只会关掉一个（实测踩过）。这里两种写法都认：
        # `off=fog`、裸 `fog`，也支持重复写多个 `off=`。
        effects = []
        for f in self.args.flags:
            name = f[4:] if f.startswith('off=') else f
            if name in self.EFFECTS:
                effects.append(name)
        for name in effects:
            self._set_effect(name, False)
        self.plan = None
        self.recompile()

        # ---- 着色器 ----
        self.prog_scene = ShaderProgram(Shader(SCENE_VS, 'vertex'),
                                       Shader(SCENE_FS, 'fragment'))
        self.prog_depth = ShaderProgram(Shader(DEPTH_VS, 'vertex'),
                                       Shader(DIST_FS, 'fragment'))
        self.prog_shadow = ShaderProgram(Shader(SHADOW_VS, 'vertex'),
                                        Shader(SHADOW_FS, 'fragment'))
        post = {'fog_pass': ('#version 330 core\n' + csm_glsl(1) + '\n'
                             + volumetric_glsl('vol_fog', 'csm_shadow') + FOG_BODY),
                'dof_pass': '#version 330 core\n' + dof_glsl(24) + DOF_BODY,
                'motionblur_pass': '#version 330 core\n' + mb_glsl(12) + MB_BODY,
                'tonemap_pass': '#version 330 core\n' + TONEMAP_BODY,
                'present': '#version 330 core\n' + PRESENT_BODY}
        self.progs = {k: ShaderProgram(Shader(FULL_VS, 'vertex'),
                                       Shader(v, 'fragment'))
                      for k, v in post.items()}

        self.cam = OrbitCamera(target=Vec3(0, 4.0, -34), yaw=90.0, pitch=6.0,
                               dist=22.0, pitch_range=(2.0, 34.0),
                               dist_range=(10.0, 90.0))
        self.cam.fov = 50.0
        self.cam.near = 0.2
        self.cam.far = 600.0
        self.dolly_t = 0.0

        # ---- 场景 ----
        self.items = []
        for (dims, pos, col) in _scene():
            v, c = box_data(dims[0], dims[1], dims[2], 0, 0, 0, col[:3])
            self.items.append((
                self.prog_scene.vertex_list(len(v) // 3, GL_TRIANGLES,
                                            position=('f', v), colors=('f', c)),
                self.prog_depth.vertex_list(len(v) // 3, GL_TRIANGLES,
                                            position=('f', v)),
                self.prog_shadow.vertex_list(len(v) // 3, GL_TRIANGLES,
                                             position=('f', v)),
                Mat4.from_translation(Vec3(*pos))))

        self._build_targets(self.window.width, self.window.height)
        quad_pos = (-1.0, -1.0, 3.0, -1.0, -1.0, 3.0)
        quad_uv = (0.0, 0.0, 2.0, 0.0, 0.0, 2.0)   # ⚠ uv = ndc*0.5+0.5
        self.quads = {k: p.vertex_list(3, GL_TRIANGLES, position=('f', quad_pos),
                                       uv=('f', quad_uv))
                      for k, p in self.progs.items()}

        self.prev_vp = self._vp()
        self.exposure = 1.0
        self.focus = 16.0
        self.shutter = 0.6
        orbit_camera_input(self, self.cam)

    # ------------------------------------------------------------ 帧图 --
    def recompile(self):
        self.plan = self.graph.compile()
        print(f'[framegraph] 重编译 → {len(self.plan.order)} pass'
              f'{("，剔除 " + str(self.plan.culled)) if self.plan.culled else ""}'
              f'，别名组 {len(self.plan.aliases)}', flush=True)

    def _toggle(self, effect):
        self._set_effect(effect, not self._is_on(effect))

    #: 效果名 → pass 名（两者分开是因为"效果名"是给用户看的）
    EFFECTS = ('shadows', 'fog', 'dof', 'motionblur')
    PASS_OF = {'shadows': 'shadow_pass', 'fog': 'fog_pass',
               'dof': 'dof_pass', 'motionblur': 'motionblur_pass'}
    #: **纯生产者**（不读任何资源）：只能建图时决定开不开 —— 运行期 `disable` 它会被
    #: 内核正确拒绝（没有上游可以直通 ⇒ 关了下游就没数据）。这条约束本身是对的。
    PRODUCERS = ('shadows',)

    def _is_on(self, effect):
        return any(p.name == self.PASS_OF[effect] for p in self.graph.passes)

    def _set_effect(self, effect, on):
        """开/关一个效果。两种关法分开处理（见 `PRODUCERS` 的注释）。"""
        if effect in self.PRODUCERS:
            state = {e: self._is_on(e) for e in self.EFFECTS}
            state[effect] = on
            self.graph = standard_3d_plan(shadows=state['shadows'], fog=state['fog'],
                                          dof=state['dof'],
                                          motionblur=state['motionblur'])
        elif on:
            self.graph.enable(self.PASS_OF[effect])
        else:
            if self._is_on(effect):
                self.graph.disable(self.PASS_OF[effect])
        self.recompile()
        self._free_extra_targets()

    # ------------------------------------------------------------ 资源 --
    def _make_target(self, name, res, w, h):
        """按资源声明建离屏目标 —— `size` / `fmt` 都真的被用到。"""
        size = res.size
        if isinstance(size, tuple):
            tw, th = size
        elif size == 'half':
            tw, th = max(1, w // 2), max(1, h // 2)
        else:
            tw, th = max(1, w), max(1, h)
        # `world3d.engine.targets.create_texture` 会按内部格式挑数据类型（浮点必须 HALF_FLOAT /
        # UNSIGNED_BYTE 用在 16F 上在部分驱动会**静默失败**）
        fmt = res.fmt
        if fmt == 'r32f':
            tex = Texture.create(tw, th, GL_TEXTURE_2D, GL_R32F, GL_NEAREST,
                                 GL_NEAREST, GL_RED)
        else:
            internal = GL_RGBA16F if fmt == 'rgba16f' else GL_RGBA8
            tex = create_texture(tw, th, internalformat=internal, fmt=GL_RGBA)
        fbo = Framebuffer()
        fbo.attach_texture(tex, attachment=GL_COLOR_ATTACHMENT0)
        fbo.attach_renderbuffer(Renderbuffer(tw, th, GL_DEPTH_COMPONENT24),
                                attachment=GL_DEPTH_ATTACHMENT)
        if not bool(fbo.is_complete):
            print(f'[framegraph] WARNING: 资源 {name} 的 FBO 不完整 '
                  f'-> {fbo.get_status()}', flush=True)
        return dict(tex=tex, fbo=fbo, size=(tw, th))

    def _build_targets(self, w, h):
        self.targets = {}
        for name, res in self.plan.resources.items():
            if res.external or res.kind != 'texture':
                continue
            self.targets[name] = self._make_target(name, res, w, h)
        self._rt_size = (w, h)

    def _free_extra_targets(self):
        """重新编译后，把计划里**不再需要**的目标释放掉（图形 API 的最小权利）。

        ⚠ 必须容忍"目标还没建"：`setup()` 里解析 `--flag` 时就会调到这里，
        而那时 `self.targets` 还不存在 —— 少了这个守卫，第一个 flag 就会抛
        `AttributeError`，**后面的 flag 全被跳过**（实测：`off=fog,dof,motionblur`
        只有第一个生效），而且报错位置离真正的原因很远。
        """
        if not hasattr(self, 'targets'):
            return
        want = {n for n in self.targets if n in self.plan.lifetimes}
        for name in list(self.targets):
            if name not in want:
                t = self.targets.pop(name)
                t['fbo'].delete()
                t['tex'].delete()

    def _vp(self):
        aspect = self.window.width / max(1, self.window.height)
        return mat4_mul(tuple(self.cam.projection(aspect)),
                        mat4_from_Mat4(self.cam.view_matrix()))

    # ------------------------------------------------------------ 键盘 --
    def on_key(self, symbol, modifiers):
        if symbol == key._1:
            self._toggle('shadows')
        elif symbol == key._2:
            self._toggle('fog')
        elif symbol == key._3:
            self._toggle('dof')
        elif symbol == key._4:
            self._toggle('motionblur')
        elif symbol == key.P:
            self._print_plan()
        else:
            super().on_key(symbol, modifiers)

    def _print_plan(self):
        print('  pass × %d: %s' % (len(self.plan.order), ' → '.join(self.plan.order)))
        for n in self.plan.order:
            print(f'    {n:<18} reads={list(self.plan.reads_of(n))} '
                  f'writes={list(self.plan.writes_of(n))}')
        if self.plan.culled:
            print('  被剔除:', self.plan.culled)
        if self.plan.aliases:
            print('  可别名:', ' | '.join('⊕'.join(g) for g in self.plan.aliases))

    # ------------------------------------------------------------ 每帧 --
    def update(self, dt):
        if self.paused:
            return
        self.dolly_t += dt
        self.cam.dist = 22.0 + 4.0 * math.sin(self.dolly_t * 0.6)   # 推拉，让运动模糊可见

    def _light_dir(self):
        az = math.radians(248.0 + 360.0 * self.time / self.DURATION)
        el = math.radians(19.0)
        return (math.cos(el) * math.cos(az), math.sin(el),
                math.cos(el) * math.sin(az))

    # ------------------------------------------------------------ 各 pass --
    def _pass_depth_prepass(self):
        t = self.targets['distance']
        t['fbo'].bind()
        glViewport(0, 0, *t['size'])
        glClearColor(1e6, 1e6, 1e6, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.prog_depth.use()
        self.prog_depth['u_cam'] = self.cam.eye
        for (_s, depth_vl, _sh, model) in self.items:
            self.prog_depth['u_model'] = model
            depth_vl.draw(GL_TRIANGLES)
        t['fbo'].unbind()

    def _pass_shadow_pass(self):
        t = self.targets['shadow']
        t['fbo'].bind()
        glViewport(0, 0, *t['size'])
        glClearColor(1.0, 1.0, 1.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.prog_shadow.use()
        self.prog_shadow['u_light_vp'] = mat4_as_Mat4(self._cascade()['vp'])
        for (_s, _d, shadow_vl, model) in self.items:
            self.prog_shadow['u_model'] = model
            shadow_vl.draw(GL_TRIANGLES)
        t['fbo'].unbind()

    def _cascade(self):
        aspect = self.window.width / max(1, self.window.height)
        tf = math.tan(math.radians(self.cam.fov) / 2.0)
        return csm_matrices(mat4_from_Mat4(self.cam.view_matrix()), self.cam.near,
                            self.cam.far * 0.6, tf, aspect, self._light_dir(),
                            n=1, size=self.SHADOW_SIZE, pad=2.0, snap=True)[0]

    def _pass_scene_pass(self):
        t = self.targets['hdr']
        t['fbo'].bind()
        glViewport(0, 0, *t['size'])
        glClearColor(0.03, 0.04, 0.06, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.prog_scene.use()
        self.prog_scene['u_light_dir'] = self._light_dir()
        for (main_vl, _d, _s, model) in self.items:
            self.prog_scene['u_model'] = model
            main_vl.draw(GL_TRIANGLES)
        t['fbo'].unbind()

    def _bind_inputs(self, prog, reads):
        """按 `plan.reads_of()` 给的顺序绑纹理 —— **这就是"图驱动"的落点**：
        关掉一个效果后 reads 会自动指向直通后的资源，渲染端无需改一行代码。
        """
        unit = 0
        for r in reads:
            if r not in self.targets:
                continue
            bind_texture(unit, self.targets[r]['tex'])
            unit += 1
        return unit

    def _pass_fog_pass(self):
        reads = self.plan.reads_of('fog_pass')
        t = self.targets[self.plan.writes_of('fog_pass')[0]]
        t['fbo'].bind()
        glViewport(0, 0, *t['size'])
        glClearColor(0.0, 0.0, 0.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        prog = self.progs['fog_pass']
        prog.use()
        self._bind_inputs(prog, reads)          # 0=hdr 1=distance 2=shadow（若有）
        prog['u_fog_src'] = 0
        prog['u_fog_depth'] = 1
        c = self._cascade()
        prog['u_csm_vp0'] = list(c['vp'])
        prog['u_csm_split'] = c['split']        # ⚠ 单元素数组退化成标量
        prog['u_csm_pcf'] = 1.0
        if 'shadow' in reads:
            prog['u_csm_tex0'] = reads.index('shadow')
        prog['u_inv_vp'] = list(mat4_inverse(self._vp()))
        prog['u_fog_cam'] = (self.cam.eye.x, self.cam.eye.y, self.cam.eye.z)
        prog['u_fog_light_dir'] = self._light_dir()
        prog['u_fog_light_color'] = (1.0, 0.86, 0.66)
        prog['u_fog_ambient'] = (0.10, 0.12, 0.16)
        prog['u_fog_density'] = 0.030
        prog['u_fog_falloff'] = 0.13
        prog['u_fog_base'] = 0.0
        prog['u_fog_phase_g'] = 0.74
        prog['u_fog_steps'] = 40.0
        prog['u_fog_max_dist'] = float(self.cam.far)
        prog['u_fog_analytic'] = 1.0
        prog['u_fog_shafts'] = 1.0
        self.quads['fog_pass'].draw(GL_TRIANGLES)
        t['fbo'].unbind()

    def _pass_dof_pass(self):
        reads = self.plan.reads_of('dof_pass')
        t = self.targets[self.plan.writes_of('dof_pass')[0]]
        t['fbo'].bind()
        glViewport(0, 0, *t['size'])
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        prog = self.progs['dof_pass']
        prog.use()
        self._bind_inputs(prog, reads)          # 0=上游色 1=distance
        prog['u_dof_color'] = 0
        prog['u_dof_depth'] = 1
        prog['u_dof_focus'] = self.focus
        prog['u_dof_aperture'] = 0.085 / 2.8
        prog['u_dof_focal'] = 0.085
        prog['u_dof_max_coc'] = 22.0
        prog['u_dof_coc_scale'] = t['size'][1] / 0.024
        prog['u_dof_on'] = 1.0
        self.quads['dof_pass'].draw(GL_TRIANGLES)
        t['fbo'].unbind()

    def _pass_motionblur_pass(self):
        reads = self.plan.reads_of('motionblur_pass')
        t = self.targets[self.plan.writes_of('motionblur_pass')[0]]
        t['fbo'].bind()
        glViewport(0, 0, *t['size'])
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        prog = self.progs['motionblur_pass']
        prog.use()
        self._bind_inputs(prog, reads)          # 0=上游色 1=distance
        prog['u_mb_color'] = 0
        prog['u_mb_depth'] = 1
        prog['u_mb_inv_vp_cur'] = list(mat4_inverse(self._vp()))
        prog['u_mb_vp_prev'] = list(self.prev_vp)
        prog['u_mb_cam'] = (self.cam.eye.x, self.cam.eye.y, self.cam.eye.z)
        prog['u_mb_shutter'] = self.shutter
        prog['u_mb_samples'] = 12.0
        prog['u_mb_max_len'] = 70.0
        prog['u_mb_on'] = 1.0
        self.quads['motionblur_pass'].draw(GL_TRIANGLES)
        t['fbo'].unbind()

    def _pass_tonemap_pass(self):
        reads = self.plan.reads_of('tonemap_pass')
        t = self.targets[self.plan.writes_of('tonemap_pass')[0]]
        t['fbo'].bind()
        glViewport(0, 0, *t['size'])
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        prog = self.progs['tonemap_pass']
        prog.use()
        self._bind_inputs(prog, reads)
        prog['u_ldr_src'] = 0
        prog['u_exposure'] = self.exposure
        self.quads['tonemap_pass'].draw(GL_TRIANGLES)
        t['fbo'].unbind()

    def _pass_present(self):
        reads = self.plan.reads_of('present')
        # ⚠ 第一个参数是 **target**（GL_FRAMEBUFFER），不是「0 号 FBO」——
        # 传 0 会得到 GL_INVALID_ENUM(0x1280)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        w, h = self.window.width, self.window.height
        glViewport(0, 0, w, h)
        glClearColor(0.0, 0.0, 0.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        prog = self.progs['present']
        prog.use()
        src = reads[0]
        if src in self.targets:
            bind_texture(0, self.targets[src]['tex'])
            prog['u_screen'] = 0
        self.quads['present'].draw(GL_TRIANGLES)

    # ------------------------------------------------------------ 渲染 --
    def render(self):
        w, h = self.window.width, self.window.height
        if (w, h) != self._rt_size:
            for t in self.targets.values():
                t['fbo'].delete()
                t['tex'].delete()
            self._build_targets(w, h)

        aspect = w / max(1, h)
        self.window.view = self.cam.view_matrix()
        self.window.projection = self.cam.projection(aspect)

        # ★ 渲染循环就是"遍历计划" —— 这就是图驱动的全部
        for name in self.plan.order:
            getattr(self, '_pass_' + name)()
        self.prev_vp = self._vp()

    # ------------------------------------------------------------ HUD --
    def status_text(self):
        order = '→'.join(n.replace('_pass', '').replace('_prepass', '')
                         for n in self.plan.order)
        alias = len(self.plan.aliases)
        culled = ('  剔:' + ','.join(n.replace('_pass', '') for n in self.plan.culled)
                  if self.plan.culled else '')
        return (f'帧图 {len(self.plan.order)} pass [{order}]  别名组 {alias}{culled}  '
                f'对焦 {self.focus:.0f}m  快门 {self.shutter * 360:.0f}°  '
                f't={self.time:4.1f}s {self.fps:4.1f}fps')


if __name__ == '__main__':
    run(FrameGraphApp)
