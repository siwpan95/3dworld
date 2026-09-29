# -*- coding: utf-8 -*-
"""资源加载（M4）：模型文件 -> pyglet Model 列表。

版本边界（实测 2.1.16）：pyglet 发行包仅内置 OBJ 解码器（pyglet/model/codecs/
只有 base.py 与 obj.py）；glTF 解码器在开发分支，尚未随版发行。
因此本模块当前走 OBJ；待 pyglet 升级带 glTF 后，load_model 无需改动
（pyglet.model.load 按扩展名自动分派），.glb 即插即用。
"""
import os

import pyglet


def load_model(path, batch):
    """加载模型文件并创建 Model 列表（入指定 Batch）。

    Args:
        path: 相对/绝对路径（.obj，未来 .glb/.gltf）
        batch: 目标 Batch

    Returns:
        list[pyglet.model.Model]：每个 Model 的 matrix 属性可逐帧设置。

    Raises:
        FileNotFoundError: 路径不存在。
        pyglet.resource.ResourceNotFoundException: 无对应解码器。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    scene = pyglet.model.load(path)
    return scene.create_models(batch=batch)
