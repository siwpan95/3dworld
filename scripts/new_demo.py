# -*- coding: utf-8 -*-
"""脚手架（P4）：生成新 demo / 新测试模块的**能跑**骨架。

## 为什么值得做

本会话里我手写了 **2 个 demo + 7 个测试模块**，每个都要抄一遍同样的样板：
`sys.path` 显式引导块、`from world3d...` 导入、`DemoApp` 子类的 `TITLE/HELP/SHOTS`、
测试模块的 runner + `ALL_*_TESTS_PASSED` 标记。**抄错一处就是半小时的排查**
（本会话就踩过：漏了引导块 → 30 个 demo 一起 `ModuleNotFoundError`）。

## 生成的骨架必须"开箱能跑"

模板不是伪代码：`--smoke` 与 `python -m tests.<名>` 必须立刻通过。
门禁 `scaffold-templates-valid`（`scripts/verify.py`）会**渲染模板并 `ast.parse`**，
所以模板不会随着项目演进悄悄腐掉（例如导入路径改了、`DemoApp` 的钩子改名了）。

## 用法

    python scripts/new_demo.py starfield                 # 生成 examples/starfield.py
    python scripts/new_demo.py --test mathx              # 生成 tests/test_mathx.py
    python scripts/new_demo.py --test mathx --register   # 顺便登记进 run_all_tests.py
    python scripts/new_demo.py starfield --force         # 覆盖已存在的文件

生成之后**紧接着跑一次门禁**：它会告诉你还差哪几处（示例索引行、README 计数…）——
这正是"脚手架 + 门禁"配合的意义：**新增东西要改的地方由机器列出来，不靠记性。**
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEMO_TEMPLATE = '''# -*- coding: utf-8 -*-
"""${title} —— 一句话说清它演示什么。

（**为什么值得单独一个 demo**：它证明了什么、与既有 demo 的区别在哪。
  这一段是给人看的，请替换掉。）

用法::

    python examples/${name}.py
    python examples/${name}.py --smoke 3     # 冒烟（CI / verify_demos 会跑这个）
    python examples/${name}.py --shot        # 抓剧照到 docs/${name}_01.png
    python examples/${name}.py --flag auto   # 自演示（若实现了 auto）

按键：SPACE 暂停   鼠标拖动/滚轮 看视角   ESC 退出
"""
import math
import os
import sys

import pyglet
from pyglet.gl import GL_DEPTH_TEST, GL_TRIANGLES, glClearColor, glEnable
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4, Vec3
from pyglet.window import key

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ **必须显式写**：`from world3d.platform.app import ...` 不再隐式插项目根，
# 少了这行就会 `ModuleNotFoundError: No module named 'world3d'`
# （P2-1 实测：当时 30 个 demo 一起炸）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.platform.app import DemoApp, orbit_camera_input, run  # noqa: E402
from world3d.engine.baked_mesh import unit_cube  # noqa: E402
from world3d.logic.camera import OrbitCamera  # noqa: E402

VERTEX_SRC = \'\'\'#version 330 core
in vec3 position;
in vec3 shade;
uniform WindowBlock {
    mat4 projection;
    mat4 view;
} window;
uniform mat4 u_model;
uniform vec3 u_tint;
out vec3 v_col;
void main() {
    vec4 world = u_model * vec4(position, 1.0);
    v_col = shade * u_tint;
    gl_Position = window.projection * window.view * world;
}\'\'\'

FRAGMENT_SRC = \'\'\'#version 330 core
in vec3 v_col;
out vec4 final_colors;
void main() { final_colors = vec4(v_col, 1.0); }\'\'\'


class ${cls}(DemoApp):
    """${title}。

    `DemoApp` 提供的钩子（按需覆盖）：
      setup()   创建 GL 资源（窗口上下文已就绪）
      update(dt) 推进动画（`self.time` 已累加）
      render()  绘制 3D 场景（清屏由基类做）
      on_key()  额外按键
      status_text()  HUD 右上状态行（**要短**：HUD 标题在左、状态在右，太长会压上去）
    """

    TITLE = '${title}'
    HELP = 'SPACE 暂停   拖动/滚轮 看视角   ESC 退出'
    DURATION = 20.0          # `self.time` 的取模周期（秒）；None = 持续累加
    HUD = True
    #: 剧照：(时间秒, 输出路径)。**留空就没有 --shot 阶段**（verify_demos 会跳过）。
    SHOTS = ((2.0, 'docs/${name}_01.png'),)

    def setup(self):
        glEnable(GL_DEPTH_TEST)
        glClearColor(0.05, 0.06, 0.08, 1.0)
        self.program = ShaderProgram(Shader(VERTEX_SRC, 'vertex'),
                                     Shader(FRAGMENT_SRC, 'fragment'))
        pos, shade = unit_cube()          # (position, shade) 三角汤，直接喂 vertex_list
        self.cube = self.program.vertex_list(
            len(pos) // 3, GL_TRIANGLES,
            position=('f', pos), shade=('f', shade))
        self.cam = OrbitCamera(target=Vec3(0, 0, 0), yaw=35.0, pitch=25.0, dist=6.0)
        orbit_camera_input(self, self.cam)

    def update(self, dt):
        if self.paused:
            return

    def render(self):
        self.window.view = self.cam.view_matrix()
        w, h = self.window.width, self.window.height
        self.window.projection = self.cam.projection(w / max(1, h))
        prog = self.program
        prog.use()
        spin = math.radians(40.0 * self.time)
        prog['u_model'] = (Mat4.from_rotation(spin, Vec3(0, 1, 0))
                           @ Mat4.from_translation(Vec3(0, 0, 0)))
        prog['u_tint'] = (0.75, 0.78, 0.85)
        self.cube.draw(GL_TRIANGLES)

    def status_text(self):
        return f'{self.TITLE}   t={self.time:4.1f}s   {self.fps:4.1f}fps'


if __name__ == '__main__':
    run(${cls})
'''

TEST_TEMPLATE = '''# -*- coding: utf-8 -*-
"""${title} 测试套件。

