# -*- coding: utf-8 -*-
"""RID：不透明资源句柄 + 句柄表（Godot `RID` 思路，自研，零依赖）。

为什么要有这一层
----------------
渲染/物理的资源在引擎内部是可以被替换、被回收、被换后端的；如果上层直接拿着
对象引用（或指针），一旦内部重建/回收，上层就握着一个"看起来还在、其实已经
换了"的对象 —— 这类 bug 极难查。

RID 只暴露一个**不透明句柄**：上层能传递、能比较、能当字典键，但**拿不到背后的
对象**。想用得回句柄表去解引用，而解引用会校验**代际（generation）**：资源被
释放后槽位可以复用，但代际会自增，于是"旧句柄"一定会失效 —— 悬垂引用从
"未定义行为"变成"一次可断言的失败"。

设计要点
--------
* **代际**：每个槽位带一个代际号；`free()` 后自增。旧句柄的代际对不上 ⇒ 无效。
* **槽位复用**：被释放的槽位进 free list，下次分配复用（内存不增长）。
* **表隔离**：句柄记着所属表的 id，A 表的句柄拿到 B 表解引用会被拒绝。
* **句柄可打包成单个 int**（`as_int()`），方便做字典键 / 序列化 / 跨层传递。

本模块是**纯数据结构**：不碰 GL、不碰渲染后端，headless 可断言。
"""

from __future__ import annotations

# 打包位宽：index 24 位、generation 24 位、owner(表) 16 位
_INDEX_BITS = 24
_GEN_BITS = 24
_INDEX_MASK = (1 << _INDEX_BITS) - 1
_GEN_MASK = (1 << _GEN_BITS) - 1
_MAX_INDEX = _INDEX_MASK
_MAX_GEN = _GEN_MASK


class RIDError(ValueError):
    """句柄无效（悬垂 / 跨表 / 越界）。"""


class RID:
    """不透明资源句柄。

    只暴露 `index` / `generation` / `owner`（**仅用于调试与断言**，不是访问
    资源的通道）。真正拿对象要走 `RIDTable.get()` / `.deref()`。
    """

    __slots__ = ('index', 'generation', 'owner')

    def __init__(self, index, generation, owner=0):
        self.index = int(index)
        self.generation = int(generation)
        self.owner = int(owner)

    def as_int(self):
        """打包成单个 int（字典键 / 序列化 / 跨层传递用）。"""
        return ((self.generation & _GEN_MASK) << _INDEX_BITS) | (self.index & _INDEX_MASK)

    @property
    def is_null(self):
        """空句柄（index 0 且代际 0）。"""
        return self.index == 0 and self.generation == 0

    def __eq__(self, other):
        if not isinstance(other, RID):
            return NotImplemented
        return (self.index == other.index
                and self.generation == other.generation
                and self.owner == other.owner)

    def __hash__(self):
        return hash((self.index, self.generation, self.owner))

    def __repr__(self):
        return f'<RID #{self.index} gen{self.generation} owner{self.owner}>'


NULL_RID = RID(0, 0, 0)


class RIDTable:
    """句柄表：分配 / 释放 / 解引用，带代际与槽位复用。

    ``owner`` 是表的自增 id，用来拒绝"拿 A 表的句柄去 B 表解引用"。
    """

    _next_owner = 1
    __slots__ = ('name', 'owner', '_gen', '_slots', '_free', '_alive')

    def __init__(self, name='rid'):
        self.name = name
        self.owner = RIDTable._next_owner
        RIDTable._next_owner += 1
        # 槽位 0 保留为"空"，所以真实槽位从 1 开始 ⇒ RID(0,0) 天然是空句柄
        self._gen = [0]
        self._slots = [None]
        self._free = []
        self._alive = 0

    # ------------------------------------------------------------ 分配 --
    def allocate(self, obj=None) -> RID:
        """分配一个句柄，绑定 `obj`（可为 None，表示"先占位后填充"）。"""
        if self._free:
            idx = self._free.pop()
        else:
            idx = len(self._gen)
            if idx > _MAX_INDEX:
                raise RIDError(f'句柄表 {self.name!r} 槽位耗尽（上限 {_MAX_INDEX}）')
            self._gen.append(0)
            self._slots.append(None)
        self._slots[idx] = obj
        self._alive += 1
        return RID(idx, self._gen[idx], self.owner)

    # ------------------------------------------------------------ 释放 --
    def free(self, rid) -> bool:
        """释放句柄。已失效 / 重复释放返回 False（**不抛**，便于无条件清理）。"""
        if not self.is_valid(rid):
            return False
        idx = rid.index
        self._slots[idx] = None
        # 代际自增 —— 这是"旧句柄必然失效"的关键
        self._gen[idx] = (self._gen[idx] + 1) & _MAX_GEN
        self._free.append(idx)
        self._alive -= 1
        return True

    # ---------------------------------------------------------- 解引用 --
    def is_valid(self, rid) -> bool:
        """句柄是否仍然有效（同表 + 代际匹配 + 槽位活着）。"""
        if not isinstance(rid, RID) or rid.owner != self.owner:
            return False
        idx = rid.index
        if not (0 < idx < len(self._gen)):
            return False
        return self._gen[idx] == rid.generation and self._slots[idx] is not None

    def deref(self, rid):
        """解引用：有效返回对象，无效返回 ``None``（**不抛**，适合热路径）。"""
        if not self.is_valid(rid):
            return None
        return self._slots[rid.index]

    def get(self, rid):
        """解引用：有效返回对象，无效抛 `RIDError`（适合"必须存在"的场合）。"""
        if not isinstance(rid, RID) or rid.owner != self.owner:
            raise RIDError(f'句柄 {rid!r} 不属于表 {self.name!r}')
        idx = rid.index
        if not (0 < idx < len(self._gen)):
            raise RIDError(f'句柄 {rid!r} 槽位越界')
        if self._gen[idx] != rid.generation:
            raise RIDError(
                f'句柄 {rid!r} 已失效（槽位代际 {self._gen[idx]}）—— 悬垂引用')
        obj = self._slots[idx]
        if obj is None:
            raise RIDError(f'句柄 {rid!r} 槽位为空')
        return obj

    # ------------------------------------------------------------ 账本 --
    def alive(self) -> int:
        return self._alive

    def capacity(self) -> int:
        """已开出的槽位总数（含空闲）。"""
        return len(self._gen) - 1

    def free_slots(self) -> int:
        return len(self._free)

    def stats(self) -> dict:
        return {'name': self.name, 'owner': self.owner, 'alive': self._alive,
                'capacity': self.capacity(), 'free': self.free_slots()}

    def clear(self):
        """全部释放（代际自增 ⇒ 所有旧句柄失效）。"""
        for idx in range(1, len(self._gen)):
            if self._slots[idx] is not None:
                self._slots[idx] = None
                self._gen[idx] = (self._gen[idx] + 1) & _MAX_GEN
                self._free.append(idx)
        self._alive = 0

    def __len__(self):
        return self._alive

    def __repr__(self):
        return (f'<RIDTable {self.name} owner{self.owner} '
                f'alive={self._alive}/{self.capacity()} free={self.free_slots()}>')
