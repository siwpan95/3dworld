# -*- coding: utf-8 -*-
"""固定步长 vs 变步长：回归基准（原 `experiments/fixed_timestep/compare.py`，P0-5 转正）。

A. 帧率无关性：同一段物理，30fps 与 144fps 下的「落地时刻与落地位置」差异——
   测试指标选**对步长敏感**的事件（落地判定时刻受 dt 量化影响；若只比
   稳定滑行的终点 x，两种模式都会收敛到同一值，测不出差异）。
B. 卡帧稳健性：0.5s 卡帧**发生在撞墙之前** + 薄墙 → 检验高速穿透。

运行：
    python -m tests.bench_fixedstep        # 手工跑的基准，不在 138 项常规套件内
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from world3d.engine.fixedstep import FixedStepper              # noqa: E402
from world3d.engine.frustum import AABB                        # noqa: E402
from world3d.engine.physics import Body, PhysicsWorld          # noqa: E402

FIXED = 1.0 / 60.0
MAX_FRAME = 0.25


# ---------------------------------------------------------------- 场景 --
def make_fall():
    """自由落体 + 水平漂移（无墙）：测落地事件。"""
    w = PhysicsWorld(gravity=-22.0, cell=4.0)
    w.add_static(AABB((-20.0, -1.0, -20.0), (20.0, 0.0, 20.0)), 'ground')
    b = Body(position=(0.0, 6.0, 0.0), half=(0.25, 0.25, 0.25))
    b.velocity = (3.0, 0.0, 0.0)
    w.add_body(b)
    return w, b


def make_wall():
    """薄墙（0.1m）+ 高速球：检验卡帧下的高速穿透。"""
    w = PhysicsWorld(gravity=-22.0, cell=4.0)
    w.add_static(AABB((-10.0, -1.0, -10.0), (10.0, 0.0, 10.0)), 'ground')
    w.add_static(AABB((3.0, 0.0, -1.0), (3.1, 2.0, 1.0)), 'thin_wall')
    b = Body(position=(-3.0, 0.6, 0.0), half=(0.25, 0.25, 0.25))
    b.velocity = (12.0, 0.0, 0.0)
    w.add_body(b)
    return w, b


# ---------------------------------------------------------------- 驱动 --
def frames(fps, sec):
    return [1.0 / fps] * int(round(fps * sec))


def run_var_land(dt_list):
    """变步长（现状 substeps=2）：返回 (落地时刻, 落地时 x)。"""
    w, b = make_fall()
    t = 0.0
    for dt in dt_list:
        w.step(dt, substeps=2)
        t += dt
        if b.on_ground:
            return t, b.position[0]
    return t, b.position[0]


def run_fixed_land(dt_list):
    """固定步长：返回 (落地时刻, 落地时 x)。"""
    w, b = make_fall()
    box = {}
    t = 0.0

    def upd(fixed):
        nonlocal t
        w.step(fixed)
        t += fixed
        if b.on_ground and 't' not in box:
            box['t'], box['x'] = t, b.position[0]

    st = FixedStepper(upd, fixed_dt=FIXED, max_frame_time=MAX_FRAME)
    for dt in dt_list:
        st.advance(dt)
    return box.get('t', t), box.get('x', b.position[0])


def run_wall(dt_list, fixed_mode):
    w, b = make_wall()
    if fixed_mode:
        st = FixedStepper(lambda dt: w.step(dt), fixed_dt=FIXED,
                          max_frame_time=MAX_FRAME)
        for dt in dt_list:
            st.advance(dt)
        return b.position, st.stats
    for dt in dt_list:
        w.step(dt, substeps=2)
    return b.position, None


def wall_result(pos):
    return '穿墙!' if pos[0] > 3.35 else '拦住'


# ------------------------------------------------------------------ 主 --
def main():
    print('=' * 66)
    print('A. 帧率无关性（自由落体+漂移：对比「落地事件」）')
    print('=' * 66)
    vt30, vx30 = run_var_land(frames(30, 1.2))
    vt144, vx144 = run_var_land(frames(144, 1.2))
    ft30, fx30 = run_fixed_land(frames(30, 1.2))
    ft144, fx144 = run_fixed_land(frames(144, 1.2))
    print('  变步长  : 30fps 落地 t={:.4f}s x={:.4f} | 144fps t={:.4f}s x={:.4f}'
          ' | Δt={:.4f}s Δx={:.4f} m'.format(vt30, vx30, vt144, vx144,
                                             abs(vt30 - vt144), abs(vx30 - vx144)))
    print('  固定步长: 30fps 落地 t={:.4f}s x={:.4f} | 144fps t={:.4f}s x={:.4f}'
          ' | Δt={:.4f}s Δx={:.4f} m'.format(ft30, fx30, ft144, fx144,
                                             abs(ft30 - ft144), abs(fx30 - fx144)))
    print()

    print('=' * 66)
    print('B. 卡帧稳健性（卡帧发生在撞墙前：10 帧后 0.5s 卡顿 + 薄墙）')
    print('=' * 66)
    dt_seq = [1 / 60] * 10 + [0.5] + [1 / 60] * 30
    pv, _ = run_wall(dt_seq, fixed_mode=False)
    pf, sf = run_wall(dt_seq, fixed_mode=True)
    print(f'  变步长  : 终点 x={pv[0]:8.3f}  → {wall_result(pv)}')
    print(f'  固定步长: 终点 x={pf[0]:8.3f}  → {wall_result(pf)}'
          f'  （钳制 {sf.clamps} 次，固定步数 {sf.fixed_steps}）')
    print()

    # ---- 断言（P0-5 转正：从"打印对照"变成**回归基准**）------------------
    # A：固定步长必须**帧率无关**（逐位相等）；变步长必须可测地不同
    #    —— 后者是"这个实验有意义"的前提：若两者都相等，说明指标没选对。
    assert ft30 == ft144 and fx30 == fx144, (
        f'固定步长应帧率无关，实测 Δt={abs(ft30 - ft144)} Δx={abs(fx30 - fx144)}')
    dv = (abs(vt30 - vt144), abs(vx30 - vx144))
    assert dv[0] > 1e-3 or dv[1] > 1e-3, (
        f'变步长本应帧率相关（否则测的不是步长敏感事件）：Δ={dv}')
    # B：同样的一次 0.5s 卡帧，变步长穿墙、固定步长拦住
    assert '穿墙' in wall_result(pv), f'变步长预期穿墙，实测 {wall_result(pv)}'
    assert '拦住' in wall_result(pf), f'固定步长预期拦住，实测 {wall_result(pf)}'
    assert sf.clamps >= 1 and sf.fixed_steps > 0
    print(f'BENCH_OK  A: 固定步长帧率无关(Δx=0) 而变步长 Δ={dv[1]:.4f}m；'
          f'B: 0.5s 卡帧下 变步长穿墙 / 固定步长拦住({sf.clamps} 次钳制)')


if __name__ == '__main__':
    main()