运行::

    python -m tests.test_${name}

约定（与其它测试模块一致，`repo-consistency-gates` 的门禁会核对）：

* 模块自带 runner，通过时打印 `ALL_${upper}_TESTS_PASSED`；
* **每条断言都要能被反例打穿** —— 写完自问"我把要防的那件事做出来，它会不会红"；
* 通过时也要**给数字**（"13 项边界一致"比"通过"有用得多），不要只说 ok；
* 需要 GL 的用例单独 try/except 记 SKIP，**跳过 ≠ 通过**；
* 注册进 `scripts/run_all_tests.py` 的 `MODULES`（`--register` 可自动做），
  并在 README 的测试清单里加一行（门禁 `readme-test-modules` 会核对双向一致）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.core.contracts import CORES  # noqa: E402  ← 换成被测模块


def test_smoke():
    """最少一条：被测模块能 import、关键符号存在。"""
    assert CORES, '契约注册表不该为空'
    print(f'  ok: 契约注册表 {len(CORES)} 条（把这里换成你真正要断言的东西）')


def test_boundary_that_can_be_broken():
    """**模板示范**：写一条"能被反例打穿"的判据。

    反例思路（挑一个真的会犯的错）：
      · 边界值两处不一致（一处改了另一处忘了）；
      · 单调性/守恒（拆成 N 段之和 == 整段）；
      · 确定性（同输入两次调用逐字相同）。
    """
    from world3d.core.contracts import status_table
    first = status_table()
    second = status_table()
    assert first == second, '同输入两次调用应当逐字相同（确定性）'
    assert first, '状态表不该为空'
    print(f'  ok: status_table 确定性（{len(first)} 行逐字一致）')


