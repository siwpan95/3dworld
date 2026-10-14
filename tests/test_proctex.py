# -*- coding: utf-8 -*-
"""程序化纹理 × PBR 图集测试套件（纯字节生成，零 GL、零窗口）。

运行::

    python -m tests.test_proctex

**钉死的性质**（§19 Step 1）：

1. **height 暴露**——5 个图集生成器全部有 ``height`` 键（长度/值域正确）；
2. **量化边界**——``_quantize_height`` 对负值/超 1 值裁剪（rock 的裂缝下沉是负的）；
3. **通道打包逐像素**——三张图集与生成器输出逐像素一致，且
   ``normal.A == 量化高度``、``mr == 生成器 mr``（glTF ORM 位：R=255/G=粗糙/B=金属）；
4. **图集网格/错误**——行列与格址正确；未知名字报错。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.engine import proctex as P  # noqa: E402

_NAMES = ('sandstone', 'plaster', 'crate', 'metal', 'sand')
_CELL, _COLS, _SEED = 32, 3, 0


def test_height_exposed():
    for nm in _NAMES:
        gen = P._ATLAS_CELLS[nm]
        pat = gen(size=_CELL, seed=_SEED)
        assert 'height' in pat, f'{nm} 缺 height 键'
        h = pat['height']
        assert len(h) == _CELL * _CELL, f'{nm} height 长度 {len(h)} != {_CELL ** 2}'
        assert min(h) >= 0 and max(h) <= 255, f'{nm} height 值域越界'
    # marble 顺带暴露（同款机械一行）
    assert 'height' in P.make_marble(size=_CELL)
    print('  ok: 5 个图集生成器 + marble 的 height 键（长度/值域正确）')


def test_quantize_height_clamps():
    q = P._quantize_height([0.0, 1.0, 0.5, -0.4, 1.7])
    assert q[0] == 0 and q[1] == 255, (q[0], q[1])
    assert abs(q[2] - 128) <= 1, f'0.5 应量化到 128 附近，实际 {q[2]}'
    assert q[3] == 0, '负值必须裁剪到 0（rock 裂缝下沉）'
    assert q[4] == 255, '超 1 值必须裁剪到 255'
    print('  ok: _quantize_height 边界裁剪（负值→0 / 超 1→255 / 0.5→128）')


def test_atlas_channels_pixel_exact():
    atlas = P.make_atlas(_NAMES, cell=_CELL, cols=_COLS, seed=_SEED)
    w, h = atlas['width'], atlas['height']
    assert w == _COLS * _CELL and h == 2 * _CELL, (w, h)
    for ch in ('base', 'normal', 'mr'):
        assert len(atlas[ch]) == w * h * 4, f'{ch} 尺寸不对'
    checked = 0
    for i, nm in enumerate(_NAMES):
        pat = P._ATLAS_CELLS[nm](size=_CELL, seed=_SEED + i * 7)
        cx, cy = (i % _COLS) * _CELL, (i // _COLS) * _CELL
        for y in range(_CELL):
            si = y * _CELL * 4
            di = ((cy + y) * w + cx) * 4
            assert atlas['base'][di:di + _CELL * 4] == pat['base'][si:si + _CELL * 4], \
                f'{nm} base 行 {y} 不一致'
            assert atlas['mr'][di:di + _CELL * 4] == pat['mr'][si:si + _CELL * 4], \
                f'{nm} mr 行 {y} 不一致'
            for x in range(_CELL):
                o = di + x * 4
                so = si + x * 4
                assert atlas['normal'][o:o + 3] == pat['normal'][so:so + 3], \
                    f'{nm} normal RGB ({x},{y}) 不一致'
                assert atlas['normal'][o + 3] == pat['height'][y * _CELL + x], \
                    f'{nm} normal.A != height ({x},{y})'
                checked += 1
    # ORM 惯例自检：R 通道恒 255（AO 保留位）
    for nm in _NAMES:
        pat = P._ATLAS_CELLS[nm](size=_CELL, seed=_SEED)
        for k in range(0, len(pat['mr']), 4):
            assert pat['mr'][k] == 255, f'{nm} mr.R 应为 255（AO 保留位）'
    print(f'  ok: 三通道图集逐像素一致（{checked} 像素 × 3 通道校验，'
          f'normal.A==height / mr 逐字节）')


def test_atlas_layout_and_errors():
    atlas = P.make_atlas(('sandstone', 'plaster'), cell=16, cols=2, seed=3)
    assert atlas['cols'] == 2 and atlas['rows'] == 1
    assert atlas['width'] == 32 and atlas['height'] == 16
    assert atlas['names'] == ['sandstone', 'plaster']
    try:
        P.make_atlas(('nope',), cell=8)
        raise AssertionError('未知格名应报错')
    except ValueError as e:
        assert 'nope' in str(e)
    # 确定性：同 seed 两次生成逐字节一致
    a1 = P.make_atlas(_NAMES, cell=16, cols=3, seed=7)
    a2 = P.make_atlas(_NAMES, cell=16, cols=3, seed=7)
    assert a1['base'] == a2['base'] and a1['normal'] == a2['normal'] \
        and a1['mr'] == a2['mr'], '同 seed 必须确定性一致'
    print('  ok: 图集网格尺寸/格址 + 未知格名报错 + 同 seed 确定性')


def main():
    print('test_height_exposed');            test_height_exposed()
    print('test_quantize_height_clamps');    test_quantize_height_clamps()
    print('test_atlas_channels_pixel_exact'); test_atlas_channels_pixel_exact()
    print('test_atlas_layout_and_errors');   test_atlas_layout_and_errors()
    print('ALL_PROCTEX_TESTS_PASSED')


if __name__ == '__main__':
    main()
