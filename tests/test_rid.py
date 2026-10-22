# -*- coding: utf-8 -*-
"""RID 不透明句柄表的无窗口单测（纯数据结构 / 无 GL）。

`python -m tests.test_rid` 直接跑。聚合 runner 按 `  ok:` 行统计。

借鉴来源：Godot `RID`（MIT）的"不透明句柄 + 代际失效"思路；本实现自研，
不搬 Godot 源码。
"""

import sys

from world3d.engine.rid import (NULL_RID, RID, RIDError, RIDTable)


def test_allocate_returns_distinct_handles():
    t = RIDTable('tex')
    a = t.allocate('texA')
    b = t.allocate('texB')
    assert a != b, (a, b)
    assert t.deref(a) == 'texA' and t.deref(b) == 'texB'
    assert t.alive() == 2


def test_handle_is_opaque_and_hashable():
    """句柄不暴露对象，但可作字典键。"""
    t = RIDTable('tex')
    a = t.allocate('texA')
    assert t.deref(a) == 'texA'
    # 句柄本身只能拿到 index/gen/owner，拿不到对象
    assert not any(isinstance(v, str) and v == 'texA'
                   for v in (a.index, a.generation, a.owner))
    d = {a: 'mapping'}
    assert d[t.allocate('x') if False else a] == 'mapping'
    assert isinstance(a.as_int(), int)


def test_free_invalidates_handle_by_generation():
    """释放后槽位可复用，但代际自增 ⇒ 旧句柄必然失效。"""
    t = RIDTable('tex')
    a = t.allocate('A')
    idx = a.index
    assert t.free(a) is True
    assert t.is_valid(a) is False
    assert t.deref(a) is None
    # 槽位复用：新句柄拿到同一个 index，但代际不同
    b = t.allocate('B')
    assert b.index == idx, f'槽位应复用：{b.index} vs {idx}'
    assert b.generation != a.generation, '代际必须自增'
    assert t.is_valid(b) and not t.is_valid(a)


def test_double_free_is_safe():
    t = RIDTable('tex')
    a = t.allocate('A')
    assert t.free(a) is True
    assert t.free(a) is False, '重复释放应返回 False 而不抛'
    assert t.alive() == 0


def test_get_raises_on_dangling_handle():
    t = RIDTable('tex')
    a = t.allocate('A')
    t.free(a)
    try:
        t.get(a)
    except RIDError as exc:
        assert '失效' in str(exc) or '悬垂' in str(exc), str(exc)
    else:
        raise AssertionError('get() 应拒绝悬垂句柄')


def test_cross_table_handle_is_rejected():
    t1 = RIDTable('t1')
    t2 = RIDTable('t2')
    a = t1.allocate('A')
    assert t1.is_valid(a)
    assert t2.is_valid(a) is False, '跨表句柄必须无效'
    assert t2.deref(a) is None
    try:
        t2.get(a)
    except RIDError:
        pass
    else:
        raise AssertionError('跨表 get() 应抛错')


def test_slot_reuse_does_not_grow_capacity():
    """反复分配/释放，槽位总数不增长（free list 生效）。"""
    t = RIDTable('tex')
    first = t.allocate('x')
    t.free(first)
    cap0 = t.capacity()
    for _ in range(50):
        r = t.allocate('y')
        t.free(r)
    assert t.capacity() == cap0, f'槽位不应增长：{t.capacity()} vs {cap0}'
    assert t.alive() == 0
    assert t.free_slots() == cap0


def test_clear_invalidates_everything():
    t = RIDTable('tex')
    handles = [t.allocate(f'A{i}') for i in range(5)]
    t.clear()
    assert t.alive() == 0
    for h in handles:
        assert not t.is_valid(h), h


def test_null_rid_is_never_valid():
    t = RIDTable('tex')
    assert NULL_RID.is_null
    assert not t.is_valid(NULL_RID)
    assert t.deref(NULL_RID) is None


def test_rid_equality_and_repr():
    t = RIDTable('tex')
    a = t.allocate('A')
    b = RID(a.index, a.generation, a.owner)
    assert a == b
    assert a != RID(a.index, a.generation + 1, a.owner)
    assert 'RID' in repr(a)


def test_stats_bookkeeping():
    t = RIDTable('tex')
    a = t.allocate('A')
    t.allocate('B')
    t.free(a)
    s = t.stats()
    assert s['alive'] == 1 and s['capacity'] == 2 and s['free'] == 1
    assert len(t) == 1


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith('test_') and callable(fn)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'  ok: {name}')
        except AssertionError as exc:
            failed.append((name, exc))
            print(f'  FAIL: {name}: {exc}')
        except Exception as exc:                            # noqa: BLE001
            failed.append((name, exc))
            print(f'  ERROR: {type(exc).__name__}: {exc}')
    print('=' * 60)
    print(f'[test_rid] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_RID_TESTS_FAILED')
        return 1
    print('ALL_RID_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
