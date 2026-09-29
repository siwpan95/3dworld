# -*- coding: utf-8 -*-
"""3dworld 入口（M1 分层重构后）。

用法：
  python main.py            正常运行
  python main.py --smoke    冒烟测试：2 秒后自动关闭
"""
import sys

import pyglet

from presentation.app import WorldApp


def run():
    app = WorldApp()
    if '--smoke' in sys.argv:
        pyglet.clock.schedule_once(lambda dt: app.close(), 2.0)
    pyglet.app.run()
    print('EXIT_OK')


if __name__ == '__main__':
    run()
