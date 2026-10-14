# -*- coding: utf-8 -*-
"""PBR 材质：金属-粗糙度工作流（对齐 glTF 2.0 / KHR_materials_pbrSpecularGlossiness 之外的事实标准）。

- ``base_color``：介电体=漫反射色，金属=F0（高光色）。线性空间。
- ``metallic``：0=介电体（F0 固定 0.04），1=金属（无漫反射）。
- ``roughness``：0=镜面，1=完全粗糙。**平方后**才进 GGX 公式 —— 这一步
  "感知重映射"让滑杆手感线性，否则 90% 的材质变化都挤在 0~0.3。

贴图槽与 glTF 一致：metal-rough 共用一张（B=金属度，G=粗糙度）。
贴图缺失时绑一张 1x1 的常量贴图（白 / 法线默认值），**用采样代替分支**——
着色器里少一个 if，也让"有贴图/没贴图"走完全相同的代码路径。
"""
from pyglet.math import Vec3

__all__ = ['Material', 'PRESETS', 'default_textures', 'WHITE_TEX']

# 缺省常量贴图（首次用到时才创建：需要 GL 上下文）
_CACHE = {}


def _make_1x1(r, g, b, a=255):
    from world3d.engine.targets import make_solid_texture
    return make_solid_texture(r, g, b, a)


def default_textures():
    """返回 (white, normal, black) 三张 1x1 常量贴图（带缓存）。"""
    if 'white' not in _CACHE:
        _CACHE['white'] = _make_1x1(255, 255, 255, 255)     # base/occlusion
        _CACHE['normal'] = _make_1x1(128, 128, 255, 255)    # 法线：(0,0,1)
        _CACHE['black'] = _make_1x1(0, 0, 0, 255)           # emissive
    return _CACHE['white'], _CACHE['normal'], _CACHE['black']


def WHITE_TEX():                      # 便于外部单独取用
    return default_textures()[0]


class Material:
    """一个 PBR 材质实例。"""

    __slots__ = ('name', 'base_color', 'metallic', 'roughness', 'emissive',
                 'base_tex', 'mr_tex', 'normal_tex', 'occlusion_tex',
                 'emissive_tex', 'alpha', 'alpha_mode', 'alpha_cutoff',
                 'double_sided')

    ALPHA_MODES = ('OPAQUE', 'MASK', 'BLEND')

    def __init__(self, name='material', base_color=(0.9, 0.9, 0.9),
                 metallic=0.0, roughness=0.5, emissive=(0.0, 0.0, 0.0),
                 base_tex=None, mr_tex=None, normal_tex=None,
                 occlusion_tex=None, emissive_tex=None, alpha=1.0,
                 alpha_mode='OPAQUE', alpha_cutoff=0.5, double_sided=False):
        self.name = name
        self.base_color = tuple(base_color)
        self.metallic = float(metallic)
        self.roughness = float(roughness)
        self.emissive = tuple(emissive)
        self.base_tex = base_tex
        self.mr_tex = mr_tex
        self.normal_tex = normal_tex
        self.occlusion_tex = occlusion_tex
        self.emissive_tex = emissive_tex
        self.alpha = float(alpha)
        assert alpha_mode in self.ALPHA_MODES, f'未知 alpha_mode: {alpha_mode}'
        self.alpha_mode = alpha_mode
        self.alpha_cutoff = float(alpha_cutoff)
        self.double_sided = double_sided

    # ---- 绑定 ----
    def apply(self, program, unit_start=0):
        """把材质写进着色器，并按需绑贴图。

        返回用掉的纹理单元数量（调用方接着从该编号继续绑，避免撞车）。
        """
        white, normal, black = default_textures()
        program['uBaseColor'] = Vec3(*self.base_color)
        program['uMetallic'] = self.metallic
        program['uRoughness'] = self.roughness
        program['uEmissive'] = Vec3(*self.emissive)
        program['uAlpha'] = self.alpha
        program['uUseNormalMap'] = 1 if self.normal_tex is not None else 0

        from world3d.engine.targets import bind_texture
        u = unit_start
        for tex, name in ((self.base_tex or white, 'uBaseTex'),
                          (self.mr_tex or white, 'uMRTex'),
                          (self.normal_tex or normal, 'uNormalTex'),
                          (self.occlusion_tex or white, 'uOcclusionTex'),
                          (self.emissive_tex or black, 'uEmissiveTex')):
            bind_texture(u, tex)
            program[name] = u
            u += 1
        return u - unit_start

    # ---- 其他 ----
    def clone(self, **overrides):
        m = Material(self.name, self.base_color, self.metallic, self.roughness,
                     self.emissive, self.base_tex, self.mr_tex, self.normal_tex,
                     self.occlusion_tex, self.emissive_tex, self.alpha,
                     self.alpha_mode, self.alpha_cutoff, self.double_sided)
        for k, v in overrides.items():
            setattr(m, k, v)
        return m

    def to_dict(self):
        """序列化（贴图只记名字，二进制不进 JSON）。"""
        return {
            'name': self.name,
            'base_color': list(self.base_color),
            'metallic': self.metallic,
            'roughness': self.roughness,
            'emissive': list(self.emissive),
            'alpha': self.alpha,
            'alpha_mode': self.alpha_mode,
            'alpha_cutoff': self.alpha_cutoff,
            'double_sided': self.double_sided,
        }

    @staticmethod
    def from_dict(d):
        return Material(**{k: v for k, v in d.items()
                           if k in ('name', 'base_color', 'metallic', 'roughness',
                                    'emissive', 'alpha', 'alpha_mode',
                                    'alpha_cutoff', 'double_sided')})

    def __repr__(self):
        return (f'Material({self.name!r}, metallic={self.metallic:.2f}, '
                f'roughness={self.roughness:.2f})')


# 常用预设（数值取自 glTF 官方材质示例 / Filament 的实测表）
PRESETS = {
    'gold': Material('gold', (1.0, 0.766, 0.336), 1.0, 0.25),
    'copper': Material('copper', (0.955, 0.638, 0.538), 1.0, 0.30),
    'steel': Material('steel', (0.562, 0.565, 0.578), 1.0, 0.42),
    'iron': Material('iron', (0.531, 0.512, 0.496), 1.0, 0.65),
    'plastic_red': Material('plastic_red', (0.85, 0.12, 0.10), 0.0, 0.35),
    'rubber': Material('rubber', (0.06, 0.06, 0.06), 0.0, 0.90),
    'ceramic': Material('ceramic', (0.92, 0.92, 0.90), 0.0, 0.12),
    'marble': Material('marble', (0.78, 0.77, 0.74), 0.0, 0.22),
    'emerald_light': Material('emerald_light', (0.10, 0.80, 0.35), 0.0, 0.15,
                              emissive=(0.05, 0.55, 0.22)),
    'neon': Material('neon', (0.05, 0.05, 0.05), 0.0, 0.4,
                     emissive=(0.0, 1.6, 2.2)),
}
