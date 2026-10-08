# -*- coding: utf-8 -*-
"""渲染目标：FBO / 纹理 / 深度附件的封装。

pyglet 2.x 自带 ``pyglet.image.Framebuffer``，但它是"裸"的：不管 viewport、
不管 MRT 的 ``glDrawBuffers``、也不管 mip 级的存储分配。这里补齐三件事：

1. :class:`RenderTarget` —— 彩色纹理（可多张 = MRT）+ 深度附件 + 自动 viewport；
2. :class:`MipTarget`    —— 逐 mip 级可渲染的纹理（IBL 预过滤金字塔需要：
   每一级对应一个粗糙度，靠 ``textureLod`` 一次采样拿到"这一粗糙度下的环境光"）；
3. 尺寸变更时整体重建（窗口 resize 时必须跟着变，否则后处理会拉伸）。

实测坑位（Intel UHD / GL 4.6）:

- 只 attach 不调 ``glDrawBuffers`` 的话，MRT 的 COLOR1 不会写——
  ``glDrawBuffers`` 必须在 **bind 之后** 调用；
- 渲染到 mip 级之前必须先用 ``glTexImage2D`` 把该级的存储分配出来，
  否则 FBO 报 incomplete；
- ``Texture.create`` 默认 min_filter 是 LINEAR（无 mip），做金字塔必须显式
  设成 ``LINEAR_MIPMAP_LINEAR``。
"""
from pyglet.gl import (GL_COLOR_ATTACHMENT0, GL_DEPTH_ATTACHMENT, GL_DRAW_FRAMEBUFFER,
                       GL_FRAMEBUFFER, GL_HALF_FLOAT, GL_LINEAR, GL_LINEAR_MIPMAP_LINEAR,
                       GL_RGBA, GL_RGBA16F, GL_DEPTH_COMPONENT24, GL_TEXTURE_2D,
                       GL_UNSIGNED_BYTE, GL_R16F, GL_RED, glBindTexture,
                       glDrawBuffers, glGenerateMipmap, glTexImage2D, glViewport)
from pyglet.image import Framebuffer, Texture
from pyglet.image.buffer import Renderbuffer

__all__ = ['create_texture', 'RenderTarget', 'MipTarget', 'bind_texture']


def create_texture(width, height, internalformat=GL_RGBA16F, fmt=GL_RGBA,
                   min_filter=GL_LINEAR, mag_filter=GL_LINEAR, data_type=None):
    """创建可作为渲染目标的纹理。

    ``data_type`` 默认按 internalformat 选：浮点格式用 ``GL_HALF_FLOAT``，
    8 位格式用 ``GL_UNSIGNED_BYTE``（用错会在部分驱动上静默失败）。
    """
    if data_type is None:
        data_type = GL_HALF_FLOAT if internalformat in (GL_RGBA16F, GL_R16F) else GL_UNSIGNED_BYTE
    return Texture.create(width, height, internalformat=internalformat, fmt=fmt,
                          min_filter=min_filter, mag_filter=mag_filter)


def bind_texture(unit, texture):
    """把纹理绑到第 unit 号纹理单元（采样器 uniform 设为同一个号）。"""
    from pyglet.gl import glActiveTexture, GL_TEXTURE0
    glActiveTexture(GL_TEXTURE0 + unit)
    glBindTexture(texture.target, texture.id)


