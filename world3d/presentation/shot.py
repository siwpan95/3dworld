# -*- coding: utf-8 -*-
"""离屏抓帧工具：把当前帧缓冲写成 PNG（``--shot`` 渲染验证共用）。

为什么不用 ``window.get_color_buffer()``：它按 GL_VIEWPORT 读取，而 viewport
在 DPI 缩放环境下会被乘 scale，越界读出黑边。这里直接按**实际 buffer 尺寸**
调 ``glReadPixels``，并统一设 ``dpi_scaling='real'``（见 presentation/kkrieger_app.py
顶部的实测记录），逻辑尺寸 = 物理尺寸，读数不会错位。

pyglet 图像约定原点在左下（与 glReadPixels 一致），PNG 编码器保存时自动翻到
顶部起始——原样保存即为正立图像，无需手工翻转。
"""
import ctypes
import os

import pyglet
from pyglet.gl import GL_RGBA, GL_UNSIGNED_BYTE, glReadPixels


def capture_window(window, path):
    """抓取当前帧缓冲并写入 path（自动建目录），返回绝对路径。"""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    w, h = window.width, window.height
    row = w * 4
    buf = (ctypes.c_ubyte * (row * h))()
    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, buf)
    pyglet.image.ImageData(w, h, 'RGBA', bytes(buf), row).save(path)
    return path
