# -*- coding: utf-8 -*-
""".kkrieger 致敬 Demo 入口：程序化科幻走廊漫游（120s 五幕，含程序化配乐）。

用法：
  python kkrieger_demo.py             正常运行（SPACE 暂停 / R 重播 / N,P 切幕 / M 静音）
  python kkrieger_demo.py --smoke     冒烟测试：2.5 秒后自动关闭
  python kkrieger_demo.py --shot      各幕抓一张验证截图到 docs/ 后自动关闭

截图时刻对应各幕"信息量峰值"：
  8.8s  走廊：第一道舱门已开，走廊纵深/管道/货箱/频闪都入画（第一幕）
  26.5s 大厅：纪念碑唤醒 + 全息扫描（第二幕，幕字幕正在淡出）
  62.0s 核心：过载峰值（第三幕，环加速 + 能量束增亮）
  86.0s 撤离：走廊灯带逐段熄灭（第四幕）
  101.8s 突入：第五幕高速穿越（数据流光带跑马、舱门速开）
  113.0s 收拢：无人机群收拢环绕纪念碑（爆发前兆）
  117.7s 爆发：核心白闪 + 能量束爆亮 + 碎片飞散
"""
import os
import sys

import pyglet

from presentation.kkrieger_app import KKApp

SHOTS = (
    (8.8,   os.path.join('docs', 'kkrieger_corridor.png')),
    (26.5,  os.path.join('docs', 'kkrieger_atrium.png')),
    (62.0,  os.path.join('docs', 'kkrieger_core.png')),
    (86.0,  os.path.join('docs', 'kkrieger_blackout.png')),
    (101.8, os.path.join('docs', 'kkrieger_sprint.png')),
    (113.0, os.path.join('docs', 'kkrieger_converge.png')),
    (117.7, os.path.join('docs', 'kkrieger_flash.png')),
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