def make_solid_texture(r, g, b, a=255, repeat=False):
    """创建 1x1 常量贴图，且**不破坏当前纹理绑定**。

    pyglet 的 ``ImageData.get_texture()`` 会把新纹理绑到"当前激活的纹理
    单元"上——在渲染循环中途创建它会静默冲掉那个单元已有的绑定（实测把
    BRDF LUT 冲掉后，金属材质第一帧全黑）。这里存/恢复激活单元与绑定。
    """
    import ctypes

    import pyglet
    from pyglet.gl import (GL_ACTIVE_TEXTURE, GL_NEAREST, GL_TEXTURE_2D,
                           GL_TEXTURE_BINDING_2D, GLint, glActiveTexture,
                           glBindTexture, glGetIntegerv)
    active = GLint()
    bound = GLint()
    glGetIntegerv(GL_ACTIVE_TEXTURE, ctypes.byref(active))
    glGetIntegerv(GL_TEXTURE_BINDING_2D, ctypes.byref(bound))
    try:
        img = pyglet.image.ImageData(1, 1, 'RGBA', bytes((r, g, b, a)))
        tex = img.get_texture()
        tex.min_filter = tex.mag_filter = GL_NEAREST
        if repeat:
            from pyglet.gl import (GL_REPEAT, GL_TEXTURE_WRAP_S,
                                   GL_TEXTURE_WRAP_T, glTexParameteri)
            glBindTexture(tex.target, tex.id)
            glTexParameteri(tex.target, GL_TEXTURE_WRAP_S, GL_REPEAT)
            glTexParameteri(tex.target, GL_TEXTURE_WRAP_T, GL_REPEAT)
    finally:
        # 恢复"创建前"的单元与绑定，调用方的 sampler 指向不受影响
        glActiveTexture(active.value)
        glBindTexture(GL_TEXTURE_2D, bound.value)
    return tex


class RenderTarget:
    """离屏渲染目标：N 张彩色纹理（MRT）+ 一个深度附件。

    用法::

        rt = RenderTarget(960, 540, color_count=2)
        with rt:                 # 绑定 + 设 viewport + 清屏
            scene.draw()
        rt.color[0]              # 拿去当后处理输入
    """

    def __init__(self, width, height, color_count=1, depth=True,
                 internalformat=GL_RGBA16F, fmt=GL_RGBA, depth_attachment='renderbuffer'):
        self.width = int(width)
        self.height = int(height)
        self.color_count = int(color_count)
        self.internalformat = internalformat
        self.fmt = fmt
        self.depth_attachment = depth_attachment if depth else None
        self.color = []
        self.depth = None
        self.fbo = None
        self._draw_buffers = None
        self._build()

    # ---- 构建 ----
    def _build(self):
        w, h = max(1, self.width), max(1, self.height)
        self.color = [create_texture(w, h, self.internalformat, self.fmt)
                      for _ in range(self.color_count)]
        self.fbo = Framebuffer()
        for i, tex in enumerate(self.color):
            self.fbo.attach_texture(tex, attachment=GL_COLOR_ATTACHMENT0 + i)
        if self.depth_attachment == 'renderbuffer':
            self.depth = Renderbuffer(w, h, GL_DEPTH_COMPONENT24)
            self.fbo.attach_renderbuffer(self.depth, attachment=GL_DEPTH_ATTACHMENT)
        elif self.depth_attachment == 'texture':
            # 深度纹理的 fmt 必须是 GL_DEPTH_COMPONENT（用 GL_RGBA 会在
            # glTexImage2D 上直接 GL_INVALID_OPERATION），滤波只能 NEAREST。
            from pyglet.gl import GL_DEPTH_COMPONENT, GL_NEAREST
            self.depth = create_texture(w, h, GL_DEPTH_COMPONENT24, GL_DEPTH_COMPONENT,
                                        GL_NEAREST, GL_NEAREST, GL_UNSIGNED_BYTE)
            self.fbo.attach_texture(self.depth, attachment=GL_DEPTH_ATTACHMENT)
        if self.color_count > 1:
            self._draw_buffers = (GL_COLOR_ATTACHMENT0 + i for i in range(self.color_count))

    def _apply_draw_buffers(self):
        """MRT 必须显式声明写哪几个 COLOR_ATTACHMENT（bind 之后调用）。"""
        if self.color_count > 1:
            import ctypes
            bufs = (ctypes.c_uint * self.color_count)(
                *[GL_COLOR_ATTACHMENT0 + i for i in range(self.color_count)])
            glDrawBuffers(self.color_count, bufs)

    # ---- 生命周期 ----
    def bind(self):
        self.fbo.bind()
        self._apply_draw_buffers()
        glViewport(0, 0, self.width, self.height)

    def unbind(self):
        self.fbo.unbind()

    def clear(self, color=(0.0, 0.0, 0.0, 0.0), depth=True):
        """清屏。

        不用 ``Framebuffer.clear()``：它把 ``COLOR_ATTACHMENTi`` 的枚举值直接
        OR 起来喂给 ``glClear``（要的是 ``GL_COLOR_BUFFER_BIT``），MRT 下会抛
        ``GL_INVALID_VALUE``。这里自己调 glClear，顺便把浮点目标的清屏色
        也管上（默认全 0，天空由后处理前的 sky pass 写）。
        """
        from pyglet.gl import (glClear, glClearColor, glClearDepth,
                               GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT)
        glClearColor(*color)
        if depth:
            glClearDepth(1.0)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        else:
            glClear(GL_COLOR_BUFFER_BIT)

    def __enter__(self):
        self.bind()
        return self

    def __exit__(self, *exc):
        self.unbind()
        return False

    def resize(self, width, height):
        """尺寸变化后重建（窗口 resize 必须调，否则后处理会拉伸）。"""
        w, h = max(1, int(width)), max(1, int(height))
        if w == self.width and h == self.height:
            return False
        self.delete()
        self.width, self.height = w, h
        self._build()
        return True

    def delete(self):
        if self.fbo is not None:
            self.fbo.delete()
        if self.depth is not None and hasattr(self.depth, 'delete'):
            try:
                self.depth.delete()
            except Exception:
                pass
        self.color = []
        self.fbo = None

    @property
    def is_complete(self):
        self.fbo.bind()
        ok = self.fbo.is_complete
        self.fbo.unbind()
        return ok


