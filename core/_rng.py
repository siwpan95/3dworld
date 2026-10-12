# -*- coding: utf-8 -*-
"""确定性伪随机工具（供各共同核复用）。

**为什么不用 `random`**：本项目要求"同种子同结果"，而 `random` 的实现随 Python 版本
可能变化（Mersenne Twister 的播种/取值细节、`random.random()` 的字节序），
跨版本会漂移。这里用最朴素的 **LCG**（数值序列完全由本文件定义），
代价是随机质量一般 —— 对掉落/抖动用途足够。
"""
from __future__ import annotations

_A = 1103515245
_C = 12345
_M = 0x7FFFFFFF


def lcg_next(state: int) -> int:
    """推进一步，返回新状态（``0.._M``）。"""
    return (int(state) * _A + _C) & _M


def hash32(s) -> int:
    """FNV-1a 的 32 位变体：字符串 -> 稳定整数（永不为 0）。"""
    h = 2166136261
    for ch in str(s):
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h or 1


class Rng:
    """确定性随机源。``Rng('seed/table/7')`` 与 ``Rng(123)`` 都可。

    典型用法::

        r = Rng(f'{seed}/wave/{i}')
        r.next_f()            # 0.0 <= x < 1.0
        r.next_int(2, 5)      # 闭区间整数
    """

    __slots__ = ('s',)

    def __init__(self, seed):
        self.s = (int(seed) if isinstance(seed, int) else hash32(seed)) & _M

    def next_f(self) -> float:
        self.s = lcg_next(self.s)
        return self.s / _M

    def next_int(self, lo: int, hi: int) -> int:
        """闭区间 ``[lo, hi]`` 的整数。"""
        lo, hi = int(lo), int(hi)
        if hi <= lo:
            return lo
        return lo + int(self.next_f() * (hi - lo + 1))

    def choice(self, seq):
        if not seq:
            return None
        return seq[self.next_int(0, len(seq) - 1)]

    def weighted(self, pairs):
        """``[(value, weight), ...]`` 按权重取一个值。"""
        total = sum(float(w) for _v, w in pairs)
        if total <= 0:
            return pairs[0][0] if pairs else None
        x = self.next_f() * total
        acc = 0.0
        for v, w in pairs:
            acc += float(w)
            if x <= acc:
                return v
        return pairs[-1][0]

    def __repr__(self):
        return f'Rng(state={self.s})'


__all__ = ['Rng', 'lcg_next', 'hash32']
