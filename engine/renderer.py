# -*- coding: utf-8 -*-
"""渲染器：把场景画进 MRT 目标，再跑后处理链输出到屏幕。

一帧的流程::

    1) 绑定到场景目标（COLOR0=HDR 颜色，COLOR1=视空间法线+线性深度）
    2) 天空 pass（先画，关深度写入，不参与 SSAO）
    3) 几何 pass：不透明（按材质分组） -> 半透明（按距离从远到近，关深度写入）
    4) SSAO（读 COLOR1） -> 盒式模糊
    5) Bloom（读 COLOR0） -> 金字塔 -> 合成前的 bloom 纹理
    6) 合成：HDR * AO + Bloom -> 曝光 -> tonemap -> 暗角 -> gamma -> 屏幕

两个容易踩的点:

- **WindowBlock 与渲染目标无关**：即使画到 FBO，``window.projection`` /
  ``window.view`` 仍然生效（它是全局 UBO），所以相机照常设这两个即可。
- **半透明必须最后画且关深度写入**：先画不透明保证深度正确，再按距离
  从远到近画半透明——顺序反了会看到后面的物体"透过"前面的。
"""
from pyglet.gl import (GL_BLEND, GL_DEPTH_TEST, GL_ONE_MINUS_SRC_ALPHA,
                       GL_SRC_ALPHA, GL_TRIANGLES, glBlendFunc, glDepthMask,
                       glDisable, glEnable)
from pyglet.math import Mat4, Vec3

from engine.mathkit import mat4_to_Mat4
from engine.postfx import FullscreenQuad, PostChain, PostSettings, bind_screen
from engine.shaders import get
from engine.targets import RenderTarget
from engine.scene import Scene

__all__ = ['Renderer', 'PointLight']

PointLight = tuple      # (position(x,y,z), color(r,g,b))


