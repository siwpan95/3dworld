# -*- coding: utf-8 -*-
"""场景图：节点层级 + 世界矩阵 + 可见性收集。

节点变换是 **TRS**（translation / rotation / scale），组合顺序 ``T * R * S``——
与 glTF 2.0 一致，也和时间轴的通道一一对应。

世界矩阵带**脏标记**：不动的节点不重算。层级动画（机械臂 4 级）每帧只
重算动了的那一支，其余节点直接读缓存。

场景与渲染解耦：``Scene`` 只管结构和矩阵，"谁可见、怎么画"交给
``Renderer``；``Scene.collect()`` 返回的是可绘制物清单，不碰 GL。
"""
from world3d.engine.frustum import AABB, SpatialHash
from world3d.engine.mathkit import (mat4_from_trs, mat4_identity, mat4_mul,
                            quat_normalize)

__all__ = ['Node', 'Scene']


class Node:
    """场景节点：局部 TRS + 子节点 + 可选的可绘制物。"""

    def __init__(self, name='node', translation=(0.0, 0.0, 0.0),
                 rotation=(1.0, 0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0),
                 drawable=None, visible=True):
        self.name = name
        self.translation = tuple(translation)
        self.rotation = quat_normalize(tuple(rotation))
        self.scale = tuple(scale)
        self.children = []
        self.parent = None
        self.drawable = drawable            # Mesh / InstancedMesh / None
        self.visible = visible
        self.user_data = {}
        self._local = mat4_identity()
        self._world = mat4_identity()
        self._dirty = True

    # ---- 层级 ----
    def add(self, child):
        assert child.parent is None, f'{child.name} 已有父节点'
        child.parent = self
        self.children.append(child)
        return child

    def remove(self, child):
        if child in self.children:
            self.children.remove(child)
            child.parent = None
        return self

    def traverse(self):
        """深度优先遍历（含自己）。"""
        yield self
        for c in self.children:
            yield from c.traverse()

    def find(self, name):
        for n in self.traverse():
            if n.name == name:
                return n
        return None

    @property
    def depth(self):
        d, p = 0, self.parent
        while p is not None:
            d += 1
            p = p.parent
        return d

    # ---- 变换 ----
    def set_translation(self, t):
        self.translation = tuple(t)
        self._dirty = True
        return self

    def set_rotation(self, q):
        self.rotation = quat_normalize(tuple(q))
        self._dirty = True
        return self

    def set_scale(self, s):
        self.scale = tuple(s)
        self._dirty = True
        return self

    def mark_dirty(self):
        self._dirty = True
        for c in self.children:
            c.mark_dirty()

    @property
    def local_matrix(self):
        if self._dirty:
            self._local = mat4_from_trs(self.translation, self.rotation, self.scale)
            self._dirty = False
        return self._local

    @property
    def world_matrix(self):
        return self._world

    def update_world(self, parent_matrix=None, force=False):
        """递归刷新世界矩阵（脏标记生效时跳过未变动的子树）。"""
        parent = parent_matrix if parent_matrix is not None else mat4_identity()
        dirty = self._dirty or force
        if dirty:
            self._local = mat4_from_trs(self.translation, self.rotation, self.scale)
            self._dirty = False
        self._world = mat4_mul(parent, self._local)
        for c in self.children:
            c.update_world(self._world, dirty or force)
        return self._world

    def world_position(self):
        return (self._world[12], self._world[13], self._world[14])

    # ---- 包围体 ----
    def world_aabb(self):
        """本节点（含子树）的世界包围盒。"""
        box = None
        if self.drawable is not None:
            box = getattr(self.drawable, 'world_aabb', None)
            if box is not None:
                box = box.copy()
        for c in self.children:
            cb = c.world_aabb()
            if cb is None:
                continue
            box = cb.copy() if box is None else box.union(cb)
        return box

    # ---- 序列化 ----
    def to_dict(self):
        d = {'name': self.name,
             'translation': list(self.translation),
             'rotation': list(self.rotation),
             'scale': list(self.scale)}
        if self.children:
            d['children'] = [c.to_dict() for c in self.children]
        if self.user_data:
            d['user_data'] = dict(self.user_data)
        if self.drawable is not None:
            mat = getattr(self.drawable, 'material', None)
            if mat is not None:
                d['material'] = mat.to_dict()
            d['mesh'] = getattr(getattr(self.drawable, 'data', None), 'name', '')
        return d

    @staticmethod
    def from_dict(d):
        n = Node(d.get('name', 'node'),
                 d.get('translation', (0.0, 0.0, 0.0)),
                 d.get('rotation', (1.0, 0.0, 0.0, 0.0)),
                 d.get('scale', (1.0, 1.0, 1.0)))
        n.user_data = dict(d.get('user_data', {}))
        for cd in d.get('children', ()):
            n.add(Node.from_dict(cd))
        return n

    def __repr__(self):
        return f'Node({self.name!r}, children={len(self.children)})'


