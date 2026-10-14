# -*- coding: utf-8 -*-
"""3dworld 入口（M1 分层重构 / M6 五幕编排）。

用法：
  python main.py                正常运行（48s 五幕循环，SPACE 暂停 / C 切相机）
  python main.py --smoke        冒烟测试：2 秒后自动关闭
  python main.py --shot         渲染四张验证截图到 docs/ 后自动关闭
  python main.py --shot --only 2  只截第 3 张（调试单帧用）

截图时刻刻意选在四幕的"信息量最大"处：升起中段 / 环形波峰值 /
机械臂夹持瞬间（t=22.0 正是逆运动学取件时刻）/ 螺旋柱成形。
"""
import os
import sys

import pyglet

from world3d.presentation.app import WorldApp

# (播头时刻, 输出路径, 该帧应该看到什么)
SHOTS = (
    (5.6,  os.path.join('docs', 'world_act1_rise.png'),
     '第一幕中段：环形立方体错峰升起'),
    (14.0, os.path.join('docs', 'world_act2_wave.png'),
     '第二幕峰值：行波扫过环带'),
    (22.0, os.path.join('docs', 'world_act3_arm.png'),
     '第三幕取件瞬间：夹爪与宝石重合'),
    (36.0, os.path.join('docs', 'world_act4_helix.png'),
     '第四幕：螺旋柱成形 + 核心脉冲'),
)


def run():
    shots = SHOTS if '--shot' in sys.argv else ()
    only = None
    if '--only' in sys.argv:
        only = int(sys.argv[sys.argv.index('--only') + 1])
        shots = (SHOTS[only],)

    app = WorldApp()
    if '--smoke' in sys.argv:
        pyglet.clock.schedule_once(lambda dt: app.close(), 2.0)
    elif shots:
        pyglet.clock.schedule_once(
            lambda dt: [_shoot(app, t, path) for t, path, _ in shots] + [app.close()],
            0.4)
    pyglet.app.run()

    if shots:
        missing = [p for _, p, _ in shots if not os.path.exists(p)]
        if missing:
            raise SystemExit(f'[shot] missing: {missing}')
        print(f'[shot] OK {len(shots)} files')
    print('EXIT_OK')


def _shoot(app, t, path):
    print(f'[shot] t={t:.1f}s -> {path}')
    app.capture_at(t, path)


if __name__ == '__main__':
    run()
