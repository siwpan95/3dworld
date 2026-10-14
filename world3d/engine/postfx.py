# -*- coding: utf-8 -*-
"""后处理链：SSAO -> Bloom -> 合成（曝光 / tone-map / 暗角 / gamma）。

三个效果各自独立可关，管线顺序固定::

    场景 HDR ─┬─> SSAO(半分辨率) ─> 盒式模糊 ─┐
              └─> Bloom 金字塔 ───────────────┴─> 合成 ─> 屏幕

顺序的讲究（研究与实测都指向同一结论）:

- **Bloom 必须在 tone-map 之前叠加**：tone-map 之后的值已经不是光能，
  再加 bloom 会变成一圈灰雾；
- **SSAO 在 Bloom 之前、作为环境光的乘子**：AO 描述的是"环境光被挡了多少"，
  乘在 ambient 上才对，这里放在合成阶段统一乘（等价于乘 ambient，但省一次
  全屏 pass）；
- **暗角 / gamma 在 tone-map 之后**：它们是显示空间的观感处理。

分辨率策略：SSAO 走半分辨率（AO 是低频信号，全分辨率只是白烧 GPU），
Bloom 走 1/2 起的金字塔（大范围光晕靠金字塔，不靠大卷积核）。
"""
from pyglet.gl import (GL_DEPTH_TEST, GL_FRAMEBUFFER, GL_TRIANGLES,
                       glBindFramebuffer, glDisable, glEnable, glViewport)

from world3d.engine.geometry import fullscreen_quad
from world3d.engine.shaders import get
from world3d.engine.targets import RenderTarget, bind_texture

__all__ = ['PostSettings', 'FullscreenQuad', 'PostChain']


class PostSettings:
    """后处理参数（可运行时改，HUD/性能面板直接绑这个对象）。"""

    def __init__(self):
        # 曝光与 tone-map
        self.exposure = 1.0
        self.tone_map = 1                # 0=Reinhard  1=ACES
        self.vignette = 0.35
        # Bloom
        self.bloom_enabled = True
        # 阈值要高于天空亮度（天空 HDR ~0.9），否则整屏都在泛光、画面发灰
        self.bloom_threshold = 1.2
        self.bloom_knee = 0.4
        self.bloom_intensity = 0.5
        self.bloom_levels = 6
        self.bloom_radius = 1.0
        # SSAO
        self.ssao_enabled = True
        self.ssao_radius = 0.8
        self.ssao_bias = 0.05
        self.ssao_intensity = 1.6
        self.ssao_strength = 0.85        # AO 的实际生效比例（1=AO 全权）
        # 调试视图：0=最终 1=AO 2=Bloom
        self.debug = 0

    def toggle(self, name):
        key = f'{name}_enabled'
        setattr(self, key, not getattr(self, key))
        return getattr(self, key)


class FullscreenQuad:
    """全屏四边形（按着色器缓存顶点列表）。

    顶点直接落在 NDC，不依赖 WindowBlock 的 projection/view；全屏 pass 阶段
    那两个矩阵本来就是"别参与"的。
    """

    def __init__(self):
        self._cache = {}

    def draw(self, program):
        vl = self._cache.get(program)
        if vl is None:
            data = fullscreen_quad()
            vl = program.vertex_list(6, GL_TRIANGLES,
                                     position=('f', data.positions))
            self._cache[program] = vl
        vl.draw(GL_TRIANGLES)


def bind_screen(width, height):
    """切回窗口默认帧缓冲（0 = 默认 FBO）。"""
    glBindFramebuffer(GL_FRAMEBUFFER, 0)
    glViewport(0, 0, int(width), int(height))


