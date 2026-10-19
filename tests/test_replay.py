# -*- coding: utf-8 -*-
"""回放系统测试：浏览面 + 确定性自检（含「能抓住不确定性」的反向用例）。

关键设计：``test_verify_determinism_catches_nondeterminism`` 用**故意不确定**
的 run_fn 验证自检真的会红 —— 永远为绿的自检等于装饰品（本仓门禁哲学的
测试侧镜像）。
"""
from __future__ import annotations

import os
import sys
import tempfile

from world3d.logic.replay import Replay, save_replay, verify_determinism


def _mk_events():
    """构造一组带 seq/ts/type/text/tags 的事件（dict 形态）。"""
    return [
        {'seq': 1, 'ts': 0.0, 'type': 'move', 'text': '勇士向北', 'tags': ['a']},
        {'seq': 2, 'ts': 1.5, 'type': 'combat', 'text': '勇士击中哥布林', 'tags': ['a', 'b']},
        {'seq': 3, 'ts': 2.0, 'type': 'move', 'text': '', 'tags': []},  # 无文本
        {'seq': 4, 'ts': 3.2, 'type': 'loot', 'text': '获得金币', 'tags': ['b']},
    ]


def test_replay_basic():
    """构造 / len / iter / meta。"""
    rp = Replay(_mk_events(), actions=[{'actor': 'p1', 'act': 'move'}],
                meta={'seed': 7})
    assert len(rp) == 4
    assert [e['seq'] for e in rp] == [1, 2, 3, 4]
    assert rp.meta['seed'] == 7
    # 空构造
    empty = Replay()
    assert len(empty) == 0 and empty.meta == {}
    print('  ok: 构造/len/iter/meta')


def test_timeline_narrative():
    """timeline 只收有文本的事件；narrative 去前缀。"""
    rp = Replay(_mk_events())
    tl = rp.timeline()
    assert len(tl) == 3, '无文本事件不进 timeline'
    assert tl[0] == '#1 [0.0] move: 勇士向北'
    assert tl[1] == '#2 [1.5] combat: 勇士击中哥布林'
    nar = rp.narrative()
    assert nar == ['勇士向北', '勇士击中哥布林', '获得金币']
    print('  ok: timeline 过滤无文本 + 格式；narrative 纯文本')


def test_filter_summary():
    """按类型/标签过滤 + 汇总计数。"""
    rp = Replay(_mk_events())
    assert len(rp.filter(type='move')) == 2
    assert len(rp.filter(type='combat')) == 1
    assert len(rp.filter(tag='b')) == 2
    assert len(rp.filter(type='move', tag='b')) == 0
    s = rp.summary()
    assert s['events'] == 4 and s['actions'] == 0
    assert s['by_type'] == {'move': 2, 'combat': 1, 'loot': 1}
    print('  ok: filter 类型/标签/组合；summary 计数')


def test_actions_of():
    """动作序列按提出者过滤。"""
    rp = Replay(actions=[{'actor': 'p1', 'act': 'move'},
                         {'actor': 'p2', 'act': 'attack'},
                         {'actor': 'p1', 'act': 'wait'}])
    assert len(rp.actions_of()) == 3
    assert len(rp.actions_of('p1')) == 2
    assert rp.actions_of('p1')[0]['act'] == 'move'
    assert rp.actions_of('nobody') == []
    print('  ok: actions_of 全量/按 actor/无匹配')


def test_verify_determinism_pass():
    """确定性 run_fn：identical=True, first_diff=None。"""
    r = verify_determinism(lambda: [i * i for i in range(10)])
    assert r['identical'] is True
    assert r['first_diff'] is None
    assert r['runs'] == 2
    # count=3 同样通过
    r3 = verify_determinism(lambda: list(range(5)), count=3)
    assert r3['identical'] is True and r3['runs'] == 3
    print('  ok: 确定性输入 → identical')


def test_verify_determinism_catches_nondeterminism():
    """**反向用例**：不确定的 run_fn 必须被抓住（自检不是装饰品）。"""
    state = {'n': 0}

    def flaky():
        state['n'] += 1
        return [state['n'], 'x']  # 每次调用值都不同

    r = verify_determinism(flaky)
    assert r['identical'] is False, '不确定的 run_fn 居然判成一致？'
    assert r['first_diff'] == 0, '首个不一致项应是第 0 项'
    # 前缀一致、后面分叉：first_diff 应指向分叉点
    state2 = {'n': 0}

    def diverge_late():
        state2['n'] += 1
        return ['same', 'same', state2['n']]

    r2 = verify_determinism(diverge_late)
    assert r2['identical'] is False
    assert r2['first_diff'] == 2, 'first_diff 应指向第 2 项（前两项相同）'
    print('  ok: 不确定输入被抓住（首项分叉 + 延迟分叉两种）')


def test_json_roundtrip():
    """to_dict/from_dict + save_json/load_json 往返无损。"""
    rp = Replay(_mk_events(), actions=[{'actor': 'p1', 'act': 'move'}],
                meta={'seed': 7, 'ruleset': 'test'})
    d = rp.to_dict()
    rp2 = Replay.from_dict(d)
    assert rp2.events == rp.events
    assert rp2.actions == rp.actions
    assert rp2.meta == rp.meta
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'replay.json')
        rp.save_json(p)
        rp3 = Replay.load_json(p)
        assert rp3.events == rp.events
        assert rp3.meta == rp.meta
        # 文件是合法 JSON 且 UTF-8（中文不转义）
        import json
        raw = open(p, encoding='utf-8').read()
        assert '勇士向北' in raw, '中文应原样存储（ensure_ascii=False）'
        json.loads(raw)
    print('  ok: dict/JSON 往返无损 + 中文原样')


def test_save_replay():
    """save_replay 导出 timeline 纯文本。"""
    rp = Replay(_mk_events())
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'battle.txt')
        save_replay(rp, p)
        text = open(p, encoding='utf-8').read()
        lines = text.splitlines()
        assert len(lines) == 3, '只导出有文本的事件'
        assert lines[0] == '#1 [0.0] move: 勇士向北'
        assert text.endswith('\n'), '文件应以换行结尾'
    print('  ok: save_replay 纯文本导出')


def test_object_events():
    """对象形态事件（带属性的 Event 类）同样支持（_field 双形态）。"""
    class Ev:
        def __init__(self, seq, ts, etype, text, tags=None):
            self.seq, self.ts, self.type = seq, ts, etype
            self.text, self.tags = text, tags or []

    rp = Replay([Ev(1, 0.0, 'move', '对象事件', ['x'])])
    assert rp.timeline() == ['#1 [0.0] move: 对象事件']
    assert rp.narrative() == ['对象事件']
    assert len(rp.filter(type='move')) == 1
    assert len(rp.filter(tag='x')) == 1
    print('  ok: 对象形态事件（属性访问）与 dict 形态等价')


def main() -> int:
    tests = [
        test_replay_basic,
        test_timeline_narrative,
        test_filter_summary,
        test_actions_of,
        test_verify_determinism_pass,
        test_verify_determinism_catches_nondeterminism,
        test_json_roundtrip,
        test_save_replay,
        test_object_events,
    ]
    failed = []
    for t in tests:
        print(t.__name__)
        try:
            t()
        except Exception as e:
            failed.append((t.__name__, e))
            import traceback
            traceback.print_exc()
    print('=' * 60)
    print(f'[test_replay] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, e in failed:
            print(f'  - {n}: {e}')
        print('ALL_REPLAY_TESTS_FAILED')
        return 1
    print('ALL_REPLAY_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