class MipTarget:
    """逐 mip 级可渲染的纹理目标（IBL 预过滤金字塔）。

    每一级对应一个粗糙度：级 0 是锐利环境，级越高越模糊，着色器里用
    ``textureLod(tex, uv, roughness * max_lod)`` 一次采样取到对应模糊度。

    存储必须逐层用 ``glTexImage2D`` 预分配——只 attach 不分配的话 FBO
    直接 incomplete。
    """

    def __init__(self, width, height, levels=6, internalformat=GL_RGBA16F, fmt=GL_RGBA):
        self.width = int(width)
        self.height = int(height)
        self.levels = int(levels)
        self.internalformat = internalformat
        self.fmt = fmt
        self.texture = create_texture(self.width, self.height, internalformat, fmt,
                                      min_filter=GL_LINEAR_MIPMAP_LINEAR,
                                      mag_filter=GL_LINEAR)
        self._alloc_levels()
        self.fbo = Framebuffer()

    def _alloc_levels(self):
        type_ = GL_HALF_FLOAT if self.internalformat in (GL_RGBA16F, GL_R16F) else GL_UNSIGNED_BYTE
        glBindTexture(GL_TEXTURE_2D, self.texture.id)
        for i in range(1, self.levels):
            w = max(1, self.width >> i)
            h = max(1, self.height >> i)
            glTexImage2D(GL_TEXTURE_2D, i, self.internalformat, w, h, 0,
                         self.fmt, type_, None)
        # 关键：GL_TEXTURE_MAX_LEVEL 默认 1000， completeness 要求 mip 链一直
        # 排到 1x1——只分配 levels 层的话纹理是 mipmap-incomplete，采样直接
        # 返回黑（而且 glCheckFramebufferStatus 看不出来）。必须显式声明末级。
        from pyglet.gl import GL_TEXTURE_MAX_LEVEL, glTexParameteri
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, self.levels - 1)

    def level_size(self, level):
        return max(1, self.width >> level), max(1, self.height >> level)

    def bind_level(self, level):
        """绑定某一级为渲染目标（viewport 同步到该级尺寸）。

        用 ``glFramebufferTexture2D`` 而**不是** pyglet 的
        ``attach_texture_layer``：后者走的是 ``glFramebufferTextureLayer``，
        那是给 3D / 数组纹理用的，对普通 2D 纹理直接 GL_INVALID_OPERATION。
        """
        from pyglet.gl import glFramebufferTexture2D
        w, h = self.level_size(level)
        self.fbo.bind()
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, self.texture.id, level)
        glViewport(0, 0, w, h)
        return w, h

    def unbind(self):
        self.fbo.unbind()

    def generate_mipmaps(self):
        """兜底：直接由 GPU 生成盒式 mip 链（比逐级渲染糙，但不会失败）。"""
        glBindTexture(GL_TEXTURE_2D, self.texture.id)
        glGenerateMipmap(GL_TEXTURE_2D)

    @property
    def max_lod(self):
        return float(self.levels - 1)

    def delete(self):
        try:
            self.fbo.delete()
        except Exception:
            pass