class Scene:
    """一个场景：根节点 + 光照环境 + 剔除统计。"""

    def __init__(self, name='scene', environment=None):
        self.name = name
        self.root = Node(name='root')
        self.environment = environment
        self._drawables = None
        self.stats = {'nodes': 0, 'drawables': 0, 'visible': 0, 'culled': 0}
        self.use_spatial_hash = False
        self._hash = None
        self._hash_cell = 4.0

    # ---- 构建 ----
    def add(self, node, parent=None):
        (parent or self.root).add(node)
        self._drawables = None
        return node

    def add_mesh(self, mesh, name=None, parent=None, **trs):
        """把一个可绘制物挂成节点（省掉手写 Node 的样板）。"""
        n = Node(name or getattr(mesh, 'name', 'mesh'), drawable=mesh, **trs)
        return self.add(n, parent)

    # ---- 每帧 ----
    def update(self, dt=None):
        """刷新世界矩阵，并把矩阵同步到各可绘制物。"""
        self.root.update_world(mat4_identity(), force=False)
        for node in self.root.traverse():
            d = node.drawable
            if d is not None and hasattr(d, 'set_model'):
                d.set_model(node.world_matrix)
        return self

    def drawables(self):
        """收集所有可绘制物（带缓存，结构变化后需 ``invalidate()``）。"""
        if self._drawables is None:
            out = []
            for n in self.root.traverse():
                if n.drawable is not None and n.visible:
                    out.append(n.drawable)
            self._drawables = out
        return self._drawables

    def invalidate(self):
        self._drawables = None
        self._hash = None
        return self

    # ---- 剔除 ----
    def cull(self, frustum, use_sphere_prepass=True):
        """视锥剔除：球粗筛 -> AABB 精筛。返回可见清单。

        先测球（一次点积）再测盒（p-顶点）是标准两级做法：球测试便宜且
        旋转不变，能先砍掉大部分；剩下的再用 AABB 精确些地筛。
        """
        visible, culled = [], 0
        for d in self.drawables():
            if not getattr(d, 'visible', True):
                culled += 1
                continue
            if use_sphere_prepass and hasattr(d, 'world_sphere'):
                c, r = d.world_sphere
                if not frustum.intersects_sphere(c, r):
                    culled += 1
                    continue
            box = getattr(d, 'world_aabb', None)
            if box is not None and not frustum.intersects_aabb(box):
                culled += 1
                continue
            visible.append(d)
        self.stats.update(nodes=sum(1 for _ in self.root.traverse()),
                          drawables=len(self.drawables()),
                          visible=len(visible), culled=culled)
        return visible

    def build_spatial_hash(self, cell=4.0):
        """为静态场景建均匀网格（物体多时可加速粗筛）。"""
        self._hash = SpatialHash(cell)
        self._hash_cell = cell
        for i, d in enumerate(self.drawables()):
            box = getattr(d, 'world_aabb', None)
            if box is not None:
                self._hash.insert(i, box)
        return self._hash

    # ---- 序列化（几何不进 JSON，只记结构） ----
    def to_dict(self):
        d = {'name': self.name, 'root': self.root.to_dict()}
        if self.environment is not None:
            d['environment'] = self.environment.to_dict()
        return d

    @staticmethod
    def from_dict(d):
        s = Scene(d.get('name', 'scene'))
        s.root = Node.from_dict(d.get('root', {'name': 'root'}))
        return s

    def __repr__(self):
        return f'Scene({self.name!r}, drawables={len(self.drawables())})'