class Renderer:
    """前向 PBR 渲染器 + 后处理链。"""

    MAX_POINT_LIGHTS = 4

    def __init__(self, width, height, environment=None, post_settings=None,
                 quad=None):
        from engine.material import default_textures
        # 默认贴图（白/法线/黑）要赶在任何绑定之前建好：懒创建会撞上
        # "pyglet 建纹理时冲掉当前激活单元绑定"的坑（见 targets 文档）
        default_textures()
        self.width, self.height = int(width), int(height)
        self.environment = environment
        self.quad = quad or FullscreenQuad()
        self.post = PostChain(self.width, self.height, quad=self.quad,
                              settings=post_settings or PostSettings())
        self.scene_target = RenderTarget(self.width, self.height, color_count=2)
        self.point_lights = []
        self.programs = {}
        self.stats = {'draw_calls': 0, 'triangles': 0, 'visible': 0,
                      'culled': 0, 'instances': 0}
        self.sort_transparent = True

    # ---- 着色器 ----
    def program(self, name):
        if name not in self.programs:
            self.programs[name] = get(name)
        return self.programs[name]

    # ---- 尺寸 ----
    def resize(self, width, height):
        w, h = int(width), int(height)
        if w == self.width and h == self.height:
            return False
        self.width, self.height = w, h
        self.scene_target.resize(w, h)
        self.post.resize(w, h)
        return True

    # ---- 灯光 ----
    def set_point_lights(self, lights):
        """最多 4 盏点光（着色器里是 4 个具名 uniform）。"""
        self.point_lights = list(lights)[:self.MAX_POINT_LIGHTS]
        return self

    def _apply_lights(self, program):
        program['uPointCount'] = len(self.point_lights)
        for i in range(self.MAX_POINT_LIGHTS):
            if i < len(self.point_lights):
                pos, col = self.point_lights[i]
                program[f'uPointPos{i}'] = Vec3(*pos)
                program[f'uPointColor{i}'] = Vec3(*col)
            else:
                program[f'uPointPos{i}'] = Vec3(0, 0, 0)
                program[f'uPointColor{i}'] = Vec3(0, 0, 0)

    # ---- 一帧 ----
    def render(self, camera, scene, aspect=None, window=None):
        """渲染一帧到屏幕。返回统计字典。"""
        w, h = self.width, self.height
        aspect = aspect if aspect else (w / max(1.0, float(h)))

        # 相机矩阵进 WindowBlock（画到 FBO 也走这一套）
        if window is not None:
            window.projection = camera.projection(aspect)
            window.view = camera.view_Mat4()

        target = self.scene_target
        target.bind()
        target.clear(color=(0.0, 0.0, 0.0, 1.0), depth=True)
        glEnable(GL_DEPTH_TEST)

        self._draw_sky(camera, aspect)

        visible = scene.cull(camera.frustum(aspect))
        opaque, transparent = [], []
        for d in visible:
            mode = getattr(getattr(d, 'material', None), 'alpha_mode', 'OPAQUE')
            (transparent if mode == 'BLEND' else opaque).append(d)

        stats = self._draw_list(opaque, camera)
        if transparent:
            if self.sort_transparent:
                eye = camera.position
                transparent.sort(key=lambda d: -_dist2(d, eye))
            glDepthMask(False)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            t = self._draw_list(transparent, camera)
            glDisable(GL_BLEND)
            stats['draw_calls'] += t['draw_calls']
            stats['triangles'] += t['triangles']
            glDepthMask(True)
        target.unbind()

        # ---- 后处理 ----
        post = self.post
        ao_tex, bloom_tex = None, None
        if post.settings.ssao_enabled:
            ao_tex = post.render_ssao(
                target.color[1],
                camera.projection(aspect),                       # Mat4
                mat4_to_Mat4(camera.inverse_view_projection(aspect)))
        if post.settings.bloom_enabled:
            bloom_tex = post.render_bloom(target.color[0])
        post.composite(target.color[0], w, h, bloom_tex, ao_tex)

        stats.update(visible=len(visible),
                     culled=scene.stats.get('culled', 0))
        self.stats.update(stats)
        return stats

    # ---- 各个 pass ----
    def _draw_sky(self, camera, aspect):
        if self.environment is None or not getattr(self.environment, 'baked', False):
            return
        prog = self.program('sky')
        glDisable(GL_DEPTH_TEST)
        glDepthMask(False)
        prog.use()
        self.environment.apply_sky(prog,
                                   Mat4(*camera.inverse_view_projection(aspect)),
                                   camera.position)
        self.quad.draw(prog)
        glDepthMask(True)
        glEnable(GL_DEPTH_TEST)

    def _draw_list(self, drawables, camera):
        """画一批可绘制物（已按材质/透明度分好组）。

        材质相同的连续物体只应用一次材质（纹理单元是全局状态，相邻绘制
        之间没人动 3~7 号单元，重复 apply 是纯浪费——每帧几百次 GL 调用
        就是这么攒出来的）。矩阵上传的去重在 Mesh 内部做（比较上次上传值）。
        """
        stats = {'draw_calls': 0, 'triangles': 0, 'instances': 0}
        by_program = {}
        for d in drawables:
            prog_name = 'pbr_inst' if d.__class__.__name__ == 'InstancedMesh' else 'pbr'
            by_program.setdefault(prog_name, []).append(d)
        for prog_name, items in by_program.items():
            prog = self.program(prog_name)
            prog.use()
            # 环境（IBL 三张图 + 太阳）与灯光每帧设一次，物体间不用重复
            start = 0
            if self.environment is not None and getattr(self.environment, 'baked', False):
                start = self.environment.apply(prog, 0)
            else:
                prog['uSunDir'] = Vec3(0, 1, 0)
                prog['uSunColor'] = Vec3(0, 0, 0)
                prog['uIblIntensity'] = 0.0
                prog['uPrefilterMaxLod'] = 0.0
            prog['uCamPos'] = Vec3(*camera.position)
            self._apply_lights(prog)
            last_material = None
            for d in items:
                same_material = d.material is last_material
                d.draw(unit_start=start, skip_material=same_material)
                last_material = d.material
                stats['draw_calls'] += 1
                data = getattr(d, 'data', None)
                if data is not None:
                    n = data.triangle_count
                    cnt = getattr(d, 'count', 1) or 1
                    stats['triangles'] += n * cnt
                    if cnt > 1:
                        stats['instances'] += cnt
        return stats


def _dist2(drawable, eye):
    box = getattr(drawable, 'world_aabb', None)
    c = box.center if box is not None else (0.0, 0.0, 0.0)
    return ((c[0] - eye[0]) ** 2 + (c[1] - eye[1]) ** 2 + (c[2] - eye[2]) ** 2)
