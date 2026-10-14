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

from world3d.logic.animation import quat_to_mat4


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
def build_arm(skeleton, base=None):
    """机械臂节点树：转台(Y 轴) → 大臂(2.4) → 小臂(1.8) → 夹爪。

    层级比早期版本多两层，因为**取件与放件不在同一个方位**：只靠绕 Z 的
    两段臂只能在同一个竖直平面里划弧，够不到侧后方的台座。加上"转台绕 Y"
    这一级方位自由度后，两连杆逆运动学才有解（见 ``logic/choreography.py``
    的 ``_arm_ik``）。

    节点约定（名字被 choreography / renderer / 测试共用，改名要同步）：
      turret 局部平移 = 底座位置（世界坐标）
      seg1   局部平移 = (0, 0.6, 0)   —— 肩关节在转台顶面
      seg2   局部平移 = (0, 2.4, 0)   —— 肘关节在大臂末端
      grip   局部平移 = (0, 1.8, 0)   —— 腕关节在小臂末端 = 抓取点
    """
    from world3d.logic.choreography import ARM_BASE, L1, L2, SHOULDER_H
    base = ARM_BASE if base is None else base
    skeleton.add('turret', None, translation=base)
    skeleton.add('seg1', 'turret', translation=Vec3(0.0, SHOULDER_H, 0.0))
    skeleton.add('seg2', 'seg1', translation=Vec3(0.0, L1, 0.0))
    skeleton.add('grip', 'seg2', translation=Vec3(0.0, L2, 0.0))


def grip_world(skeleton):
    """夹爪（抓取点）的世界坐标——宝石被夹持时的落点。"""
    return skeleton.nodes['grip'].world_translation()
