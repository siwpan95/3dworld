# -*- coding: utf-8 -*-
"""反射注册表的无窗口单测（纯数据 / 无 GL）。

`python -m tests.test_reflect` 直接跑。聚合 runner 按 `  ok:` 行统计。

核心要钉的是"**一物多用**"：同一份属性元数据，同时驱动序列化 / 编辑器检视 /
脚本绑定 / 校验 —— 加一个字段只改一处，四边同时生效。

借鉴来源：Godot `ClassDB`（MIT）的运行时注册表、O3DE `ReflectContext` 的多
上下文思路；本实现自研，不搬源码。
"""

import sys

import numpy as np

from world3d.engine.reflect import (ClassDB, Property, ReflectError,
                                     default_db)


class _Light:
    """示例类（故意不带任何序列化代码 —— 全靠注册表驱动）。"""

    def __init__(self):
        self.intensity = 1.0
        self.color = np.array([1.0, 1.0, 1.0])
        self.mode = 'point'
        self.cast_shadow = True


class _SpotLight(_Light):
    def __init__(self):
        super().__init__()
        self.angle = 0.6


def _db():
    d = ClassDB()
    d.register('Light', props=[
        Property('intensity', 'float', 1.0, min=0.0, max=20.0, doc='灯强'),
        Property('color', 'color', (1, 1, 1)),
        Property('mode', 'enum', 'point', options=('point', 'spot', 'dir')),
        Property('cast_shadow', 'bool', True),
    ])
    return d


def test_register_and_lookup():
    d = _db()
    assert d.has('Light') and not d.has('Nope')
    info = d.get('Light')
    assert set(info.props) == {'intensity', 'color', 'mode', 'cast_shadow'}
    assert d.classes() == ['Light']


def test_duplicate_and_unknown_base_are_rejected():
    d = _db()
    for fn, needle in ((lambda: d.register('Light'), '已注册'),
                       (lambda: d.register('X', base='Ghost'), '尚未注册')):
        try:
            fn()
        except ReflectError as exc:
            assert needle in str(exc), str(exc)
        else:
            raise AssertionError(f'应拒绝：{needle}')


def test_serialization_roundtrip():
    """用途①：to_dict / update 按注册字段读写，numpy 也转成 JSON 友好。"""
    d = _db()
    light = _Light()
    light.intensity = 3.5
    data = d.to_dict(light, 'Light')
    assert data['intensity'] == 3.5
    assert isinstance(data['color'], list), type(data['color'])
    other = _Light()
    other.intensity = 0.1
    applied, unknown, errors = d.update(other, data, 'Light')
    assert not errors, errors
    assert not unknown, unknown
    assert set(applied) == {'intensity', 'color', 'mode', 'cast_shadow'}
    assert other.intensity == 3.5
    print('  ok: 序列化往返一致（numpy→list，逐字段回填）')


def test_unknown_keys_are_reported_not_swallowed():
    d = _db()
    light = _Light()
    applied, unknown, errors = d.update(light, {'bogus': 1, 'intensity': 2.0}, 'Light')
    assert 'bogus' in unknown, '未注册的键必须报出来，不能静默丢弃'
    assert 'intensity' in applied


def test_editor_schema_carries_enough_to_build_widgets():
    """用途②：schema 给出类型/默认值/范围/选项/说明。"""
    d = _db()
    schema = {s['name']: s for s in d.schema('Light')}
    assert schema['intensity']['type'] == 'float'
    assert schema['intensity']['min'] == 0.0 and schema['intensity']['max'] == 20.0
    assert schema['mode']['options'] == ['point', 'spot', 'dir']
    assert schema['cast_shadow']['type'] == 'bool'
    assert schema['intensity']['doc'] == '灯强'


def test_script_bindings_read_and_write():
    """用途③：脚本层只通过 (getter, setter) 访问，不碰类内部。"""
    d = _db()
    light = _Light()
    b = d.bindings('Light', light)
    assert b['intensity'][0]() == 1.0
    b['intensity'][1](7.0)
    assert light.intensity == 7.0


def test_validation_catches_range_and_enum():
    """用途④：范围与枚举越界一次查清。"""
    d = _db()
    light = _Light()
    assert d.validate(light, 'Light') == []
    light.intensity = 999.0
    errs = d.validate(light, 'Light')
    assert any('上限' in e for e in errs), errs
    light.intensity = 1.0
    light.mode = 'laser'
    errs = d.validate(light, 'Light')
    assert any('选项' in e for e in errs), errs


def test_inheritance_expands_base_properties():
    """子类看到基类属性，且子类同名属性覆盖基类。"""
    d = _db()
    d.register('SpotLight', base='Light', props=[
        Property('angle', 'float', 0.5, min=0.0, max=3.0),
    ])
    props = d.all_properties('SpotLight')
    assert 'intensity' in props and 'angle' in props
    assert d.ancestors('SpotLight') == ['Light', 'SpotLight']
    spot = _SpotLight()
    data = d.to_dict(spot, 'SpotLight')
    assert 'angle' in data and 'intensity' in data
    print('  ok: 继承链展开（基类在前，子类覆盖）')


def test_bad_values_are_rejected_on_update():
    d = _db()
    light = _Light()
    applied, _unknown, errors = d.update(light, {'mode': 'laser'}, 'Light')
    assert errors, '非法枚举值应报错'
    assert 'mode' not in applied


def test_one_metadata_serves_all_four_uses():
    """**一物多用**的总闸：加一个字段，四边同时生效（这是本模块存在的理由）。"""
    d = ClassDB()
    d.register('Widget', props=[Property('ratio', 'float', 0.5, min=0.0, max=1.0)])

    class _W:
        ratio = 0.5

    w = _W()
    # ① 序列化
    assert d.to_dict(w, 'Widget') == {'ratio': 0.5}
    # ② 编辑器
    assert d.schema('Widget')[0]['max'] == 1.0
    # ③ 脚本绑定
    d.bindings('Widget', w)['ratio'][1](0.25)
    assert w.ratio == 0.25
    # ④ 校验
    w.ratio = 5.0
    assert d.validate(w, 'Widget')
    print('  ok: 一份元数据驱动序列化/检视/绑定/校验四边')


def test_default_db_is_shared():
    db = default_db()
    assert db is default_db()
    assert isinstance(db, ClassDB)


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
    print(f'[test_reflect] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_REFLECT_TESTS_FAILED')
        return 1
    print('ALL_REFLECT_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