class PostChain:
    """SSAO + Bloom + 合成。持有自己的离屏目标，随窗口尺寸重建。"""

    def __init__(self, width, height, quad=None, settings=None):
        self.settings = settings or PostSettings()
        self.quad = quad or FullscreenQuad()
        self.width, self.height = int(width), int(height)
        self._build_targets()

    # ---- 目标管理 ----
    def _build_targets(self):
        w, h = self.width, self.height
        s = self.settings
        # AO：半分辨率 + 同尺寸模糊目标
        aw, ah = max(1, w // 2), max(1, h // 2)
        self.ao_target = RenderTarget(aw, ah, color_count=1, depth=False)
        self.ao_blur = RenderTarget(aw, ah, color_count=1, depth=False)
        # Bloom：1/2 起的金字塔
        self.bloom_levels_tex = []
        bw, bh = aw, ah
        for _ in range(max(2, s.bloom_levels)):
            self.bloom_levels_tex.append(
                RenderTarget(max(1, bw), max(1, bh), color_count=1, depth=False))
            bw, bh = bw // 2, bh // 2
            if bw < 2 or bh < 2:
                break
        # 上采样用的第二组目标（COD 的 ping-pong：本级 = up(下一级) + 本级）
        self.bloom_up_tex = [
            RenderTarget(t.width, t.height, color_count=1, depth=False)
            for t in self.bloom_levels_tex[:-1]]
        self.bloom_texture = self.bloom_levels_tex[0].color[0] if self.bloom_levels_tex else None
        self.ao_texture = self.ao_blur.color[0]

    def resize(self, width, height):
        w, h = int(width), int(height)
        if w == self.width and h == self.height:
            return False
        self._release()
        self.width, self.height = w, h
        self._build_targets()
        return True

    def _release(self):
        for t in ([self.ao_target, self.ao_blur]
                  + list(self.bloom_levels_tex) + list(self.bloom_up_tex)):
            try:
                t.delete()
            except Exception:
                pass
        self.bloom_levels_tex = []
        self.bloom_up_tex = []

    # ---- SSAO ----
    def render_ssao(self, normal_depth_tex, projection, inv_projection):
        """由"视空间法线 + 线性深度"算 AO。返回 AO 纹理。"""
        s = self.settings
        prog = get('ssao')
        self.ao_target.bind()
        self.ao_target.clear(color=(1.0, 1.0, 1.0, 1.0), depth=False)
        glDisable(GL_DEPTH_TEST)
        prog.use()
        bind_texture(0, normal_depth_tex)
        prog['uNormalDepth'] = 0
        bind_texture(1, _noise_texture())
        prog['uNoise'] = 1
        prog['uProjection'] = projection
        prog['uInvProjection'] = inv_projection
        aw, ah = self.ao_target.width, self.ao_target.height
        prog['uNoiseScale'] = (aw / 4.0, ah / 4.0)
        prog['uRadius'] = s.ssao_radius
        prog['uBias'] = s.ssao_bias
        prog['uIntensity'] = s.ssao_intensity
        self.quad.draw(prog)
        self.ao_target.unbind()

        # 4x4 盒式模糊：不模糊的话 AO 噪点会像一层砂纸
        blur = get('ssao_blur')
        self.ao_blur.bind()
        blur.use()
        bind_texture(0, self.ao_target.color[0])
        blur['uAO'] = 0
        blur['uTexel'] = (1.0 / aw, 1.0 / ah)
        self.quad.draw(blur)
        self.ao_blur.unbind()
        glEnable(GL_DEPTH_TEST)
        self.ao_texture = self.ao_blur.color[0]
        return self.ao_texture

    # ---- Bloom ----
    def render_bloom(self, scene_tex):
        """阈值提取 -> 逐级降采样 -> 逐级帐篷上采样累加。返回 bloom 纹理。"""
        s = self.settings
        levels = self.bloom_levels_tex
        if not levels:
            return None
        glDisable(GL_DEPTH_TEST)

        # 1) 预过滤（软膝盖阈值 + Karis average）
        pre = get('bloom_prefilter')
        levels[0].bind()
        pre.use()
        bind_texture(0, scene_tex)
        pre['uScene'] = 0
        pre['uThresholdKnee'] = (s.bloom_threshold,
                                 max(0.0, s.bloom_threshold - s.bloom_knee),
                                 s.bloom_knee * 2.0)
        pre['uKaris'] = 1.0
        self.quad.draw(pre)
        levels[0].unbind()

        # 2) 逐级降采样
        down = get('bloom_down')
        for i in range(1, len(levels)):
            src = levels[i - 1]
            dst = levels[i]
            dst.bind()
            down.use()
            bind_texture(0, src.color[0])
            down['uSrc'] = 0
            down['uTexel'] = (1.0 / src.width, 1.0 / src.height)
            self.quad.draw(down)
            dst.unbind()

        # 3) 逐级上采样并累加（tent 滤波）
        up = get('bloom_up')
        for i in range(len(levels) - 2, -1, -1):
            src = levels[i + 1]           # 更小的（更模糊的）
            dst = self.bloom_up_tex[i]    # 与 levels[i] 同尺寸
            dst.bind()
            up.use()
            bind_texture(0, src.color[0])
            up['uSrc'] = 0
            up['uTexel'] = (1.0 / src.width, 1.0 / src.height)
            up['uRadius'] = s.bloom_radius
            self.quad.draw(up)
            dst.unbind()
            # 加回本级：只叠加，不做第二遍模糊（省一个 pass，观感差别很小）
            _add(levels[i], dst.color[0], self.quad)
        glEnable(GL_DEPTH_TEST)
        self.bloom_texture = levels[0].color[0]
        return self.bloom_texture

    # ---- 合成 ----
    def composite(self, scene_tex, width, height, bloom_tex=None, ao_tex=None):
        """合成到屏幕（调用前确保已切到默认帧缓冲）。"""
        s = self.settings
        prog = get('composite')
        bind_screen(width, height)
        glDisable(GL_DEPTH_TEST)
        prog.use()
        bind_texture(0, scene_tex)
        prog['uScene'] = 0
        bind_texture(1, bloom_tex or scene_tex)
        prog['uBloom'] = 1
        bind_texture(2, ao_tex or _white_ao())
        prog['uAO'] = 2
        prog['uBloomIntensity'] = s.bloom_intensity if bloom_tex else 0.0
        prog['uExposure'] = s.exposure
        prog['uAOStrength'] = s.ssao_strength if ao_tex else 0.0
        prog['uVignette'] = s.vignette
        prog['uToneMap'] = s.tone_map
        prog['uDebug'] = s.debug
        self.quad.draw(prog)
        glEnable(GL_DEPTH_TEST)


# ------------------------------------------------------------------ 配件 --
def _add(target, texture, quad):
    """把一张纹理加性混合到目标上（Bloom 上采样的累加步骤）。"""
    from pyglet.gl import (GL_BLEND, GL_FUNC_ADD, GL_ONE, glBlendEquation,
                           glBlendFunc, glEnable, glDisable)
    prog = get('debug')
    target.bind()
    glEnable(GL_BLEND)
    glBlendFunc(GL_ONE, GL_ONE)
    glBlendEquation(GL_FUNC_ADD)
    prog.use()
    bind_texture(0, texture)
    prog['uTex'] = 0
    prog['uChannel'] = 0
    prog['uScale'] = 1.0
    quad.draw(prog)
    glDisable(GL_BLEND)
    target.unbind()


_NOISE = None
_WHITE_AO = None


def _noise_texture():
    """4x4 随机切线向量噪声图（SSAO 用来打散条纹，确定性生成）。"""
    global _NOISE
    if _NOISE is None:
        import pyglet
        from world3d.engine.mathkit import hash3
        from world3d.engine.targets import make_solid_texture   # noqa: F401  触发保存/恢复语义
        data = bytearray()
        for y in range(4):
            for x in range(4):
                # 只需 xy（z 固定 0）：着色器里用 Gram-Schmidt 正交化到法线
                rx = hash3(x, y, 1)
                ry = hash3(x, y, 2)
                data += bytes((int(rx * 255), int(ry * 255), 128, 255))
        img = pyglet.image.ImageData(4, 4, 'RGBA', bytes(data))
        _NOISE = img.get_texture()
        from pyglet.gl import (GL_NEAREST, GL_REPEAT, GL_TEXTURE_2D,
                               GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T,
                               glBindTexture, glTexParameteri)
        _NOISE.min_filter = _NOISE.mag_filter = GL_NEAREST
        # ImageData.get_texture 会把纹理绑到当前激活单元（冲掉已有绑定），
        # 这里先记后还——与 targets.make_solid_texture 同一个坑。
        import ctypes
        from pyglet.gl import (GL_ACTIVE_TEXTURE, GL_TEXTURE_BINDING_2D,
                               GLint, glActiveTexture, glGetIntegerv)
        active, bound = GLint(), GLint()
        glGetIntegerv(GL_ACTIVE_TEXTURE, ctypes.byref(active))
        glGetIntegerv(GL_TEXTURE_BINDING_2D, ctypes.byref(bound))
        try:
            glBindTexture(GL_TEXTURE_2D, _NOISE.id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT)
        finally:
            glActiveTexture(active.value)
            glBindTexture(GL_TEXTURE_2D, bound.value)
    return _NOISE


def _white_ao():
    """AO 关闭时的占位纹理（全 1 = 不遮蔽）。"""
    global _WHITE_AO
    if _WHITE_AO is None:
        from world3d.engine.targets import make_solid_texture
        _WHITE_AO = make_solid_texture(255, 255, 255, 255)
    return _WHITE_AO
