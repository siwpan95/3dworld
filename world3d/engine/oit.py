# -*- coding: utf-8 -*-
"""Weighted Blended OIT（P2-2）—— 顺序无关透明。

**合规**：参考 Easy3D `dual_depth_peeling.cpp` / `average_color_blending.cpp` 的**问题意识**
（"透明靠排序必然有错"），但**方案独立选型**为 McGuire 2013 的 **Weighted Blended OIT**
（单趟 + 一次合成），**未借鉴其任何代码**（Easy3D 为 GPL-3.0）。

原理
----
透明物**不做排序**，一次 pass 写两张 MRT：

    accum  = Σ (c·α·w) , Σ (α·w)      blend = (ONE, ONE)                    加法
    reveal = Π (1-α)                  blend = (ZERO, ONE_MINUS_SRC_COLOR)   乘法

最后合成：

    C        = accum.rgb / max(accum.a, eps)      （加权平均色）
    final    = mix(背景色, C, 1 - reveal)

⇒ 无论绘制顺序如何，结果一致（这就是"顺序无关"）。代价是近似（深度权重非线性），
但对"多层玻璃/粒子/水面叠加"这类场景视觉上足够，且**比逐层剥离快得多**。

依赖
----
`glBlendFunci(index, ...)`（GL 4.0）—— 本机驱动可用（实测报 3.3 但放宽），
`supported()` 做运行时探测，不可用则由调用方回退到"排序透明"（现状路径）。
"""
from __future__ import annotations

from pyglet.gl import (GL_BLEND, GL_COLOR, GL_COLOR_BUFFER_BIT,
                       GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST, GL_ONE,
                       GL_ONE_MINUS_SRC_COLOR, GL_ZERO, glBlendFunci,
                       glClear, glClearBufferfv, glDisable, glEnable)
from pyglet.gl import GLfloat

from world3d.engine.postfx import FullscreenQuad
from world3d.engine.shaders import get
from world3d.engine.targets import RenderTarget, bind_texture


def supported() -> bool:
    """`glBlendFunci` 是否可用（WBOIT 的 per-RT blend 必需）。"""
    import pyglet.gl as G
    return hasattr(G, 'glBlendFunci')


class WBOIT:
    """Weighted Blended OIT 链：透明 pass（MRT 累积）+ 合成。

    用法::

        oit = WBOIT(w, h)
        # 1) 不透明场景画进 scene_tex（或主 framebuffer，另存一份）
        # 2) oit.begin(); 画所有透明物（用 shaders.get('wboit'）); oit.end()
        # 3) oit.composite(scene_tex, w, h)   # 合成到当前 framebuffer
    """

    def __init__(self, width: int, height: int):
        self.width = int(width)
        self.height = int(height)
        self.rt = RenderTarget(self.width, self.height, color_count=2, depth=True)
        self.quad = FullscreenQuad()
        self.prog = get('wboit_composite')

    # ------------------------------------------------------------ 尺寸
    def resize(self, width: int, height: int):
        if (int(width), int(height)) == (self.width, self.height):
            return
        self.width, self.height = int(width), int(height)
        self.rt.resize(self.width, self.height)

    # -------------------------------------------------- 透明 pass（累积）
    def begin(self):
        """绑定 MRT + 清屏（accum=0 / reveal=1）+ 设 per-RT blend。"""
        rt = self.rt
        rt.bind()
        # 逐附件清屏：accum 全 0，reveal 全 1
        # ⚠️ 第 1 参是 **GL_COLOR (0x1800)**，不是 GL_COLOR_BUFFER_BIT(0x4000)，
        #    更不是 0x0400(=GL_STENCIL_BUFFER_BIT)——写错会直接 GL_INVALID_ENUM。
        zero = (GLfloat * 4)(0.0, 0.0, 0.0, 0.0)
        one = (GLfloat * 4)(1.0, 1.0, 1.0, 1.0)
        glClearBufferfv(GL_COLOR, 0, zero)
        glClearBufferfv(GL_COLOR, 1, one)
        # 深度也要清（否则 oit 自己的 depth 是垃圾值，深度测试全失败 -> 透明物整片消失）
        glClear(GL_DEPTH_BUFFER_BIT)
        # 深度：透明物**不写深度**但参与测试（与常规 alpha 混合一致）
        glEnable(GL_BLEND)
        # 附件 0（accum）：加法
        glBlendFunci(0, GL_ONE, GL_ONE)
        # 附件 1（reveal）：乘法 -> dst = src * (1 - dst) = 1 * (1-dst)
        glBlendFunci(1, GL_ZERO, GL_ONE_MINUS_SRC_COLOR)
        from pyglet.gl import glDepthMask
        glDepthMask(False)
        glEnable(GL_DEPTH_TEST)

    def end(self):
        """结束透明 pass（恢复状态，避免污染后续绘制）。"""
        from pyglet.gl import glDepthMask
        glDepthMask(True)
        glDisable(GL_BLEND)
        self.rt.unbind()

    # ------------------------------------------------------------ 合成
    def composite(self, scene_tex, out_width=None, out_height=None):
        """把 (不透明场景 + 透明累积) 合成到**当前绑定的 framebuffer**。"""
        tex = self.prog
        tex.use()
        bind_texture(0, self.rt.color[0])        # accum
        tex['uAccum'] = 0
        bind_texture(1, self.rt.color[1])        # reveal
        tex['uReveal'] = 1
        bind_texture(2, scene_tex)               # 背景
        tex['uScene'] = 2
        glDisable(GL_DEPTH_TEST)
        self.quad.draw(tex)
        glEnable(GL_DEPTH_TEST)

    def delete(self):
        self.rt.delete()


__all__ = ['WBOIT', 'supported']
