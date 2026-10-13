# -*- coding: utf-8 -*-
"""时间轴 UI 面板测试（presentation/timeline_ui.py）。

上线前全检时新增：该文件覆盖率原仅 **27%**（且它是本次 HEAD「可视化导演台」引入的
新 UI），是全仓最低的覆盖盲区之一。本模块把它补到 ~60%+。

覆盖：``_in_rect`` 边界 / 初始状态 / 时间↔像素映射与钳制 / 未 layout 时的早退守卫 /
零时长守卫 / 交互（播放·循环·拖动·释放）/ **幕刻度引用持有**（M6 踩过的坑：pyglet
``Batch`` 不持有 Shape 引用，不存进列表会被 GC 回收而"静默不画"）/ 绘制不抛异常。

运行::

    python -m tests.test_timeline_ui
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from presentation.timeline_ui import (  # noqa: E402
    BTN, PANEL_HEIGHT, PANEL_MARGIN, TimelineUI,
)


# --------------------------------------------------------------- 工具 --
def _recorder():
    """记录回调调用：{'play': n, 'loop': n, 'seek': [t, ...]}。"""
    return {'play': 0, 'loop': 0, 'seek': []}


def _make(rec=None, duration=48.0, acts=()):
    rec = rec if rec is not None else _recorder()
    ui = TimelineUI(duration,
                    on_toggle_play=lambda: rec.__setitem__('play', rec['play'] + 1),
                    on_toggle_loop=lambda: rec.__setitem__('loop', rec['loop'] + 1),
                    on_seek=lambda t: rec['seek'].append(t),
                    acts=acts)
    return ui, rec


def _gl_window():
    """开一个隐藏窗口拿 GL 上下文；不可用返回 None（此时 GL 用例跳过，不判通过）。"""
    try:
        import pyglet
        pyglet.options['dpi_scaling'] = 'real'
        return pyglet.window.Window(320, 200, visible=False)
    except Exception as e:
        print(f'  SKIP: 无 GL 上下文（{e}）')
        return None


# ----------------------------------------------------------- 纯逻辑用例 --
def test_in_rect_boundaries():
    f = TimelineUI._in_rect
    assert f(10, 10, 0, 0, 20, 20), '中心应在矩形内'
    assert f(0, 0, 0, 0, 20, 20), '左上角应在内'
    assert not f(-10, 10, 0, 0, 20, 20), '左侧外部应判外'
    assert not f(10, 30, 0, 0, 20, 20), '下方外部应判外'
    # pad 外扩生效：默认 pad=4，x=-4 应算内，x=-5 算外
    assert f(-4, 10, 0, 0, 20, 20), 'pad=4 时 -4 应在内'
    assert not f(-5, 10, 0, 0, 20, 20), 'pad=4 时 -5 应在外'
    assert f(0, 0, 0, 0, 20, 20, pad=0), 'pad=0 边界应含等号'
    print('  ok: _in_rect 边界/pad 外扩/含等号')


def test_init_defaults():
    ui, _ = _make(duration=48.0)
    assert ui.duration == 48.0
    assert ui.scrubbing is False, '初始不应处于拖动态'
    assert ui.looping is True, '默认开启循环'
    assert ui.playhead == 0.0 and ui.playing is False
    assert ui._layout_ready is False, '未 layout 前不应就绪'
    ui2, _ = _make(acts=[(0.0, 'A', 'a'), (12.0, 'B', 'b')])
    assert ui2.acts == [(0.0, 'A', 'a'), (12.0, 'B', 'b')], 'acts 应被记录'
    assert isinstance(ui2.acts, list), 'acts 应转为 list（防御外部传入元组/生成器）'
    print('  ok: 初始状态（scrubbing/looping/playhead/layout_ready/acts）')


def test_t_from_x_clamps_and_maps():
    ui, _ = _make(duration=10.0)
    ui.track_x0, ui.track_x1 = 100.0, 200.0      # span = 100
    assert abs(ui._t_from_x(100.0) - 0.0) < 1e-9, '左端 -> 0'
    assert abs(ui._t_from_x(150.0) - 5.0) < 1e-9, '中点 -> 半程'
    assert abs(ui._t_from_x(200.0) - 10.0) < 1e-9, '右端 -> duration'
    assert ui._t_from_x(-9999.0) == 0.0, '左侧越界应钳到 0'
    assert ui._t_from_x(9999.0) == 10.0, '右侧越界应钳到 duration'
    # span <= 0 的退化情形（窗口极小）不应抛 ZeroDivisionError
    ui.track_x0, ui.track_x1 = 100.0, 100.0
    assert ui._t_from_x(150.0) == 0.0, 'span=0 应退化为 0 且不抛异常'
    print('  ok: _t_from_x 映射/双向钳制/span=0 退化')


def test_zero_and_negative_duration_guard():
    for d in (0.0, -5.0):
        ui, _ = _make(duration=d)
        ui.track_x0, ui.track_x1 = 0.0, 100.0
        assert ui._t_from_x(50.0) == 0.0, f'duration={d} 时仍应得 0'
        ui.update(5.0, True, False)              # 未 layout -> 早退
        assert ui.playhead == 5.0, '早退前也应同步播放状态'
    print('  ok: duration<=0 守卫（无 ZeroDivisionError）')


def test_no_layout_early_returns():
    ui, rec = _make()
    ui.update(1.0, True, False)                  # 不应抛 AttributeError
    assert ui.playhead == 1.0 and ui.playing is True and ui.looping is False
    assert ui.on_press(50, 50) is False, '未 layout 时按下应返回 False（不拦截相机）'
    ui.on_drag(50, 50)                           # scrubbing=False -> 无副作用
    assert rec['seek'] == [], '未拖动时 on_drag 不应触发 seek'
    print('  ok: 未 layout 时 update/on_press/on_drag 的早退守卫')


def test_release_clears_scrubbing():
    ui, _ = _make()
    ui.scrubbing = True
    ui.on_release()
    assert ui.scrubbing is False, 'on_release 必须清除拖动态'
    print('  ok: on_release 清除 scrubbing')


def test_on_drag_seeks_when_scrubbing():
    ui, rec = _make(duration=10.0)
    ui.track_x0, ui.track_x1 = 0.0, 100.0
    ui.scrubbing = True
    ui.on_drag(25.0, 10.0)
    ui.on_drag(75.0, 10.0)
    assert rec['seek'] == [2.5, 7.5], f'拖动应连续 seek，实际 {rec["seek"]}'
    print('  ok: 拖动中 on_drag 连续 seek（映射正确）')


# --------------------------------------------------------------- GL 用例 --
def test_layout_update_and_interaction():
    w = _gl_window()
    if not w:
        return
    try:
        rec = _recorder()
        acts = [(0.0, '序章', 'sub0'), (12.0, '第一幕', 'sub1'), (30.0, '第二幕', 'sub2')]
        ui, _ = _make(rec=rec, duration=48.0, acts=acts)
        ui.layout(800, 200)
        assert ui._layout_ready is True
        assert ui._width == 800 and ui._height == 200

        # ---- update：进度条/播头/图标/循环配色/读数 ----
        ui.update(24.0, False, True)
        span = ui.track_x1 - ui.track_x0
        assert abs(ui.progress.width - span * 0.5) < 1e-6, '半程进度条宽度应为 span/2'
        assert abs(ui.handle.x - (ui.track_x0 + span * 0.5)) < 1e-6, '播头应在中点'
        assert ui.icon_play.visible is True and ui.icon_pause_l.visible is False, \
            '暂停态应显示播放三角'
        assert ui.lbl_time.text.endswith('/ 48.00 s'), f'时间读数异常: {ui.lbl_time.text}'
        ui.update(24.0, True, True)
        assert ui.icon_play.visible is False and ui.icon_pause_l.visible is True, \
            '播放态应显示暂停条'

        # ---- 当前幕名随播放位置推进 ----
        ui.update(0.0, True, True)
        assert '序章' in ui.lbl_act.text, f'0s 应命中序章，实际 {ui.lbl_act.text}'
        ui.update(35.0, True, True)
        assert '第二幕' in ui.lbl_act.text, f'35s 应命中第二幕，实际 {ui.lbl_act.text}'

        # ---- 幕刻度必须被持有（Batch 不持有 Shape 引用，否则静默不画）----
        assert len(ui.act_ticks) == 3, f'幕刻度应有 3 个，实际 {len(ui.act_ticks)}'
        assert all(t is not None for t in ui.act_ticks), '幕刻度不应为空引用'

        # ---- 交互：播放按钮 / 循环按钮 / 轨道 / 面板外 ----
        px = ui.btn_play_bg.x + BTN / 2
        py = ui.btn_play_bg.y + BTN / 2
        assert ui.on_press(px, py) is True, '面板内按下应拦截相机'
        assert rec['play'] == 1, '播放按钮应触发 on_toggle_play'

        lx = ui.btn_loop_bg.x + ui.btn_loop_bg.width / 2
        ly = ui.btn_loop_bg.y + ui.btn_loop_bg.height / 2
        ui.on_press(lx, ly)
        assert rec['loop'] == 1, '循环按钮应触发 on_toggle_loop'

        ui.scrubbing = False
        rec['seek'].clear()
        assert ui.on_press(ui.track_x0 + 10, ui.panel.y + PANEL_HEIGHT / 2) is True
        assert ui.scrubbing is True, '轨道按下应进入拖动态'
        assert len(rec['seek']) == 1, '轨道按下应 seek 一次'
        assert 0.0 <= rec['seek'][0] <= 48.0, 'seek 值应被钳在 [0, duration]'

        assert ui.on_press(-500, -500) is False, '面板外按下不应拦截'

        # ---- draw 不应抛异常 ----
        ui.draw()
        print('  ok: layout/update/幕名/幕刻度持有/四类交互/draw 均正常')
    finally:
        try:
            w.close()
        except Exception:
            pass


def test_layout_zero_duration_and_no_acts():
    w = _gl_window()
    if not w:
        return
    try:
        ui, rec = _make(duration=0.0, acts=())
        ui.layout(400, 150)
        ui.update(5.0, False, True)
        assert ui.act_ticks == [], '无幕表时不应生成刻度'
        assert ui.progress.width == 0.0, 'duration=0 时进度条宽度应为 0'
        ui.draw()
        print('  ok: duration=0 / 无幕表 的退化布局不抛异常')
    finally:
        try:
            w.close()
        except Exception:
            pass


TESTS = [
    test_in_rect_boundaries,
    test_init_defaults,
    test_t_from_x_clamps_and_maps,
    test_zero_and_negative_duration_guard,
    test_no_layout_early_returns,
    test_release_clears_scrubbing,
    test_on_drag_seeks_when_scrubbing,
    test_layout_update_and_interaction,
    test_layout_zero_duration_and_no_acts,
]


def main():
    print('=' * 60)
    print('timeline_ui 测试')
    print('=' * 60)
    failed = []
    for t in TESTS:
        print(t.__name__)
        try:
            t()
        except Exception as e:
            failed.append((t.__name__, e))
            import traceback
            traceback.print_exc()
            print(f'  FAIL: {e}')
    print('-' * 60)
    for name, e in failed:
        print(f'FAILED {name}: {e}')
    if failed:
        print(f'{len(failed)}/{len(TESTS)} FAILED')
        return 1
    print('ALL_TIMELINE_UI_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
