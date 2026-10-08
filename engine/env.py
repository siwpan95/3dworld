# -*- coding: utf-8 -*-
"""光照环境：程序化天空 + IBL 三件套的烘焙。

烘焙流水线（一次性，建窗口后跑一遍，之后每帧只采样）::

    天空解析式 ──ENV_GEN──> 等距柱状环境图 (512x256, RGBA16F)
        ├──IRRADIANCE──> 辐照度图 (64x32)            漫反射 IBL
        ├──PREFILTER──> 预过滤金字塔 (256x128, 6 级)  镜面 IBL
        └──BRDF_LUT──> 2D LUT (128x128)              split-sum 第二半

为什么是等距柱状而不是立方体贴图：pyglet 没有立方体贴图类（只有 GL 常量），
自己造要走 ``glTexImage2D`` + 六个面 + ``GL_TEXTURE_CUBE_MAP_SEAMLESS``，
成本高且容易在采样器类型上翻车。等距柱状的代价是两极有挤压畸变——
对"低频天空 + 环境光"这种信号完全够用，实测看不出来。

辐照度图里存的是 **E/PI**（即 Li 的余弦加权均值，烘焙时已除过 PI），
所以着色器里 diffuse = albedo * texture(...)，不再除 PI。
"""
from pyglet.math import Vec3

from engine.postfx import FullscreenQuad
from engine.shaders import get
from engine.targets import MipTarget, RenderTarget, bind_texture

__all__ = ['LightEnvironment']


class LightEnvironment:
    """一个可烘焙的光照环境（程序化天空 + 太阳 + IBL）。"""

    def __init__(self, sun_dir=(0.45, 0.75, 0.35), sun_color=(1.0, 0.94, 0.86),
                 sun_intensity=1.0, sky_zenith=(0.24, 0.44, 0.78),
                 sky_horizon=(0.62, 0.76, 0.90), sky_ground=(0.16, 0.15, 0.14),
                 ibl_intensity=1.0, sky_intensity=1.0,
                 env_size=(512, 256), prefilter_levels=6, quad=None):
        self.sun_dir = tuple(sun_dir)
        self.sun_color = tuple(sun_color)
        self.sun_intensity = float(sun_intensity)
        self.sky_zenith = tuple(sky_zenith)
        self.sky_horizon = tuple(sky_horizon)
        self.sky_ground = tuple(sky_ground)
        self.ibl_intensity = float(ibl_intensity)
        self.sky_intensity = float(sky_intensity)
        self.env_size = tuple(env_size)
        self.prefilter_levels = int(prefilter_levels)
        self.quad = quad or FullscreenQuad()

        self.env_tex = None
        self.irradiance_tex = None
        self.prefilter = None
        self.brdf_lut_tex = None
        self.baked = False

    # ---- 烘焙 ----
    def bake(self):
        """跑完四趟烘焙 pass。需要 GL 上下文（窗口已创建）。"""
        self._bake_env()
        self._bake_irradiance()
        self._bake_prefilter()
        self._bake_brdf_lut()
        self.baked = True
        return self

    def _sky_uniforms(self, program):
        program['uSkyZenith'] = Vec3(*self.sky_zenith)
        program['uSkyHorizon'] = Vec3(*self.sky_horizon)
        program['uSkyGround'] = Vec3(*self.sky_ground)
        program['uSunDir'] = Vec3(*self.sun_dir)
        program['uSunColor'] = Vec3(*self.sun_color)
        program['uSunIntensity'] = self.sun_intensity

    def _bake_env(self):
        w, h = self.env_size
        rt = RenderTarget(w, h, color_count=1, depth=False)
        prog = get('env_gen')
        rt.bind()
        prog.use()
        self._sky_uniforms(prog)
        self.quad.draw(prog)
        rt.unbind()
        self.env_tex = rt.color[0]
        self._env_target = rt

    def _bake_irradiance(self):
        rt = RenderTarget(64, 32, color_count=1, depth=False)
        prog = get('irradiance')
        rt.bind()
        prog.use()
        bind_texture(0, self.env_tex)
        prog['uEnv'] = 0
        self.quad.draw(prog)
        rt.unbind()
        self.irradiance_tex = rt.color[0]
        self._irr_target = rt

    def _bake_prefilter(self):
        """逐级渲染到 mip 链：级 i 对应 roughness = i / (levels-1)。

        级 0 给一个极小的粗糙度（0.02）而不是 0：GGX 在 roughness=0 时
        重要性采样的分母会退化到镜像单一方向，出现可见的采样噪点。
        """
        self.prefilter = MipTarget(256, 128, levels=self.prefilter_levels)
        prog = get('prefilter')
        for level in range(self.prefilter_levels):
            self.prefilter.bind_level(level)
            prog.use()
            bind_texture(0, self.env_tex)
            prog['uEnv'] = 0
            prog['uRoughness'] = max(0.02, level / max(1, self.prefilter_levels - 1))
            self.quad.draw(prog)
            self.prefilter.unbind()

    def _bake_brdf_lut(self):
        rt = RenderTarget(128, 128, color_count=1, depth=False)
        prog = get('brdf_lut')
        rt.bind()
        prog.use()
        self.quad.draw(prog)
        rt.unbind()
        self.brdf_lut_tex = rt.color[0]
        self._lut_target = rt

    # ---- 使用 ----
    def apply(self, program, unit_start=0):
        """把环境（太阳 + IBL 三张图）写进 PBR 着色器。返回占用的纹理单元数。"""
        assert self.baked, 'LightEnvironment 尚未 bake()'
        u = unit_start
        bind_texture(u, self.irradiance_tex)
        program['uIrradiance'] = u
        u += 1
        bind_texture(u, self.prefilter.texture)
        program['uPrefilter'] = u
        u += 1
        bind_texture(u, self.brdf_lut_tex)
        program['uBrdfLUT'] = u
        u += 1
        program['uPrefilterMaxLod'] = self.prefilter.max_lod
        program['uIblIntensity'] = self.ibl_intensity
        program['uSunDir'] = Vec3(*self.sun_dir)
        program['uSunColor'] = Vec3(*self.sun_color) * self.sun_intensity
        return u - unit_start

    def apply_sky(self, program, inv_view_proj, cam_pos):
        """把天空背景参数写进 sky 着色器。"""
        bind_texture(0, self.env_tex)
        program['uEnv'] = 0
        program['uInvViewProj'] = inv_view_proj
        program['uCamPos'] = Vec3(*cam_pos)
        program['uSkyIntensity'] = self.sky_intensity

    # ---- 运行时调节 ----
    def set_sun(self, direction=None, color=None, intensity=None, rebake=True):
        if direction is not None:
            self.sun_dir = tuple(direction)
        if color is not None:
            self.sun_color = tuple(color)
        if intensity is not None:
            self.sun_intensity = float(intensity)
        if rebake and self.baked:
            self.bake()
        return self

    def to_dict(self):
        return {
            'sun_dir': list(self.sun_dir), 'sun_color': list(self.sun_color),
            'sun_intensity': self.sun_intensity,
            'sky_zenith': list(self.sky_zenith),
            'sky_horizon': list(self.sky_horizon),
            'sky_ground': list(self.sky_ground),
            'ibl_intensity': self.ibl_intensity,
            'sky_intensity': self.sky_intensity,
        }

    def __repr__(self):
        return f'LightEnvironment(baked={self.baked}, sun={self.sun_dir})'