def main() -> int:
    tests = [
        ('test_smoke', test_smoke),
        ('test_boundary_that_can_be_broken', test_boundary_that_can_be_broken),
    ]
    failed = []
    for name, fn in tests:
        try:
            print(name)
            fn()
        except AssertionError as exc:
            failed.append((name, exc))
            print(f'  FAIL: {exc}')
        except Exception as exc:                            # noqa: BLE001
            failed.append((name, exc))
            print(f'  ERROR: {type(exc).__name__}: {exc}')
    print('=' * 60)
    print(f'[test_${name}] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, exc in failed:
            print(f'  - {n}: {exc}')
        print('ALL_${upper}_TESTS_FAILED')
        return 1
    print('ALL_${upper}_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
'''


def _cls_name(name):
    return ''.join(part.capitalize() for part in re.split(r'[_-]', name)) + 'App'


def render_demo(name):
    """渲染 demo 模板。

    ⚠ 用 `string.Template`（`$name`）而不是 `str.format`：模板体里有 **GLSL 的花括号**
    （`uniform WindowBlock { … }`），`format` 会把它们当占位符（`KeyError: '\n    mat4
    projection;…'`）。`Template` 只认 `$`，花括号原样保留 —— 模板里有大括号就该用它。
    """
    return string.Template(DEMO_TEMPLATE).substitute(
        name=name, cls=_cls_name(name), title=name)


def render_test(name):
    return string.Template(TEST_TEMPLATE).substitute(
        name=name, upper=name.upper(), title=name.replace('_', ' '))


def _register_test(name, quiet=False):
    """把新测试模块登记进 `scripts/run_all_tests.py` 的 MODULES（幂等）。"""
    path = os.path.join(ROOT, 'scripts', 'run_all_tests.py')
    text = open(path, encoding='utf-8').read()
    mod = f'tests.test_{name}'
    if f"'{mod}'" in text:
        if not quiet:
            print(f'[new] {mod} 已在 MODULES 里，跳过登记')
        return False
    m = re.search(r'^MODULES = \[\n(.*?)^\]$', text, re.S | re.M)
    if not m:
        print('[new] 找不到 MODULES 列表 —— 请手工加一行：'
              f"\n      ('{mod}', 'ALL_{name.upper()}_TESTS_PASSED', '{name}'),")
        return False
    entry = (f"    ('{mod}', 'ALL_{name.upper()}_TESTS_PASSED', '{name}'),\n")
    new = text[:m.end(1)] + entry + text[m.end(1):]
    ast.parse(new)                      # 改坏了立刻抛，不写盘
    open(path, 'w', encoding='utf-8', newline='\n').write(new)
    if not quiet:
        print(f'[new] 已登记进 run_all_tests.py：{mod}')
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='脚手架：新 demo / 新测试模块')
    ap.add_argument('name', help='名字（小写字母/下划线；demo 用文件名，测试会自动加 test_ 前缀）')
    ap.add_argument('--test', action='store_true', help='生成测试模块而不是 demo')
    ap.add_argument('--register', action='store_true',
                    help='（仅测试）顺便登记进 run_all_tests.py 的 MODULES')
    ap.add_argument('--force', action='store_true', help='覆盖已存在的文件')
    args = ap.parse_args(argv)

    name = args.name.strip()
    if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
        print(f'[new] 名字要小写字母开头、只含小写与下划线：{name!r}')
        return 2
    if args.test:
        rel = f'tests/test_{name}.py'
        text = render_test(name)
    else:
        rel = f'examples/{name}.py'
        text = render_demo(name)

    path = os.path.join(ROOT, rel)
    if os.path.exists(path) and not args.force:
        print(f'[new] {rel} 已存在 —— 要覆盖请加 --force')
        return 2
    ast.parse(text)                     # 模板自身先过一遍语法
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w', encoding='utf-8', newline='\n').write(text)
    print(f'[new] 已生成 {rel}（{len(text.splitlines())} 行）')

    if args.test and args.register:
        _register_test(name)

    print()
    print('[new] 接下来（**跑一次门禁，它会列出还差哪几处**）：')
    print('      python scripts/verify.py')
    if args.test:
        print(f'      python -m tests.test_{name}          # 先确认骨架能跑')
        print(f"      → 若没自动登记：把 ('tests.test_{name}', "
              f"'ALL_{name.upper()}_TESTS_PASSED', '{name}') 加进 run_all_tests.MODULES")
        print(f'      → README「测试与验收」加一行（门禁 readme-test-modules 会核对）')
    else:
        print(f'      python examples/{name}.py --smoke 3   # 先确认骨架能跑')
        print('      → examples/README.md 加一行（门禁 examples-index-pairs 会核对）')
        print('      → README 的示例数 +1（门禁 readme-demo-count 会核对）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
