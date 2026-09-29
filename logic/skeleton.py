# -*- coding: utf-8 -*-
"""节点级层级动画（M5 兜底方案）：骨骼语义的节点树 TRS 继承。

Spike 结论（2026-09-29，实测）：pyglet 2.1.16 发行包无 glTF 解码器
（model/codecs/ 仅 OBJ），蒙皮骨骼动画（skin/JOINTS/WEIGHTS/关节矩阵管线）
在此版本不可行。故按设计文档走兜底路线：把"骨骼"建模为节点树，
父节点变换自动继承到子节点——覆盖机械臂/摆锤/简的角色等演示需求。
真实蒙皮的演进前提：pyglet 升级带 glTF+skin，或自研解码器 + 蒙皮着色器。

纯数学实现，不 import pyglet.gl，可无窗口单测。
"""
from pyglet.math import Mat4, Quaternion, Vec3

from logic.animation import Channel, Sampler, quat_from_axis_angle, quat_to_mat4


class BoneNode:
    """骨骼节点：局部 TRS（相对父节点）+ 世界矩阵缓存。"""

    def __init__(self, name, parent, translation=Vec3(0, 0, 0)):
        self.name = name
        self.parent = parent                  # BoneNode | None
        self.translation = translation        # 局部平移（相对父节点）
        self.rotation = Quaternion()          # 局部旋转（四元数）
        self.world_matrix = Mat4()
        if parent is not None:
            parent.children.append(self)
        self.children = []

    def local_matrix(self):
        return Mat4.from_translation(self.translation) @ quat_to_mat4(self.rotation)

    def world_translation(self):
        return Vec3(self.world_matrix[12], self.world_matrix[13], self.world_matrix[14])


class Skeleton:
    """节点树。约定：父节点必须先于子节点注册（按注册序计算世界矩阵）。"""

    def __init__(self):
        self.nodes = {}                       # name -> BoneNode（保序）
        self.order = []                       # [BoneNode] 注册序

    def add(self, name, parent=None, translation=Vec3(0, 0, 0)):
        assert name not in self.nodes, f'节点重名: {name}'
        if parent is not None:
            assert parent in self.nodes, f'父节点 {parent} 必须先注册'
        node = BoneNode(name, self.nodes.get(parent), translation)
        self.nodes[name] = node
        self.order.append(node)
        return node

    def apply_sample(self, sampled):
        """把时间轴采样结果写入节点局部 TRS（未覆盖的属性保持原值）。"""
        for name, node in self.nodes.items():
            data = sampled.get(name)
            if not data:
                continue
            if 'rotation' in data:
                node.rotation = data['rotation']
            if 'translation' in data:
                node.translation = data['translation']

    def compute(self):
        """自根向叶计算世界矩阵：world = parent.world @ local。"""
        for node in self.order:
            local = node.local_matrix()
            node.world_matrix = (node.parent.world_matrix @ local
                                 if node.parent else local)


# ---------------------------------------------------------- 机械臂编排 --
def build_arm(skeleton, base=Vec3(-6, 0, -3)):
    """两段机械臂：底座(固定) -> seg1(长 2.4) -> seg2(长 1.8)。"""
    skeleton.add('seg1', None, translation=base)
    skeleton.add('seg2', 'seg1', translation=Vec3(0, 2.4, 0))


def build_arm_channels():
    """机械臂摆动编排：seg1 主摆 ±30°，seg2 相对摆动（Z 轴，4 秒循环）。"""
    axis = (0, 0, 1)
    times = [0.0, 1.0, 2.0, 3.0, 4.0]
    seg1_q = [quat_from_axis_angle(axis, a) for a in (0, -28, 26, -28, 0)]
    seg2_q = [quat_from_axis_angle(axis, a) for a in (0, 24, 44, 24, 0)]
    return [
        Channel('seg1', 'rotation', Sampler(times, seg1_q, 'LINEAR')),
        Channel('seg2', 'rotation', Sampler(times, seg2_q, 'LINEAR')),
    ]
