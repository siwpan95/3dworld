# -*- coding: utf-8 -*-
"""蒙皮骨骼动画（P2-3）：glTF ``skin`` -> 关节矩阵管线。

纯数学，不 import pyglet.gl，可无窗口单测。

管线::

    jointMatrix[i] = jointWorld[i] @ inverseBind[i]
    顶点变换        = Σ_k w_k · jointMatrix[joint_k] · v

本模块只负责"关节矩阵怎么算"；矩阵如何上 GPU（uniform 数组）与顶点着色器
如何混合，见 ``engine/shaders.py`` 的 ``PBR_SKIN_VERT`` 与 ``engine/mesh.py``
的 ``SkinnedMesh``。
"""
from __future__ import annotations

from pyglet.math import Mat4

# 关节矩阵以 ``uniform mat4 u_joints[MAX_JOINTS]`` 上传（P2-3 决策②③）
MAX_JOINTS = 64


class Skin:
    """glTF skin 的运行时形态：关节节点 + 逆绑定矩阵。

    Args:
        name: skin 名（仅调试用）。
        joints: 关节节点列表（``engine.scene.Node``）——**顺序须与 inverse_bind 一致**。
        inverse_bind: 每关节一个列主序 4×4（16 元组），即 glTF 的 inverseBindMatrices。
        max_joints: 上限（默认 64）；超出抛错。
    """

    def __init__(self, joints, inverse_bind, name='', max_joints=MAX_JOINTS):
        if len(joints) != len(inverse_bind):
            raise ValueError(
                f'skin {name!r}: joints({len(joints)}) 与 inverse_bind({len(inverse_bind)}) 数量不等')
        if len(joints) > max_joints:
            raise ValueError(
                f'skin {name!r}: 关节数 {len(joints)} 超过 MAX_JOINTS={max_joints}')
        self.name = name
        self.joints = list(joints)
        self.inverse_bind = [tuple(m) for m in inverse_bind]
        self.max_joints = max_joints

    @property
    def joint_count(self):
        return len(self.joints)

    def joint_matrices(self):
        """计算 ``jointWorld @ inverseBind``（列主序 16 元组列表）。

        调用前须保证关节节点的 ``world_matrix`` 已刷新（``Node.update_world()``）。
        """
        out = []
        for node, ib in zip(self.joints, self.inverse_bind):
            world = Mat4(*tuple(node.world_matrix))
            inv_b = Mat4(*tuple(ib))
            out.append(tuple(world @ inv_b))
        return out

    def uniform_matrices(self):
        """关节矩阵 -> ``Mat4`` 列表（长度 == ``max_joints``）。

        可直接赋给 ``uniform mat4 u_joints[MAX_JOINTS]``。

        **实测（pyglet 2.1.16）**：数组 uniform 的赋值方式是
        ``program['u_joints'] = [Mat4, ...]``（key 不带下标），且**列表长度必须
        等于数组声明长度**（须完整上传，不能只传前 n 个）。故未使用的槽位补
        **单位矩阵**（而非全 0——全 0 会让"权重指向越界槽"的顶点塌到原点）。
        """
        ms = [Mat4(*m) for m in self.joint_matrices()]
        ident = Mat4()
        ms.extend([ident] * (self.max_joints - len(ms)))
        return ms

    def __repr__(self):
        return f'Skin({self.name!r}, joints={self.joint_count})'


def make_skin_from_gltf(gltf, node_map, skin_index=0, max_joints=MAX_JOINTS):
    """从 ``GLTF`` 的第 ``skin_index`` 个 skin 构造运行时 :class:`Skin`。

    Args:
        gltf: ``engine.gltf.GLTF`` 实例。
        node_map: ``{node_index: engine.scene.Node}``——由 ``GLTF.build_scene_ex`` 返回。
        skin_index: 用哪个 skin。
    """
    skins = gltf.skins()
    if not (0 <= skin_index < len(skins)):
        raise IndexError(f'skin_index 越界: {skin_index}（共 {len(skins)} 个）')
    sk = skins[skin_index]
    joints = []
    for idx in sk['joints']:
        node = node_map.get(idx)
        if node is None:
            raise KeyError(f'skin {sk["name"]!r}: 关节节点 {idx} 不在场景节点映射中')
        joints.append(node)
    return Skin(joints, sk['inverse_bind'], name=sk['name'], max_joints=max_joints)


__all__ = ['Skin', 'make_skin_from_gltf', 'MAX_JOINTS']
