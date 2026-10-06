# -*- coding: utf-8 -*-
""".kkrieger 致敬 Demo 入口：程序化科幻走廊漫游（96s 四幕）。

用法：
  python kkrieger_demo.py             正常运行（SPACE 暂停 / R 重播 / N,P 切幕）
  python kkrieger_demo.py --smoke     冒烟测试：2.5 秒后自动关闭
  python kkrieger_demo.py --shot      四幕各抓一张验证截图到 docs/ 后自动关闭

截图时刻对应四幕各自的"信息量峰值"：
  8.8s  走廊：第一道舱门已开，走廊纵深/管道/货箱/频闪都入画（第一幕）
  26.5s 大厅：纪念碑唤醒 + 全息扫描（第二幕，幕字幕正在淡出）
  62.0s 核心：过载峰值（第三幕，环加速 + 能量束增亮）
  86.0s 撤离：走廊灯带逐段熄灭（第四幕）
"""
import os
import sys

import pyglet

from presentation.kkrieger_app import KKApp

SHOTS = (
    (8.8,  os.path.join('docs', 'kkrieger_corridor.png')),
    (26.5, os.path.join('docs', 'kkrieger_atrium.png')),
    (62.0, os.path.join('docs', 'kkrieger_core.png')),
    (86.0, os.path.join('docs', 'kkrieger_blackout.png')),
)


def run():
    shots = SHOTS if '--shot' in sys.argv else []
    app = KKApp(shots=shots)
    if '--smoke' in sys.argv:
        pyglet.clock.schedule_once(lambda dt: app.close(), 2.5)
    elif shots:
        # 抓帧是"跳帧采样"，不需要实时播放 96 秒
        pyglet.clock.schedule_once(lambda dt: (app.capture_shots(), app.close()), 0.4)
    pyglet.app.run()

    if shots:
        missing = [p for _, p in shots if not os.path.exists(p)]
        if missing:
            raise SystemExit(f'[shot] missing: {missing}')
        print(f'[shot] OK {len(shots)} files')
    print('EXIT_OK')


if __name__ == '__main__':
    run()
